"""Accès SQLite : connexion (WAL), schéma, et toutes les requêtes.

Règle de concurrence : **une connexion par thread** (les connexions sqlite3 ne
sont pas sûres à partager entre threads). Chaque fonction prend une `conn`
explicite. WAL permet au thread ingress de lire pendant que le worker écrit.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .models import Brand, InboundMessage, Job, JobStatus, TERMINAL_STATUSES


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_expiry(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Ouvre une connexion configurée (WAL, busy_timeout, Row factory)."""
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


# --- Schéma -----------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS brands (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  da_path    TEXT NOT NULL,
  n_images   INTEGER NOT NULL DEFAULT 6,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_brand (
  channel    TEXT NOT NULL,
  user_ref   TEXT NOT NULL,
  brand_id   TEXT NOT NULL REFERENCES brands(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (channel, user_ref)
);

CREATE TABLE IF NOT EXISTS allowed_users (
  channel    TEXT NOT NULL,
  user_ref   TEXT NOT NULL,
  note       TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (channel, user_ref)
);

CREATE TABLE IF NOT EXISTS jobs (
  id               TEXT PRIMARY KEY,
  channel          TEXT NOT NULL,
  user_ref         TEXT NOT NULL,
  brand_id         TEXT REFERENCES brands(id),
  status           TEXT NOT NULL,
  da_path          TEXT NOT NULL,
  n_images         INTEGER NOT NULL DEFAULT 6,
  reference_path   TEXT NOT NULL,
  attempts         INTEGER NOT NULL DEFAULT 0,
  error            TEXT,
  claimed_by       TEXT,
  lease_expires_at TEXT,
  ack_sent_at      TEXT,
  delivered_at     TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_job_per_user
  ON jobs (channel, user_ref) WHERE status NOT IN ('done','failed');
CREATE INDEX IF NOT EXISTS ix_jobs_queue ON jobs (status, created_at);

CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id  TEXT REFERENCES jobs(id) ON DELETE CASCADE,
  ts      TEXT NOT NULL,
  stage   TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_job ON events (job_id, id);

CREATE TABLE IF NOT EXISTS channel_cursor (
  channel        TEXT PRIMARY KEY,
  last_update_id INTEGER NOT NULL DEFAULT 0
);

-- File d'entrée unifiée : tout message reçu (Telegram long-poll, webhook
-- WhatsApp…) atterrit ici d'abord, puis l'ingress unique la draine. L'unicité
-- (channel, external_id) déduplique les redélivraisons (retries webhook, replays
-- long-poll avant ack).
CREATE TABLE IF NOT EXISTS inbox (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  channel      TEXT NOT NULL,
  external_id  TEXT NOT NULL,
  payload      TEXT NOT NULL,
  received_at  TEXT NOT NULL,
  consumed_at  TEXT,
  UNIQUE (channel, external_id)
);
CREATE INDEX IF NOT EXISTS ix_inbox_unconsumed ON inbox (id) WHERE consumed_at IS NULL;
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# --- Audit ------------------------------------------------------------------
def log_event(conn: sqlite3.Connection, job_id: str | None, stage: str, message: str) -> None:
    conn.execute(
        "INSERT INTO events (job_id, ts, stage, message) VALUES (?,?,?,?)",
        (job_id, now_iso(), stage, message),
    )


# --- Marques ----------------------------------------------------------------
def upsert_brand(conn: sqlite3.Connection, brand: Brand) -> None:
    conn.execute(
        """INSERT INTO brands (id, name, da_path, n_images, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, da_path=excluded.da_path, n_images=excluded.n_images""",
        (brand.id, brand.name, str(brand.da_path), brand.n_images, now_iso()),
    )


def get_brand(conn: sqlite3.Connection, brand_id: str) -> Brand | None:
    row = conn.execute("SELECT * FROM brands WHERE id=?", (brand_id,)).fetchone()
    return _row_to_brand(row) if row else None


def get_brand_for_user(conn: sqlite3.Connection, channel: str, user_ref: str) -> Brand | None:
    row = conn.execute(
        """SELECT b.* FROM user_brand ub JOIN brands b ON b.id = ub.brand_id
           WHERE ub.channel=? AND ub.user_ref=?""",
        (channel, user_ref),
    ).fetchone()
    return _row_to_brand(row) if row else None


def assign_user_brand(conn: sqlite3.Connection, channel: str, user_ref: str, brand_id: str) -> None:
    conn.execute(
        """INSERT INTO user_brand (channel, user_ref, brand_id, created_at)
           VALUES (?,?,?,?)
           ON CONFLICT(channel, user_ref) DO UPDATE SET brand_id=excluded.brand_id""",
        (channel, user_ref, brand_id, now_iso()),
    )


def unassign_user_brand(conn: sqlite3.Connection, channel: str, user_ref: str) -> None:
    """Retire le mapping d'un utilisateur → il retombe sur la marque par défaut."""
    conn.execute("DELETE FROM user_brand WHERE channel=? AND user_ref=?", (channel, user_ref))


def _row_to_brand(row: sqlite3.Row) -> Brand:
    return Brand(
        id=row["id"],
        name=row["name"],
        da_path=Path(row["da_path"]),
        n_images=row["n_images"],
    )


# --- Whitelist sécurité -----------------------------------------------------
def is_allowed(conn: sqlite3.Connection, channel: str, user_ref: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM allowed_users WHERE channel=? AND user_ref=?", (channel, user_ref)
    ).fetchone()
    return row is not None


def allow_user(conn: sqlite3.Connection, channel: str, user_ref: str, note: str | None = None) -> None:
    conn.execute(
        """INSERT INTO allowed_users (channel, user_ref, note, created_at)
           VALUES (?,?,?,?)
           ON CONFLICT(channel, user_ref) DO UPDATE SET note=excluded.note""",
        (channel, user_ref, note, now_iso()),
    )


def deny_user(conn: sqlite3.Connection, channel: str, user_ref: str) -> None:
    conn.execute("DELETE FROM allowed_users WHERE channel=? AND user_ref=?", (channel, user_ref))


def list_allowed(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT channel, user_ref, note, created_at FROM allowed_users ORDER BY created_at"
    ).fetchall()


# --- Jobs : création / verrou ----------------------------------------------
def create_job(
    conn: sqlite3.Connection,
    channel: str,
    user_ref: str,
    brand_id: str | None,
    da_path: Path,
    n_images: int,
    reference_path: Path,
    job_id: str | None = None,
) -> str | None:
    """Insère un job. Retourne l'id, ou None si l'utilisateur a déjà un job actif
    (rejet par l'index unique partiel — atomique, pas de TOCTOU)."""
    job_id = job_id or uuid.uuid4().hex
    ts = now_iso()
    try:
        conn.execute(
            """INSERT INTO jobs
               (id, channel, user_ref, brand_id, status, da_path, n_images,
                reference_path, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, channel, user_ref, brand_id, JobStatus.RECEIVED,
                str(da_path), n_images, str(reference_path), ts, ts,
            ),
        )
    except sqlite3.IntegrityError:
        return None
    return job_id


def active_job_for_user(conn: sqlite3.Connection, channel: str, user_ref: str) -> Job | None:
    row = conn.execute(
        """SELECT * FROM jobs
           WHERE channel=? AND user_ref=? AND status NOT IN ('done','failed')
           ORDER BY created_at DESC LIMIT 1""",
        (channel, user_ref),
    ).fetchone()
    return _row_to_job(row) if row else None


def get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs_by_status(conn: sqlite3.Connection, status: str) -> list[Job]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status=? ORDER BY created_at", (status,)
    ).fetchall()
    return [_row_to_job(r) for r in rows]


# --- Jobs : claim / lease / statut -----------------------------------------
def claim_next_job(conn: sqlite3.Connection, worker_id: str) -> Job | None:
    """Réclame atomiquement le prochain job 'queued' (ou au bail expiré).
    Passe le statut à generating_prompts et pose un bail. Pas de SELECT+UPDATE."""
    now = now_iso()
    row = conn.execute(
        """UPDATE jobs SET status=?, claimed_by=?, lease_expires_at=?, updated_at=?
           WHERE id = (
             SELECT id FROM jobs
             WHERE status='queued'
               AND (lease_expires_at IS NULL OR lease_expires_at < ?)
             ORDER BY created_at LIMIT 1
           )
           RETURNING *""",
        (JobStatus.GENERATING_PROMPTS, worker_id, _lease_expiry(config.LEASE_SECONDS), now, now),
    ).fetchone()
    return _row_to_job(row) if row else None


def renew_lease(conn: sqlite3.Connection, job_id: str, worker_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET lease_expires_at=?, updated_at=? WHERE id=? AND claimed_by=?",
        (_lease_expiry(config.LEASE_SECONDS), now_iso(), job_id, worker_id),
    )


def set_status(conn: sqlite3.Connection, job_id: str, status: JobStatus, error: str | None = None) -> None:
    conn.execute(
        "UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?",
        (status, error, now_iso(), job_id),
    )


def requeue_job(conn: sqlite3.Connection, job_id: str, error: str | None = None) -> None:
    """Remet un job en file après échec transitoire, incrémente attempts, vide le bail."""
    conn.execute(
        """UPDATE jobs SET status='queued', attempts=attempts+1,
           claimed_by=NULL, lease_expires_at=NULL, error=?, updated_at=? WHERE id=?""",
        (error, now_iso(), job_id),
    )


def finish_job(conn: sqlite3.Connection, job_id: str, status: JobStatus, error: str | None = None) -> None:
    """Termine un job (done/failed) et vide le bail."""
    conn.execute(
        """UPDATE jobs SET status=?, error=?, claimed_by=NULL,
           lease_expires_at=NULL, updated_at=? WHERE id=?""",
        (status, error, now_iso(), job_id),
    )


def mark_ack_sent(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET ack_sent_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), job_id))


def mark_delivered(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET delivered_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), job_id))


# --- Recovery au démarrage --------------------------------------------------
def recover_orphans(conn: sqlite3.Connection) -> list[str]:
    """Requeue les jobs non terminaux au bail expiré (worker mort). Retourne les ids."""
    now = now_iso()
    rows = conn.execute(
        """SELECT id FROM jobs
           WHERE status NOT IN ('done','failed','queued')
             AND (lease_expires_at IS NULL OR lease_expires_at < ?)""",
        (now,),
    ).fetchall()
    ids = [r["id"] for r in rows]
    for jid in ids:
        conn.execute(
            """UPDATE jobs SET status='queued', attempts=attempts+1,
               claimed_by=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?""",
            (now, jid),
        )
        log_event(conn, jid, "recover", "requeued orphan (lease expired)")
    return ids


def fail_exhausted(conn: sqlite3.Connection) -> list[Job]:
    """Passe à 'failed' les jobs queued ayant épuisé leurs tentatives. Retourne
    les jobs concernés (pour notifier l'utilisateur)."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status='queued' AND attempts >= ?",
        (config.MAX_ATTEMPTS,),
    ).fetchall()
    jobs = [_row_to_job(r) for r in rows]
    for job in jobs:
        finish_job(conn, job.id, JobStatus.FAILED, error="max attempts exhausted")
        log_event(conn, job.id, "error", "failed: max attempts exhausted")
    return jobs


# --- Curseur d'ingestion ----------------------------------------------------
def get_cursor(conn: sqlite3.Connection, channel: str) -> int:
    row = conn.execute(
        "SELECT last_update_id FROM channel_cursor WHERE channel=?", (channel,)
    ).fetchone()
    return row["last_update_id"] if row else 0


def set_cursor(conn: sqlite3.Connection, channel: str, last_update_id: int) -> None:
    conn.execute(
        """INSERT INTO channel_cursor (channel, last_update_id) VALUES (?,?)
           ON CONFLICT(channel) DO UPDATE SET last_update_id=excluded.last_update_id""",
        (channel, last_update_id),
    )


# --- Inbox unifiée ----------------------------------------------------------
def enqueue_inbound(conn: sqlite3.Connection, msg: InboundMessage, external_id: str) -> bool:
    """Enfile un message normalisé. Retourne True si inséré, False si c'était un
    doublon (même (channel, external_id) déjà présent) — dédup des redélivraisons."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO inbox (channel, external_id, payload, received_at)
           VALUES (?,?,?,?)""",
        (msg.channel, external_id, json.dumps(asdict(msg)), now_iso()),
    )
    return cur.rowcount > 0


def drain_inbox(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Renvoie les messages non consommés, dans l'ordre d'arrivée."""
    return conn.execute(
        "SELECT id, payload FROM inbox WHERE consumed_at IS NULL ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()


def mark_inbox_consumed(conn: sqlite3.Connection, inbox_id: int) -> None:
    conn.execute("UPDATE inbox SET consumed_at=? WHERE id=?", (now_iso(), inbox_id))


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        channel=row["channel"],
        user_ref=row["user_ref"],
        brand_id=row["brand_id"],
        status=JobStatus(row["status"]),
        da_path=Path(row["da_path"]),
        n_images=row["n_images"],
        reference_path=Path(row["reference_path"]),
        attempts=row["attempts"],
        error=row["error"],
        ack_sent_at=row["ack_sent_at"],
        delivered_at=row["delivered_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
