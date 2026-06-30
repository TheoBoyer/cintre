"""Registre des canaux : name -> instance Channel.

Permet à la livraison de reconstruire le bon canal depuis `job.channel` lu en
DB, sans dépendre de la socket d'origine. Ajouter un canal = une ligne ici.
"""

from __future__ import annotations

from .base import Channel


class ChannelRegistry:
    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, channel: Channel) -> None:
        self._channels[channel.name] = channel

    def get(self, name: str) -> Channel:
        try:
            return self._channels[name]
        except KeyError as exc:
            raise KeyError(f"canal inconnu : {name!r}") from exc

    def all(self) -> list[Channel]:
        return list(self._channels.values())
