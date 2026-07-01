"""Fixtures partagées : DB temporaire, JobStore, faux canal, faux pipeline.

Aucun test ne touche claude/codex/Telegram : le pipeline est monkeypatché et le
canal est un double en mémoire.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from cintre import brands, db
from cintre.channels.base import Sender
from cintre.channels.registry import ChannelRegistry
from cintre.models import InboundMessage


class FakeChannel(Sender):
    """Sender en mémoire : enregistre textes et albums, fabrique des médias bidon."""

    name = "telegram"

    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.albums = []

    def download_media(self, msg, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"FAKEJPEG")

    def send_text(self, user_ref, text):
        self.texts.append((user_ref, text))

    def send_album(self, album):
        self.albums.append(album)


def photo(uid: str, update_id: int, gid: str | None = None) -> InboundMessage:
    return InboundMessage("telegram", uid, update_id, gid, f"file{update_id}", {})


def text_msg(uid: str, update_id: int) -> InboundMessage:
    return InboundMessage("telegram", uid, update_id, None, None, {})


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.sqlite")
    db.init_schema(c)
    brands.seed_default_brand(c)
    yield c
    c.close()


@pytest.fixture
def store(tmp_path):
    from cintre.jobstore import JobStore

    return JobStore(tmp_path / "jobs")


@pytest.fixture
def channel():
    return FakeChannel()


@pytest.fixture
def registry(channel):
    r = ChannelRegistry()
    r.register(channel)
    return r


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Remplace les appels claude/codex par des doubles déterministes.
    Retourne un compteur d'appels que les tests peuvent inspecter."""
    from cintre.pipeline import runner

    calls = {"prompts": 0, "images": 0}
    lock = threading.Lock()  # generate_image tourne en parallèle (pool de threads)

    def fake_prompts(image_path, da_path, n, model=None, log_path=None, user_note=None):
        calls["prompts"] += 1
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("fake")
        return [f"prompt {i}" for i in range(1, n + 1)]

    def fake_image(prompt, ref, out, model=None, log_path=None):
        with lock:
            calls["images"] += 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PNGDATA")
        if log_path:
            log_path.write_text("fake")
        return 100  # tokens fictifs

    def fake_anon(src, dst, method=None, pad=None, score_threshold=None):
        calls["anon"] = calls.get("anon", 0) + 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes() if src.exists() else b"X")
        return 0

    monkeypatch.setattr(runner, "generate_prompts", fake_prompts)
    monkeypatch.setattr(runner, "generate_image", fake_image)
    monkeypatch.setattr(runner, "anonymize_image", fake_anon)
    return calls
