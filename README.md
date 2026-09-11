# Xiaomi MiMo Desktop Proxy

Local reverse proxy that turns your **logged-in Xiaomi MiMo Desktop** session into an OpenAI-compatible API. Use it to call models such as `mimo-x-pro-preview` from **Codex**, **Claude Code**, **CC Switch**, or any OpenAI SDK — without giving those tools a Xiaomi account cookie.

> Unofficial community project. Not affiliated with or endorsed by Xiaomi. The upstream protocol can change without notice.

## Purpose

Xiaomi MiMo Desktop already has access to models like:（11.09.2026）

| Model ID | Display name |
|----------|----------------|
| `mimo-x-pro-preview` | MiMo X Pro Preview |
| `mimo-x-flash-preview` | MiMo X Flash Preview |
| `mimo-pro` | MiMo Pro |
| `mimo-flash` | MiMo Flash |

Those models are only convenient inside the Desktop app. This project exposes them on **loopback** (`127.0.0.1`) as:

- `POST /v1/chat/completions` — OpenAI Chat Completions (stream + non-stream)
- `POST /v1/responses` — OpenAI Responses API shim (stream + non-stream)
- `GET /v1/models` / `GET /health`

so you can point CC Switch / Codex / Claude Code at a normal `base_url` + local API key.

```
┌─────────────────┐     127.0.0.1:18080/v1      ┌──────────────────────┐
│ Codex / CC Switch│ ──────────────────────────► │ this proxy           │
│ Claude Code      │   Bearer <local-key>        │  (OpenAI-compatible) │
└─────────────────┘                              └──────────┬───────────┘
                                                            │ Desktop cookies
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │ Xiaomi MiMo Desktop  │
                                                 │ session / upstream   │
                                                 └──────────────────────┘
```

## Two ways to use this repo

| Version | Path | Best for |
|---------|------|----------|
| **One-click + CC Switch** | [`oneclick/`](oneclick/) | Start/stop scripts and a provider template you paste into CC Switch |
| **Skill** | [`skill/SKILL.md`](skill/SKILL.md) | Portable skill doc for agents/IDEs that load a `SKILL.md` |

Both share the same core: [`proxy/proxy.py`](proxy/proxy.py).

## Requirements

- macOS with **Xiaomi MiMo Desktop installed and logged in**
- Python 3.10+
- Network access to Xiaomi’s MiMo API host

## Quick start (one-click)

```bash
git clone https://github.com/LIGHTNINGWHALE/Xiaomi-Mimo-Desktop-Proxy.git
cd Xiaomi-Mimo-Desktop-Proxy
chmod +x oneclick/start.sh oneclick/stop.sh
./oneclick/start.sh
```

Example output:

```text
  Base URL : http://127.0.0.1:18080/v1
  API Key  : mimo-local-xxxxxxxx
  Model    : mimo-x-pro-preview
```

Stop:

```bash
./oneclick/stop.sh
```

## Wire into CC Switch

1. Run `./oneclick/start.sh` and copy the printed **API Key**.
2. In CC Switch, add an **OpenAI-compatible** provider:
   - **Base URL**: `http://127.0.0.1:18080/v1`
   - **API Key**: the local key from `proxy/.local-api-key`
   - **Model**: `mimo-x-pro-preview`
3. If the UI asks for an **upstream format**, choose **Chat Completions** (Responses is also available).
4. Optional template: [`oneclick/cc-switch.example.json`](oneclick/cc-switch.example.json) — replace `YOUR_LOCAL_KEY` locally; **do not commit a filled copy**.

## Codex

```bash
export OPENAI_BASE_URL="http://127.0.0.1:18080/v1"
export OPENAI_API_KEY="$(cat proxy/.local-api-key)"
```

Pick model `mimo-x-pro-preview`. Clients that only speak the Responses API should call `POST /v1/responses`.

## Claude Code / OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:18080/v1",
    api_key=open("proxy/.local-api-key").read().strip(),
)

resp = client.chat.completions.create(
    model="mimo-x-pro-preview",
    messages=[{"role": "user", "content": "用一句话介绍你自己"}],
)
print(resp.choices[0].message.content)
```

## Skill version

Copy or symlink [`skill/SKILL.md`](skill/SKILL.md) into whatever skill directory your agent loads. It documents start/stop, endpoints, CC Switch wiring, and troubleshooting.

## Smoke test

```bash
KEY=$(cat proxy/.local-api-key)
curl -sS http://127.0.0.1:18080/health
curl -sS http://127.0.0.1:18080/v1/models -H "Authorization: Bearer $KEY"
curl -sS http://127.0.0.1:18080/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"mimo-x-pro-preview","messages":[{"role":"user","content":"hi"}],"stream":false}'
```

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `MIMO_PROXY_HOST` | `127.0.0.1` | Listen host (keep loopback) |
| `MIMO_PROXY_PORT` | `18080` | Listen port |
| `MIMO_PROXY_COOKIE_DB` | `~/Library/Application Support/Xiaomi MiMo/Partitions/xiaomi-account/Cookies` | Desktop cookie DB |
| `MIMO_PROXY_UPSTREAM` | `https://mimo-server-cn.xiaomimimo.com/api` | Upstream API base |
| `MIMO_PROXY_DEFAULT_MODEL` | `mimo-x-pro-preview` | Fallback model id |
| `MIMO_PROXY_KEY_FILE` | `proxy/.local-api-key` | Local API key path |

## Privacy & security

- The proxy reads **local** Xiaomi MiMo Desktop cookies on your machine to talk to Xiaomi’s API. Cookies are **not** uploaded to this repo or any third party.
- A random local API key is generated on first run (`proxy/.local-api-key`) and is **gitignored**.
- Never commit cookies, keys, or a filled CC Switch config.
- Default bind address is **127.0.0.1 only**. Do not expose the port to the public internet.
- Anyone who can reach the port **and** read the local key can use your Desktop quota.

## Limits & caveats

- Requires a valid Xiaomi MiMo Desktop login on the same machine.
- Unofficial reverse engineering of the Desktop session path; Xiaomi may change or block it.
- `mimo-x-pro-preview` always returns `reasoning_content` (thinking). Thinking “effort” levels are **not** officially supported by the upstream.
- Not a multi-tenant hosted gateway.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No Xiaomi desktop cookies found` | Open MiMo Desktop and log in; or set `MIMO_PROXY_COOKIE_DB` |
| Upstream 401 | Re-login in Desktop; next request re-warms the session |
| Connection refused | `./oneclick/start.sh` and check `oneclick/proxy.log` |
| Client 404 `/v1/responses` | Use this repo’s proxy (Responses shim included) |
| Upstream 400 body errors | Client sent empty/unsupported message content |

## Repository layout

```text
proxy/proxy.py                 # OpenAI-compatible local proxy (shared core)
proxy/requirements.txt
oneclick/start.sh              # one-click start + prints key/URL for CC Switch
oneclick/stop.sh
oneclick/cc-switch.example.json
skill/SKILL.md                 # portable skill version
docs/compose/spec/             # internal compose spec
```

## License

See [LICENSE](LICENSE).
