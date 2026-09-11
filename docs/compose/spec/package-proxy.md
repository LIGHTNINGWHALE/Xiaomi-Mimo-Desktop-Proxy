---
feature: package-proxy
status: delivered
updated: 2026-09-11
branch: main
commits: a086c3b..HEAD
---

# Package Xiaomi MiMo Desktop Proxy

## Report

**What was built** — A public, privacy-safe package at
`https://github.com/LIGHTNINGWHALE/Xiaomi-Mimo-Desktop-Proxy.git` that turns a
logged-in Xiaomi MiMo Desktop session into a loopback OpenAI-compatible API.
Shared core `proxy/proxy.py` serves `/health`, `/v1/models`,
`/v1/chat/completions`, and `/v1/responses` on `127.0.0.1:18080`, generates a
local API key, and warms the upstream session from Desktop cookies resolved via
`Path.home()`. Two delivery shapes ship: a portable `skill/SKILL.md` and a
one-click `oneclick/start.sh` + `cc-switch.example.json` flow for CC Switch /
Codex / Claude Code.

**Verification** —
- `python3 -m py_compile proxy/proxy.py` PASS
- `./oneclick/start.sh` → `/health` 200 with model list PASS
- `/v1/models`, non-stream `/v1/chat/completions`, non-stream `/v1/responses` PASS
- Privacy grep of staged tree (usernames, real keys, cookie material, `/Users/`) clean
- Review subagent: Spec compliance PASS, Correctness PASS, no critical findings

**Journey log** —
1. Hardcoded personal cookie path was the main privacy leak; replaced with
   `Path.home()` + `MIMO_PROXY_COOKIE_DB`.
2. Accidentally staged `oneclick/proxy.pid`; unstaged and gitignored before commit.
3. `stop.sh` fallback `pkill -f "python3 proxy.py"` was too broad; narrowed to
   this repo’s `proxy/proxy.py` path.
4. Upstream is Chat Completions; Responses is a local shim — CC Switch should
   prefer Chat Completions when asked.

## [S1] Problem

Users who are logged into Xiaomi MiMo Desktop want to reuse models such as
`mimo-x-pro-preview` from Codex, Claude Code, or CC Switch. The working reverse
proxy previously lived only in a local scratch checkout, hardcoded personal
paths, and was not packaged for reuse.

## [S2] Design

Ship a public, privacy-safe product with two delivery shapes that share one
proxy core.

### Shared proxy core

- `proxy/proxy.py` — FastAPI app that:
  - Reads Xiaomi MiMo Desktop cookies from the standard Application Support
    path, resolved at runtime via `Path.home()` (never a hardcoded username).
  - Warms `GET {UPSTREAM}/user/xiaomi/me` before chat traffic.
  - Exposes OpenAI-compatible routes on loopback only:
    - `GET /health`
    - `GET /v1/models`
    - `POST /v1/chat/completions` (stream + non-stream)
    - `POST /v1/responses` (Responses API shim, stream + non-stream)
  - Generates a local API key on first run into `.local-api-key` (gitignored).
  - Maps Responses `reasoning.effort` → chat `reasoning_effort`.
  - Retries once after re-warming on upstream 401/403.
- Default listen: `127.0.0.1:18080`.
- Default model: `mimo-x-pro-preview`.
- Configurable via env: `MIMO_PROXY_HOST`, `MIMO_PROXY_PORT`,
  `MIMO_PROXY_UPSTREAM`, `MIMO_PROXY_COOKIE_DB`, `MIMO_PROXY_DEFAULT_MODEL`.

### Skill version

- `skill/SKILL.md` — portable skill document (not bound to a specific host
  path). Covers start/stop, endpoints, key location, and CC Switch / Codex /
  Claude Code wiring.

### One-click + CC Switch version

- `oneclick/start.sh` — installs deps if needed, starts `proxy/proxy.py`
  detached, waits for `/health`, prints Base URL / key / model.
- `oneclick/stop.sh` — stops the detached process (pid file; narrow fallback).
- `oneclick/cc-switch.example.json` — CC Switch provider template with
  placeholder key and `mimo-x-pro-preview`. No real credentials.

### README

- Purpose, install/usage for both versions, privacy notes, CC Switch steps.

### Privacy / non-goals

- Never commit cookies, keys, personal paths, or account ids.
- Do not auto-rewrite the user's CC Switch config on disk.
- Unofficial project; document Desktop-session dependency.

## [S3] Out of Scope

- Windows service installers, Docker images, public hosting of the proxy.
- Auto-updating cookies from a remote machine.
- Anthropic Messages native upstream (Chat Completions + Responses shim only).
- Guaranteed thinking-level control on the Xiaomi upstream.

## Tasks

- [x] T1: Scaffold repo layout, `.gitignore`, sanitized `proxy/proxy.py` + requirements — acceptance: `python -m py_compile proxy/proxy.py` passes; no personal paths/keys in tree (covers: S2)
- [x] T2: Add `oneclick/start.sh`, `stop.sh`, `cc-switch.example.json` — acceptance: `start.sh` is executable, starts proxy, `/health` returns ok; example JSON has placeholders only (covers: S2; depends: T1)
- [x] T3: Add portable `skill/SKILL.md` — acceptance: documents start/stop, endpoints, key location, CC Switch / Codex / Claude Code wiring (covers: S2; depends: T1)
- [x] T4: Write root `README.md` with purpose, both versions, privacy notes — acceptance: README states purpose, install, usage, CC Switch steps, privacy; no secrets (covers: S2)
- [x] T5: Local verify (compile, health, models, chat/responses smoke) — acceptance: health 200; models lists mimo-x-pro-preview; chat/responses return 200 when Desktop cookies present (covers: S2)
- [x] T6: Review privacy + correctness, then commit and push `main` — acceptance: no secrets in `git ls-files`/diff; push succeeds (covers: S2)
