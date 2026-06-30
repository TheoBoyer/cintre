"""Worker : reprend les jobs orphelins au démarrage, puis traite la file.

Un seul worker pour l'instant, mais le claim est atomique et basé sur un bail :
passer à un pool = lancer K instances, sans rien changer d'autre.
"""

from __future__ import annotations

import sqlite3
import threading
import time

from . import config, db
from .channels.registry import ChannelRegistry
from .jobstore import JobStore
from .logsetup import get_logger
from .models import JobStatus
from .pipeline.runner import run_job

log = get_logger("worker")


class Worker:
    def __init__(
        self,
        conn: sqlite3.Connection,
        store: JobStore,
        channels: ChannelRegistry,
        worker_id: str = "worker-1",
    ) -> None:
        self.conn = conn
        self.store = store
        self.channels = channels
        self.worker_id = worker_id

    def run_forever(self, stop: threading.Event) -> None:
        self._startup_recovery()
        log.info("worker %s prêt, en attente de jobs", self.worker_id)
        while not stop.is_set():
            job = db.claim_next_job(self.conn, self.worker_id)
            if job is None:
                time.sleep(config.WORKER_IDLE_SLEEP)
                continue
            self._process(job)

    # --- Démarrage ----------------------------------------------------------
    def _startup_recovery(self) -> None:
        self._recover_incomplete_ingress()
        orphans = db.recover_orphans(self.conn)
        if orphans:
            log.warning("reprise : %d job(s) orphelin(s) remis en file", len(orphans))
            db.log_event(self.conn, None, "recover", f"requeued {len(orphans)} orphan(s)")
        self._notify_exhausted()

    def _recover_incomplete_ingress(self) -> None:
        """Jobs restés en 'received' (ingress interrompu) : promus en 'queued'
        si la référence est là, sinon échoués."""
        for job in db.list_jobs_by_status(self.conn, JobStatus.RECEIVED):
            if self.store.reference_path(job.id).exists():
                db.set_status(self.conn, job.id, JobStatus.QUEUED)
                log.warning("reprise : job 'received' %s promu en 'queued'", job.id)
            else:
                db.finish_job(self.conn, job.id, JobStatus.FAILED, error="ingress incomplet")
                log.warning("reprise : job 'received' %s échoué (référence absente)", job.id)
                self._notify_failure(job.channel, job.user_ref)

    def _notify_exhausted(self) -> None:
        """Passe à failed les jobs ayant épuisé leurs tentatives et prévient l'utilisateur."""
        for job in db.fail_exhausted(self.conn):
            log.warning("job %s échoué (tentatives épuisées)", job.id)
            self._notify_failure(job.channel, job.user_ref)

    # --- Traitement d'un job ------------------------------------------------
    def _process(self, job) -> None:
        log.info("job %s réclamé (tentative %d) — début du traitement", job.id, job.attempts)
        try:
            status = run_job(self.conn, self.store, job, self.channels, self.worker_id)
            log.info("job %s terminé : %s", job.id, status)
        except Exception as exc:
            # Échec transitoire : on requeue et on incrémente attempts. Si le
            # quota est atteint, fail_exhausted le finalisera et notifiera.
            log.exception("job %s a planté : %s", job.id, exc)
            db.log_event(self.conn, job.id, "error", f"run_job failed: {exc}")
            db.requeue_job(self.conn, job.id, error=str(exc))
            for failed in db.fail_exhausted(self.conn):
                log.warning("job %s définitivement échoué", failed.id)
                self._notify_failure(failed.channel, failed.user_ref)

    def _notify_failure(self, channel: str, user_ref: str) -> None:
        try:
            self.channels.get(channel).send_text(user_ref, config.FAILED_TEXT)
        except Exception as exc:
            log.error("notification d'échec impossible (%s:%s) : %s", channel, user_ref, exc)
            db.log_event(self.conn, None, "error", f"failure notice failed: {exc}")
