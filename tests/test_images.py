"""Tests unitaires de la génération d'image (parsing tokens), sans API."""

from __future__ import annotations

from cintre.pipeline.images import _parse_tokens


def test_parse_tokens_codex_format():
    stdout = "Used the built-in image_gen workflow...\ntokens used\n75,994\n"
    assert _parse_tokens(stdout) == 75994


def test_parse_tokens_inline_and_missing():
    assert _parse_tokens("tokens used: 1234") == 1234
    assert _parse_tokens("aucune info") == 0
    assert _parse_tokens(None) == 0
    assert _parse_tokens("") == 0
