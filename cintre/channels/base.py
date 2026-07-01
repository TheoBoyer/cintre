"""Contrats de canal : la couture qui rend le cœur agnostique à l'UI.

Le canal est scindé en deux responsabilités de natures différentes :

- `Sender` (sortant) : livrer textes/albums, télécharger un média entrant. C'est
  uniformément du *push* (appel API) pour toutes les plateformes — aucune
  divergence. Enregistré dans le `ChannelRegistry`, résolu par `job.channel`.
- `Receiver` (entrant) : alimente la table `inbox`. C'est ici, et ici seulement,
  que push et pull divergent : Telegram *tire* (long-poll), WhatsApp est *poussé*
  (webhook). Chaque receiver confine sa mécanique de transport ; l'ingress ne voit
  qu'une file `inbox` uniforme.

Ajouter un canal = un `Sender` + un `Receiver` + deux lignes dans `app.py`. Le
cœur ne manipule jamais que `InboundMessage` / `OutboundAlbum` et la chaîne opaque
`user_ref` — jamais de détail propre à une plateforme.
"""

from __future__ import annotations

import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from ..models import InboundMessage, OutboundAlbum


class Sender(ABC):
    """Sortant : livraison et téléchargement de média. Push pour tous les canaux."""

    name: str  # identifiant stocké dans jobs.channel (ex 'telegram', 'whatsapp')

    @abstractmethod
    def download_media(self, msg: InboundMessage, dest: Path) -> None:
        """Télécharge le média (photo de référence) du message vers `dest`."""

    @abstractmethod
    def send_text(self, user_ref: str, text: str) -> None:
        """Envoie un message texte à l'utilisateur."""

    @abstractmethod
    def send_album(self, album: OutboundAlbum) -> None:
        """Livre toutes les images (Telegram=sendMediaGroup, WhatsApp=boucle de
        médias, Discord=N pièces jointes, web=push/DB)."""


class Receiver(ABC):
    """Entrant : alimente l'inbox durable. La divergence push/pull vit ici.

    `run` bloque dans son propre thread (une connexion sqlite dédiée) jusqu'à
    `stop`. À chaque message reçu, le receiver appelle `db.enqueue_inbound(conn,
    msg, external_id)` : l'insertion est dédupliquée sur (channel, external_id),
    ce qui absorbe naturellement les redélivraisons (retries webhook Meta,
    redélivrance long-poll avant ack).
    """

    name: str  # même identifiant que le Sender du même canal

    @abstractmethod
    def run(self, conn: sqlite3.Connection, stop: threading.Event) -> None:
        """Boucle de réception : remplit `inbox` jusqu'à `stop`. `conn` est la
        connexion propre au thread du receiver (jamais partagée)."""
