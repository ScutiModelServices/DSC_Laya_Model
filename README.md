# LayaModel

The one place Laya (the `laya` package, torch and the model weights) is installed. Projects send it
typed questions over HTTP instead of loading the models themselves.

## API

| Call | Purpose |
|---|---|
| `POST /v1/ask` | `{"strategy", "project", "text", "questions", "max_len", "min_confidence", "thresholds", "prune_ratio", "pair_ratio", "trace"}` → `{"strategy", "model", "fallback", "project_used", "answers", "uncertain", "calls"}`. Callers pick a strategy and optionally a project. |
| `GET /v1/strategies` | The strategy names and the default (`auto`). |
| `POST /v1/validate` | `{"questions"}` → `{"ok": true}`, or 422 with what is wrong. The one definition of a valid question set (types, `criteria` shapes, 2 to 10 options, 1 to 8 questions); `/v1/ask` applies it too. Callers check their questions here instead of keeping a copy of the rules. |
| `POST /v1/combine` | `{"voters", "options", "prune_ratio", "pair_ratio"}` → the voters over `options` (renormalised), their `average`, the `kept` options and, with two voters or more, the pairwise `final` comparison. For a caller that adds its own voter (Garrison's cloud fallback, Jev) to the local models. |
| `GET /health` | `{"status": "ok", "models": {name: loaded}, "project_models": {project: checkpoint_path}, ...}`. No token needed; `ready` still requires both base models. |

When `LAYA_ADMIN_PASSWORD` is set, send a project token (made on `/admin/`) as `X-Internal-Token`. Errors: 422 bad request or question, 413 text longer
than the window, 502 no model could answer, 500 anything else. A 413 or 502 body is
`{"detail": {"message", "calls"}}`, so the caller can still log the model calls that were made.

`calls` lists each model call of a request (`model`, `fallback`, `input_tokens`, `duration_ms`, `ok`) for usage logs.
`trace: true` adds `rounds`, and for a choice question the service could not settle it also returns `options` and
`voters` (each model's latest answer), which is what `/v1/combine` continues from.

### Project checkpoints

Training happens elsewhere. Copy a finished typed-decisions checkpoint into
`<LAYA_PROJECT_MODELS_DIR>/<project>/`. Set `LAYA_PROJECT_MODELS_DIR` to enable local project models;
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

`LAYA_MAX_PROJECT_MODELS` (default `2`, minimum `1`) bounds resident project engines per replica using LRU.
Busy engines are not evicted; if all slots stay busy a request waits up to `LAYA_PROJECT_WAIT_SECONDS` (default `30`),
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

## Run

Local: `run.bat` (port 9120). Docker, with a load balancer:

```bash
docker compose up -d --build
```

`laya` runs `LAYA_REPLICAS` copies (default 1) behind an nginx (`laya_lb`, `least_conn`, one retry on
error/timeout). Each replica keeps its own models in RAM; downloaded weights are shared in the `models`
volume. After changing the replica count, run `docker compose restart laya_lb`. Full guide (standalone vs the
Citadel stack, first start, scaling, troubleshooting): [deployment.md](deployment.md).

### Guides

- [Docker on the same device](docs/guide-docker-same-device.md)
- [Docker on a deployment device](docs/guide-docker-deployment-device.md)
- [Without Docker on the same device](docs/guide-no-docker-same-device.md)
- [Admin web UI](docs/guide-web-ui.md): tokens and CPU threads at `/admin/`; the same for every run option

## Settings

`LAYA_HOST` (127.0.0.1), `LAYA_PORT` (9120), `LAYA_ADMIN_PASSWORD` (unlocks `/admin/`; empty = no auth), `LAYA_WARM` (1 loads both models at start), `LAYA_TORCH_THREADS` (cap on torch CPU threads; `/health` reports the value in use),
`LAYA_OFFLINE` (1 uses only already downloaded weights).

## Consumers

Every project reaches the service the same way: `POST /v1/ask` with a strategy.

- **Garrison** (`apps/garrison/src/llm/laya_agent.py`, class `LAYAAgent`): set `LAYA_SERVICE_URL` and `LAYA_TOKEN` to a project token made on `/admin/`.
  Its triage flow asks for a strategy per question type and adds Jev for what the service could not settle,
  using `/v1/combine`, and checks questions with `/v1/validate`. Garrison loads no Laya model and keeps no copy of
  the question rules.
- **Armory** keeps its `LAYA_URL`: it asks Garrison's triage agent, which asks this service.
- Not yet moved: `ascension`, `chess`, `sortie` and `ClaudeRouters` still carry their own copy of Laya.

## License

The service code is MIT. The `laya` package and the Laya weights it downloads are by Convai Innovations
under Apache-2.0; read their terms before redistributing the weights. See [LICENSE](LICENSE) and [CHANGELOG.md](CHANGELOG.md).
