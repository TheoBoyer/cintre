"""Disposition disque d'un job + écritures atomiques + détection de reprise.

Le disque est la **source de vérité** pour la reprise : une image n'est
considérée produite que si son marqueur `.done` existe (un PNG partiel laissé
par un crash de Codex ne compte donc pas).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import config


class JobStore:
    def __init__(self, jobs_dir: Path | None = None) -> None:
        self.jobs_dir = jobs_dir or config.JOBS_DIR

    # --- Chemins ------------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def reference_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "reference.jpg"

    def anon_path(self, job_id: str) -> Path:
        """Référence anonymisée (visages masqués) — image vue par claude/codex."""
        return self.job_dir(job_id) / "reference_anon.jpg"

    def da_copy_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "da.md"

    def prompts_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "prompts.json"

    def note_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "note.txt"

    def meta_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "meta.json"

    def usage_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "usage.json"

    def images_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "images"

    def image_path(self, job_id: str, idx: int) -> Path:
        return self.images_dir(job_id) / f"{idx:02d}.png"

    def image_done_marker(self, job_id: str, idx: int) -> Path:
        return self.images_dir(job_id) / f"{idx:02d}.done"

    def logs_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "logs"

    def log_path(self, job_id: str, name: str) -> Path:
        return self.logs_dir(job_id) / name

    # --- Création / écritures ----------------------------------------------
    def init_job_dir(self, job_id: str) -> None:
        """Crée l'arborescence du job (idempotent)."""
        self.images_dir(job_id).mkdir(parents=True, exist_ok=True)
        self.logs_dir(job_id).mkdir(parents=True, exist_ok=True)

    def save_reference(self, job_id: str, src: Path) -> Path:
        dest = self.reference_path(job_id)
        shutil.copyfile(src, dest)
        return dest

    def save_reference_bytes(self, job_id: str, data: bytes) -> Path:
        dest = self.reference_path(job_id)
        _write_bytes_atomic(dest, data)
        return dest

    def copy_da(self, job_id: str, da_path: Path) -> Path:
        dest = self.da_copy_path(job_id)
        shutil.copyfile(da_path, dest)
        return dest

    def save_note(self, job_id: str, note: str) -> Path:
        dest = self.note_path(job_id)
        _write_text_atomic(dest, note)
        return dest

    def load_note(self, job_id: str) -> str | None:
        path = self.note_path(job_id)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_json_atomic(self, path: Path, obj) -> None:
        _write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2))

    def write_meta(self, job_id: str, meta: dict) -> None:
        self.write_json_atomic(self.meta_path(job_id), meta)

    def mark_image_done(self, job_id: str, idx: int) -> None:
        self.image_done_marker(job_id, idx).touch()

    # --- Détection de reprise ----------------------------------------------
    def prompts_ready(self, job_id: str) -> bool:
        """True si prompts.json existe, parse, et contient une liste non vide."""
        path = self.prompts_path(job_id)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return isinstance(data, list) and len(data) > 0

    def load_prompts(self, job_id: str) -> list[str]:
        return json.loads(self.prompts_path(job_id).read_text(encoding="utf-8"))

    def completed_image_indices(self, job_id: str, n: int) -> set[int]:
        """Indices 1..n dont le marqueur .done est présent."""
        return {i for i in range(1, n + 1) if self.image_done_marker(job_id, i).exists()}

    def missing_image_indices(self, job_id: str, n: int) -> list[int]:
        done = self.completed_image_indices(job_id, n)
        return [i for i in range(1, n + 1) if i not in done]


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
