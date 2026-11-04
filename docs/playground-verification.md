# Playground verification — 2026-11-05

The Try it card is implemented in all twelve model services and all three tool services. One identical `web/playground.js` is copied to all fifteen. Model HTML remains identical except CONFIG; tool HTML and admin_api.py remain identical across their three services. All twelve `tools/check_sync.py` checks pass, including the renamed STTWhisperModel sibling and the new shared JavaScript file. Independent final review found no remaining important issues.

## Authentication decision

Option **(b)**: paste a public service token, held only in memory. Requests go directly to the normal public routes, with existing authentication, token scope, throttling, admission limits, ownership and caches. The admin password is never forwarded to inference. Tokens are cleared on reset, sign-out and page unload. Curl substitutes `<SERVICE_TOKEN>` and all displayed text is credential-redacted before clipping. Tools require a requester username as ordinary HTTP clients do.

## Per-service results

Every service's browser test opened its actual admin page, signed in, submitted a real HTTP request through its public route and rendered the result using a fake engine. Live below means an additional browser request with actual cached model inference, not merely a live HTTP server.

| Service | Full suite | Fake-engine browser | Additional live inference |
|---|---:|---|---|
| LayaModel | 113 passed | Passed, including auxiliary operations and error/security/bounds regressions | Attempted; readable HTTP 500, complete pinned Laya checkpoint unavailable offline |
| MiniCPM52BModel | 112 passed | Passed | Not run; chat family verified with LFM3 |
| BitNet2BModel | 73 passed | Passed | Not run; chat family verified with LFM3 |
| LFM3Model | 74 passed | Passed | Passed: actual LFM3, streamed greeting, 25.5 s |
| SparkX26Model | 74 passed | Passed | Not run; chat family verified with LFM3 |
| S1MiniModel | 61 passed | Passed | Passed: actual S1-mini, “um hello there” → “Hello there.”, 45.5 s |
| STTWhisperModel | 64 passed | Passed, raw PCM, stereo WAV conversion and upload bound | Passed: actual faster-whisper base.en, 1 s PCM, NDJSON segment/done, 1.36 s |
| TTSPiperModel | 68 passed | Passed | Passed: actual cached Piper voice, 97,324-byte WAV, 4.58 s |
| TTSParadeeModel | 67 passed | Passed | Not run; TTS family verified with Piper and Bard |
| TTSAudio8Model | 82 passed | Passed | Not run; TTS family verified with Piper and Bard |
| TEMImageModel | 54 passed, 1 skipped | Passed, inline PNG and Blob download | Not run; real-model test prerequisites unavailable |
| SGLangModel | 47 passed | Passed, multimodal image request | Not run; actual SGLang backend requires Linux/GPU deployment |
| Bard | 122 passed, 1 browser-module skip in native environment; separate browser run 2 passed | Passed, owned MP3 follow-up and auth error | Passed: actual Piper, 31,459-byte playable MP3, 4.39 s audio, 2.54 s request |
| Scribe | 109 passed | Passed, encoded upload/transcript and auth error | Not run separately; speech recognition family verified with STTWhisperModel |
| UnlimitedOCR | 94 passed | Passed, upload → public parse and auth error | Not run; real OCR backend unavailable |

Live checks used offline-only mode, existing cached artifacts, fresh invented task credentials and isolated temporary data directories. Existing deployment secrets/settings were not read or changed; no models were downloaded. Piper reused the existing local Bard voice. Whisper used its actual model and service engine with a loader directed to the existing Scribe snapshot: default Hub artifact discovery refused missing ancillary README/.gitattributes metadata while offline. The live check does not claim that incomplete Hub snapshot discovery was repaired.

## Commands and environment limitations

- Model full suites: `uv run pytest -q`, with service-local `--basetemp` under `.pytest_cache`. Audio8 and Whisper required `uv run python -m pytest -q` because moved pytest executable launchers reported `uv trampoline failed to canonicalize script path`.
- Set `UV_CACHE_DIR=D:/User/Documents/Programming/.uv-cache` and `UV_LINK_MODE=copy`; TEMP/TMP also used service-local test folders.
- Windows sandbox blocked Python asyncio localhost socketpair startup. HTTP and Chromium checks ran through approved outside-sandbox execution.
- Bard's normal dependency sync could not resolve an optional spaCy wheel because DNS was unavailable. Its complete native suite passed with `uv run --no-sync pytest -q`; the native environment lacks usable Playwright. Python 3.14 cannot import the existing OCR environment's Python 3.13 greenlet extension, so Bard browser tests used the compatible OCR environment separately. Native inference was also tested through a separate temporary server driven by that browser environment.
- Scribe's full suite, including browser tests, passed using the existing OCR environment without dependency installs. UnlimitedOCR used its existing `.venv_ocr` environment. An attempted full Bard run using only OCR dependencies failed because that environment lacks numpy/Piper; the native full suite and separate browser suite supersede that unsuitable-environment attempt.
- TEM's existing real-model test was skipped for missing real-model prerequisites. Existing Starlette, pytest-asyncio and pydantic warnings were non-failing.

## Changes supporting verification

The public UnlimitedOCR `/api/parse` adapter delegates to the existing owned-file parse service; tests cover token/identity/ownership, malformed and oversized inputs, caching and sanitized backend failures. A pre-existing Whisper import typo was corrected from the service name to the dependency's actual `WhisperModel` export, with a regression test. A stale Audio8 browser assertion now expects the actual default model name.

Browser regressions cover 401/422/429/503 bodies, cancellation, hostile model text, credential escaping and clipping boundaries, output/upload caps, JSON request download for large curl bodies, reset/sign-out cleanup and PCM conversion. Media uses bounded Blob URLs and downloads, not base64 response strings. Stream frames are appended live as readable NDJSON/SSE text. Desktop/mobile captures and live screenshots were inspected; local evidence is retained under the relevant `.pytest_cache` folders.

README, deployment/web UI guides and changelogs are updated per service. The matrix and intentional family differences are updated in `AI/ModelServices/_docs/SHARED-FEATURES.md` and `SERVICE-SPECIFIC.md`; the workspace parity skill includes the shared module. Those shared documentation/skill files are outside the individual service repositories and remain workspace changes. No environment setting was added, so `.env.example` required no change.

Task files are committed per service with author email `214214011+scutiii2@users.noreply.github.com`. No push or publish operation is part of this task. Existing tool `shell.bat` files remain untouched. Initial task-generated test directories are retained under ignored `.pytest_cache` folders without deleting files.
