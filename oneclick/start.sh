#!/usr/bin/env bash
# One-click start for Xiaomi MiMo Desktop local reverse proxy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXY_DIR="$ROOT/proxy"
LOG_FILE="${MIMO_PROXY_LOG:-$ROOT/oneclick/proxy.log}"
PID_FILE="$ROOT/oneclick/proxy.pid"
HOST="${MIMO_PROXY_HOST:-127.0.0.1}"
PORT="${MIMO_PROXY_PORT:-18080}"
KEY_FILE="$PROXY_DIR/.local-api-key"

mkdir -p "$(dirname "$LOG_FILE")"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Proxy already running (pid $(cat "$PID_FILE"))."
else
  # Prefer uv if present; fall back to python3 + pip --user.
  cd "$PROXY_DIR"
  if command -v uv >/dev/null 2>&1; then
    if [[ ! -d .venv ]]; then
      uv venv >/dev/null
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    uv pip install -q -r requirements.txt
  else
    python3 -m pip install --user -q -r requirements.txt
  fi

  nohup python3 proxy.py >"$LOG_FILE" 2>&1 </dev/null &
  echo $! >"$PID_FILE"
  echo "Started proxy pid $(cat "$PID_FILE") (log: $LOG_FILE)"
fi

# Wait for health
for _ in $(seq 1 30); do
  if curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.3
done

if ! curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "ERROR: proxy did not become healthy. See $LOG_FILE" >&2
  tail -n 40 "$LOG_FILE" >&2 || true
  exit 1
fi

KEY=""
if [[ -f "$KEY_FILE" ]]; then
  KEY="$(tr -d '[:space:]' <"$KEY_FILE")"
fi

cat <<EOF

Xiaomi MiMo Desktop Proxy is ready.

  Base URL : http://${HOST}:${PORT}/v1
  API Key  : ${KEY}
  Model    : mimo-x-pro-preview
  Health   : http://${HOST}:${PORT}/health

CC Switch / Codex / Claude Code:
  - Provider base URL: http://${HOST}:${PORT}/v1
  - API key:           ${KEY}
  - Model:             mimo-x-pro-preview
  - Prefer Chat Completions if the client asks for an upstream format.

Stop with: oneclick/stop.sh
EOF
