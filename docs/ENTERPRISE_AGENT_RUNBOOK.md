# Enterprise Runbook: Spin Up the SoftPower React App

Instructions for an agent (human or AI) deploying on the enterprise host.
Applies to the current release (**2.0.3** at time of writing) and any release
**≥ 1.8.31** — the first with the `/proxy_chat` chat-completions passthrough
in both proxy implementations (`server/main.py` and `scripts/llm_proxy.py`).
Tool-calling requires that passthrough; never adapt the agent to
`/proxy_query`, which is a plain text-completion endpoint that cannot
transport `tools` definitions or return structured `tool_calls`.

---

## 1. Prerequisites

1. **Repo checkout at the deployed release's commit/tag** on the enterprise
   host. The host proxy runs from this checkout, so an old checkout = old
   proxy = 404s, regardless of the container version. If a local workaround
   was ever applied on the enterprise checkout (payload reshaping, endpoint
   rewrites in `agent/llm/openai_compat.py` or elsewhere), **revert it before
   deploying** — check with:

   ```bash
   git status
   git diff          # any diff touching agent/, server/main.py, scripts/llm_proxy.py is suspect
   ```
2. **The release image** (e.g. `mmorrisj/softpower-analytics:2.0.3`)
   available to the host Docker daemon:
   - With registry access: `docker pull mmorrisj/softpower-analytics:<tag>`
   - Airgapped: on a connected machine
     `docker save mmorrisj/softpower-analytics:<tag> -o softpower-<tag>.tar`,
     transfer, then `docker load -i softpower-<tag>.tar`
3. **`.env` in the repo root** with at minimum: `DB_HOST`, `DB_PORT`,
   `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, plus the LLM settings
   in step 3.

---

## 2. Check `.env` for stale image pins (this bit us in the past)

`docker-compose.enterprise.yml` uses `${APP_IMAGE:-mmorrisj/softpower-analytics:2.0.3}`
(the default pins the current release). An `APP_IMAGE` (or `APP_VERSION`)
line in `.env` **silently overrides** the compose default and keeps you on an
old image:

```bash
grep -E '^APP_IMAGE|^APP_VERSION' .env
# Either delete those lines, or set:
# APP_IMAGE=mmorrisj/softpower-analytics:<the release you are deploying>
```

## 3. LLM environment settings (enterprise)

In the **host** `.env` (the proxy process reads it):

| Var | Value | Why |
|---|---|---|
| `ENV` | `production` | Enables the production routing contract: LiteLLM → Azure, **no public-OpenAI fallback** (a failure returns 502 with the backend errors — this is intentional; do not "fix" it by adding keys). |
| `LITELLM_URL` | enterprise LiteLLM endpoint | Primary backend. Authenticated per-request by the gateway JWT (`x-kiosk-gateway-jwt` header), which the agent forwards through the proxy automatically. |
| `LITELLM_MODEL` | approved model name | Used when the client doesn't send a model. A client-sent model **wins** over this (since 1.8.31) — pin `AGENT_LLM_MODEL` in the container env only if you want to force the agent's model explicitly. |
| `LLM_PROXY_PORT` | `7001` (default) | Must match what the container's `API_URL` points at (compose wires `API_URL=http://127.0.0.1:${LLM_PROXY_PORT:-7001}` — the app service uses `network_mode: host`). |

Things **not** to set:

- `AGENT_LLM_BASE_URL` — forces the agent to bypass the proxy entirely
  (direct mode). On an egress-restricted host this recreates the original
  "agent hits the LLM endpoint directly" connection errors.
- `GAI_DEFAULT_SOURCE=openai` — dev/laptop setting; on enterprise leave it
  unset (defaults to `proxy`).

## 4. Start the host proxy (from the updated checkout)

Whichever of the two you use, **restart it after updating the checkout** — a
running process keeps executing the old code:

```bash
# Option A — lightweight proxy (what production-deploy.sh references):
python scripts/llm_proxy.py

# Option B — full server as proxy:
uvicorn server.main:app --host 0.0.0.0 --port 7001
```

Verify it serves the agent endpoint **before** starting containers:

```bash
curl -s http://127.0.0.1:7001/openapi.json | grep -o '/proxy_chat'
# Must print: /proxy_chat        (if empty -> proxy is running old code)
```

## 5. Start the stack

```bash
docker compose -f docker-compose.enterprise.yml up -d
docker compose -f docker-compose.enterprise.yml ps   # confirm the image tag matches the release you are deploying and status healthy
```

Deploy targets do **not** auto-pull; if `ps` shows an old tag, re-pull the
image (section 1) and re-check `.env` pins (section 2).

## 6. Verification sequence (run in order)

```bash
# 1. App up, React UI served
curl -s http://127.0.0.1:8000/api/health
# expect: {"status":"healthy", ...}   — React app is at http://<host>:8000/

# 2. Agent routing — the critical check
curl -s http://127.0.0.1:8000/api/agent/health
# expect: "llm_source":"proxy" and "llm_target":"http://127.0.0.1:7001/proxy_chat"
# If llm_source is "direct" or "openai": AGENT_LLM_BASE_URL or GAI_DEFAULT_SOURCE
# is set wrong (see section 3).

# 3. Proxy accepts chat-completions payloads (messages array, NOT sys_prompt/prompt)
curl -s -X POST http://127.0.0.1:7001/proxy_chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"reply with the word ok"}]}'
# expect: JSON with "choices":[{"message":{...}}]
# A 502 here means both LiteLLM and Azure failed — the detail field names the
# real errors (JWT missing/expired, unreachable endpoint). Fix those; the
# proxy no longer masks them by falling back to public OpenAI.

# 4. End-to-end agent turn (tool-calling path)
curl -s -X POST http://127.0.0.1:8000/api/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What soft power activities has China conducted recently?"}'
# expect: structured JSON like {"action":"propose_run","scope":{...},...}
# NOT prose describing tool calls.
```

## 7. Failure modes → fixes

| Observation | Fix |
|---|---|
| Check 3 returns **404** | Host proxy is old code. Update checkout, restart proxy (section 4). |
| Check 3 returns **422 about `sys_prompt`/`prompt`** | The request hit `/proxy_query` — a local workaround is rewriting agent traffic. Revert local diffs (section 1, item 1). |
| Agent replies describe tool calls but nothing executes | Same root cause as above: traffic is going through `/proxy_query`. Confirm the container image is ≥ 1.8.31 and check 3 passes. |
| Check 3 returns **502 "all production LLM backends failed"** | Read the `detail` — it lists the LiteLLM and Azure errors verbatim. Usually gateway JWT absent/expired or `LITELLM_URL` unreachable from the host. |
| Agent calls time out | Client timeout is 240s by default (`AGENT_LLM_REQUEST_TIMEOUT` in the container env to override); if hit, the proxy's downstream (90s/backend) is the bottleneck — check LiteLLM latency. |
| Wrong model in use | Precedence (since 1.8.31): `AGENT_LLM_MODEL` (container) > `LITELLM_MODEL` (container) > proxy's `LITELLM_MODEL` > `gpt-4.1-mini`. `/api/agent/health` shows the configured model. |
