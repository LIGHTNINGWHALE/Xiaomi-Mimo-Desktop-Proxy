#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/oneclick/proxy.pid"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" || true
    echo "Stopped proxy (pid $PID)."
  else
    echo "Proxy not running (stale pid file)."
  fi
  rm -f "$PID_FILE"
else
  # Fallback: only match this repo's proxy.py
  pkill -f "$ROOT/proxy/proxy.py" 2>/dev/null && echo "Stopped proxy via pkill." || echo "No running proxy found."
fi
