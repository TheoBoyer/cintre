"""Adaptateur Telegram (long-polling + sendMediaGroup).

Port des primitives de l'ancien main.py, avec en plus la normalisation vers
`InboundMessage` (photo vs document, media_group_id) et la livraison en album.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .. import config
from ..logsetup import get_logger
from ..models import InboundMessage, OutboundAlbum
from .base import Channel

log = get_logger("telegram")


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, token: str) -> None:
        self.token = token
        self.api = f"https://api.telegram.org/bot{token}"

    # --- Ingestion ----------------------------------------------------------
    def poll(self, cursor: int) -> tuple[list[InboundMessage], int]:
        """Long-poll getUpdates. `cursor` = dernier update_id traité.
        Telegram acquitte via offset = cursor + 1.

        Les coupures réseau (reset de connexion en attente, timeouts) sont
        normales avec le long-polling : on les avale en silence (niveau debug)
        et on rend une liste vide — la boucle ingress réessaiera."""
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
            return [], cursor

        messages: list[InboundMessage] = []
        new_cursor = cursor
        for update in updates:
            new_cursor = max(new_cursor, update["update_id"])
            msg = self._to_inbound(update)
            if msg is not None:
                messages.append(msg)
        return messages, new_cursor

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

    # --- Sortie -------------------------------------------------------------
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


def _extract_photo_file_id(message: dict) -> str | None:
    """file_id de la meilleure résolution (photo compressée ou document image)."""
    if message.get("photo"):
        return message["photo"][-1]["file_id"]
    document = message.get("document")
    if document and str(document.get("mime_type", "")).startswith("image/"):
        return document["file_id"]
    return None
