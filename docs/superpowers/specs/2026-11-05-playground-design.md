# Service playground design

Status: approved by the user (proceed), implemented; verification recorded in the companion report.
Date: 2026-11-05 (Asia/Manila).
Builder review: fresh reviewer found nothing important left unanswered.

## Intent and scope

Let an administrator exercise each service from its existing admin page, using the same public authentication, limits, cache and inference path as an ordinary client. Add a Try it card to all twelve model services and Bard, Scribe and UnlimitedOCR. Preserve current themes, keyboard use and admin behavior. Do not touch OmniRouteService, Hooks, Brain, _ClaudeSkills or unrelated projects. Do not push, publish, delete files or disclose credentials.

## Verified survey

Read workspace instructions, every service AGENTS.md, the parity skill, shared-feature documentation, request models and route handlers. Models share index.html except CONFIG and line endings. The three tool index.html files are byte-identical. Both server families already mount their web directory, so a common static module can be deployed without new dependencies.

Every model public route uses X-Internal-Token when an admin password is configured. Tools use their configured internal token for /api and /mcp; HTTP operations also require X-Requester-Username. Model admin authentication uses X-Internal-Token with the admin password; tool admin authentication uses X-Admin-Password. These are separate from public credentials.

| Service | Public route | Request | Response | Streaming |
|---|---|---|---|---|
| LayaModel | POST /v1/ask | text, questions, strategy, optional project/max_len/min_confidence/thresholds/prune_ratio/pair_ratio/trace/stream | JSON answers | NDJSON answer events |
| MiniCPM52BModel | POST /v1/chat | messages, project, temperature, max_tokens, stream; tools or schema, thinking | JSON chat result | NDJSON content/thinking/done/error |
| BitNet2BModel | POST /v1/chat | messages, project, temperature, max_tokens, stream | JSON chat result | NDJSON |
| LFM3Model | POST /v1/chat | Same as BitNet | JSON chat result | NDJSON |
| SparkX26Model | POST /v1/chat | Same as BitNet | JSON chat result | NDJSON |
| S1MiniModel | POST /v1/normalize | text, styling, structure, context, stream | JSON normalized text | NDJSON sentence/done |
| STTWhisperModel | POST /v1/transcribe | Raw 16 kHz mono s16le PCM; language, beam_size, word_timestamps and stream query options | JSON transcript | NDJSON segment/done |
| TTSPiperModel | POST /v1/speak | text, voice, speed, stream | WAV | WAV chunks |
| TTSParadeeModel | POST /v1/speak | Same as Piper | WAV | WAV chunks |
| TTSAudio8Model | POST /v1/speak | text, voice, seed, temperature, stream | WAV | WAV chunks |
| TEMImageModel | POST /v1/generate | Exactly one of class_id or label; steps, step_size, seed | PNG | No |
| SGLangModel | POST /v1/chat/completions | model, multimodal messages and generation options | OpenAI-style JSON | Forwarded SSE |
| Bard | POST /api/speak, then GET /api/files/{file_id} | text, optional voice; username header on both requests | JSON metadata, then MP3 | Audio file transfer |
| Scribe | POST /api/transcribe | Encoded audio body, X-Filename, username | JSON text/duration | No |
| UnlimitedOCR | POST /api/files, then MCP tool_ocr_parse | Encoded document, then owned file_id | Upload metadata, then OCR text/pages | No |

Laya also exposes /v1/combine, /v1/validate and /v1/strategies. TTS exposes /v1/voices; TEM exposes /v1/classes; SGLang exposes /v1/models. MiniCPM alone supports tools/schema/thinking; the other chat models reject them. TEM is ImageNet class generation, not freeform prompt generation. UnlimitedOCR has no existing public HTTP parse route.

## Approaches and authentication decision

1. Admin-authenticated forwarding: convenient, but public token scope and per-token throttling cannot be reproduced by calling inference handlers with only an admin password. It would need additional credential and identity policy across two server frameworks.
2. Direct public fetch with a pasted token, kept only in memory: exercises the exact ordinary client path and makes scope, throttling, cache and error tests meaningful. This is the proposed choice, authorized as an option in the task; it is an implementation recommendation, not a recorded user preference.

Never substitute the admin password for a public token. Use a password input with autocomplete disabled; do not persist or include its contents in URLs, logs or curl. Clear it on sign-out, unload and reset. Redact the active public token and admin credential from displayed response/error text in case a backend echoes them. Curl uses a literal credential placeholder and shell-safe quoting. Reject external request URLs or redirects so credentials stay on the current origin.

## Shared implementation

Keep the existing model index.html identical except its one-line CONFIG. Add CONFIG.playground containing operations with route, method, encoding, fields, response type, optional authenticated lookup and optional follow-up file request. Mount one reusable Playground class from web/playground.js in both admin layouts; copy this file verbatim to all fifteen repositories. Add its synchronization check without overwriting unrelated existing check_sync.py edits.

Model descriptors live in CONFIG; tool descriptors come from authenticated admin metadata supplied at registration by each app.py. The tool page remains identical across all three services. No renderer branches on service names. Generic encoding adapters cover JSON, nested message fields, JSON-valued inputs, raw uploads, PCM audio conversion and image-input JSON. Follow-up descriptors support Bard's owned audio download and UnlimitedOCR's upload/parse sequence. Add named response adapters for JSON, text, NDJSON, SSE, audio and image.

Existing check_sync.py changes add the stale sibling name WhisperModel, while the actual directory is STTWhisperModel; that service's own checker omits itself from its sibling list. Make the narrow task-relevant correction to include STTWhisperModel in every checker, alongside the new shared-module check, preserving any remaining pre-existing changes.

Reuse each page's DOM creation, error and lifecycle helpers where their signatures allow it. Keep the playground's public fetch separate from admin fetch wrappers: these inject admin credentials and use timeouts inappropriate for inference streams. Add cleanup hooks to sign-out and authentication expiry.

## Forms and media

- Laya: text, questions JSON, optional project, strategy selector loaded from /v1/strategies, streaming toggle; auxiliary operations for validate/combine/strategies.
- Chat: prompt mapped to messages[0].content, optional project, temperature/max_tokens, streaming toggle; MiniCPM adds thinking and optional tools/schema JSON.
- Normalize: text, allowed styling/structure/context options and streaming toggle.
- TTS: text and token-filtered voice selector loaded from /v1/voices; appropriate speed or seed/temperature fields and optional stream request.
- Whisper: raw PCM or browser-decodable audio upload. Decode audio asynchronously with Web Audio; downmix/resample to 16 kHz mono and serialize s16le PCM in bounded worker chunks. Raw PCM has an explicit format label. Upload alone satisfies the file-or-mic requirement; microphone recording is not required.
- TEM: label or class ID selector, mutually exclusive, plus generation parameters; display PNG inline.
- SGLang: bounded PNG/JPEG/WebP upload and prompt, encoded into the multimodal message contract used by UnlimitedOCR, including required parsing options. Base64 is necessary here because the existing request API requires a data URL; response images/audio use Blob URLs.
- Bard: requester username, installed voice selector from /api/voices, text; render downloaded MP3 with controls and download.
- Scribe: requester username and encoded audio upload; render transcript JSON.
- UnlimitedOCR: requester username and PDF/image upload; upload via /api/files, then parse through the thin public adapter below.

## UnlimitedOCR public adapter

Add async POST /api/parse accepting an owned file_id. It must use the existing InternalTokenMiddleware, require X-Requester-Username, call the same service.parse(owner, file_id) as tool_ocr_parse, and count work through usage.track. Reuse existing service exceptions and sanitized status mappings; never copy OCR engine, cache, quota or semaphore logic. Return the existing ParseResult fields. This is a general public client route, not an admin bypass. Add HTTP tests for auth, identity, ownership, success and service failures.

## Request lifecycle and bounds

Use one AbortController per run, covering lookups/follow-up operations where applicable. Disable duplicate submits; enable Cancel until the operation ends. Announce status through aria-live, showing HTTP status, elapsed milliseconds and readable error bodies including 401/403/422/429/503 and Retry-After. Preserve status codes for every step of multi-request operations.

Read NDJSON/SSE incrementally through ReadableStream and TextDecoder, handling UTF-8 and event boundaries across chunks. Append using textContent or text nodes only, batch DOM updates, cap visible text at 256 KiB, and cancel the reader when the response limit is reached. Pretty-print complete bounded JSON. Limit lookup responses and individual stream frames as well as total output.

Proposed browser caps: 10 MiB encoded uploads, 150 seconds decoded Whisper audio, 32 MiB binary responses, 256 KiB displayed text. Server limits remain authoritative and may be lower. Check file sizes before read/encoding. For WAV/MP3/PNG, read bounded binary chunks into a Blob, create an object URL and revoke it on replacement/sign-out/unload. Audio playback begins after the bounded response completes; NDJSON/SSE update live. No response media becomes base64.

Curl mirrors actual method, public route, query, non-secret headers and request body. Binary requests use @input-file with an explicit format note; converted Whisper curl says @input.pcm and includes its PCM format. Multi-step Bard/OCR runs expose each request. Copy through navigator.clipboard with readable fallback if unavailable. Credential placeholders remain literal.

## Verification and delivery

First add meaningful browser tests in Laya, one chat model, one TTS, Whisper and TEM, running against fake engines through real public routes. Then propagate model descriptors/page/module and add the remaining family tests, including normalize, SGLang SSE, Bard's MP3 follow-up, Scribe upload and UnlimitedOCR upload/parse. Cover error visibility, cancellation, hostile model text, output/upload bounds and credential redaction. Playwright tests may skip if unavailable, but report skips explicitly.

Run check_sync.py in every model repository and uv run pytest -q in all fifteen repositories. Set UV_CACHE_DIR to a writable folder on D: and UV_LINK_MODE=copy if cross-drive uv operations fail. Laya CI intentionally uses uv run --no-sync after excluding runtime-only laya/torch; inspect local dependencies before choosing a fallback and report it. Do not silently substitute static tests for browser tests.

Open every page in Playwright. Send one real inference request per family only where an engine is available without reading secret files, changing backends or downloading large models. Report real versus fake verification per service, plus hardware/browser/dependency limitations. Do not claim real inference merely because a real HTTP route passed with a fake engine.

Update every service README.md, deployment.md or docs/guide-web-ui.md, and CHANGELOG.md. No new environment setting is planned, so no .env.example access/change is required. Add the Try it panel matrix row in _docs/SHARED-FEATURES.md, describe family descriptors and UnlimitedOCR adapter in SERVICE-SPECIFIC.md, and align the workspace parity skill if its shared-file rules change. Do not change AGENTS.md or unrelated files.

Commit each repository's task files only, author email 214214011+scutiii2@users.noreply.github.com. Preserve pre-existing modifications: eleven model tools/check_sync.py files are already modified, and each tool service has untracked shell.bat. Shared _docs and skill files live outside these service repositories; report their working-tree location separately rather than claiming they belong to service commits.

## Acceptance and remaining builder choices

All fifteen panels work; model and tool sharing rules hold; all available required tests pass; unavoidable skips are explicit; docs/matrix are current; task changes have separate service commits; no pushes or publication. Exact internal helper names and placement remain builder choices within this design. No further product decision is needed before implementation beyond required design approval.
