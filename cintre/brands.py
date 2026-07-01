"""Résolution marque pour un utilisateur, et seed de la marque par défaut."""

from __future__ import annotations

import sqlite3

from . import config, db
from .models import Brand


def default_brand() -> Brand:
    return Brand(
        id=config.DEFAULT_BRAND_ID,
        name=config.DEFAULT_BRAND_NAME,
        da_path=config.DEFAULT_DA_PATH,
        n_images=config.DEFAULT_N_IMAGES,
    )


def seed_default_brand(conn: sqlite3.Connection) -> None:
    """Crée/maj la marque par défaut si besoin (idempotent)."""
    db.upsert_brand(conn, default_brand())


def resolve_brand(conn: sqlite3.Connection, channel: str, user_ref: str) -> Brand:
    """Marque de l'utilisateur si mappée, sinon la marque par défaut."""
    brand = db.get_brand_for_user(conn, channel, user_ref)
    if brand is not None:
        return brand
    brand = db.get_brand(conn, config.DEFAULT_BRAND_ID)
    return brand if brand is not None else default_brand()
