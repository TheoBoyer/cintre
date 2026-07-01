"""Adaptateur WhatsApp (Meta Cloud API) : Sender (Graph API) + Receiver (webhook).

WhatsApp est *poussé* : Meta POST les messages sur une URL publique. Le Receiver
lance un petit serveur HTTP (stdlib, mono-thread → la connexion sqlite du thread
n'est jamais partagée) qui, à chaque webhook, valide la signature HMAC puis pousse
le message dans l'inbox. Meta livre en at-least-once : la déduplication
`(channel, external_id=wamid)` de l'inbox absorbe les retries.

Différences avec Telegram, portées ici et invisibles pour le cœur :
- pas de `media_group_id` (chaque image est un message isolé ; le verrou « un job
  actif par utilisateur » gère naturellement les envois multiples) ;
- livraison sans album natif → `send_album` boucle (upload média → envoi par id) ;
- téléchargement de média en deux temps (résolution de l'URL, puis binaire).

Hors fenêtre de 24 h après le dernier message utilisateur, Meta n'autorise que des
templates pré-approuvés : les textes libres (ack/échec) supposent qu'on est dans
la fenêtre — vrai tant que la livraison reste rapide.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from .. import config, db
from ..logsetup import get_logger
from ..models import InboundMessage, OutboundAlbum
from .base import Receiver, Sender

log = get_logger("whatsapp")


class WhatsAppSender(Sender):
    name = "whatsapp"

    def __init__(self, token: str, phone_number_id: str) -> None:
        self.token = token
        self.phone_number_id = phone_number_id
        self.graph = f"https://graph.facebook.com/{config.WHATSAPP_GRAPH_VERSION}"

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    # --- Entrée : téléchargement du média de référence ----------------------
    def download_media(self, msg: InboundMessage, dest: Path) -> None:
        if not msg.media_file_id:
            raise ValueError("le message ne contient pas de média")
        # 1) résoudre l'URL temporaire du média depuis son id
        meta = requests.get(
            f"{self.graph}/{msg.media_file_id}", headers=self._auth, timeout=30
        )
        meta.raise_for_status()
        url = meta.json()["url"]
        # 2) télécharger le binaire (l'URL Graph exige aussi le bearer)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, headers=self._auth, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

    # --- Sortie -------------------------------------------------------------
    def send_text(self, user_ref: str, text: str) -> None:
        try:
            requests.post(
                f"{self.graph}/{self.phone_number_id}/messages",
                headers=self._auth,
                json={
                    "messaging_product": "whatsapp",
                    "to": user_ref,
                    "type": "text",
                    "text": {"body": text},
                },
                timeout=30,
            )
        except requests.RequestException:
            pass  # la confirmation n'est pas critique

    def send_album(self, album: OutboundAlbum) -> None:
        """Pas d'album natif : on envoie chaque image individuellement (upload
        média → envoi par id), légende sur la première seulement."""
        for i, path in enumerate(album.image_paths):
            media_id = self._upload_media(path)
            caption = album.caption if i == 0 else None
            self._send_image(album.user_ref, media_id, caption)

    def _upload_media(self, path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        with open(path, "rb") as f:
            resp = requests.post(
                f"{self.graph}/{self.phone_number_id}/media",
                headers=self._auth,
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (path.name, f, mime)},
                timeout=120,
            )
        resp.raise_for_status()
        return resp.json()["id"]

    def _send_image(self, user_ref: str, media_id: str, caption: str | None) -> None:
        image: dict[str, str] = {"id": media_id}
        if caption:
            image["caption"] = caption
        resp = requests.post(
            f"{self.graph}/{self.phone_number_id}/messages",
            headers=self._auth,
            json={
                "messaging_product": "whatsapp",
                "to": user_ref,
                "type": "image",
                "image": image,
            },
            timeout=60,
        )
        resp.raise_for_status()


class WhatsAppReceiver(Receiver):
    name = "whatsapp"

    def __init__(
        self,
        verify_token: str,
        app_secret: str | None,
        host: str,
        port: int,
    ) -> None:
        self.verify_token = verify_token
        self.app_secret = app_secret
        self.host = host
        self.port = port

    def run(self, conn: sqlite3.Connection, stop: threading.Event) -> None:
        server = _WebhookServer((self.host, self.port), _WebhookHandler)
        server.timeout = 1  # handle_request rend la main ~1 s → on relit `stop`
        server.conn = conn
        server.verify_token = self.verify_token
        server.app_secret = self.app_secret
        log.info("receiver WhatsApp démarré (webhook %s:%d)", self.host, self.port)
        try:
            while not stop.is_set():
                server.handle_request()  # mono-thread : même thread que `conn`
        finally:
            server.server_close()


class _WebhookServer(HTTPServer):
    """HTTPServer mono-thread portant l'état partagé avec le handler.

    Mono-thread est délibéré : le handler écrit dans `conn`, qui appartient au
    thread du receiver — pas de ThreadingHTTPServer (violerait la règle une
    connexion par thread). Le volume webhook est faible, c'est amplement suffisant.
    """

    conn: sqlite3.Connection
    verify_token: str
    app_secret: str | None

    def verify_signature(self, body: bytes, header: str | None) -> bool:
        if not self.app_secret:
            return True  # pas de secret configuré (dev) → on ne vérifie pas
        if not header or not header.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(
            self.app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, header)

    def ingest(self, body: bytes) -> None:
        payload = json.loads(body.decode("utf-8"))
        n = 0
        for msg, external_id in _parse_webhook(payload):
            if db.enqueue_inbound(self.conn, msg, external_id=external_id):
                n += 1
        if n:
            log.info("%d message(s) WhatsApp enfilé(s)", n)


class _WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence le logging par défaut
        pass

    def do_GET(self) -> None:
        """Handshake de vérification Meta : renvoie hub.challenge si le token colle."""
        params = parse_qs(urlparse(self.path).query)
        mode = params.get("hub.mode", [""])[0]
        token = params.get("hub.verify_token", [""])[0]
        challenge = params.get("hub.challenge", [""])[0]
        if mode == "subscribe" and token == self.server.verify_token:
            self._respond(200, challenge.encode())
        else:
            self._respond(403, b"forbidden")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        if not self.server.verify_signature(body, self.headers.get("X-Hub-Signature-256")):
            self._respond(403, b"bad signature")
            return
        # Acquitter vite (Meta retente sur non-2xx) ; le traitement suit.
        self._respond(200, b"ok")
        try:
            self.server.ingest(body)
        except Exception as exc:  # payload inattendu : on log, l'ack est déjà envoyé
            log.warning("webhook WhatsApp illisible : %s", exc)

    def _respond(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _parse_webhook(payload: dict) -> list[tuple[InboundMessage, str]]:
    """Extrait les messages entrants d'un payload webhook Meta.

    On ignore les événements de statut (accusés de livraison/lecture) : seuls les
    messages `image`/`document image`/`text` produisent un InboundMessage.
    """
    out: list[tuple[InboundMessage, str]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                parsed = _message_to_inbound(message)
                if parsed is not None:
                    out.append((parsed, message["id"]))
    return out


def _message_to_inbound(message: dict) -> InboundMessage | None:
    user_ref = message.get("from")
    if not user_ref:
        return None
    mtype = message.get("type")
    media_file_id: str | None = None
    caption: str | None = None
    if mtype == "image":
        media_file_id = message.get("image", {}).get("id")
        caption = message.get("image", {}).get("caption")
    elif mtype == "document":
        document = message.get("document", {})
        if str(document.get("mime_type", "")).startswith("image/"):
            media_file_id = document.get("id")
            caption = document.get("caption")
    elif mtype == "text":
        caption = message.get("text", {}).get("body")
    else:
        return None  # audio, sticker, réactions… non pris en charge
    return InboundMessage(
        channel="whatsapp",
        user_ref=str(user_ref),
        update_id=0,  # WhatsApp n'a pas d'offset ; dedup via external_id=wamid
        media_group_id=None,  # pas d'album natif WhatsApp
        media_file_id=media_file_id,
        raw=message,
        caption=caption,
    )
