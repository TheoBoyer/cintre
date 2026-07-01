"""Entrypoint : initialise la DB, seede, démarre ingress + worker.

Lancement :
    uv run python -m cintre.app

Deux threads partagent la DB (WAL) et le système de fichiers, chacun avec sa
propre connexion SQLite (les connexions ne se partagent pas entre threads).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading

from dotenv import load_dotenv

from . import brands, config, db
from .channels.base import Receiver
from .channels.registry import ChannelRegistry
from .channels.telegram import TelegramReceiver, TelegramSender
from .channels.whatsapp import WhatsAppReceiver, WhatsAppSender
from .ingress import Ingress
from .jobstore import JobStore
from .logsetup import get_logger, setup_logging
from .pipeline import anonymize
from .web import StaticServer
from .worker import Worker

log = get_logger("app")


def bootstrap(conn) -> None:
    """Schéma + seeds idempotents (marque par défaut + owner whitelisté)."""
    db.init_schema(conn)
    brands.seed_default_brand(conn)
    for channel, user_ref in config.OWNER_USERS:
        db.allow_user(conn, channel, user_ref, note="owner")


def build_channels() -> tuple[ChannelRegistry, list[Receiver]]:
    """Assemble le registry des senders et la liste des receivers depuis l'env.

    Telegram est requis ; WhatsApp s'active seulement si ses variables sont
    présentes. Ajouter un canal = une ligne sender + une ligne receiver ici.
    """
    senders = ChannelRegistry()
    receivers: list[Receiver] = []

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant (cf. .env).")
    senders.register(TelegramSender(token))
    receivers.append(TelegramReceiver(token))

    wa_token = os.environ.get("WHATSAPP_TOKEN")
    wa_phone = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    wa_verify = os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if wa_token and wa_phone and wa_verify:
        host = os.environ.get("WHATSAPP_WEBHOOK_HOST", config.WHATSAPP_WEBHOOK_HOST)
        port = int(os.environ.get("WHATSAPP_WEBHOOK_PORT", config.WHATSAPP_WEBHOOK_PORT))
        senders.register(WhatsAppSender(wa_token, wa_phone))
        receivers.append(
            WhatsAppReceiver(wa_verify, os.environ.get("WHATSAPP_APP_SECRET"), host, port)
        )
        log.info("WhatsApp activé (webhook %s:%d)", host, port)
    else:
        log.info("WhatsApp désactivé (variables WHATSAPP_* absentes)")

    return senders, receivers


def _parse_args() -> argparse.Namespace:
    """Args CLI. Précédence : argument CLI > variable d'env > défaut config."""
    parser = argparse.ArgumentParser(prog="cintre", description="Bot cintre + site vitrine.")
    parser.add_argument(
        "--web-host",
        default=os.environ.get("CINTRE_WEB_HOST", config.WEB_HOST),
        help=f"interface d'écoute du site vitrine (défaut : {config.WEB_HOST})",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=int(os.environ.get("CINTRE_WEB_PORT", config.WEB_PORT)),
        help=f"port du site vitrine (défaut : {config.WEB_PORT})",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()  # avant _parse_args : les défauts peuvent venir du .env
    args = _parse_args()
    setup_logging()
    if not config.DEFAULT_DA_PATH.exists():
        raise SystemExit(f"DA par défaut introuvable : {config.DEFAULT_DA_PATH}")

    senders, receivers = build_channels()

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
    # Threads : 1 ingress (draine l'inbox) + 1 worker + 1 receiver par canal.
    def _ingress_target() -> None:
        Ingress(db.connect(), senders, store).run_forever(stop)

    def _worker_target() -> None:
        Worker(db.connect(), store, senders).run_forever(stop)

    def _receiver_target(receiver: Receiver) -> None:
        receiver.run(db.connect(), stop)

    def _web_target() -> None:
        # Sert des fichiers uniquement (pas de DB) → pas de db.connect() ici.
        StaticServer(args.web_host, args.web_port, config.LANDING_DIR).run(stop)

    threads = [
        threading.Thread(target=_ingress_target, name="ingress", daemon=True),
        threading.Thread(target=_worker_target, name="worker", daemon=True),
    ]
    for receiver in receivers:
        threads.append(
            threading.Thread(
                target=_receiver_target, args=(receiver,),
                name=f"receiver-{receiver.name}", daemon=True,
            )
        )
    if config.WEB_ENABLED and not os.environ.get("CINTRE_WEB_DISABLE"):
        threads.append(threading.Thread(target=_web_target, name="web", daemon=True))

    log.info("cintre démarré. Jobs dans : %s", config.JOBS_DIR)
    log.info("Utilisateurs autorisés (seed) : %s", config.OWNER_USERS)
    log.info("Canaux actifs : %s", [r.name for r in receivers])
    log.info("Ingress + worker + receivers actifs. Ctrl+C pour arrêter.")
    for t in threads:
        t.start()

    try:
        while not stop.is_set():
            stop.wait(0.5)
    finally:
        for t in threads:
            t.join(timeout=5)
    sys.exit(0)


if __name__ == "__main__":
    main()
