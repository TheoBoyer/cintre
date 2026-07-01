"""Tests de l'anonymisation (masquage des visages), sans dépendre du modèle."""

from __future__ import annotations

import numpy as np

from cintre.pipeline import anonymize


def _solid(h=100, w=100, value=200):
    return np.full((h, w, 3), value, dtype=np.uint8)


def test_apply_mask_black_zeroes_region():
    img = _solid()
    out = anonymize.apply_mask(img, [(40, 40, 20, 20)], method="black", pad=0.0)
    # la box est noire…
    assert out[40:60, 40:60].sum() == 0
    # …et le reste intact
    assert out[0:10, 0:10].mean() == 200


def test_apply_mask_blur_changes_but_not_black():
    img = _solid()
    img[45:55, 45:55] = 0  # un détail au centre pour que le flou ait un effet
    out = anonymize.apply_mask(img, [(40, 40, 20, 20)], method="blur", pad=0.0)
    region = out[40:60, 40:60]
    assert region.sum() > 0  # pas tout noir
    assert not np.array_equal(region, img[40:60, 40:60])  # modifié


def test_apply_mask_padding_expands_box():
    img = _solid()
    out = anonymize.apply_mask(img, [(40, 40, 20, 20)], method="black", pad=0.5)
    # pad=0.5 de 20px => +10px de chaque côté => 30..70 noir
    assert out[30:70, 30:70].sum() == 0
    assert out[29, 29].mean() == 200  # juste en dehors


def test_yunet_model_downloads_and_loads():
    # le modèle se télécharge s'il est absent, puis le détecteur réel se charge
    anonymize.ensure_model()
    assert anonymize.MODEL_PATH.exists(), f"modèle manquant : {anonymize.MODEL_PATH}"
    boxes = anonymize.detect_faces(_solid(120, 120), score_threshold=0.6)
    assert boxes == []  # pas de visage sur un aplat uni


def test_anonymize_image_no_faces_copies(tmp_path, monkeypatch):
    import cv2

    src = tmp_path / "src.png"
    cv2.imwrite(str(src), _solid())
    dst = tmp_path / "anon.png"
    monkeypatch.setattr(anonymize, "detect_faces", lambda img, score_threshold: [])
    n = anonymize.anonymize_image(src, dst)
    assert n == 0 and dst.exists()
    assert np.array_equal(cv2.imread(str(dst)), cv2.imread(str(src)))


def test_anonymize_image_masks_detected_face(tmp_path, monkeypatch):
    import cv2

    src = tmp_path / "src.png"
    cv2.imwrite(str(src), _solid())
    dst = tmp_path / "anon.png"
    monkeypatch.setattr(anonymize, "detect_faces", lambda img, score_threshold: [(40, 40, 20, 20)])
    n = anonymize.anonymize_image(src, dst, method="black", pad=0.0)
    assert n == 1
    out = cv2.imread(str(dst))
    assert out[40:60, 40:60].sum() == 0  # visage masqué
