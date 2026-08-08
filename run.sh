#!/usr/bin/env bash
# Resume Tailor — one-command launcher for the web app.
#
#   ./run.sh            # start server + open browser
#   ./run.sh --port 9000
#   ./run.sh --no-browser
#
set -euo pipefail
cd "$(dirname "$0")"

PORT="${RESUME_TAILOR_PORT:-8177}"
OPEN_BROWSER=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-browser) OPEN_BROWSER=0 ;;
    --port=*) PORT="${1#--port=}" ;;
    --port) PORT="$2"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "No .venv found — creating it and installing the project (web extras)..."
  python3 -m venv .venv
  "$PYTHON" -m pip install -q --upgrade pip
  "$PYTHON" -m pip install -q -e ".[web]"
fi

# Do not clobber the sample corpus: only collect live when the corpus is empty.
if curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/"; then
  echo "▸ A server is already running on http://127.0.0.1:${PORT}/ — opening it."
  command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || true
  command -v open >/dev/null && open "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || true
  exit 0
fi

LOG="${TMPDIR:-/tmp}/resume_tailor_web.log"
echo "▸ Starting Resume Tailor on http://127.0.0.1:${PORT} (Ctrl+C to stop) — log: ${LOG}"
RESUME_TAILOR_PORT="$PORT" "$PYTHON" -m resume_tailor.web > "$LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

# Wait for the server to answer, then open the browser.
for _ in $(seq 1 30); do
  if curl -sf -o /dev/null "http://127.0.0.1:${PORT}/"; then
    echo "▸ Ready → http://127.0.0.1:${PORT}/"
    if [ "$OPEN_BROWSER" = 1 ]; then
      command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || true
      command -v open >/dev/null && open "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || true
    fi
    break
  fi
  sleep 1
done
wait "$SERVER_PID"
