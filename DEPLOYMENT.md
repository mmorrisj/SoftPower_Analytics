# Deployment Guide — Start Here

The project has several deployment paths documented across multiple files. This
page is the **decision tree**: find your scenario, then follow the linked doc.
(The linked docs remain authoritative for their scenario; this page only routes.)

```
What are you doing?
│
├─ Local demo / first run on a laptop ─────────────► docs/DEMO_RUNBOOK.md
│     default docker-compose.yml, zero prerequisites
│
├─ Local development with hot-reload / debugging ──► DOCKER_WORKFLOW.md
│     docker-compose.dev.yml (external volume + network, mirrors prod)
│
├─ Standard production (permissive Docker daemon) ─► README.md § Production Operations
│     docker-compose.production.yml
│
├─ Enterprise / hardened daemon ───────────────────► docker-compose.enterprise.yml
│     scripts/docker/enterprise-deploy.sh (hosted PostgreSQL, host networking);
│     raw `docker run` fallback: PRODUCTION_DOCKER_RUN.md
│
├─ Migrating data to a new host / Rocky 9 ─────────► scripts/docker/production-wipe.sh
│     then `production-deploy.sh rebuild-db [file]` (see PRODUCTION_DOCKER_RUN.md)
│
└─ Just pulling the published image ───────────────► docs/DOCKERHUB_README.md
```

## Quick reference

| Scenario | Compose file | Primary doc |
|---|---|---|
| Demo / quickstart | `docker-compose.yml` | [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md) |
| Local dev (hot-reload) | `docker-compose.dev.yml` | [DOCKER_WORKFLOW.md](DOCKER_WORKFLOW.md) |
| Standard production | `docker-compose.production.yml` | [README.md](README.md) § Production Operations |
| Enterprise hardened daemon (hosted PostgreSQL) | `docker-compose.enterprise.yml` + `scripts/docker/enterprise-deploy.sh` | [PRODUCTION_DOCKER_RUN.md](PRODUCTION_DOCKER_RUN.md) (raw `docker run` fallback) |
| Windows host | `docker-compose.windows.yml` | [DOCKER_WORKFLOW.md](DOCKER_WORKFLOW.md) |
| Laptop stack (pull-and-run, no build) | `docker-compose.laptop.yml` | header comments in the compose file |
| Laptop GPU embedding runner (preprocessing-image overlay) | `docker-compose.laptop.embed.yml` | header comments in the compose file |
| Laptop GPU device reservation (overlay) | `docker-compose.laptop.gpu.yml` | header comments in the compose file |
| Preprocessing batch jobs | `docker-compose.preprocessing.yml` | [services/pipeline/batch/README_BATCH_PROCESSING.md](services/pipeline/batch/README_BATCH_PROCESSING.md) |
| Data migration / restore | — | `scripts/docker/production-wipe.sh` + `production-deploy.sh rebuild-db [file]` ([PRODUCTION_DOCKER_RUN.md](PRODUCTION_DOCKER_RUN.md)) |

## ⚠️ Important: do not mix daemon assumptions

The **enterprise hardened-daemon** path (`PRODUCTION_DOCKER_RUN.md`) deliberately
avoids patterns that the dev/standard docs use freely — `docker exec`, custom
bridge networks, and `--rm`. Applying permissive-daemon instructions on a
hardened host (or vice-versa) leads to silent failures. Confirm which daemon you
are on **before** choosing a doc.

## Production database: external Postgres 18 vs. the bundled container

Postgres 18 + the required extensions is validated, so **production can run
against a native/managed Postgres instead of the bundled pgvector container.**
In `docker-compose.production.yml` the `db` service is gated behind the
`bundled-db` profile, and `app`/`migrate` depend on it with `required: false`.

**Required Postgres extensions** (must exist in the target database):
- `vector` (pgvector) — embeddings / semantic search
- `pg_trgm` — fuzzy / similarity matching (e.g. entity name resolution in the chat/RAG service)

Enable them once per database (superuser):
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

**Option A — external/native Postgres 18 (recommended for production):**
```bash
# In .env: point at the Postgres host (defaults to 127.0.0.1, i.e. the host's
# native Postgres under host networking) and set creds.
#   DB_HOST=127.0.0.1        # or pg18.internal for a remote managed instance
#   DB_PORT=5432
#   POSTGRES_USER=... POSTGRES_PASSWORD=... POSTGRES_DB=...

# Migrate, then start — NO bundled container:
docker compose -f docker-compose.production.yml --profile migrate up
docker compose -f docker-compose.production.yml up -d
```

**Option B — bundled pgvector container (legacy / self-contained):**
```bash
docker compose -f docker-compose.production.yml --profile bundled-db --profile migrate up
docker compose -f docker-compose.production.yml --profile bundled-db up -d
```

> Development and the demo quickstart keep the bundled container by default
> (`docker-compose.yml`, `docker-compose.dev.yml`) — no change there.
> See `docs/archive/MAINTAINABILITY_ASSESSMENT_2026-06.md` §9 for the rationale.

## Common to all paths

- Populate `.env` from `.env.example` (DB creds, `CLAUDE_KEY`, optional AWS/S3).
- Run Alembic migrations after the DB is up (`--profile migrate`).
- See [CLAUDE.md](CLAUDE.md) for architecture and the environment-variable hierarchy.
