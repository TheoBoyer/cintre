"""Entrypoint : initialise la DB, seede, démarre ingress + worker.

Lancement :
    uv run python -m cintre.app

Deux threads partagent la DB (WAL) et le système de fichiers, chacun avec sa
propre connexion SQLite (les connexions ne se partagent pas entre threads).
"""

from __future__ import annotations

import os
import signal
import sys
import threading

from dotenv import load_dotenv

from . import brands, config, db
from .channels.registry import ChannelRegistry
from .channels.telegram import TelegramChannel
from .ingress import Ingress
from .jobstore import JobStore
from .logsetup import get_logger, setup_logging
from .pipeline import anonymize
from .worker import Worker

log = get_logger("app")


def bootstrap(conn) -> None:
    """Schéma + seeds idempotents (marque par défaut + owner whitelisté)."""
    db.init_schema(conn)
    brands.seed_default_brand(conn)
    for channel, user_ref in config.OWNER_USERS:
        db.allow_user(conn, channel, user_ref, note="owner")


def main() -> None:
    load_dotenv()
    setup_logging()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant (cf. .env).")
    if not config.DEFAULT_DA_PATH.exists():
        raise SystemExit(f"DA par défaut introuvable : {config.DEFAULT_DA_PATH}")

    # Init (connexion dédiée au bootstrap).
    boot_conn = db.connect()
    bootstrap(boot_conn)
    boot_conn.close()

    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)

    # Récupère le modèle d'anonymisation maintenant (best-effort). En cas d'échec
    # réseau, on démarre quand même : l'anonymisation réessaiera et, à défaut,
    # échouera le job (fail-closed) plutôt que de laisser passer un visage réel.
    try:
        anonymize.ensure_model()
    except Exception as exc:
        log.warning("modèle d'anonymisation non récupéré au démarrage : %s", exc)

    store = JobStore()
    stop = threading.Event()

    def _shutdown(*_):
        print("\nArrêt en cours…")
        stop.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # IMPORTANT : une connexion sqlite3 ne peut servir que dans le thread qui
    # l'a créée. Chaque thread ouvre donc la sienne dans sa fonction cible.
    def _ingress_target() -> None:
        ingress = Ingress(db.connect(), TelegramChannel(token), store)
        ingress.run_forever(stop)

    def _worker_target() -> None:
        registry = ChannelRegistry()
        registry.register(TelegramChannel(token))
        worker = Worker(db.connect(), store, registry)
        worker.run_forever(stop)

    t_ingress = threading.Thread(target=_ingress_target, name="ingress", daemon=True)
    t_worker = threading.Thread(target=_worker_target, name="worker", daemon=True)

    log.info("cintre démarré. Jobs dans : %s", config.JOBS_DIR)
    log.info("Utilisateurs autorisés (seed) : %s", config.OWNER_USERS)
    log.info("Ingress + worker actifs. Ctrl+C pour arrêter.")
    t_ingress.start()
    t_worker.start()

    try:
        while not stop.is_set():
            stop.wait(0.5)
    finally:
        t_ingress.join(timeout=5)
        t_worker.join(timeout=5)
    sys.exit(0)


if __name__ == "__main__":
    main()
