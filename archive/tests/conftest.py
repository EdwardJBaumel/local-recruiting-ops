"""Shared pytest setup. Makes both `sentinel.*` and bare `core.*` /
`agents.*` imports resolve, matching how the app itself boots (cwd is
sentinel/ when run via start.ps1 / start.sh)."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SENTINEL_DIR = REPO_ROOT / "sentinel"
for p in (REPO_ROOT, SENTINEL_DIR):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
