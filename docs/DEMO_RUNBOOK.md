# Demo Runbook

A minimal, reproducible path to stand up the Soft Power Analytics stack for a
demo from a fresh checkout. Targets the **default `docker-compose.yml`**
(Compose-managed volume + network — no pre-steps).

---

## 1. Prerequisites

- Docker Engine + Compose v2 (`docker compose version`). The legacy
  `docker-compose` v1 also works.
- A populated `.env` (copy `.env.example` and fill in the values below).
- Network egress to the OpenAI-compatible LLM endpoint (for chat/RAG and report
  features) and to AWS S3 (only if demoing S3-backed ingestion/embeddings).

## 2. Minimal `.env` for a demo

```bash
cp .env.example .env
```

At minimum set:

```ini
# Database (any values; the stack creates this DB on first run)
POSTGRES_USER=softpower
POSTGRES_PASSWORD=change-me
POSTGRES_DB=softpower

# API_PORT does double duty in docker-compose.yml:
#   1. Host port the API/React UI is published on (container always listens on 8000)
#   2. Port containers use to call back to a host-run server/main.py proxy
#      (API_URL / S3_PROXY_URL = http://host.docker.internal:${API_PORT:-7001})
# .env.example ships API_PORT=8000 — with that value the UI publishes on 8000
# and containers expect any host proxy on 8000 too. Compose defaults to 7001
# when the variable is unset. Either works for a demo; just browse to the port
# you set.
API_PORT=7001
# Host port for the Streamlit dashboard. docker-compose.yml interpolates
# DASHBOARD_PORT (default 8501); note .env.example lists STREAMLIT_PORT=8501,
# which the default compose file does NOT read (it is used by other stacks).
DASHBOARD_PORT=8501

# Skip enterprise JWT for the demo so the UI is reachable without a gateway
DEV_AUTH_BYPASS=true
DEV_AUTH_ROLE=admin

# LLM access (needed for chat/RAG, summaries, report generation)
CLAUDE_KEY=sk-...

# Keep the experimental agent out of the demo build
DISABLE_AGENT=true
```

Leave `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` blank unless demoing S3.

## 3. Start the stack

```bash
# Build images and start db + api + dashboard + redis
docker compose up -d --build

# Apply database migrations (one-off)
docker compose --profile migrate up

# Watch logs
docker compose logs -f api
```

## 4. Smoke checks

```bash
# API health (expect HTTP 200 / {"status": ...})
curl -fsS http://localhost:${API_PORT:-7001}/api/health && echo OK

# DB connectivity from the host (optional)
python -c "from shared.database.database import health_check; print('DB OK' if health_check() else 'DB FAIL')"
```

Then in a browser:

- **React UI + API:** `http://localhost:7001` (or your `API_PORT`)
- **Streamlit dashboard:** `http://localhost:8501` (or your `DASHBOARD_PORT`)

If the database is empty, load a demo dataset/backup before showing data-heavy
pages (see `PRODUCTION_DOCKER_RUN.md` for import/restore, and the embeddings
backup/restore docs under `services/pipeline/embeddings/`).

## 5. Teardown

```bash
# Stop containers (data persists in the managed postgres_data volume)
docker compose down

# Full reset (DELETES demo data)
docker compose down -v
```

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `env file .../.env not found` | No `.env` | `cp .env.example .env` and fill it in |
| `POSTGRES_USER ... must be set` | Empty required var | Set DB vars in `.env` |
| API 401 / login wall in demo | JWT enforced | Set `DEV_AUTH_BYPASS=true` |
| Port already allocated | Host port in use | Change `API_PORT` / `DASHBOARD_PORT` in `.env` |
| Empty dashboards | No data loaded | Restore a DB/embeddings backup |
| `/api/agent/*` errors | Experimental agent | Set `DISABLE_AGENT=true` (see `agent/README.md`) |

## 7. Compose file selection

- `docker-compose.yml` — **this runbook** (default dev/demo, zero prerequisites).
- `docker-compose.dev.yml` — production-mirroring dev (external volume/network).
- `docker-compose.production.yml` — production from Docker Hub images, host networking; see `PRODUCTION_DOCKER_RUN.md`.
- `docker-compose.enterprise.yml` — enterprise host (hosted Postgres, host networking, gateway JWT); see `docs/ENTERPRISE_AGENT_RUNBOOK.md`.
- `docker-compose.laptop.yml` — laptop pipeline/dev stack.
- `docker-compose.laptop.embed.yml` — embed-container overlay for the laptop stack (models + `./data` bind mounts).
- `docker-compose.laptop.gpu.yml` — GPU overlay for the laptop stack.
- `docker-compose.preprocessing.yml` — preprocessing/pipeline stack (batch jobs, re-embeds).
- `docker-compose.windows.yml` — Windows host adjustments.
