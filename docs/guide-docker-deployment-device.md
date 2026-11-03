# LayaModel: Docker on a deployment device (ZimaOS server)

Run the service on a separate always-on box (default `user@your-server.local`, folder
`/media/ZimaOS-HD/Apps/LayaModel`). Laya is self-contained: it needs no other project on the box. Callers reach it
over the published port.

## Requirements on the box
- Docker + Compose v2, ssh access (root over ssh is refused; use the normal user).
- Enough RAM for the models times `LAYA_REPLICAS`.

## 1. Publish the code (from your PC)
Run `LayaModel\tools\deploy.bat`. Menu:

| Option | Does |
|---|---|
| 1 | Publish code to the `live` branch |
| 2 | Copy your `.env` to the server (one ssh/tar session, one password prompt) |
| 3 | Both |
| 4, 5 | Dry runs |
| 6 | chown helper, if option 2 says `Permission denied` |

Override the target with `DEPLOY_HOST`, `DEPLOY_DIR`, `DEPLOY_WEB_PORT` before running. Commit `main` first: publish
reads committed `main`.

## 2. First-time setup on the box
```bash
mkdir -p /media/ZimaOS-HD/Apps
git clone -b live <your-repo-url> /media/ZimaOS-HD/Apps/LayaModel
cd /media/ZimaOS-HD/Apps/LayaModel
```
Create `.env` (deploy option 2 copies yours, or `cp .env.example .env` and edit):
- `LAYA_ADMIN_PASSWORD`: a real password.
- `LAYA_REPLICAS=1` on a small box.
- `LAYA_PORT=9120` (published on all interfaces by this compose file; firewall it if the LAN should not reach it,
  or bind it to `127.0.0.1` in the `ports:` line when only callers on the box need it).

## 3. Start
```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:9120/health
```
First start downloads weights into the `models` volume; `laya_lb` waits for a healthy replica.

## 4. Connect callers
1. Open `http://<box-ip>:9120/admin/`, log in with the admin password, create one token per caller.
2. The caller holds the URL and token. Laya never references it:
   - Caller on another machine: `http://<box-ip>:9120`.
   - Caller in a Docker stack on the same box: `http://host.docker.internal:9120`.
   - Citadel: set `LAYA_SERVICE_URL` and `LAYA_TOKEN` on the Citadel side, then run `docker compose up -d` there
     (not `restart`; `restart` does not reload `env_file`).

## Project checkpoints

Train typed-decisions on your PC and transfer the complete project directory to the deployment host.
Set `LAYA_PROJECT_MODELS_DIR=/models/projects` in the host's Compose `.env` (unset = off), recreate containers,
then copy the directory into the shared volume:

```bash
docker compose up -d --build
docker compose exec laya mkdir -p /models/projects
docker compose cp ./garrison laya:/models/projects/garrison
```

See [required files](../README.md#project-checkpoints). Send `"project": "garrison"` with `/v1/ask`;
names match `^[a-z0-9][a-z0-9_-]{0,47}$` or return 422. A project replaces only TYPED; English stays base.
Missing artifacts fall back; `model`, `fallback` and `project_used` report actual selection. `LAYA_MAX_PROJECT_MODELS` defaults
to `2` per replica, on top of the base engines. `/health` lists resident `project_models`.
Hub weight downloads never touch local artifacts. Restart replicas after replacing a loaded checkpoint.

## Updating
```bash
cd /media/ZimaOS-HD/Apps/LayaModel
git pull
docker compose up -d --build
docker compose restart laya_lb
```

## Problems
| Symptom | Fix |
|---|---|
| Caller cannot reach Laya | Check the caller's URL and token, and that `curl http://<box-ip>:9120/health` works. |
| nginx 502 after scaling or update | `docker compose restart laya_lb`. |
| Permission denied on push | deploy.bat option 6. |
| Port 9120 in use | Another Laya stack is running on the box (for example one built by a caller's own compose); both use 9120. |
