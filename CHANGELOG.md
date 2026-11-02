# Changelog

## Unreleased

- Admin page **Updates** card: reports a newer `laya` package (PyPI) and newer weights (Hugging Face), checks every
  `LAYA_UPDATE_CHECK_HOURS` (default 6), and downloads new weights on request, then reloads the models on that replica.
  The package is only reported. New routes: `GET /admin/updates`, `POST /admin/updates/check`, `POST /admin/updates/weights`.

## 1.0.0

First public release.

- Admin page at `/admin/`: health badge with polling, token list with created, last used and request count,
  copy button, in-app revoke dialog, CPU thread control, light and dark themes, phone layout.
- Per-project tokens stored as hashes; `last_used` and `requests` are written at most once a minute per token,
  so the counters are approximate and a crash can lose up to a minute of them.
- Docker Compose deployment behind nginx with configurable replicas; guides for Docker on the same device,
  Docker on a deployment device, and no Docker.
- Browser smoke test (Playwright) and a GitHub Actions workflow that runs the tests.
