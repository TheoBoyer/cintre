"""Boucle d'ingestion : reçoit les messages d'un canal et crée les jobs.

Ordre des contrôles (sécurité d'abord) :
  1. whitelist : un expéditeur non autorisé est ignoré (audité), avant tout
     téléchargement de média ou création de job ;
  2. résolution de la marque (→ DA + n_images) ;
  3. création atomique du job (l'index unique partiel garantit « un seul job
     actif par utilisateur ») : gagné → ack unique ; rejeté → message « déjà
     en cours », dédupliqué par media_group_id (un album = N updates).
Le curseur n'avance qu'après persistance (idempotence à la redélivraison).
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid

from . import brands, config, db
from .channels.base import Channel
from .jobstore import JobStore
from .logsetup import get_logger
from .models import InboundMessage, JobStatus

log = get_logger("ingress")


class Ingress:
    def __init__(self, conn: sqlite3.Connection, channel: Channel, store: JobStore) -> None:
        self.conn = conn
        self.channel = channel
        self.store = store
        # media_group_id récemment rejetés → évite N messages de rejet pour un album.
        self._recent_reject_groups: set[str] = set()

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                cursor = db.get_cursor(self.conn, self.channel.name)
                messages, new_cursor = self.channel.poll(cursor)
                if messages:
                    log.info("%d message(s) reçu(s)", len(messages))
                for msg in messages:
                    self._handle(msg)
                if new_cursor != cursor:
                    db.set_cursor(self.conn, self.channel.name, new_cursor)
            except Exception as exc:  # réseau, API… on log et on retente
                db.log_event(self.conn, None, "ingress", f"poll error: {exc}")
                log.warning("erreur de poll : %s", exc)
                time.sleep(3)

    def _handle(self, msg: InboundMessage) -> None:
        ch, user = msg.channel, msg.user_ref

        # 1. Whitelist (avant tout). Non autorisé → ignorer + auditer.
        if not db.is_allowed(self.conn, ch, user):
            db.log_event(self.conn, None, "ingress", f"denied (not allowed): {ch}:{user}")
            log.warning("refusé (non autorisé) : %s:%s", ch, user)
            if config.DENY_TEXT:
                self.channel.send_text(user, config.DENY_TEXT)
            return
        log.info("message autorisé de %s:%s (photo=%s)", ch, user, bool(msg.media_file_id))

        # Pas une photo → on guide l'utilisateur (seulement s'il n'a pas de job en cours).
        if not msg.media_file_id:
            if db.active_job_for_user(self.conn, ch, user) is None:
                self.channel.send_text(user, config.NEED_PHOTO_TEXT)
            return

        # 2. Marque (→ DA + n_images), pinnée dans le job.
        brand = brands.resolve_brand(self.conn, ch, user)

        # 3. Création atomique du job. On alloue l'id ici pour connaître le
        #    chemin de référence dès l'insertion.
        job_id = uuid.uuid4().hex
        created = db.create_job(
            self.conn, ch, user, brand.id, brand.da_path, brand.n_images,
            reference_path=self.store.reference_path(job_id), job_id=job_id,
        )

        if created is None:
            # Rejet : un job est déjà actif. Dédupliquer par album.
            self._reject(msg)
            return

        # Gagné : on matérialise le dossier et les artefacts, puis on ack.
        try:
            self.store.init_job_dir(job_id)
            self.channel.download_media(msg, self.store.reference_path(job_id))
            self.store.copy_da(job_id, brand.da_path)
            note = (msg.caption or "").strip()
            if note:
                self.store.save_note(job_id, note)
            self._write_meta(job_id, msg, brand)
            # Tous les artefacts sont sur disque : le job devient réclamable.
            db.set_status(self.conn, job_id, JobStatus.QUEUED)
            db.log_event(self.conn, job_id, "ingress", f"job created for {ch}:{user}")
            log.info("job %s créé pour %s:%s (note=%s)", job_id, ch, user, bool(note))
        except Exception as exc:
            db.finish_job(self.conn, job_id, JobStatus.FAILED, error=f"ingress error: {exc}")
            db.log_event(self.conn, job_id, "error", f"ingress failed: {exc}")
            log.exception("ingress a échoué pour le job %s", job_id)
            self.channel.send_text(user, config.FAILED_TEXT)
            return

        # Ack unique, lié à la création réussie.
        self.channel.send_text(user, config.ACK_TEXT)
        db.mark_ack_sent(self.conn, job_id)

    def _reject(self, msg: InboundMessage) -> None:
        gid = msg.media_group_id
        if gid is not None:
            if gid in self._recent_reject_groups:
                return  # déjà rejeté ce même album
            self._recent_reject_groups.add(gid)
            if len(self._recent_reject_groups) > 256:
                self._recent_reject_groups.clear()
        db.log_event(self.conn, None, "ingress", f"rejected (job in progress): {msg.user_ref}")
        log.info("rejet (job déjà en cours) pour %s", msg.user_ref)
        self.channel.send_text(msg.user_ref, config.REJECT_TEXT)

    def _write_meta(self, job_id: str, msg: InboundMessage, brand) -> None:
        self.store.write_meta(
            job_id,
            {
                "id": job_id,
                "channel": msg.channel,
                "user_ref": msg.user_ref,
                "brand_id": brand.id,
                "brand_name": brand.name,
                "n_images": brand.n_images,
                "da_source": str(brand.da_path),
                "note": (msg.caption or "").strip() or None,
            },
        )
