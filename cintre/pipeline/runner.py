"""Orchestrateur d'un job : machine à états idempotente et reprenable.

Principe : le disque fait foi. Chaque étape vérifie ses artefacts avant de
(re)faire le travail, donc `run_job` peut être rappelée après un crash et
reprend exactement où elle s'était arrêtée.
"""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import config, db
from ..channels.registry import ChannelRegistry
from ..jobstore import JobStore
from ..logsetup import get_logger
from ..models import Job, JobStatus, OutboundAlbum
from .anonymize import anonymize_image
from .images import generate_image
from .prompts import generate_prompts

log = get_logger("runner")


def run_job(
    conn: sqlite3.Connection,
    store: JobStore,
    job: Job,
    channels: ChannelRegistry,
    worker_id: str,
    model_prompts: str | None = None,
    model_images: str | None = None,
) -> JobStatus:
    """Exécute (ou reprend) un job jusqu'à done/failed. Lève en cas d'échec
    transitoire (le worker décidera retry vs fail via attempts)."""
    jid = job.id
    store.init_job_dir(jid)

    # 1. La référence est indispensable et irrécupérable si absente.
    if not store.reference_path(jid).exists():
        db.finish_job(conn, jid, JobStatus.FAILED, error="reference image missing")
        db.log_event(conn, jid, "error", "reference image missing")
        return JobStatus.FAILED

    # 2. Livraison déjà effectuée (crash juste après l'envoi) → finaliser.
    if job.delivered_at:
        db.finish_job(conn, jid, JobStatus.DONE)
        db.log_event(conn, jid, "deliver", "already delivered, finalizing")
        return JobStatus.DONE

    # 2b. Anonymisation des visages (avant tout appel modèle). L'image
    #     anonymisée devient la référence vue par claude/codex. Fail-closed :
    #     une erreur ici lève et empêche tout envoi d'un visage réel.
    ref_for_models = store.reference_path(jid)
    if config.ANONYMIZE_ENABLED:
        if not store.anon_path(jid).exists():
            log.info("job %s : anonymisation des visages…", jid)
            n_faces = anonymize_image(store.reference_path(jid), store.anon_path(jid))
            db.log_event(conn, jid, "anonymize", f"{n_faces} face(s) masked")
            log.info("job %s : %d visage(s) masqué(s)", jid, n_faces)
        ref_for_models = store.anon_path(jid)

    # 3. Prompts (skip si déjà produits).
    if store.prompts_ready(jid):
        log.info("job %s : prompts déjà présents, étape sautée", jid)
    else:
        db.set_status(conn, jid, JobStatus.GENERATING_PROMPTS)
        db.log_event(conn, jid, "prompts", "generating prompts")
        log.info("job %s : génération des prompts (claude)…", jid)
        prompts = generate_prompts(
            image_path=ref_for_models,
            da_path=store.da_copy_path(jid),
            n=job.n_images,
            model=model_prompts,
            log_path=store.log_path(jid, "prompts.log"),
            user_note=store.load_note(jid),
        )
        store.write_json_atomic(store.prompts_path(jid), prompts)
        db.set_status(conn, jid, JobStatus.PROMPTS_READY)
        db.log_event(conn, jid, "prompts", f"{len(prompts)} prompts ready")
        log.info("job %s : %d prompts générés", jid, len(prompts))

    # 4. Charger les prompts (le disque fait foi sur le nombre).
    prompts = store.load_prompts(jid)
    n = len(prompts)

    # 5. Images : génération PARALLÈLE bornée, en ne (re)faisant que les manquantes.
    #    Les tâches du pool ne font QUE du sous-process + écriture fichier (pas de
    #    DB) ; les écritures DB (log, lease) restent dans le thread principal.
    db.set_status(conn, jid, JobStatus.GENERATING_IMAGES)
    missing = store.missing_image_indices(jid, n)
    log.info(
        "job %s : %d/%d image(s) à générer (concurrence %d)",
        jid, len(missing), n, config.IMAGE_CONCURRENCY,
    )

    tokens_by_idx = _load_usage(store, jid)  # tokens déjà connus (reprise)
    errors: list[tuple[int, BaseException]] = []

    def _gen(idx: int) -> int:
        toks = generate_image(
            prompt=prompts[idx - 1],
            ref=ref_for_models,
            out=store.image_path(jid, idx),
            model=model_images,
            log_path=store.log_path(jid, f"image_{idx:02d}.log"),
        )
        store.mark_image_done(jid, idx)
        return toks or 0

    if missing:
        with ThreadPoolExecutor(max_workers=max(1, config.IMAGE_CONCURRENCY)) as pool:
            futs = {pool.submit(_gen, idx): idx for idx in missing}
            for fut in as_completed(futs):
                idx = futs[fut]
                try:
                    toks = fut.result()
                    tokens_by_idx[idx] = toks
                    db.log_event(conn, jid, "image", f"image {idx}/{n} ok ({toks} tokens)")
                    db.renew_lease(conn, jid, worker_id)
                    log.info("job %s : image %d/%d ✅ (%d tokens)", jid, idx, n, toks)
                except Exception as exc:
                    errors.append((idx, exc))
                    log.exception("job %s : image %d a échoué", jid, idx)

    _write_usage(store, jid, tokens_by_idx)

    if errors:
        raise RuntimeError(f"{len(errors)} image(s) en échec : {sorted(i for i, _ in errors)}")
    if store.missing_image_indices(jid, n):
        raise RuntimeError("images incomplètes après la génération")
    db.set_status(conn, jid, JobStatus.IMAGES_READY)
    log.info("job %s : images prêtes — conso ~%d tokens", jid, sum(tokens_by_idx.values()))

    # 6. Livraison en album, depuis (channel, user_ref) — UI-agnostique.
    db.set_status(conn, jid, JobStatus.DELIVERING)
    log.info("job %s : livraison de l'album (%d images) à %s", jid, n, job.user_ref)
    channel = channels.get(job.channel)
    image_paths = [store.image_path(jid, i) for i in range(1, n + 1)]
    channel.send_album(OutboundAlbum(job.user_ref, image_paths, caption=config.DELIVERY_CAPTION))
    db.mark_delivered(conn, jid)  # immédiatement après le 2xx, avant la finalisation

    # 7. Terminé.
    db.finish_job(conn, jid, JobStatus.DONE)
    db.log_event(conn, jid, "deliver", f"album of {n} images delivered")
    log.info("job %s : album livré ✅", jid)
    return JobStatus.DONE


def _load_usage(store: JobStore, jid: str) -> dict[int, int]:
    """Charge les tokens déjà connus (usage.json) pour cumuler à la reprise."""
    path = store.usage_path(jid)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(k): int(v) for k, v in data.get("per_image", {}).items()}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _write_usage(store: JobStore, jid: str, tokens_by_idx: dict[int, int]) -> None:
    store.write_json_atomic(
        store.usage_path(jid),
        {
            "per_image": {str(k): v for k, v in sorted(tokens_by_idx.items())},
            "total_tokens": sum(tokens_by_idx.values()),
        },
    )
