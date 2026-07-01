"""Vérifie que ingress + worker tournent dans de vrais threads, chacun avec sa
propre connexion sqlite (régression du bug 'SQLite objects in another thread').

L'ingress draine l'inbox : on y pré-enfile un message, puis on laisse les threads
créer le job (ingress) et livrer l'album (worker)."""

from __future__ import annotations

import threading
import time

from cintre import brands, db
from cintre.ingress import Ingress
from cintre.jobstore import JobStore
from cintre.models import JobStatus
from cintre.worker import Worker

from conftest import FakeChannel, photo


def test_threads_each_own_connection(tmp_path, monkeypatch):
    db_path = tmp_path / "t.sqlite"
    boot = db.connect(db_path)
    db.init_schema(boot)
    brands.seed_default_brand(boot)
    db.allow_user(boot, "telegram", "856", "owner")
    # pré-enfile un message dans l'inbox : l'ingress le drainera dans son thread
    db.enqueue_inbound(boot, photo("856", 1), external_id="1")
    boot.close()

    store = JobStore(tmp_path / "jobs")

    # faux pipeline patché au niveau module (vu par le thread worker)
    from cintre.pipeline import runner

    monkeypatch.setattr(
        runner, "generate_prompts",
        lambda image_path, da_path, n, model=None, log_path=None, user_note=None: [f"p{i}" for i in range(n)],
    )

    def fake_image(prompt, ref, out, model=None, log_path=None):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PNG")

    monkeypatch.setattr(runner, "generate_image", fake_image)
    monkeypatch.setattr(
        runner, "anonymize_image",
        lambda src, dst, method=None, pad=None, score_threshold=None: (dst.write_bytes(b"X"), 0)[1],
    )

    # un sender partagé : l'ingress télécharge la référence, le worker y livre
    from cintre.channels.registry import ChannelRegistry

    channel = FakeChannel()
    registry = ChannelRegistry()
    registry.register(channel)

    stop = threading.Event()

    def ingress_target():
        ing = Ingress(db.connect(db_path), registry, store)
        ing.run_forever(stop)

    def worker_target():
        Worker(db.connect(db_path), store, registry, "w1").run_forever(stop)

    ti = threading.Thread(target=ingress_target, daemon=True)
    tw = threading.Thread(target=worker_target, daemon=True)
    ti.start()
    tw.start()

    # attendre la livraison (max ~5s)
    deadline = time.time() + 5
    check = db.connect(db_path)
    delivered = False
    while time.time() < deadline:
        row = check.execute("SELECT status FROM jobs LIMIT 1").fetchone()
        if row and row["status"] == JobStatus.DONE:
            delivered = True
            break
        time.sleep(0.1)
    stop.set()
    ti.join(timeout=2)
    tw.join(timeout=2)

    assert delivered, "le job aurait dû être livré par les threads"
    assert len(channel.albums) == 1
