"""Registre des senders : name -> instance Sender.

Permet à la livraison (worker/runner) et à l'ingress de reconstruire le bon
`Sender` depuis `job.channel` / `msg.channel`, sans dépendre de la socket
d'origine. Ajouter un canal = enregistrer son Sender ici.
"""

from __future__ import annotations

from .base import Sender


class ChannelRegistry:
    def __init__(self) -> None:
        self._senders: dict[str, Sender] = {}

    def register(self, sender: Sender) -> None:
        self._senders[sender.name] = sender

    def get(self, name: str) -> Sender:
        try:
            return self._senders[name]
        except KeyError as exc:
            raise KeyError(f"canal inconnu : {name!r}") from exc

    def all(self) -> list[Sender]:
        return list(self._senders.values())
