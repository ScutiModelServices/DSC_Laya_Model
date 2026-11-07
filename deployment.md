# Deploying DSC_Laya_Model

DSC_Laya_Model is one HTTP service (port 9120) that owns the Laya models. It runs as `dsc_laya_model` replicas behind an nginx
load balancer, `dsc_laya_model_lb`. Callers use `http://<host>:9120`; they never reach a replica directly.

It is its own Docker stack. Other projects (Citadel's legion, for example) call it over HTTP and never build or start it.

## Ways to run it

Each path has its own guide. Pick one.

| Path | Guide |
|---|---|
| Docker Desktop on this PC | [guide-docker-same-device.md](docs/guide-docker-same-device.md) |
| Docker on another always-on machine, published with `run.bat` (Deploy) | [guide-docker-deployment-device.md](docs/guide-docker-deployment-device.md) |
| Directly on Windows, no Docker | [guide-no-docker-same-device.md](docs/guide-no-docker-same-device.md) |

Once it runs, the admin page is covered by [guide-web-ui.md](docs/guide-web-ui.md).
