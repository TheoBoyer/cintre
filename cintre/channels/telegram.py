"""Adaptateur Telegram : Sender (sendMediaGroup) + Receiver (long-polling).

Le Receiver tire les updates via getUpdates et les pousse dans l'inbox ; le
curseur `channel_cursor` sert d'offset d'acquittement (Telegram cesse de
redélivrer une fois l'offset avancé). Le Sender livre album/texte et télécharge
la référence.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import requests

from .. import config, db
from ..logsetup import get_logger
from ..models import InboundMessage, OutboundAlbum
from .base import Receiver, Sender

log = get_logger("telegram")


class TelegramSender(Sender):
    name = "telegram"

    def __init__(self, token: str) -> None:
        self.token = token
        self.api = f"https://api.telegram.org/bot{token}"

    def download_media(self, msg: InboundMessage, dest: Path) -> None:
        if not msg.media_file_id:
            raise ValueError("le message ne contient pas de média")
        meta = requests.get(
            f"{self.api}/getFile", params={"file_id": msg.media_file_id}, timeout=30
        )
        meta.raise_for_status()
        file_path = meta.json()["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

    def send_text(self, user_ref: str, text: str) -> None:
        try:
            requests.post(
                f"{self.api}/sendMessage",
                json={"chat_id": user_ref, "text": text},
                timeout=30,
            )
        except requests.RequestException:
            pass  # la confirmation n'est pas critique

    def send_album(self, album: OutboundAlbum) -> None:
        """Envoie toutes les images en un seul message via sendMediaGroup.
        Telegram limite un album à 10 médias → on découpe par lots de 10."""
        paths = list(album.image_paths)
        for batch_start in range(0, len(paths), 10):
            batch = paths[batch_start : batch_start + 10]
            media = []
            files = {}
            for i, p in enumerate(batch):
                key = f"photo{i}"
                item = {"type": "photo", "media": f"attach://{key}"}
                if i == 0 and album.caption and batch_start == 0:
                    item["caption"] = album.caption
                media.append(item)
                files[key] = open(p, "rb")
            try:
                resp = requests.post(
                    f"{self.api}/sendMediaGroup",
                    data={"chat_id": album.user_ref, "media": json.dumps(media)},
                    files=files,
                    timeout=120,
                )
                resp.raise_for_status()
            finally:
                for fh in files.values():
                    fh.close()


class TelegramReceiver(Receiver):
    name = "telegram"

    def __init__(self, token: str) -> None:
        self.api = f"https://api.telegram.org/bot{token}"

    def run(self, conn: sqlite3.Connection, stop: threading.Event) -> None:
        log.info("receiver Telegram démarré (long-poll)")
        while not stop.is_set():
            try:
                self._poll_once(conn)
            except Exception as exc:  # réseau, API… on log et on retente
                db.log_event(conn, None, "receiver", f"telegram poll error: {exc}")
                log.warning("erreur de poll : %s", exc)
                time.sleep(3)

    def _poll_once(self, conn: sqlite3.Connection) -> None:
        """Long-poll getUpdates. Le curseur = dernier update_id acquitté ;
        l'offset avance après enfilage dans l'inbox (durable), donc une coupure
        entre enqueue et set_cursor est idempotente : la redélivrance est
        dédupliquée par (channel, external_id)."""
        cursor = db.get_cursor(conn, self.name)
        try:
            resp = requests.get(
                f"{self.api}/getUpdates",
                params={"offset": cursor + 1, "timeout": config.INGRESS_POLL_TIMEOUT},
                timeout=config.INGRESS_POLL_TIMEOUT + 10,
            )
            resp.raise_for_status()
            updates = resp.json()["result"]
        except requests.RequestException as exc:
            log.debug("poll réseau interrompu (normal en long-poll) : %s", exc)
            time.sleep(3)  # léger backoff pour ne pas boucler en cas de panne réelle
            return

        new_cursor = cursor
        n_enqueued = 0
        for update in updates:
            new_cursor = max(new_cursor, update["update_id"])
            msg = self._to_inbound(update)
            if msg is not None:
                if db.enqueue_inbound(conn, msg, external_id=str(update["update_id"])):
                    n_enqueued += 1
        if n_enqueued:
            log.info("%d message(s) enfilé(s)", n_enqueued)
        if new_cursor != cursor:
            db.set_cursor(conn, self.name, new_cursor)

    def _to_inbound(self, update: dict) -> InboundMessage | None:
        message = update.get("message") or update.get("channel_post")
        if not message:
            return None
        chat = message.get("chat")
        if not chat:
            return None
        return InboundMessage(
            channel=self.name,
            user_ref=str(chat["id"]),
            update_id=update["update_id"],
            media_group_id=message.get("media_group_id"),
            media_file_id=_extract_photo_file_id(message),
            raw=update,
            caption=(message.get("caption") or message.get("text")),
        )


def _extract_photo_file_id(message: dict) -> str | None:
    """file_id de la meilleure résolution (photo compressée ou document image)."""
    if message.get("photo"):
        return message["photo"][-1]["file_id"]
    document = message.get("document")
    if document and str(document.get("mime_type", "")).startswith("image/"):
        return document["file_id"]
    return None
