"""Interface Channel : la couture qui rend le cœur agnostique à l'UI.

Ajouter WhatsApp/Discord/web = implémenter cette classe + une ligne de registry.
Le cœur ne manipule jamais que `InboundMessage` / `OutboundAlbum` et la chaîne
opaque `user_ref` — jamais de détail propre à une plateforme.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import InboundMessage, OutboundAlbum


class Channel(ABC):
    name: str  # identifiant stocké dans jobs.channel (ex 'telegram')

    @abstractmethod
    def poll(self, cursor: int) -> tuple[list[InboundMessage], int]:
        """Retourne (messages normalisés, nouveau curseur).

        Pour un canal long-poll (Telegram) : interroge l'API. Pour un canal
        webhook (WhatsApp/web) : draine une table tampon. Le curseur est un
        entier opaque propre au canal.
        """

    @abstractmethod
    def download_media(self, msg: InboundMessage, dest: Path) -> None:
        """Télécharge le média (photo de référence) du message vers `dest`."""

    @abstractmethod
    def send_text(self, user_ref: str, text: str) -> None:
        """Envoie un message texte à l'utilisateur."""

    @abstractmethod
    def send_album(self, album: OutboundAlbum) -> None:
        """Livre toutes les images en UN lot (Telegram=sendMediaGroup,
        Discord=N pièces jointes, WhatsApp=boucle de médias, web=push/DB)."""
