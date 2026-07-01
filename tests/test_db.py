"""Tests de la couche DB : whitelist, marques, verrou actif, claim, recovery."""

from __future__ import annotations

from pathlib import Path

from cintre import config, db
from cintre.models import Brand, JobStatus


def test_whitelist(conn):
    assert db.is_allowed(conn, "telegram", "x") is False
    db.allow_user(conn, "telegram", "x", "owner")
    assert db.is_allowed(conn, "telegram", "x") is True
    db.deny_user(conn, "telegram", "x")
    assert db.is_allowed(conn, "telegram", "x") is False


def test_brand_mapping_and_default(conn):
    # marque par défaut seedée par la fixture
    assert db.get_brand(conn, config.DEFAULT_BRAND_ID) is not None
    db.upsert_brand(conn, Brand("ete", "Été", Path("da/ete.md"), 4))
    db.assign_user_brand(conn, "telegram", "42", "ete")
    b = db.get_brand_for_user(conn, "telegram", "42")
    assert b and b.id == "ete" and b.n_images == 4
    assert db.get_brand_for_user(conn, "telegram", "inconnu") is None


def test_unique_active_job_lock(conn):
    j1 = db.create_job(conn, "telegram", "856", config.DEFAULT_BRAND_ID, Path("da.md"), 6, Path("ref.jpg"))
    assert j1
    j2 = db.create_job(conn, "telegram", "856", config.DEFAULT_BRAND_ID, Path("da.md"), 6, Path("ref.jpg"))
    assert j2 is None  # rejeté par l'index unique partiel
    assert db.active_job_for_user(conn, "telegram", "856").id == j1


def test_atomic_claim_and_unblock(conn):
    j1 = db.create_job(conn, "telegram", "856", config.DEFAULT_BRAND_ID, Path("da.md"), 6, Path("ref.jpg"))
    db.set_status(conn, j1, JobStatus.QUEUED)  # l'ingress fait ça après les artefacts
    claimed = db.claim_next_job(conn, "w1")
    assert claimed.id == j1 and claimed.status == JobStatus.GENERATING_PROMPTS
    assert db.claim_next_job(conn, "w1") is None  # plus rien à réclamer
    db.finish_job(conn, j1, JobStatus.DONE)
    assert db.active_job_for_user(conn, "telegram", "856") is None  # débloqué
    j2 = db.create_job(conn, "telegram", "856", config.DEFAULT_BRAND_ID, Path("da.md"), 6, Path("ref.jpg"))
    assert j2 and j2 != j1


def test_received_job_not_claimable_until_queued(conn):
    # régression de la course ingress/worker : un job 'received' (artefacts pas
    # encore sur disque) ne doit pas être réclamable.
    j = db.create_job(conn, "telegram", "7", config.DEFAULT_BRAND_ID, Path("da.md"), 6, Path("ref.jpg"))
    assert db.get_job(conn, j).status == JobStatus.RECEIVED
    assert db.claim_next_job(conn, "w1") is None  # pas encore réclamable
    db.set_status(conn, j, JobStatus.QUEUED)
    assert db.claim_next_job(conn, "w1").id == j  # réclamable une fois prêt


def test_cursor(conn):
    assert db.get_cursor(conn, "telegram") == 0
    db.set_cursor(conn, "telegram", 99)
    assert db.get_cursor(conn, "telegram") == 99


def test_inbox_enqueue_dedup_drain_consume(conn):
    from conftest import photo

    # premier enfilage : inséré ; redélivrance (même external_id) : ignorée
    assert db.enqueue_inbound(conn, photo("856", 1), external_id="1") is True
    assert db.enqueue_inbound(conn, photo("856", 1), external_id="1") is False
    assert db.enqueue_inbound(conn, photo("856", 2), external_id="2") is True

    rows = db.drain_inbox(conn, limit=10)
    assert len(rows) == 2  # le doublon n'a pas créé de seconde ligne

    # le payload se reconstruit en InboundMessage
    import json
    from cintre.models import InboundMessage

    msg = InboundMessage(**json.loads(rows[0]["payload"]))
    assert msg.channel == "telegram" and msg.user_ref == "856"

    # une fois consommé, il ne ressort plus du drainage
    db.mark_inbox_consumed(conn, rows[0]["id"])
    assert len(db.drain_inbox(conn, limit=10)) == 1


def test_recover_orphans_and_fail_exhausted(conn, monkeypatch):
    j = db.create_job(conn, "telegram", "1", config.DEFAULT_BRAND_ID, Path("da.md"), 6, Path("ref.jpg"))
    db.set_status(conn, j, JobStatus.QUEUED)
    db.claim_next_job(conn, "w1")  # passe en generating_prompts avec un bail
    # forcer le bail expiré
    conn.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (j,))
    recovered = db.recover_orphans(conn)
    assert recovered == [j]
    job = db.get_job(conn, j)
    assert job.status == JobStatus.QUEUED and job.attempts == 1

    # épuisement des tentatives
    monkeypatch.setattr(config, "MAX_ATTEMPTS", 1)
    failed = db.fail_exhausted(conn)
    assert [f.id for f in failed] == [j]
    assert db.get_job(conn, j).status == JobStatus.FAILED
