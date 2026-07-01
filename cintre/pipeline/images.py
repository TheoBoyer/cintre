"""Étape 2 — génération d'une image via `codex exec`.

Refactor de l'ancien generate_image.py : même commande Codex, mais la sortie
verbeuse de l'agent est **capturée dans un log** (plus de spam terminal), et
l'image n'est validée que via une écriture atomique + un marqueur `.done`.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

from .. import config

# Préfixe qui force Codex à utiliser le générateur d'images (cf. tuto_codex.md).
IMAGEGEN_PREFIX = "$imagegen"


class ImageGenError(RuntimeError):
    """Échec de la génération d'une image."""


def build_codex_prompt(prompt: str, out_name: str) -> str:
    """Assemble le message passé à Codex : génération + consigne de sauvegarde."""
    return (
        f"{IMAGEGEN_PREFIX} {prompt}\n\n"
        f"Use the attached reference image as the exact garment reference: keep the "
        f"garment's colors, pattern, fabric and drape strictly identical to it. "
        f"Save the generated image to ./{out_name} and do not write any other file."
    )


def generate_image(
    prompt: str,
    ref: Path,
    out: Path,
    model: str | None = None,
    log_path: Path | None = None,
) -> int:
    """Génère une image vers `out` via Codex. Retourne le nombre de tokens
    consommés rapporté par Codex (0 si non trouvé).

    Codex écrit d'abord dans un nom temporaire (dans le même dossier), puis on
    renomme atomiquement vers `out`. Toute la sortie de Codex va dans `log_path`.
    Lève ImageGenError en cas d'échec.
    """
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    # Nettoie d'éventuels temporaires orphelins du même index (interruption
    # précédente) avant d'en créer un nouveau.
    for old in out.parent.glob(f".{out.stem}.*.tmp.png"):
        old.unlink(missing_ok=True)

    # Nom temporaire : Codex écrit ici, on renomme atomiquement ensuite. Cela
    # évite qu'un crash laisse un PNG partiel à l'emplacement final.
    tmp_name = f".{out.stem}.{uuid.uuid4().hex[:8]}.tmp.png"
    tmp_path = out.parent / tmp_name

    codex_prompt = build_codex_prompt(prompt, tmp_name)

    cmd = [
        "codex",
        "exec",
        "--image",
        str(ref.resolve()),
        "--cd",
        str(out.parent),
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(codex_prompt)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.CODEX_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        _write_timeout_log(log_path, exc)
        _cleanup(tmp_path)
        raise ImageGenError(
            f"codex exec a dépassé le timeout ({config.CODEX_TIMEOUT}s). Voir le log : {log_path}"
        ) from exc

    _write_log(log_path, result)

    if result.returncode != 0:
        _cleanup(tmp_path)
        raise ImageGenError(
            f"codex exec a échoué (code {result.returncode}). Voir le log : {log_path}"
        )

    if not tmp_path.exists():
        raise ImageGenError(
            f"Codex a terminé mais l'image attendue est absente. Voir le log : {log_path}"
        )

    os.replace(tmp_path, out)
    # Le résumé « tokens used » de Codex est écrit sur STDERR (pas STDOUT).
    return _parse_tokens(f"{result.stdout or ''}\n{result.stderr or ''}")


def _parse_tokens(text: str | None) -> int:
    """Extrait le « tokens used\\n N » du résumé Codex (0 si absent)."""
    m = re.search(r"tokens used[\s:]*([\d,]+)", text or "", re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else 0


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_log(log_path: Path | None, result: subprocess.CompletedProcess) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"returncode={result.returncode}\n\n# STDOUT\n")
        f.write(result.stdout or "")
        f.write("\n\n# STDERR\n")
        f.write(result.stderr or "")


def _write_timeout_log(log_path: Path | None, exc: subprocess.TimeoutExpired) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"TIMEOUT après {exc.timeout}s\n\n# STDOUT (partiel)\n{out}\n\n# STDERR (partiel)\n{err}")
