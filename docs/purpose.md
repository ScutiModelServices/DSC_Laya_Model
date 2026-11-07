# Purpose

**Kind:** typed question + options → ranked answer (decision)

**Group:** Decision

**Tags:** `cpu`, `streaming`, `project-models`

| | |
|---|---|
| Input | Text plus 1–8 questions, each with 2–10 options. Strategy name, optional project, confidence thresholds. |
| Output | JSON: chosen answers, per-option scores, `uncertain` flag. |
| Runs on | CPU/torch. Own per-project models and strategies. |

Also: `/v1/validate` checks a question set. `/v1/combine` merges votes from several voters, local or cloud.

**Use for:** choosing among given options. Per-project prediction.
**Not for:** free-form chat or text generation.
