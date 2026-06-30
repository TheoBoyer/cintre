"""Configuration du logging applicatif.

Affiche sur stdout (visible quand on lance `uv run autopub`) et écrit aussi
dans `autopub.log`. Niveau réglable via la variable d'env AUTOPUB_LOG_LEVEL
(défaut INFO ; mettre DEBUG pour plus de détail).
"""

from __future__ import annotations

import logging
import os
import sys

from . import config

_CONFIGURED = False


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, os.environ.get("AUTOPUB_LOG_LEVEL", "INFO").upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"

    root = logging.getLogger("autopub")
    root.setLevel(level)
    root.handlers.clear()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(stream)

    try:
        fileh = logging.FileHandler(config.ROOT / "autopub.log", encoding="utf-8")
        fileh.setFormatter(logging.Formatter(fmt, datefmt))
        root.addHandler(fileh)
    except OSError:
        pass  # pas de fichier de log => on garde au moins stdout

    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"autopub.{name}")
