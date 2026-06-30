"""Tests du JobStore : reprise pilotée par le disque."""

from __future__ import annotations


def test_prompts_ready_and_atomic_json(store):
    jid = "job1"
    store.init_job_dir(jid)
    assert store.prompts_ready(jid) is False
    store.write_json_atomic(store.prompts_path(jid), ["a", "b", "c"])
    assert store.prompts_ready(jid) is True
    assert store.load_prompts(jid) == ["a", "b", "c"]


def test_empty_prompts_not_ready(store):
    jid = "job2"
    store.init_job_dir(jid)
    store.write_json_atomic(store.prompts_path(jid), [])
    assert store.prompts_ready(jid) is False


def test_done_markers_and_partial_image(store):
    jid = "job3"
    n = 3
    store.init_job_dir(jid)
    assert store.missing_image_indices(jid, n) == [1, 2, 3]

    store.image_path(jid, 1).write_bytes(b"x")
    store.mark_image_done(jid, 1)
    store.image_path(jid, 2).write_bytes(b"x")
    store.mark_image_done(jid, 2)
    assert store.completed_image_indices(jid, n) == {1, 2}
    assert store.missing_image_indices(jid, n) == [3]

    # PNG partiel sans marqueur (crash) => toujours "manquant"
    store.image_path(jid, 3).write_bytes(b"partial")
    assert store.missing_image_indices(jid, n) == [3]
    store.mark_image_done(jid, 3)
    assert store.missing_image_indices(jid, n) == []
