# Deploying LayaModel

LayaModel is one HTTP service (port 9120) that owns the Laya models. It runs as `laya` replicas behind an nginx
load balancer, `laya_lb`. Callers use `http://<host>:9120`; they never reach a replica directly.

It is its own Docker stack. Other projects (Citadel's legion, for example) call it over HTTP and never build or start it.

## Ways to run it

Each path has its own guide. Pick one.

| Path | Guide |
|---|---|
| Docker Desktop on this PC | [guide-docker-same-device.md](docs/guide-docker-same-device.md) |
| Docker on another always-on machine, published with `tools\deploy.bat` | [guide-docker-deployment-device.md](docs/guide-docker-deployment-device.md) |
| Directly on Windows, no Docker | [guide-no-docker-same-device.md](docs/guide-no-docker-same-device.md) |

Once it runs, the admin page is covered by [guide-web-ui.md](docs/guide-web-ui.md).
