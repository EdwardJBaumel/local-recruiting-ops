#!/usr/bin/env bash
# SENTINEL dev launcher (macOS / Linux)
# Starts the Python pipeline + API on :8099 and the Vite dashboard on :3000.
# Press Ctrl+C to stop both.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Resolve Python (prefer local venv) ------------------------------
PY=""
for candidate in "$ROOT/venv/bin/python" "$ROOT/.venv/bin/python"; do
  if [[ -x "$candidate" ]]; then PY="$candidate"; break; fi
done

# --- Auto-create venv if none exists so first-run users aren't stuck
# without sentence-transformers and friends. Only runs when the user
# hasn't already provisioned their own venv/.venv.
if [[ -z "$PY" ]]; then
  SYS_PY=""
  if   command -v python3 >/dev/null 2>&1; then SYS_PY="$(command -v python3)"
  elif command -v python  >/dev/null 2>&1; then SYS_PY="$(command -v python)"
  else
    echo "ERROR: Python not found. Install Python 3.11+ from https://python.org" >&2
    exit 1
  fi
  echo "Creating Python venv at $ROOT/venv (one-time)..."
  "$SYS_PY" -m venv "$ROOT/venv"
  PY="$ROOT/venv/bin/python"
fi

# --- Install Python requirements when missing or stale ---------------
# We key the marker on requirements.txt's mtime so edits force a refresh.
REQS="$ROOT/sentinel/requirements.txt"
if [[ -f "$REQS" ]]; then
  VENV_ROOT="$(dirname "$(dirname "$PY")")"
  MARKER="$VENV_ROOT/.deps-installed"
  NEED_INSTALL=1
  if [[ -f "$MARKER" && "$MARKER" -nt "$REQS" ]]; then NEED_INSTALL=0; fi
  if [[ "$NEED_INSTALL" -eq 1 ]]; then
    echo "Installing Python dependencies from requirements.txt (this may take a minute)..."
    "$PY" -m pip install --upgrade pip --quiet
    "$PY" -m pip install -r "$REQS"
    touch "$MARKER"
  fi
fi

# --- Resolve npm -----------------------------------------------------
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not found. Install Node.js 18+ from https://nodejs.org" >&2
  exit 1
fi

UI_DIR="$ROOT/sentinel-ui"
if [[ ! -f "$UI_DIR/package.json" ]]; then
  echo "ERROR: $UI_DIR/package.json not found." >&2
  exit 1
fi

# --- Install UI deps once if needed ----------------------------------
if [[ ! -d "$UI_DIR/node_modules" ]]; then
  echo "Installing dashboard dependencies (one-time)..."
  (cd "$UI_DIR" && npm install)
fi

echo ""
echo "  SENTINEL launcher"
echo "  - Python : $PY"
echo "  - UI dir : $UI_DIR"
echo "  - Backend: http://127.0.0.1:8099"
echo "  - Dash   : http://127.0.0.1:3000"
echo ""

# --- Start Ollama if installed but not running ----------------------
# The pipeline LLM calls go through Ollama on :11434. If the user has
# it installed but forgot to start it, we spawn `ollama serve` so a
# fresh launch gives a working LLM stack. Only spawn if :11434 isn't
# already bound, to avoid fighting an existing Ollama instance. PID
# is tracked so the trap cleans it up (only when WE started it).
OLLAMA_PID=""
if (echo >/dev/tcp/127.0.0.1/11434) >/dev/null 2>&1; then
  echo "Ollama already running on :11434"
elif command -v ollama >/dev/null 2>&1; then
  echo "Starting Ollama (ollama serve)..."
  ollama serve >/dev/null 2>&1 &
  OLLAMA_PID=$!
else
  echo "Ollama not found on PATH. Install from https://ollama.com/download - the pipeline will skip LLM steps until it's running."
fi

# --- Spawn both, kill both on exit -----------------------------------
# SENTINEL_NO_BROWSER=1 stops main.py opening :8099 (which serves the
# built dist/ - can be stale). We open :3000 ourselves once Vite binds.
# SENTINEL_MANUAL_MODE=1 keeps the pipeline idle until the UI triggers
# a cycle. Override externally if you want the scheduled interval loop:
#   SENTINEL_MANUAL_MODE=0 ./start.sh
MANUAL_MODE="${SENTINEL_MANUAL_MODE:-1}"
( cd "$ROOT/sentinel" && SENTINEL_NO_BROWSER=1 SENTINEL_MANUAL_MODE="$MANUAL_MODE" "$PY" main.py ) &
BACK_PID=$!

( cd "$UI_DIR" && npm run dev ) &
FRONT_PID=$!

# Open the Vite dev URL as soon as :3000 is bound. Uses OS-native openers.
# The #launch=<stamp> hash focuses the existing SPA tab (hash-only URL
# changes are same-document navigations) and fires 'hashchange' so the
# app can flash a 'welcome back' toast. Query params reload the page
# and browsers often open a duplicate tab instead - hash avoids that.
DASH_URL="http://127.0.0.1:3000/#launch=$(date +%s)"
(
  for _ in $(seq 1 40); do
    if (echo >/dev/tcp/127.0.0.1/3000) >/dev/null 2>&1; then
      if command -v xdg-open >/dev/null 2>&1; then xdg-open "$DASH_URL" >/dev/null 2>&1 || true
      elif command -v open     >/dev/null 2>&1; then open     "$DASH_URL" >/dev/null 2>&1 || true
      fi
      break
    fi
    sleep 0.5
  done
) &

cleanup() {
  echo ""
  echo "Stopping SENTINEL..."
  # OLLAMA_PID is only set when WE spawned ollama - an existing
  # external instance stays up.
  kill "$BACK_PID" "$FRONT_PID" ${OLLAMA_PID:-} 2>/dev/null || true
  wait "$BACK_PID" "$FRONT_PID" ${OLLAMA_PID:-} 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Wait for either child to exit, then trip cleanup.
wait -n "$BACK_PID" "$FRONT_PID"
