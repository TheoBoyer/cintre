"""Étape d'anonymisation — masque les visages de la photo de référence.

Sécurité « defense-in-depth » : on supprime les visages AVANT que la moindre
image ne soit envoyée à claude/codex. La référence ne sert qu'au vêtement ; les
prompts imposent par ailleurs un visage de mannequin inventé.

Détecteur : YuNet (OpenCV), robuste sur les poses/tailles variées.
Méthodes : 'black' (carré plein, défaut), 'blur' (flou gaussien), 'pixelate'.
Fail-closed : si la détection est impossible, on lève — jamais de visage réel
ne doit passer en aval.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import cv2
import requests

from .. import config
from ..logsetup import get_logger

log = get_logger("anonymize")

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"
MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
_model_lock = threading.Lock()


class AnonymizeError(RuntimeError):
    """Échec de l'étape d'anonymisation."""


def ensure_model() -> None:
    """Télécharge le modèle YuNet s'il est absent (écriture atomique).
    Le modèle n'est pas versionné ; on le récupère au premier lancement."""
    if MODEL_PATH.exists():
        return
    with _model_lock:
        if MODEL_PATH.exists():
            return
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODEL_PATH.with_name(MODEL_PATH.name + ".tmp")
        log.info("Modèle de détection de visages absent — téléchargement…")
        try:
            with requests.get(MODEL_URL, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            os.replace(tmp, MODEL_PATH)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise AnonymizeError(f"téléchargement du modèle YuNet impossible : {exc}") from exc
        log.info("Modèle téléchargé : %s", MODEL_PATH)


def detect_faces(image_bgr, score_threshold: float) -> list[tuple[int, int, int, int]]:
    """Retourne les bounding boxes de visages (x, y, w, h) en pixels."""
    ensure_model()
    h, w = image_bgr.shape[:2]
    det = cv2.FaceDetectorYN_create(str(MODEL_PATH), "", (w, h), score_threshold=score_threshold)
    det.setInputSize((w, h))
    _, faces = det.detect(image_bgr)
    boxes: list[tuple[int, int, int, int]] = []
    if faces is not None:
        for f in faces:
            x, y, fw, fh = f[:4]
            boxes.append((int(x), int(y), int(fw), int(fh)))
    return boxes


def _expand(box, pad: float, w: int, h: int) -> tuple[int, int, int, int]:
    """Élargit la box de `pad` (fraction) et la clampe dans l'image."""
    x, y, bw, bh = box
    dx, dy = int(bw * pad), int(bh * pad)
    return (max(0, x - dx), max(0, y - dy), min(w, x + bw + dx), min(h, y + bh + dy))


def apply_mask(image_bgr, boxes, method: str, pad: float):
    """Applique le masque choisi sur chaque box. Retourne une nouvelle image."""
    h, w = image_bgr.shape[:2]
    out = image_bgr.copy()
    for box in boxes:
        x0, y0, x1, y1 = _expand(box, pad, w, h)
        if x1 <= x0 or y1 <= y0:
            continue
        roi = out[y0:y1, x0:x1]
        if method == "black":
            out[y0:y1, x0:x1] = 0
        elif method == "blur":
            k = max(31, ((x1 - x0) // 3) | 1)  # noyau impair, large
            out[y0:y1, x0:x1] = cv2.GaussianBlur(roi, (k, k), 0)
        elif method == "pixelate":
            sw, sh = max(1, (x1 - x0) // 16), max(1, (y1 - y0) // 16)
            small = cv2.resize(roi, (sw, sh), interpolation=cv2.INTER_LINEAR)
            out[y0:y1, x0:x1] = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        else:
            raise AnonymizeError(f"méthode d'anonymisation inconnue : {method!r}")
    return out


def anonymize_image(
    src: Path,
    dst: Path,
    method: str | None = None,
    pad: float | None = None,
    score_threshold: float | None = None,
) -> int:
    """Détecte et masque les visages de `src` vers `dst` (écriture atomique).
    Retourne le nombre de visages masqués. Lève AnonymizeError en cas d'échec."""
    method = method or config.ANONYMIZE_METHOD
    pad = config.ANON_PAD if pad is None else pad
    score_threshold = config.ANON_SCORE_THRESHOLD if score_threshold is None else score_threshold

    img = cv2.imread(str(src))
    if img is None:
        raise AnonymizeError(f"image illisible : {src}")

    boxes = detect_faces(img, score_threshold)
    result = apply_mask(img, boxes, method, pad) if boxes else img

    dst.parent.mkdir(parents=True, exist_ok=True)
    # Nom temporaire qui CONSERVE l'extension image (cv2 en a besoin pour choisir
    # l'encodeur), puis renommage atomique.
    tmp = dst.with_name(f".{dst.stem}.tmp{dst.suffix}")
    if not cv2.imwrite(str(tmp), result):
        raise AnonymizeError(f"échec d'écriture : {tmp}")
    os.replace(tmp, dst)
    return len(boxes)
