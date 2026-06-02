#!/usr/bin/env bash
# Local Recruiting Ops dev launcher (macOS / Linux)
# Boots Local Recruiting Ops on one visible local URL by default: http://127.0.0.1:8099.
# Set LRO_DEV_UI=1 to also run the Vite dashboard on :3000 for UI work.
# Press Ctrl-C in this terminal to stop both.
#
# Usage:
#   ./start.sh
#
# First-run bootstrap is automatic:
#   - Creates a Python venv at ./venv if missing
#   - Installs lro/api/requirements.txt
#   - Seeds lro/api/config.json from lro/api/config.example.json
#   - Installs lro/ui/node_modules via `npm install`

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT/lro/api"
UI_DIR="$ROOT/lro/ui"
DEV_UI="${LRO_DEV_UI:-0}"

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
    echo "  -> Edit lro/api/config.json to customise companies / models / preferences."
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
echo "  Local Recruiting Ops launcher"
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
        ollama_just_started=1
    else
        echo "Ollama not found on PATH. Install from https://ollama.com/download." >&2
    fi
else
    echo "Ollama already running on :11434"
fi

# --- Pull core LLM if missing (parse + analyse) -------------------------
# Set LRO_SKIP_MODEL_PULL=1 to skip. Checks /api/tags on the running
# server — not raw files on disk. If you use a custom OLLAMA_MODELS path,
# export it before ./start.sh (or set it in your shell profile).
pull_models_if_needed() {
    if [[ "${LRO_SKIP_MODEL_PULL:-}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
        return 0
    fi
    if ! nc -z 127.0.0.1 11434 2>/dev/null; then
        return 0
    fi
    local wait_sec=0
    if [[ -n "${ollama_just_started:-}" ]]; then
        wait_sec=30
    fi
    local deadline=$(( $(date +%s) + wait_sec ))
    local tags=""
    while true; do
        tags="$(curl -sf http://127.0.0.1:11434/api/tags 2>/dev/null | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print('\n'.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || true)"
        if [[ -n "$tags" || $wait_sec -eq 0 || $(date +%s) -ge $deadline ]]; then
            break
        fi
        sleep 2
    done
    if echo "$tags" | grep -qx 'qwen3:8b' || echo "$tags" | grep -q 'qwen3:8b'; then
        return 0
    fi
    if [[ -n "$tags" ]]; then
        echo "Ollama tags visible: $(echo "$tags" | tr '\n' ', ' | sed 's/, $//')"
    elif [[ -n "${OLLAMA_MODELS:-}" ]]; then
        echo "Ollama models dir: $OLLAMA_MODELS"
    fi
    echo
    echo "Ollama is up but missing: qwen3:8b"
    echo "Pulling qwen3:8b (~5 GB, one-time). Set LRO_SKIP_MODEL_PULL=1 to skip."
    if command -v ollama >/dev/null 2>&1; then
        ollama pull qwen3:8b || echo "WARNING: ollama pull qwen3:8b failed."
    fi
    echo
}
pull_models_if_needed

# --- Cleanup on Ctrl-C -------------------------------------------------
backend_pid=""
frontend_pid=""
cleanup() {
    echo
    echo "Stopping Local Recruiting Ops..."
    for pid in "$backend_pid" "$frontend_pid" "$ollama_pid"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- Spawn backend (+ optional frontend dev server) --------------------
# LRO_NO_BROWSER stops the backend from opening its own tab. The
# launcher opens the canonical dashboard URL once ready.
# LRO_MANUAL_MODE makes the orchestrator wait for /api/run-cycle
# instead of auto-firing every interval.
export LRO_NO_BROWSER=1
export LRO_MANUAL_MODE="${LRO_MANUAL_MODE:-1}"

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
