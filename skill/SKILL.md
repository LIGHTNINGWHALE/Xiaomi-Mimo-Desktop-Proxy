---
name: xiaomi-mimo-desktop-proxy
description: Start and use a local OpenAI-compatible reverse proxy that exposes Xiaomi MiMo Desktop models (mimo-x-pro-preview) to Codex, Claude Code, CC Switch, and other clients on this machine. Use when the user wants to call MiMo models from another tool, wire CC Switch, or debug the local proxy.
---

# Xiaomi MiMo Desktop Proxy

Local reverse proxy for models already available in a logged-in **Xiaomi MiMo Desktop** app. It turns that desktop session into an OpenAI-compatible API on loopback.

## When to use

- User wants `mimo-x-pro-preview` (or flash/pro) in Codex / Claude Code / CC Switch.
- User asks to “本地反代 MiMo” / “把 mimo-x-pro-preview 接到 CC Switch”.
- User needs the local Base URL, API key, or health check.

## Preconditions

1. Xiaomi MiMo Desktop is installed and **logged in** on this machine.
2. Python 3.10+ available (`python3`).
3. Network access to `mimo-server-cn.xiaomimimo.com`.

If Desktop is not logged in, the proxy cannot authenticate. Ask the user to open MiMo Desktop and sign in first.

## Start the proxy (one-click)

From the repository root:

```bash
chmod +x oneclick/start.sh oneclick/stop.sh
./oneclick/start.sh
```

The script prints:

- Base URL: `http://127.0.0.1:18080/v1`
- API key (also at `proxy/.local-api-key`)
- Default model: `mimo-x-pro-preview`

Stop with `./oneclick/stop.sh`.

### Manual start

```bash
cd proxy
python3 -m pip install -r requirements.txt   # or: uv pip install -r requirements.txt
python3 proxy.py
```

## Endpoints

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/health` | none | Liveness + model list |
| GET | `/v1/models` | Bearer | OpenAI model list |
| POST | `/v1/chat/completions` | Bearer | Chat Completions (stream + non-stream) |
| POST | `/v1/responses` | Bearer | OpenAI Responses API shim (stream + non-stream) |

Auth header: `Authorization: Bearer <local-key>`.

## Wire a client

### CC Switch

1. Run `./oneclick/start.sh` and copy the printed key.
2. Add an OpenAI-compatible provider:
   - Base URL: `http://127.0.0.1:18080/v1`
   - API key: the local key
   - Model: `mimo-x-pro-preview`
3. Prefer **Chat Completions** if the UI asks for upstream format.
4. Template: `oneclick/cc-switch.example.json`.

### Codex

```bash
export OPENAI_BASE_URL="http://127.0.0.1:18080/v1"
export OPENAI_API_KEY="$(cat proxy/.local-api-key)"
```

Select model `mimo-x-pro-preview`. If the client only speaks Responses API, `/v1/responses` is available.

### Claude Code / OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:18080/v1",
    api_key=open("proxy/.local-api-key").read().strip(),
)
print(client.chat.completions.create(
    model="mimo-x-pro-preview",
    messages=[{"role": "user", "content": "你好"}],
))
```

## Smoke test

```bash
KEY=$(cat proxy/.local-api-key)
curl -sS http://127.0.0.1:18080/health
curl -sS http://127.0.0.1:18080/v1/models -H "Authorization: Bearer $KEY"
curl -sS http://127.0.0.1:18080/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"mimo-x-pro-preview","messages":[{"role":"user","content":"hi"}]}'
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `No Xiaomi desktop cookies found` | Desktop not logged in / non-standard install | Open MiMo Desktop and sign in; set `MIMO_PROXY_COOKIE_DB` if cookies live elsewhere |
| Upstream 401 | Desktop session expired | Re-login in Desktop; proxy re-warms automatically on next request |
| Connection refused | Proxy not running | `./oneclick/start.sh` |
| Client 404 on `/v1/responses` | Old proxy without Responses route | Update to this repo’s `proxy/proxy.py` and restart |
| Empty / 400 from upstream | Invalid message body from client | Ensure messages have non-empty text content |

## Privacy

- Cookies are read **locally** from the Desktop cookie DB; they are not uploaded.
- The local API key is generated on first run (`proxy/.local-api-key`) and is gitignored.
- Never commit cookies, keys, or a filled `cc-switch.json`.
- Bind address defaults to `127.0.0.1` only.

## Config (optional env)

| Variable | Default | Purpose |
|----------|---------|---------|
| `MIMO_PROXY_HOST` | `127.0.0.1` | Listen host |
| `MIMO_PROXY_PORT` | `18080` | Listen port |
| `MIMO_PROXY_COOKIE_DB` | `~/Library/Application Support/Xiaomi MiMo/Partitions/xiaomi-account/Cookies` | Cookie SQLite path |
| `MIMO_PROXY_UPSTREAM` | `https://mimo-server-cn.xiaomimimo.com/api` | Upstream base |
| `MIMO_PROXY_DEFAULT_MODEL` | `mimo-x-pro-preview` | Fallback model |
| `MIMO_PROXY_KEY_FILE` | `proxy/.local-api-key` | Where the local key is stored |

## Limits

- Depends on the local Xiaomi MiMo Desktop login; not a hosted API.
- Thinking is always on (`reasoning_content`); effort levels are not officially supported upstream.
- Unofficial / community project; upstream protocol may change.
