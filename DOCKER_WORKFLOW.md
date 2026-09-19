# Docker Workflow Reference

Quick reference for building, running, and maintaining the SoftPower Analytics Docker container.

> **Enterprise / hardened hosts:** this doc assumes a permissive Docker daemon — on hardened daemons (no `docker exec`, no bridge networks) see [`PRODUCTION_DOCKER_RUN.md`](./PRODUCTION_DOCKER_RUN.md) instead.

## Architecture

The production image is a **single container** running two services via supervisord:
- **FastAPI** (port 8000 inside container) — React UI + API + Chat/RAG
- **Streamlit** (port 8501 inside container) — Analytics dashboard

The database (PostgreSQL + pgvector) runs separately — either as its own container or on the host.

**LLM Proxy Relay:** The container does **not** call LLM APIs (OpenAI, Azure, etc.) directly — it lacks the certificate authorizations needed to reach external services. Instead, LLM requests are proxied through a **host-side FastAPI instance** running on port 7001, which has the proper certs and network access. The flow:

```
Container (port 8000)  -->  Host FastAPI (port 7001)  -->  LLM API (OpenAI/Azure/LiteLLM)
    via API_URL                has certs/auth              external service
```

This means the host-side FastAPI must be running for any LLM features (report generation, chat/RAG, validation) to work.

## 1. Get the Image

### Development (internet-connected machine) — build from source

```bash
sudo docker build -f docker/registry.Dockerfile -t softpower-analytics:latest .
```

**What happens during the build:**
- Stage 1: `node:22-bookworm-slim` (digest-pinned) installs npm deps and runs `npm run build` (compiles React to static files)
- Stage 2: `python:3.13-slim-trixie` (digest-pinned) installs system packages and all pip dependencies (including ML packages)
- The built React files are copied from Stage 1 into Stage 2
- Node.js is **not** present in the final image — React runs as pre-built static files

**Build times:**
- First build: ~10-15 minutes (downloads all dependencies)
- Subsequent builds: ~1-2 minutes (Docker caches unchanged layers)

**If the build fails with TypeScript errors:**
Fix the source files in `client/src/`, then re-run the build command. Only the changed layers rebuild.

## 2. Run the Container

```bash
sudo docker run -d \
  --name api-service \
  -p 8005:8000 \
  -p 8503:8501 \
  --env-file .env \
  -e DOCKER_ENV=true \
  -e DATABASE_URL=postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@host.docker.internal:5432/${POSTGRES_DB} \
  -e API_URL=http://host.docker.internal:7001 \
  --add-host=host.docker.internal:host-gateway \
  softpower-analytics:latest
```

> Container naming: the dev/demo compose stack calls this container `api-service`;
> the production stack (`docker-compose.production.yml` / `production-deploy.sh`)
> calls it `sp_prod_app`. The examples below use `api-service`.

**Port mapping format: `-p HOST:CONTAINER`**
- `-p 8005:8000` → access FastAPI/React at `http://localhost:8005`
- `-p 8503:8501` → access Streamlit at `http://localhost:8503`

**Flags explained:**
| Flag | Purpose |
|------|---------|
| `-d` | Run in background (detached) |
| `--name api-service` | Name the container for easy reference |
| `-p HOST:CONTAINER` | Map host port to container port |
| `--env-file .env` | Load environment variables from .env |
| `-e DOCKER_ENV=true` | Tell the app it's running in Docker |
| `-e DATABASE_URL=...` | Database connection string |
| `-e API_URL=...` | LLM/S3 proxy relay — base URL for host proxy on port 7001 (code appends `/proxy_query`, `/s3/*`, etc.) |
| `--add-host=host.docker.internal:host-gateway` | Linux-only: lets container reach host network |

**IMPORTANT:** The `-e API_URL` flag **overrides** the value from `.env`. The `.env` default is `API_URL=http://localhost:8000` (matching `API_PORT`); the host-side proxy port is separate (`LLM_PROXY_PORT=7001`). Either way, `localhost` is wrong inside the container — it means the container itself. The `-e` override rewrites it to `host.docker.internal:7001` so the container reaches the host proxy.

**Prerequisites:**
- Host-side LLM proxy must be running on port 7001 for LLM features.

  **Option A: Lightweight proxy (recommended — only needs `fastapi`, `uvicorn`, `openai`, `boto3`)**
  ```bash
  pip install fastapi uvicorn openai boto3 python-dotenv python-multipart
  python scripts/llm_proxy.py
  ```

  **Option B: Full server (needs full `requirements.txt`)**
  ```bash
  source venv/bin/activate
  uvicorn server.main:app --host 0.0.0.0 --port 7001
  ```

**If port 8005 is already in use**, pick another host port (e.g., `-p 9000:8000`).

## 3. Check Container Status

```bash
# Is the container running?
sudo docker ps

# Check both services (FastAPI + Streamlit)
sudo docker logs api-service 2>&1 | tail -30

# Check FastAPI specifically
sudo docker logs api-service 2>&1 | grep -E "fastapi|uvicorn|ERROR|FATAL"

# Check Streamlit specifically
sudo docker logs api-service 2>&1 | grep -i streamlit

# Follow logs in real time
sudo docker logs -f api-service

# Health check
curl http://localhost:8005/api/health

# Verify proxy env var is correct (should show host.docker.internal, NOT localhost)
sudo docker exec api-service printenv API_URL
```

## 4. Stop the Container

```bash
sudo docker stop api-service
```

## 5. Remove a Container

You must stop a container before removing it (or use `-f` to force).

```bash
# Stop then remove
sudo docker stop api-service && sudo docker rm api-service

# Force remove (even if running)
sudo docker rm -f api-service
```

## 6. Restart After Code Changes

After editing source files, rebuild and restart:

```bash
sudo docker rm -f api-service && \
sudo docker build -f docker/registry.Dockerfile -t softpower-analytics:latest . && \
sudo docker run -d \
  --name api-service \
  -p 8005:8000 \
  -p 8503:8501 \
  --env-file .env \
  -e DOCKER_ENV=true \
  -e DATABASE_URL=postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@host.docker.internal:5432/${POSTGRES_DB} \
  -e API_URL=http://host.docker.internal:7001 \
  --add-host=host.docker.internal:host-gateway \
  softpower-analytics:latest
```

## 7. Common Errors and Fixes

### "port is already allocated"

Something else is using that host port.

```bash
# Find what's using port 8005
sudo lsof -i :8005

# Pick a different host port
sudo docker run -d ... -p 9000:8000 -p 9501:8501 ...
```

### "container name is already in use"

A container with that name already exists (running or stopped).

```bash
# See all containers (including stopped)
sudo docker ps -a

# Remove the old one
sudo docker rm -f api-service
```

### FastAPI crashes / port 8005 not connecting (but Streamlit works)

Check the logs for Python import or startup errors:

```bash
sudo docker logs api-service 2>&1 | grep -E "ERROR|FATAL|Traceback|NameError|ImportError" | tail -20
```

Fix the Python source, rebuild, and restart (see section 6).

### LLM calls fail / report generation returns no response

The container proxies LLM requests through the host. Check:

1. **Host-side FastAPI running on port 7001?**
   ```bash
   curl http://localhost:7001/docs
   ```
   If not running, start the lightweight proxy:
   ```bash
   pip install fastapi uvicorn openai boto3 python-dotenv python-multipart
   python scripts/llm_proxy.py
   ```

2. **API_URL set correctly?** Must be `http://host.docker.internal:7001`.
   Check from inside the container:
   ```bash
   sudo docker exec api-service printenv API_URL
   ```

3. **Container can reach host?** Test connectivity:
   ```bash
   sudo docker exec api-service curl -s http://host.docker.internal:7001/docs | head -5
   ```

### "no such container"

The container doesn't exist. Check the name:

```bash
sudo docker ps -a --format "table {{.Names}}\t{{.Status}}"
```

## 8. Useful Docker Commands

```bash
# List all images
sudo docker images

# List running containers
sudo docker ps

# List ALL containers (including stopped/failed)
sudo docker ps -a

# Shell into a running container
sudo docker exec -it api-service bash

# Check disk usage
sudo docker system df

# Clean up dangling images (safe, frees disk space)
sudo docker image prune

# Nuclear option: remove all stopped containers and unused images
sudo docker system prune
```

## 9. Registry / Production Deployment

The **registry** path is the recommended production deployment. It produces a fully self-contained ~2GB image with ML packages and HuggingFace model baked in — pull-and-run, no manual setup.

### Build and push to Docker Hub

```bash
# Build + push the registry image (default mode)
# NOTE: with no VERSION, push-to-registry.sh tags 1.0.0 — always pass the
# release version explicitly (see README § Quick Start for the release invocation):
./scripts/docker/push-to-registry.sh registry mmorrisj <version>

# This builds docker/registry.Dockerfile with:
#   --pull --sbom=true --provenance=mode=max --push
# Produces: softpower-app (FastAPI + Streamlit + React + ML, self-contained)
# The pgvector image is built and pushed SEPARATELY via docker/pgvector.Dockerfile
# (see that file's header for build/push commands).
```

### Deploy with docker-compose.production.yml

```bash
# First time — run migrations:
docker compose -f docker-compose.production.yml --profile migrate up

# Start the stack:
docker compose -f docker-compose.production.yml up -d
```

This pulls pre-built images from Docker Hub (no local builds needed). See `docs/DOCKERHUB_README.md` for the full Docker Hub README with environment variables and restore instructions.

### Security hardening (production compose)

See [`PRODUCTION_DOCKER_RUN.md`](./PRODUCTION_DOCKER_RUN.md) and the comments in
`docker-compose.production.yml` (`no-new-privileges`, `cap_drop: ALL`, health checks, non-root `appuser`).

## 10. Transferring Images to a Production Host

Save images with `docker save ... -o <file>.tar` (app: `mmorrisj/softpower-analytics:<version>`, DB: `mmorrisj/pgvector:0.8.2-pg17`), transfer, then load them on the target with `./scripts/docker/production-deploy.sh load [dir]`.
See README § Production Operations for the full command table.

## 11. File Reference

| File | Purpose |
|------|---------|
| **Dockerfiles** | |
| `docker/registry.Dockerfile` | Production image — self-contained with ML packages + HuggingFace model (~2GB) |
| `docker/api.Dockerfile` | Dev API service (multi-stage: Node build + Python FastAPI) |
| `docker/dashboard.Dockerfile` | Dev Streamlit dashboard service |
| `docker/pgvector.Dockerfile` | Custom PostgreSQL 17 + pgvector (compiled from source) |
| `docker/supervisord.conf` | Process manager config (runs FastAPI + Streamlit in consolidated images) |
| **Compose files** | |
| `docker-compose.yml` | Default dev/demo stack (zero prerequisites, Compose-managed volume + network) |
| `docker-compose.dev.yml` | Development stack (separate containers: API, Dashboard, DB, Redis; external volume/network) |
| `docker-compose.enterprise.yml` | Enterprise stack: app + Redis against a hosted PostgreSQL (host networking) |
| `docker-compose.laptop.yml` | Laptop pull-and-run stack (pinned registry images, no local build) |
| `docker-compose.laptop.embed.yml` | GPU embedding-runner overlay on the laptop stack (preprocessing image) |
| `docker-compose.laptop.gpu.yml` | GPU device-reservation overlay (nvidia) |
| `docker-compose.preprocessing.yml` | Pipeline-only preprocessing/batch worker stack |
| `docker-compose.production.yml` | Production stack (consolidated app image from Docker Hub, host networking) |
| `docker-compose.windows.yml` | Bridge-networking override of the production stack for Docker Desktop (Windows/macOS/WSL2) |
| **Requirements** | |
| `requirements-production.txt` | Lightweight Python deps baked into production Docker image |
| `requirements-production-heavy.txt` | Heavy ML deps installed from wheels on production target |
| **Scripts** | |
| `scripts/docker/push-to-registry.sh` | Build + push images to Docker Hub (registry or production mode) |
| `scripts/docker/production-deploy.sh` | Deployment management on production target system |
| `scripts/llm_proxy.py` | Lightweight LLM+S3 proxy (only needs fastapi+uvicorn+openai+boto3) |
| **Documentation** | |
| `docs/DOCKERHUB_README.md` | Docker Hub container registry README |
| `.dockerignore` | Build context exclusions |
| `.env` | Environment variables (DB creds, API keys, etc.) |

