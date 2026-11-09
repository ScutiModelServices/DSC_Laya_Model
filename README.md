# DSC_Laya_Model

The one place Laya (the `dsc_laya_model` package, torch and the model weights) is installed. Projects send it
typed questions over HTTP instead of loading the models themselves.

## API

| Call | Purpose |
|---|---|
| `POST /v1/ask` | `{"strategy", "project", "text", "questions", "max_len", "min_confidence", "thresholds", "prune_ratio", "pair_ratio", "trace", "stream"}` → `{"strategy", "model", "fallback", "project_used", "answers", "uncertain", "calls"}`. Callers pick a strategy and optionally a project. |
| `GET /v1/strategies` | The strategy names and the default (`auto`). |
| `POST /v1/validate` | `{"questions"}` → `{"ok": true}`, or 422 with what is wrong. The one definition of a valid question set (types, `criteria` shapes, 2 to 10 options, 1 to 8 questions); `/v1/ask` applies it too. Callers check their questions here instead of keeping a copy of the rules. |
| `POST /v1/combine` | `{"voters", "options", "prune_ratio", "pair_ratio"}` → the voters over `options` (renormalised), their `average`, the `kept` options and, with two voters or more, the pairwise `final` comparison. For a caller that adds its own voter (Garrison's cloud fallback, Jev) to the local models. |
| `GET /health` | `{"status": "ok", "models": {name: loaded}, "project_models": {project: checkpoint_path}, ...}`. No token needed; `ready` still requires both base models. |

**Streaming.** With `"stream": true` the reply of `/v1/ask` is `application/x-ndjson`: a `{"type": "answer", "id", "answer"}`
line for each question as it is settled, then `{"type": "done", ...}` holding the same body a call without `stream`
returns. Most strategies ask each model once for all the questions, so the first line arrives after that call and the
rest follow at once; a strategy that asks a second model or narrows a question makes later answers wait longer. A
refusal before the first answer (413, 502, 503) is still a normal HTTP error; a failure after it is sent as
`{"type": "error", "status", "detail"}` and ends the stream. `stream` is not part of the cache key, so a streamed answer
fills the cache for plain calls and the other way round; a kept answer is streamed back at once.

When `SERVICE_ADMIN_PASSWORD` is set, send a project token (made on `/admin/`) as `X-Internal-Token`. Errors: 422 bad request or question, 413 text longer
than the window, 502 no model could answer, 500 anything else. A 413 or 502 body is
`{"detail": {"message", "calls"}}`, so the caller can still log the model calls that were made.

`calls` lists each model call of a request (`model`, `fallback`, `input_tokens`, `duration_ms`, `ok`) for usage logs.
`trace: true` adds `rounds`, and for a choice question the service could not settle it also returns `options` and
`voters` (each model's latest answer), which is what `/v1/combine` continues from.

### Project checkpoints

Training happens elsewhere. Copy a finished typed-decisions checkpoint into
`<SERVICE_PROJECT_MODELS_DIR>/<project>/`. Set the **Project models folder** on the admin Settings card (`SERVICE_PROJECT_MODELS_DIR` for `run.bat`) to enable local project models;
unset or empty disables them. The directory must contain `rl_agent_config.json`, `model.safetensors`,
`encoder/config.json` and `tokenizer/tokenizer.json` and be loadable by `laya.load(path)`.

Send `"project": "garrison"` with `/v1/ask`. Names must match `^[a-z0-9][a-z0-9_-]{0,47}$`;
invalid names (including empty strings) return 422. An absent project keeps the base engines. A missing or
incomplete artifact uses base typed-decisions with `fallback: true`; no arbitrary Hub model can be requested.
Directory aliases and required-file symlinks escaping the configured root are treated as missing artifacts.

A project checkpoint replaces **TYPED only**, because fine-tuning targets typed-decisions. English remains
`convaiinnovations/laya` in every strategy. Each call reports its actual checkpoint in `model`; the response's
`model` joins distinct successful checkpoints with `+` when a strategy uses both. `fallback` is true if a TYPED call
requested a project but used the base checkpoint. English calls and requests without a project report false.
`project_used` is true only when a project checkpoint answered; it stays false if the strategy never asked the typed
model (`english-only`, or English was sure), so a caller can tell the project was not consulted.

`SERVICE_MAX_PROJECT_MODELS` (default `2`, minimum `1`) bounds resident project engines per replica using LRU.
Busy engines are not evicted; if all slots stay busy a request waits up to `SERVICE_PROJECT_WAIT_SECONDS` (default `30`),
then gets `503` with `Retry-After: 5`. Both base engines stay outside this cap.
`/health` lists resident project names and paths in `project_models`. **Download new weights** remains Hub-only:
it neither changes nor reloads local project checkpoints. Restart replicas after replacing a resident artifact.
See [deployment.md](deployment.md) for copying checkpoints into the shared volume.

### Strategies

| Strategy | What it does | Cost |
|---|---|---|
| `english-only` | Laya English alone. | 1 call |
| `typed-only` | Laya typed-decisions alone. | 1 call |
| `english-then-typed` | English; if unsure, typed; if still unsettled, drop clearly worse options and ask again (up to 3 rounds); last, compare both models' confidence-weighted values (`also_check` / `unknown`). Today's local flow without Jev. | 1-2+ calls |
| `typed-then-english` | The same, typed first. | 1-2+ calls |
| `most-confident` | Both answer; the more confident wins; disagreement is reported. | 2 calls |
| `average` | Both answer; probabilities (choice) or values (score, noul) are averaged. | 2 calls |
| `consensus` | Both answer; certain only when they agree and both meet their threshold, else `unknown` with `candidates`. | 2 calls |
| `auto` (default) | `english-then-typed`, except noul questions skip English (its yes/no label bug). | 1-2+ calls |

Thresholds (answer confidence at or above): 2 options English 0.90 / typed 0.80; 3+ options 0.75 / 0.55.
A request's `min_confidence` replaces all of them; `thresholds` (`{"2": {"laya-english": 0.9}, "3+": {...}}`)
replaces single ones. `prune_ratio` (0.25; 0 never drops options) and `pair_ratio` (0.5) tune the narrowing and the
pairwise comparison. Answers carry the pick, `answer_confidence`, `uncertain`,
`answered_by`; `trace: true` adds `rounds`. No strategy calls a cloud model.

## Admin page

`/admin/` (password `SERVICE_ADMIN_PASSWORD`): tokens with project scope, rate limit and expiry, usage numbers, CPU threads, the Settings card (update check hours, in-flight cap, weight sync, answers kept, project models and their folder, load at start), and the Updates card (**Check now** and **Download new weights**). New weights are downloaded once into the shared models volume; the other replicas notice within a few seconds and load them on their next request. `/metrics` (admin password) is Prometheus text per token. See [the web UI guide](docs/guide-web-ui.md).

## Run

Weights load only from downloaded files. An empty installation still serves `/admin/` and `/health` with `ready: false`; inference returns 503. Open `/admin/` → **Updates** → **Download** before the first inference. Checks run online without downloading; only an admin Download replaces weights and reloads replicas.

Local: `run.bat` (port 29220). Docker, with a load balancer:

```bash
docker compose --env-file .env.compose up -d --build
```

`dsc_laya_model` runs `SERVICE_REPLICAS` copies (default 1) behind an nginx (`dsc_laya_model_lb`, `least_conn`, one retry on
error/timeout). Each replica keeps its own models in RAM; downloaded weights are shared in the `dsc_laya_model_models`
volume. After changing the replica count, run `docker compose restart dsc_laya_model_lb`. Full guide (first start, scaling,
troubleshooting): [deployment.md](deployment.md).

### Guides

- [Docker on the same device](docs/guide-docker-same-device.md)
- [Docker on a deployment device](docs/guide-docker-deployment-device.md)
- [Without Docker on the same device](docs/guide-no-docker-same-device.md)
- [Admin web UI](docs/guide-web-ui.md): tokens and CPU threads at `/admin/`; the same for every run option

## Settings

`SERVICE_HOST` (127.0.0.1), `SERVICE_PORT` (29220), `SERVICE_ADMIN_PASSWORD` (unlocks `/admin/`; empty = no auth), `SERVICE_WARM` (1 loads both models at start), `SERVICE_TORCH_THREADS` (cap on torch CPU threads; `/health` reports the value in use),
Loading always uses downloaded weights.

## Tests

```bash
uv sync
uv run pytest
```

## Limits worth knowing

- More than the in-flight cap (`max_inflight`) waiting calls get 429 with `Retry-After: 5`.
- The rate limit and the answer cache are per replica, and each replica keeps its own models in RAM.
- At most `max_project_models` project checkpoints stay loaded; a call waits up to `project_wait_seconds` for a free slot.
- No strategy calls a cloud model.

## Consumers

Every project reaches the service the same way: `POST /v1/ask` with a strategy.

- **Garrison** (`apps/garrison/src/llm/laya_agent.py`, class `LAYAAgent`): configure its Laya connection URL and a project token made on `/admin/`. Consumer configuration names belong to Citadel and are unchanged.
  Its triage flow asks for a strategy per question type and adds Jev for what the service could not settle,
  using `/v1/combine`, and checks questions with `/v1/validate`. Garrison loads no Laya model and keeps no copy of
  the question rules.
- **Armory** keeps its existing connection URL: it asks Garrison's triage agent, which asks this service.
- Not yet moved: `ascension`, `chess`, `sortie` and `ClaudeRouters` still carry their own copy of Laya.

## License

The service code is MIT. The `dsc_laya_model` package and the Laya weights it downloads are by Convai Innovations
under Apache-2.0; read their terms before redistributing the weights. See [LICENSE](LICENSE) and [CHANGELOG.md](CHANGELOG.md).

## Try it from the admin page

Open `/admin/` and use **Try it** to call the public API with a service token held only in memory. Runs use normal token scopes, limits and caches; the panel shows HTTP status, latency, responses, cancellation and a curl equivalent with a token placeholder. See [the web UI guide](docs/guide-web-ui.md#try-it-send-a-real-request).

Ask sends `text` and a questions JSON object to `POST /v1/ask`, with the selected strategy and optional project. Streaming appends one NDJSON answer event per question. The operation menu also exposes question validation, vote combination and the strategy list.

## Documentation

- [Purpose: input, output and what it is for](docs/purpose.md)
- [Test it from the command line](docs/guide-cli.md)

## Admin pages and navigation

Open `/admin/` and sign in. Overview opens first; each sidebar destination has its own URL fragment and supports browser Back/Forward and direct links:

- `#overview`: health, model readiness, replicas, active tokens and request counts.
- `#playground`: service-specific request forms and streamed or media responses.
- `#tokens`: create, edit, expire and revoke scoped access tokens.
- `#runtime`: CPU threads where supported, replica information and live/restart settings.
- `#updates`: check versions and explicitly download weights.
- `#connections`: caller addresses and public endpoints.
- `#models` and `#voices`: project models and cloned voices, shown only when supported.

Collapse/Expand switches between labeled sidebar and icon rail on desktop and mobile. The non-secret sidebar preference is saved per service in this browser. Navigation preserves form drafts and active Playground requests; sign-out still clears credentials and cancels work. Page names appear once in the main header. No preview data or cross-service selector ships in the real admin UI. Existing authentication, quotas, settings precedence and explicit-download policy remain in effect.
