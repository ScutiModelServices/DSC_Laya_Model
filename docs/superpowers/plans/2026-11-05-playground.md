# Service Playground Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans task by task. Approved user instruction: proceed.

**Goal:** Working Try it panels in all twelve model and three tool admin pages.
**Architecture:** One identical static Playground class; descriptors in model CONFIG and tool admin metadata. Public token auth, bounded streaming readers, typed media output, thin UnlimitedOCR HTTP parse adapter.
**Tech Stack:** Existing HTML/JS, FastAPI/Starlette, pytest, Playwright, uv.
**Spec:** ../specs/2026-11-05-playground-design.md

## Global Constraints

- Preserve existing edits and secrets; no deletion, push or publish.
- Model HTML differs only on CONFIG line; tool HTML and admin_api.py identical.
- Credentials memory-only; curl redacted; same-origin public requests only.
- Caps: 10 MiB uploads, 150 seconds decoded audio, 32 MiB binary, 256 KiB text.
- Commit per service with 214214011+scutiii2@users.noreply.github.com.

## Review Focus

- Streaming boundaries, UTF-8 and cancellation must preserve status and release readers.
- Public token and admin secrets never enter curl, output, URL or storage.
- Media URLs revoked on reset/sign-out; oversized inputs rejected before decoding.
- Multi-step owned files keep username/token and report each failed step.
- Descriptor defaults obey actual request contracts, including TEM class labels and OCR multimodal fields.

### Task 1: Shared panel and model integration

Files: every model web/index.html, new identical web/playground.js; tools/check_sync.py.
Interface: window.Playground.mount(container, descriptor, {secrets: () => string[]}); instance.reset(). Descriptor has operations [{label, route, method, encoding, fields, response, lookup?, followup?}]. Fields have key, label, type, default, required, options, min/max/step, location (body/query/header), transform. Operation encoding json/raw/pcm/image; response json/stream/audio/image/text; followup route and response. Dot-separated keys permit nested messages.

- [x] Add failing Playwright public-route submission tests and execute representative test.
- [x] Build generic panel with live streaming, cancellation, redaction, bounded Blob media, curl and descriptor forms.
- [x] Add model descriptors; test Laya/chat/TTS/Whisper/TEM first, then propagate copies and verify family contracts.
- [x] Update sync checks narrowly, preserving existing edits; run every model checker.

### Task 2: Tool integration

Files: tool src/app.py, shared src/admin_api.py, tests/test_admin_api.py, new/extended tests/test_admin_ui.py; shared tool HTML.
Interface: register(..., playground: dict | None = None); GET /admin/status includes playground descriptor. UnlimitedOCR POST /api/parse {file_id} returns ParseResult via existing service.parse with public middleware and username ownership. Root integrates same Playground static module and tool HTML using status.playground.

- [x] Add failing adapter tests for auth/identity/ownership/success/failure.
- [x] Add descriptors to register calls and shared status metadata; implement async OCR adapter.
- [x] Add browser tests per tool using fake engines through public routes; verify no credential persistence.
- [x] Run tool tests and preserve three-way shared-file identity.

### Task 3: Documentation

Files: each README.md, deployment.md or docs/guide-web-ui.md, CHANGELOG.md; _docs/SHARED-FEATURES.md and SERVICE-SPECIFIC.md; parity skill shared-file section.
- [x] Document auth choice (b), public quota effects, media/upload limits, token lifetime, usage steps and family differences.
- [x] Add Try it panel matrix row and shared module rule; no new environment settings.

### Task 4: Verification, review and commits

- [x] Run uv run pytest -q in all fifteen repos; preserve exact result/skip counts.
- [x] Open each page with Playwright; record real vs fake inference availability per service.
- [x] Review complete change independently; correct findings and rerun affected checks.
- [x] Stage task files explicitly and commit each repo; final report links commits and names skips.

## Execution ledger

2026-11-05: Design approved. Work in existing service checkouts to preserve visible cross-repo sibling synchronization and existing edits; no push. Independent backend/tests and documentation tasks may run as skill-authorized delegates; root owns shared panel and model descriptors. No additional approval needed for approved scope.

2026-11-05 completion: All 15 panels implemented; full service suites and separate compatible Bard browser checks green. All 12 sync checks pass. Real LFM3/S1-mini/Piper/Whisper/Bard browser inference verified offline; Laya missing complete pinned cache; TEM real-model prerequisites and SGLang backend unavailable. Independent final review clean. Fourteen sibling commits created with required email; this Laya commit completes the per-service commit set. Shared matrix/skill updates remain outside service repositories. No push/publish/deletion.
