#!/usr/bin/env bash
# Lantern dev launcher (macOS / Linux)
# Boots Lantern on one visible local URL by default: http://127.0.0.1:8099.
# Set LANTERN_DEV_UI=1 to also run the Vite dashboard on :3000 for UI work.
# Press Ctrl-C in this terminal to stop both.
#
# Usage:
#   ./start.sh
#
# First-run bootstrap is automatic:
#   - Creates a Python venv at ./venv if missing
#   - Installs lantern/api/requirements.txt
#   - Seeds lantern/api/config.json from lantern/api/config.example.json
#   - Installs lantern/ui/node_modules via `npm install`

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT/lantern/api"
UI_DIR="$ROOT/lantern/ui"
DEV_UI="${LANTERN_DEV_UI:-0}"

# --- Sanity ------------------------------------------------------------
if [[ ! -d "$API_DIR" ]]; then
    echo "ERROR: $API_DIR not found. Did the repo clone finish?" >&2
    exit 1
fi
if [[ ! -f "$UI_DIR/package.json" ]]; then
    echo "ERROR: $UI_DIR/package.json not found." >&2
    exit 1
fi

# --- Resolve / bootstrap Python venv -----------------------------------
PYTHON=""
for candidate in "venv/bin/python" ".venv/bin/python"; do
    if [[ -x "$ROOT/$candidate" ]]; then
        PYTHON="$ROOT/$candidate"
        break
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "No Python venv found — bootstrapping one (first-run setup)..."
    SYS_PYTHON="$(command -v python3 || command -v python || true)"
    if [[ -z "$SYS_PYTHON" ]]; then
        echo "ERROR: No 'python3' on PATH. Install Python 3.11+ and re-run." >&2
        exit 1
    fi
    "$SYS_PYTHON" -m venv "$ROOT/venv"
    PYTHON="$ROOT/venv/bin/python"
    echo "Installing Python dependencies (this takes ~2 min the first time)..."
    "$PYTHON" -m pip install --upgrade pip --quiet
    "$PYTHON" -m pip install -r "$API_DIR/requirements.txt"
fi

# --- First-run config bootstrap ---------------------------------------
LIVE_CONFIG="$API_DIR/config.json"
EXAMPLE_CONFIG="$API_DIR/config.example.json"
if [[ ! -f "$LIVE_CONFIG" && -f "$EXAMPLE_CONFIG" ]]; then
    echo "No config.json found — seeding from config.example.json (first-run setup)..."
    cp "$EXAMPLE_CONFIG" "$LIVE_CONFIG"
    echo "  -> Edit lantern/api/config.json to customise companies / models / preferences."
    echo "  -> Or use the dashboard's Settings tab once it's up."
fi

# --- Resolve Node ------------------------------------------------------
NPM="$(command -v npm || true)"
if [[ -z "$NPM" ]]; then
    echo "ERROR: npm not found. Install Node.js 18+ from https://nodejs.org" >&2
    exit 1
fi

# --- Install UI deps once if needed ------------------------------------
if [[ ! -d "$UI_DIR/node_modules" ]]; then
    echo "Installing dashboard dependencies (one-time)..."
    (cd "$UI_DIR" && "$NPM" install)
fi
if [[ ! "$DEV_UI" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    echo "Building dashboard for single-port mode (:8099)..."
    (cd "$UI_DIR" && "$NPM" run build)
fi

echo
echo "  Lantern launcher"
echo "  - Python : $PYTHON"
echo "  - API dir: $API_DIR"
echo "  - UI dir : $UI_DIR"
echo "  - App    : http://127.0.0.1:8099"
if [[ "$DEV_UI" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    echo "  - Dev UI : http://127.0.0.1:3000"
fi
echo

# --- Start Ollama if not running ---------------------------------------
ollama_pid=""
if ! lsof -ti :11434 >/dev/null 2>&1 && ! nc -z 127.0.0.1 11434 2>/dev/null; then
    OLLAMA="$(command -v ollama || true)"
    if [[ -n "$OLLAMA" ]]; then
        echo "Starting Ollama (ollama serve)..."
        "$OLLAMA" serve >/dev/null 2>&1 &
        ollama_pid=$!
    else
        echo "Ollama not found on PATH. Install from https://ollama.com/download." >&2
    fi
else
    echo "Ollama already running on :11434"
fi

# --- Cleanup on Ctrl-C -------------------------------------------------
backend_pid=""
frontend_pid=""
cleanup() {
    echo
    echo "Stopping Lantern..."
    for pid in "$backend_pid" "$frontend_pid" "$ollama_pid"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- Spawn backend (+ optional frontend dev server) --------------------
# LANTERN_NO_BROWSER stops the backend from opening its own tab. The
# launcher opens the canonical dashboard URL once ready.
# LANTERN_MANUAL_MODE makes the orchestrator wait for /api/run-cycle
# instead of auto-firing every interval.
export LANTERN_NO_BROWSER=1
export LANTERN_MANUAL_MODE="${LANTERN_MANUAL_MODE:-1}"

(cd "$API_DIR" && "$PYTHON" main.py) &
backend_pid=$!

if [[ "$DEV_UI" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    (cd "$UI_DIR" && "$NPM" run dev) &
    frontend_pid=$!
fi

# --- Open the dashboard once the app is listening ----------------------
if [[ "$DEV_UI" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    DASH_PORT=3000
else
    DASH_PORT=8099
fi
DASH_URL="http://127.0.0.1:${DASH_PORT}/#launch=$(date +%s)"
opened=0
while true; do
    if ! kill -0 "$backend_pid" 2>/dev/null; then
        echo "Backend exited." >&2
        break
    fi
    if [[ -n "$frontend_pid" ]] && ! kill -0 "$frontend_pid" 2>/dev/null; then
        echo "Frontend exited." >&2
        break
    fi
    if [[ $opened -eq 0 ]] && nc -z 127.0.0.1 "$DASH_PORT" 2>/dev/null; then
        echo "Opening dashboard at $DASH_URL"
        if command -v open >/dev/null; then
            open "$DASH_URL" >/dev/null 2>&1 || true
        elif command -v xdg-open >/dev/null; then
            xdg-open "$DASH_URL" >/dev/null 2>&1 || true
        else
            echo "Visit $DASH_URL manually."
        fi
        opened=1
    fi
    sleep 1
done
