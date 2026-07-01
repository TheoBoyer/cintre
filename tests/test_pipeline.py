"""Tests end-to-end ingress → worker → livraison, reprise et échec."""

from __future__ import annotations

from cintre import config, db
from cintre.ingress import Ingress
from cintre.models import JobStatus
from cintre.pipeline import runner
from cintre.worker import Worker

from conftest import photo, text_msg


def _allow(conn, uid="856"):
    db.allow_user(conn, "telegram", uid, "owner")


def test_unauthorized_ignored(conn, channel, store, registry):
    ing = Ingress(conn, registry, store)
    ing._handle(photo("999", 1))
    assert db.active_job_for_user(conn, "telegram", "999") is None
    assert channel.texts == []  # DENY_TEXT par défaut = None


def test_authorized_photo_creates_job_and_acks(conn, channel, store, registry):
    _allow(conn)
    ing = Ingress(conn, registry, store)
    ing._handle(photo("856", 2))
    job = db.active_job_for_user(conn, "telegram", "856")
    assert job and job.status == JobStatus.QUEUED
    assert channel.texts[-1] == ("856", config.ACK_TEXT)
    assert store.reference_path(job.id).exists()
    assert store.da_copy_path(job.id).exists()
    assert store.meta_path(job.id).exists()


def test_caption_saved_and_passed_to_prompts(conn, channel, store, registry, monkeypatch):
    _allow(conn)
    ing = Ingress(conn, registry, store)
    msg = photo("856", 2)
    msg = msg.__class__(**{**msg.__dict__, "caption": "la fille de gauche porte la robe"})
    ing._handle(msg)
    job = db.active_job_for_user(conn, "telegram", "856")
    # la note est sauvegardée dans le dossier du job
    assert store.load_note(job.id) == "la fille de gauche porte la robe"

    # et transmise à generate_prompts au moment du run
    seen = {}

    def capture_prompts(image_path, da_path, n, model=None, log_path=None, user_note=None):
        seen["note"] = user_note
        return [f"p{i}" for i in range(n)]

    def fake_image(prompt, ref, out, model=None, log_path=None):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PNG")

    def fake_anon(src, dst, method=None, pad=None, score_threshold=None):
        dst.write_bytes(b"X")
        return 0

    monkeypatch.setattr(runner, "generate_prompts", capture_prompts)
    monkeypatch.setattr(runner, "generate_image", fake_image)
    monkeypatch.setattr(runner, "anonymize_image", fake_anon)
    claimed = db.claim_next_job(conn, "w1")
    runner.run_job(conn, store, claimed, registry, "w1")
    assert seen["note"] == "la fille de gauche porte la robe"


def test_non_photo_prompts_for_photo(conn, channel, store, registry):
    _allow(conn)
    ing = Ingress(conn, registry, store)
    ing._handle(text_msg("856", 1))
    assert channel.texts[-1] == ("856", config.NEED_PHOTO_TEXT)


def test_reject_and_album_dedup(conn, channel, store, registry):
    _allow(conn)
    ing = Ingress(conn, registry, store)
    ing._handle(photo("856", 2))  # crée le job
    n_after_ack = len(channel.texts)
    ing._handle(photo("856", 3))  # rejet simple
    ing._handle(photo("856", 4, gid="album1"))  # rejet album
    ing._handle(photo("856", 5, gid="album1"))  # même album -> dédupliqué
    rejects = [t for t in channel.texts[n_after_ack:] if t[1] == config.REJECT_TEXT]
    assert len(rejects) == 2  # update3 + un seul pour album1
    acks = [t for t in channel.texts if t[1] == config.ACK_TEXT]
    assert len(acks) == 1  # pas de second ack


def test_worker_delivers_album(conn, channel, store, registry, fake_pipeline):
    _allow(conn)
    ing = Ingress(conn, registry, store)
    ing._handle(photo("856", 2))
    job = db.active_job_for_user(conn, "telegram", "856")

    worker = Worker(conn, store, registry, "w1")
    claimed = db.claim_next_job(conn, "w1")
    worker._process(claimed)

    done = db.get_job(conn, job.id)
    assert done.status == JobStatus.DONE and done.delivered_at
    assert len(channel.albums) == 1
    assert len(channel.albums[0].image_paths) == config.DEFAULT_N_IMAGES
    assert store.missing_image_indices(job.id, config.DEFAULT_N_IMAGES) == []
    # conso de tokens agrégée et écrite
    import json
    usage = json.loads(store.usage_path(job.id).read_text())
    assert usage["total_tokens"] == 100 * config.DEFAULT_N_IMAGES
    assert len(usage["per_image"]) == config.DEFAULT_N_IMAGES
    # l'utilisateur est débloqué
    ing._handle(photo("856", 6))
    assert db.active_job_for_user(conn, "telegram", "856").id != job.id


def test_images_run_in_parallel(conn, channel, store, registry, monkeypatch):
    import threading

    _allow(conn)
    monkeypatch.setattr(config, "IMAGE_CONCURRENCY", 3)
    # Barrière à 3 : si la génération était séquentielle, la 1re tâche resterait
    # bloquée seule et timeout -> run_job lèverait -> test échoue.
    barrier = threading.Barrier(config.DEFAULT_N_IMAGES, timeout=5)

    monkeypatch.setattr(
        runner, "generate_prompts",
        lambda image_path, da_path, n, model=None, log_path=None, user_note=None: [f"p{i}" for i in range(n)],
    )
    monkeypatch.setattr(
        runner, "anonymize_image",
        lambda src, dst, method=None, pad=None, score_threshold=None: dst.write_bytes(b"X"),
    )

    def fake_image(prompt, ref, out, model=None, log_path=None):
        barrier.wait()  # exige que toutes les tâches soient présentes simultanément
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PNG")
        return 10

    monkeypatch.setattr(runner, "generate_image", fake_image)

    ing = Ingress(conn, registry, store)
    ing._handle(photo("856", 2))
    job = db.active_job_for_user(conn, "telegram", "856")
    claimed = db.claim_next_job(conn, "w1")
    runner.run_job(conn, store, claimed, registry, "w1")  # ne doit PAS timeout
    assert db.get_job(conn, job.id).status == JobStatus.DONE


def test_resume_skips_done_work(conn, channel, store, registry, fake_pipeline):
    _allow(conn)
    ing = Ingress(conn, registry, store)
    ing._handle(photo("856", 2))
    job = db.active_job_for_user(conn, "telegram", "856")
    claimed = db.claim_next_job(conn, "w1")

    # simuler un crash après prompts + 2 images (sur 6)
    store.init_job_dir(job.id)
    store.write_json_atomic(store.prompts_path(job.id), [f"p{i}" for i in range(1, 7)])
    for i in (1, 2):
        store.image_path(job.id, i).write_bytes(b"x")
        store.mark_image_done(job.id, i)
    store.image_path(job.id, 3).write_bytes(b"partial")  # PNG partiel sans marqueur

    fake_pipeline["prompts"] = 0
    fake_pipeline["images"] = 0
    runner.run_job(conn, store, claimed, registry, "w1")

    assert fake_pipeline["prompts"] == 0  # prompts.json présent -> pas de régénération
    assert fake_pipeline["images"] == 4   # seules les images 3..6 manquantes
    assert db.get_job(conn, job.id).status == JobStatus.DONE


def test_failure_after_retries_notifies_and_unblocks(conn, channel, store, registry, fake_pipeline, monkeypatch):
    _allow(conn)
    monkeypatch.setattr(config, "MAX_ATTEMPTS", 2)

    def boom(*a, **k):
        raise RuntimeError("codex down")

    monkeypatch.setattr(runner, "generate_image", boom)

    ing = Ingress(conn, registry, store)
    ing._handle(photo("856", 2))
    job = db.active_job_for_user(conn, "telegram", "856")

    worker = Worker(conn, store, registry, "w1")
    texts_before = len(channel.texts)
    for _ in range(config.MAX_ATTEMPTS + 2):
        c = db.claim_next_job(conn, "w1")
        if c is None:
            break
        worker._process(c)

    final = db.get_job(conn, job.id)
    assert final.status == JobStatus.FAILED
    assert any(t == ("856", config.FAILED_TEXT) for t in channel.texts[texts_before:])
    assert db.active_job_for_user(conn, "telegram", "856") is None
