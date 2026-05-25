#!/usr/bin/env bash
# Build SENTINEL as a single-file binary (Linux or macOS).
#
# Requires: python3.10+, node 18+, npm, pip. Run from AI_recruiter/.
# Output: dist/sentinel
#
# First time: ./build.sh
# Skip dependency reinstall: ./build.sh --no-install

set -euo pipefail

cd "$(dirname "$0")"

NO_INSTALL=0
for arg in "$@"; do
    case "$arg" in
        --no-install) NO_INSTALL=1 ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# Pick the venv python if the dev launcher already set one up.
if [[ -x "venv/bin/python" ]]; then
    PYTHON="venv/bin/python"
elif [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="$(command -v python3 || command -v python)"
fi
echo "Using python: $PYTHON"

if [[ $NO_INSTALL -eq 0 ]]; then
    echo
    echo "[1/4] Installing Python deps..."
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install -r sentinel/requirements.txt
    "$PYTHON" -m pip install pyinstaller
fi

echo
echo "[2/4] Building React UI..."
(
    cd sentinel-ui
    if [[ $NO_INSTALL -eq 0 || ! -d node_modules ]]; then
        npm install
    fi
    npm run build
)

echo
echo "[3/4] Packaging with PyInstaller..."
rm -rf build dist
"$PYTHON" -m PyInstaller sentinel.spec --clean --noconfirm

echo
echo "[4/4] Done."
if [[ -f dist/sentinel ]]; then
    ls -lh dist/sentinel
    echo
    echo "Next: start Ollama (ollama serve), then run ./dist/sentinel"
else
    echo "ERROR: build produced no binary. Check PyInstaller output above." >&2
    exit 1
fi
