"""Modèles de données partagés : statuts, messages normalisés, job, marque."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class JobStatus(StrEnum):
    RECEIVED = "received"        # créé, artefacts pas encore tous sur disque (non réclamable)
    QUEUED = "queued"
    GENERATING_PROMPTS = "generating_prompts"
    PROMPTS_READY = "prompts_ready"
    GENERATING_IMAGES = "generating_images"
    IMAGES_READY = "images_ready"
    DELIVERING = "delivering"
    DONE = "done"
    FAILED = "failed"


# États terminaux : ils débloquent l'utilisateur (cf. index unique partiel).
TERMINAL_STATUSES = frozenset({JobStatus.DONE, JobStatus.FAILED})


@dataclass(frozen=True)
class InboundMessage:
    """Message entrant normalisé, indépendant du canal."""

    channel: str
    user_ref: str               # chaîne opaque (chat_id Telegram, n° WhatsApp, ...)
    update_id: int
    media_group_id: str | None  # regroupe les éléments d'un album
    media_file_id: str | None   # None => ce n'est pas une photo
    raw: dict
    caption: str | None = None  # texte joint à la photo (ex: "la fille de gauche")


@dataclass(frozen=True)
class OutboundAlbum:
    """Lot d'images à livrer en un seul message."""

    user_ref: str
    image_paths: list[Path]
    caption: str | None = None


@dataclass
class Brand:
    id: str
    name: str
    da_path: Path
    n_images: int


@dataclass
class Job:
    id: str
    channel: str
    user_ref: str
    brand_id: str | None
    status: JobStatus
    da_path: Path
    n_images: int
    reference_path: Path
    attempts: int
    error: str | None
    ack_sent_at: str | None
    delivered_at: str | None
    created_at: str
    updated_at: str
