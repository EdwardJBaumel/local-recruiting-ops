#!/usr/bin/env python3
"""
Lantern - Multi-Agent Job Matching Pipeline
Run: python main.py
Stop: Ctrl+C

Requires:
  - Ollama running locally (`ollama serve`) with the models from
    config.json's parse / match / analyze / digest / cover_letter
    sections pulled. See SETUP.md for the recommended set; the
    Settings -> Models picker in the dashboard surfaces what's
    actually installed and warns when a configured model is missing.
  - pip install -r requirements.txt
"""

import json
import os
import sys
import time
import webbrowser
import logging
from pathlib import Path

from core import app_paths
from orchestrator import Orchestrator


def setup_logging(config: dict):
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO"))
    log_file = log_config.get("file", "logs/lantern.log")

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def _fatal(msg: str, hint: str = ""):
    """Print a clear error + hint and pause if running from a double-click
    so the console doesn't vanish before the user reads it."""
    print()
    print("=" * 60)
    print("  " + msg)
    if hint:
        print()
        print("  " + hint)
    print("=" * 60)
    # Heuristic: if there's a console but no keyboard input, we're probably
    # a double-clicked exe. Give the user a chance to read the message.
    if getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close...")
        except Exception:
            pass
    sys.exit(1)


def _load_and_validate_config(path: Path) -> dict:
    """Read config.json and surface malformed-JSON errors cleanly before
    the orchestrator/server start wiring up state. We had a case where a
    half-written save from the Settings UI left config.json truncated
    mid-key; without this check the stack trace surfaced several stages
    in, inside agent constructors, and made diagnosis painful.

    On failure we try to preserve the broken file as config.json.broken
    so the user can inspect or restore it, and emit a hint pointing at
    the bundled default."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:
        _fatal(
            f"Could not read {path}: {e}",
            "Check file permissions and that the path exists.",
        )
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as e:
        # Keep the broken copy next to the live file for forensics.
        try:
            broken = path.with_suffix(path.suffix + ".broken")
            broken.write_text(raw, encoding="utf-8")
        except Exception:
            broken = None
        hint_lines = [
            f"Parse error at line {e.lineno} column {e.colno}: {e.msg}",
            "",
            "This usually means a save from the Settings UI was interrupted",
            "or the file was edited externally and left invalid.",
        ]
        if broken:
            hint_lines.append(f"Broken copy kept at {broken} for reference.")
        hint_lines.append(
            "Restore from defaults with:   copy defaults\\config.json config.json"
        )
        _fatal(f"config.json is not valid JSON.", "\n  ".join(hint_lines))
    if not isinstance(cfg, dict):
        _fatal(
            "config.json must be a JSON object at the top level.",
            f"Found {type(cfg).__name__}. Replace the file with the bundled default.",
        )
    return cfg


def check_ollama(model: str):
    """Verify Ollama is running and the model is available."""
    import requests
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        if not any(model in m for m in models):
            _fatal(
                f"Model '{model}' is not installed in Ollama.",
                f"Run this once and retry:   ollama pull {model}",
            )
        print(f"Ollama OK. Model '{model}' found.")
    except requests.ConnectionError:
        _fatal(
            "Cannot reach Ollama at http://localhost:11434.",
            "Start Ollama first: open a terminal and run 'ollama serve', then re-launch Lantern.",
        )
    except requests.Timeout:
        _fatal(
            "Ollama is not responding (timed out after 5s).",
            "Check whether 'ollama serve' is stuck and restart it.",
        )


def main():
    # Run everything relative to the writable runtime dir (beside the exe
    # when frozen, cwd in dev mode). This is where config.json, data/ and
    # logs/ live; the bundled read-only assets (frontend build) are
    # separately resolved from app_paths.static_dir().
    runtime = app_paths.runtime_dir()
    os.chdir(runtime)

    config_path = runtime / "config.json"
    if not config_path.exists():
        # First run of the packaged exe: drop a default config.json beside
        # the binary so the user can edit it instead of hunting for one.
        bundled_default = app_paths.bundle_dir() / "defaults" / "config.json"
        if bundled_default.exists():
            config_path.write_text(bundled_default.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"Created default config at {config_path}")
        else:
            print(f"ERROR: config.json not found at {config_path}.")
            sys.exit(1)

    config = _load_and_validate_config(config_path)
    # If the user picked an llm_profile, fill in per-stage model defaults
    # now so downstream agents see a fully-resolved config. Per-stage
    # values already set in config.json override the profile defaults
    # (see core.llm_profiles for resolution order).
    try:
        from core import llm_profiles
        config = llm_profiles.apply_profile(config)
    except Exception as e:
        # Never fail startup over profile resolution; fall back to
        # whatever the user put in config.json.
        logging.getLogger("lantern.startup").warning(
            "llm_profile resolution failed: %s", e
        )
    setup_logging(config)

    # Touch the user store on every launch so first_launch_at is recorded
    # the first time and last_launch_at rolls on every start. Also means
    # data/user.json exists from the start for the /api/setup-state GET.
    from core import user_store
    data_dir = Path(config.get("data_dir", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    user_store.touch_launch(data_dir)

    # UI-FIRST STARTUP
    # ──────────────────────────────────────────────────────────────
    # The old flow blocked the browser open on a synchronous Ollama probe
    # and a model-list call. That meant a 3-6 s stare at a terminal before
    # the dashboard appeared, and a fatal exit if Ollama wasn't running.
    # The new order: bind the API and open the UI immediately, then run
    # preflight + prewarm in background threads. The wizard/dashboard show
    # ticks/crosses as those finish. If Ollama is missing, the pipeline
    # stays gated behind the wizard's "ready_for_first_run" flag instead
    # of tearing the whole process down.
    import threading
    from server import start_server_thread, set_orchestrator

    api_port = config.get("api_port", 8099)
    start_server_thread(api_port)
    dashboard_url = f"http://127.0.0.1:{api_port}"
    print(f"Lantern dashboard: {dashboard_url}")

    # Open the browser ASAP. LANTERN_NO_BROWSER=1 lets the launcher
    # open :3000 (the Vite dev server) instead during development.
    # SENTINEL_NO_BROWSER is honored as a legacy alias (see app_paths.py).
    if (os.environ.get("LANTERN_NO_BROWSER") or os.environ.get("SENTINEL_NO_BROWSER")) != "1":
        def _open():
            # Tiny delay so the server socket has bound by the time the
            # browser issues its first GET. 400 ms is enough on any
            # machine that can run Ollama.
            time.sleep(0.4)
            try:
                webbrowser.open(dashboard_url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    # Preflight + prewarm happen in the background so they don't block
    # the UI. Results are exposed via /api/preflight and /api/prewarm.
    def _bg_preflight_prewarm():
        try:
            from core import preflight, prewarm
            # Pull the actual configured model for each task. Defaults
            # match the canonical picks documented in SETUP.md so the
            # log line below reflects what the cycle will really use.
            # Used to default to gemma4:e4b / qwen3:8b regardless of
            # config, which made the "models ready" log message lie
            # about which models would actually run the pipeline.
            # Fallbacks aligned with core/llm.DEFAULT_MODELS — three of
            # these used to mention models that are no longer defaults
            # (gemma3:12b for analyze/digest, qwen3:30b-a3b for cover).
            # On a healthy config.json these fallbacks never fire, but
            # they do surface on a wiped/fresh install, so keeping them
            # in sync with the actual defaults avoids "preflight pulled
            # the wrong model" surprises.
            configured_models = list({
                (config.get("parse", {}) or {}).get("model") or "qwen3:8b",
                (config.get("match", {}) or {}).get("model") or "qwen3:14b",
                (config.get("analyze", {}) or {}).get("model") or "qwen3:14b",
                (config.get("digest", {}) or {}).get("model") or "qwen3:14b",
                (config.get("cover_letter", {}) or {}).get("model") or "qwen3:14b",
            })
            configured_models = [m for m in configured_models if m]
            # Populate the preflight snapshot for /api/preflight. Results
            # cached in the preflight module so the UI's first GET is
            # cheap.
            preflight.run_all(configured_models)
            # Only prewarm models that exist - otherwise we burn time on
            # a dead request. Prewarm is idempotent.
            prewarm.run_background(configured_models)
        except Exception as e:
            logging.getLogger("lantern.startup").warning(
                "Background preflight/prewarm failed: %s", e
            )
    threading.Thread(target=_bg_preflight_prewarm, daemon=True).start()

    # A terse console summary, after the UI is already up. Non-blocking:
    # the user has the dashboard regardless.
    def _bg_console_summary():
        try:
            from core.llm import check_models
            status = check_models()
            if status.get("missing"):
                print(f"NOTE: Some models not found: {status['missing']}")
                print("Pipeline will fall back to available models.")
                print("To install: " + " ; ".join(f"ollama pull {m}" for m in status["missing"]))
            else:
                print(f"All models ready: {status.get('model_map')}")
        except Exception:
            print("Could not reach Ollama yet (it may still be starting).")
            print("Install from https://ollama.com and run: ollama serve")
    threading.Thread(target=_bg_console_summary, daemon=True).start()

    orchestrator = Orchestrator(config)
    set_orchestrator(orchestrator)

    # Headless flag lets the user just host the UI+API without running
    # scraping loops. They can still trigger cycles from the dashboard.
    if os.environ.get("SENTINEL_DASHBOARD_ONLY") == "1" or "--dashboard-only" in sys.argv:
        print("Dashboard-only mode. Close this window to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    else:
        orchestrator.run()


if __name__ == "__main__":
    main()
