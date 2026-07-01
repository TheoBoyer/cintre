# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**cintre** turns a single raw product photo into a publishable marketing pack, with zero tooling for the merchant. A shop owner sends **one photo of a garment** over a chat channel (Telegram, optionally WhatsApp); the bot anonymizes faces, generates visual ideas matching a brand's art direction ("DA"), produces model/mannequin images faithful to the exact garment, and returns everything as an album.

Codebase language is Python ≥3.13, managed with `uv`. Comments and user-facing strings are in French.

## Commands

```bash
uv run cintre                 # run the service (ingress + worker + receivers)
uv run pytest                 # full test suite — mocked pipeline, no API credits needed
uv run pytest tests/test_pipeline.py::test_name   # single test
uv run cintre-admin <cmd>     # admin CLI: allow-user, add-brand, assign-user, list-jobs, ...
```

Debug / side tools:
```bash
uv run generate_prompts photos/ref.jpg --da da/generic.md --n 3   # prompts stage in isolation
uv run generate_image --ref photos/ref.jpg --prompt "..." --out out.png
uv run generate-email --shop "LE DRESSING" --city Chambéry        # cold-outreach email generator
```

There is no linter/formatter configured and no build step.

## External dependencies that must be present and authenticated

The AI engines are **shelled-out CLIs**, not HTTP SDKs — the process calls them via `subprocess`:
- `claude` (Claude Code CLI) — generates prompts, with `--json-schema` structured output (`cintre/pipeline/prompts.py`).
- `codex` (`codex exec`, with `$imagegen`) — generates each image (`cintre/pipeline/images.py`).

Both must be installed and logged in on the host. Tests mock these, so `pytest` needs neither.

Config comes from `.env` (`TELEGRAM_BOT_TOKEN` required; `WHATSAPP_*` optional — see README).

## Architecture

Message flow, one job per UUID, disk-is-truth and crash-resumable:

```
photo → Receiver (per channel) → inbox table (SQLite)
      → Ingress (single, channel-agnostic): whitelist check → resolve brand → create job
      → Worker (single, leased jobs): anonymize → prompts (claude) → images (codex, parallel) → deliver album
```

**Concurrency model (`cintre/app.py`).** One process, one daemon thread each for: ingress, worker, and one receiver per channel. The hard rule, enforced everywhere: **one SQLite connection per thread** — each thread opens its own via `db.connect()` inside its target function; never pass a `conn` across threads. SQLite runs in WAL + autocommit so ingress can read while the worker writes. Every `db.*` function takes an explicit `conn`.

**Channel abstraction (`cintre/channels/`).** Split into `Sender` (outbound, uniform: `send_text` / `send_album`) and `Receiver` (inbound). Telegram *pulls* via long-poll; WhatsApp is *pushed* via webhook. Every inbound message lands in the `inbox` table, drained by the single channel-agnostic ingress. The pipeline only ever sees `(channel, user_ref)` + opaque payloads. **Adding a channel = one `Sender` + one `Receiver`, registered in `build_channels()` in `app.py`.**

**Pipeline is an idempotent state machine (`cintre/pipeline/runner.py`).** `run_job` can be re-called after any crash and resumes exactly where it stopped, because **each step checks its artifacts on disk before (re)doing work**: `.done` markers per image, `prompts.json`, `reference_anon.jpg`, `delivered_at`. Only missing images are regenerated. Image generation is parallel and bounded by `IMAGE_CONCURRENCY`; the pool tasks do **only** subprocess + file writes — all DB writes (events, lease renewal) stay on the worker's main thread. `JobStatus` transitions live in `cintre/models.py`.

**Anonymization is fail-closed (`cintre/pipeline/anonymize.py`).** Faces on the reference are masked (YuNet ONNX, auto-downloaded to `cintre/models/`) **before any model call**; the anonymized image is the only reference passed to claude/codex. An error here raises and fails the job rather than risk leaking a real face.

**State lives in two places, kept consistent:** SQLite (`cintre.sqlite`, schema in `cintre/db.py`) for the queue/whitelist/brand mapping, and a per-job directory `jobs/<uuid>/` (reference, `reference_anon.jpg`, `da.md`, `prompts.json`, `images/`, `logs/`, `usage.json`, `meta.json`, `note.txt`).

**Tenancy & auth.** Identity is `(channel, user_ref)` — no accounts. The `allowed_users` table is the only gate (unlisted senders are silently ignored; `DENY_TEXT = None`). Owner whitelist is seeded from `CINTRE_OWNER`. A "brand" (`brands` table) pins a DA file + image count; users map to a brand via `user_brand`, falling back to the `generic` brand (`da/generic.md`). DAs are hand-edited free-form Markdown in `da/`, copied into each job at run time.

All tunables (defaults, timeouts, concurrency, anonymization method, user-facing texts) are constants in `cintre/config.py` — no logic there; the rest of the package imports from it.

## Non-code note

`landing/` contains a static marketing landing page (plain HTML/CSS, no build) for cintre.app, with optimized real before/after assets under `landing/assets/` and originals in `landing/sources/`. It is unrelated to the Python service. Preview with `python3 -m http.server` from that directory.
