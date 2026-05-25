#!/usr/bin/env python3
"""
SENTINEL SETUP WIZARD
Cross-platform guided setup. Detects hardware, recommends models,
installs dependencies, and configures the pipeline.

Run: python setup.py
"""

import json
import os
import sys
import shutil
import subprocess
import platform
from pathlib import Path


# ─── COLORS ───────────────────────────────────────────────────────
class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    END = "\033[0m"

    @staticmethod
    def ok(msg): print(f"  {C.GREEN}[OK]{C.END} {msg}")
    @staticmethod
    def warn(msg): print(f"  {C.YELLOW}[!!]{C.END} {msg}")
    @staticmethod
    def err(msg): print(f"  {C.RED}[ERR]{C.END} {msg}")
    @staticmethod
    def info(msg): print(f"  {C.CYAN}[..]{C.END} {msg}")
    @staticmethod
    def head(msg): print(f"\n{C.BOLD}{msg}{C.END}")


# ─── HARDWARE DETECTION ──────────────────────────────────────────
def detect_gpu():
    """Detect GPU and estimate VRAM."""
    gpu_info = {"name": None, "vram_gb": 0, "cuda": False, "mps": False}

    # Try nvidia-smi
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            gpu_info["name"] = parts[0].strip()
            gpu_info["vram_gb"] = round(int(parts[1].strip()) / 1024)
            gpu_info["cuda"] = True
            return gpu_info
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check for Apple Silicon
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            total_ram = int(result.stdout.strip()) // (1024 ** 3)
            gpu_info["name"] = f"Apple Silicon (unified {total_ram}GB)"
            gpu_info["vram_gb"] = total_ram  # Unified memory
            gpu_info["mps"] = True
            return gpu_info
        except Exception:
            pass

    return gpu_info


def detect_ram():
    """Detect system RAM in GB."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            return int(result.stdout.strip()) // (1024 ** 3)
        elif platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        return int(line.split()[1]) // (1024 ** 2)
        elif platform.system() == "Windows":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            c_ulong = ctypes.c_ulong
            class MEMORYSTATUS(ctypes.Structure):
                _fields_ = [
                    ('dwLength', c_ulong), ('dwMemoryLoad', c_ulong),
                    ('dwTotalPhys', ctypes.c_ulonglong), ('dwAvailPhys', ctypes.c_ulonglong),
                    ('dwTotalPageFile', ctypes.c_ulonglong), ('dwAvailPageFile', ctypes.c_ulonglong),
                    ('dwTotalVirtual', ctypes.c_ulonglong), ('dwAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
                ]
            ms = MEMORYSTATUS()
            ms.dwLength = ctypes.sizeof(MEMORYSTATUS)
            kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return ms.dwTotalPhys // (1024 ** 3)
    except Exception:
        pass
    return 0


def recommend_models(vram_gb: int, ram_gb: int):
    """Recommend model configuration based on hardware."""
    configs = {
        "high": {
            "label": "Full Power",
            "description": "Best quality. Uses all three model tiers for optimal results.",
            "vram_needed": 16,
            "models": {
                "parse": "gemma4:e4b",
                "match": "gemma4:26b",
                "analyze": "qwen3:8b",
                "digest": "gemma4:e4b",
            },
            "pull": ["gemma4:e4b", "gemma4:26b", "qwen3:8b"],
            "download_gb": 18,
        },
        "medium": {
            "label": "Balanced",
            "description": "Good quality with lower resource usage. Single mid-size model.",
            "vram_needed": 8,
            "models": {
                "parse": "gemma4:e4b",
                "match": "qwen3:8b",
                "analyze": "qwen3:8b",
                "digest": "gemma4:e4b",
            },
            "pull": ["gemma4:e4b", "qwen3:8b"],
            "download_gb": 8,
        },
        "low": {
            "label": "Lightweight",
            "description": "Runs on most hardware. Single small model for everything.",
            "vram_needed": 4,
            "models": {
                "parse": "gemma4:e4b",
                "match": "gemma4:e4b",
                "analyze": "gemma4:e4b",
                "digest": "gemma4:e4b",
            },
            "pull": ["gemma4:e4b"],
            "download_gb": 3,
        },
    }

    effective_vram = max(vram_gb, ram_gb // 2)  # CPU inference uses RAM

    if effective_vram >= 16:
        recommended = "high"
    elif effective_vram >= 8:
        recommended = "medium"
    else:
        recommended = "low"

    return configs, recommended


# ─── OLLAMA CHECK ─────────────────────────────────────────────────
def check_ollama():
    """Check if Ollama is installed and running."""
    # Check installed
    ollama_path = shutil.which("ollama")
    if not ollama_path:
        return False, "not_installed"

    # Check running
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            return True, models
    except Exception:
        return True, "not_running"

    return True, "not_running"


# ─── MAIN WIZARD ──────────────────────────────────────────────────
def main():
    print()
    print(f"  {C.BOLD}{'=' * 52}{C.END}")
    print(f"  {C.BOLD}  SENTINEL Setup Wizard{C.END}")
    print(f"  {C.DIM}  Multi-Agent Job Intelligence System{C.END}")
    print(f"  {C.BOLD}{'=' * 52}{C.END}")
    print()

    # ── Step 1: System Detection ──
    C.head("Step 1/5: Detecting Your System")

    os_name = platform.system()
    os_version = platform.version()
    arch = platform.machine()
    C.ok(f"OS: {os_name} {os_version} ({arch})")

    ram_gb = detect_ram()
    if ram_gb:
        C.ok(f"RAM: {ram_gb} GB")
    else:
        C.warn("Could not detect RAM. Assuming 8 GB.")
        ram_gb = 8

    gpu = detect_gpu()
    if gpu["name"]:
        C.ok(f"GPU: {gpu['name']} ({gpu['vram_gb']} GB)")
    else:
        C.warn("No GPU detected. Pipeline will use CPU inference (slower but works).")
        gpu["vram_gb"] = 0

    # ── Step 2: Model Recommendation ──
    C.head("Step 2/5: Model Recommendation")

    configs, recommended = recommend_models(gpu["vram_gb"], ram_gb)
    rec = configs[recommended]

    print(f"\n  Based on your hardware, we recommend: {C.GREEN}{C.BOLD}{rec['label']}{C.END}")
    print(f"  {C.DIM}{rec['description']}{C.END}")
    print()

    for key, cfg in configs.items():
        marker = " >> " if key == recommended else "    "
        color = C.GREEN if key == recommended else C.DIM
        print(f"  {color}{marker}[{key[0].upper()}] {cfg['label']}: {cfg['description']}")
        print(f"       Models: {', '.join(cfg['pull'])} (~{cfg['download_gb']} GB download){C.END}")
    print()

    choice = input(f"  Choose configuration [{recommended[0].upper()}]: ").strip().lower()
    if choice == "h" or choice == "high":
        selected = "high"
    elif choice == "m" or choice == "medium":
        selected = "medium"
    elif choice == "l" or choice == "low":
        selected = "low"
    else:
        selected = recommended

    sel = configs[selected]

    if selected != recommended:
        print(f"\n  {C.YELLOW}NOTE: You selected '{sel['label']}' instead of the recommended")
        print(f"  '{rec['label']}'. This may affect performance or quality.{C.END}")
        confirm = input(f"  Continue anyway? [Y/n]: ").strip().lower()
        if confirm == "n":
            selected = recommended
            sel = configs[selected]

    C.ok(f"Selected: {sel['label']}")

    # ── Step 3: Check Prerequisites ──
    C.head("Step 3/5: Checking Prerequisites")

    # Python packages
    C.info("Checking Python packages...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q",
             "requests", "beautifulsoup4"],
            capture_output=True, check=True,
        )
        C.ok("Core packages installed (requests, beautifulsoup4)")
    except subprocess.CalledProcessError:
        C.err("Failed to install Python packages. Run: pip install requests beautifulsoup4")

    # Optional: sentence-transformers
    try:
        import sentence_transformers
        C.ok("sentence-transformers available (embedding-based matching enabled)")
    except ImportError:
        C.info("sentence-transformers not installed. Using LLM-based matching.")
        install_st = input("  Install sentence-transformers for faster matching? [y/N]: ").strip().lower()
        if install_st == "y":
            C.info("Installing (this may take a minute)...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "sentence-transformers"], capture_output=True)

    # weasyprint for PDF generation
    try:
        import weasyprint
        C.ok("weasyprint available (PDF resume generation enabled)")
    except ImportError:
        C.info("weasyprint not installed. Needed for auto-resume PDF generation.")
        install_wp = input("  Install weasyprint for PDF resume generation? [Y/n]: ").strip().lower()
        if install_wp != "n":
            C.info("Installing weasyprint...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "weasyprint"], capture_output=True)

    # Ollama
    C.info("Checking Ollama...")
    ollama_ok, ollama_status = check_ollama()
    if not ollama_ok:
        C.err("Ollama is not installed.")
        print(f"\n  {C.BOLD}Install Ollama:{C.END}")
        if os_name == "Windows":
            print("  Download from: https://ollama.com/download/windows")
        elif os_name == "Darwin":
            print("  Download from: https://ollama.com/download/mac")
            print("  Or: brew install ollama")
        else:
            print("  curl -fsSL https://ollama.com/install.sh | sh")
        print(f"\n  Then run this setup again.")
        sys.exit(1)
    elif ollama_status == "not_running":
        C.warn("Ollama is installed but not running.")
        print(f"  Start it with: {C.BOLD}ollama serve{C.END}")
        print(f"  Then run this setup again.")
        sys.exit(1)
    else:
        C.ok(f"Ollama running. Available models: {', '.join(ollama_status) if ollama_status else 'none'}")

    # ── Step 4: Pull Models ──
    C.head("Step 4/5: Downloading Models")

    available = ollama_status if isinstance(ollama_status, list) else []
    to_pull = []
    for model in sel["pull"]:
        if any(model in m for m in available):
            C.ok(f"{model} already available")
        else:
            to_pull.append(model)

    if to_pull:
        print(f"\n  Need to download: {', '.join(to_pull)} (~{sel['download_gb']} GB)")
        go = input("  Download now? [Y/n]: ").strip().lower()
        if go != "n":
            for model in to_pull:
                C.info(f"Pulling {model}... (this may take several minutes)")
                result = subprocess.run(["ollama", "pull", model])
                if result.returncode == 0:
                    C.ok(f"{model} downloaded")
                else:
                    C.err(f"Failed to pull {model}. Try manually: ollama pull {model}")
        else:
            C.warn("Skipping model download. Pull them manually before running the pipeline.")
    else:
        C.ok("All models already available.")

    # ── Step 5: Configure ──
    C.head("Step 5/5: Configuration")

    config_path = Path("config.json")
    if config_path.exists():
        C.info("Existing config.json found.")
        overwrite = input("  Overwrite with new configuration? [y/N]: ").strip().lower()
        if overwrite != "y":
            C.ok("Keeping existing config.json")
            print(f"\n  {C.BOLD}{C.GREEN}Setup complete!{C.END}")
            print(f"  Run the pipeline: {C.BOLD}python main.py{C.END}")
            return

    # Collect user info
    print(f"\n  {C.DIM}Let's personalize SENTINEL for your job search.{C.END}")
    print()

    name = input("  Your name: ").strip() or "User"
    email = input("  Email (for digest delivery, optional): ").strip()
    discord = input("  Discord webhook URL (optional): ").strip()

    print(f"\n  {C.DIM}What roles are you looking for? (comma-separated){C.END}")
    print(f"  {C.DIM}Examples: product manager, software engineer, data scientist{C.END}")
    roles_input = input("  Roles: ").strip()
    roles = [r.strip() for r in roles_input.split(",") if r.strip()] if roles_input else ["product manager"]

    print(f"\n  {C.DIM}Briefly describe your background (1-2 sentences).{C.END}")
    print(f"  {C.DIM}This helps the matching engine score jobs against your profile.{C.END}")
    profile = input("  Profile: ").strip() or f"Experienced professional seeking {', '.join(roles)} roles."

    # Build config
    config = {
        "cycle_interval_minutes": 60,
        "max_cycles": 1,
        "digest_every_n_cycles": 1,
        "discord_webhook": discord,
        "email": {
            "smtp_user": email,
            "smtp_pass": "YOUR_GMAIL_APP_PASSWORD",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "to": email,
        } if email else {},
        "ingest": {
            "delay_range": [1, 3],
            "role_keywords": roles,
            "greenhouse_companies": [
                "stripe", "airbnb", "figma", "databricks", "coinbase",
                "cloudflare", "discord", "gitlab", "instacart", "lyft",
                "twitch", "airtable", "gusto", "brex", "robinhood",
                "duolingo", "pinterest", "coreweave",
            ],
            "lever_companies": ["netflix", "spotify"],
            "ashby_companies": [["OpenAI", "openai"], ["Ramp", "ramp"], ["Vercel", "vercel"]],
            "enable_apple": True,
            "enable_amazon": True,
            "enable_google": True,
            "enable_meta": True,
            "enable_microsoft": True,
        },
        "parse": {"model": sel["models"]["parse"]},
        "match": {
            "model": sel["models"]["match"],
            "threshold": 0.55,
            "profile_text": profile,
        },
        "resume": {
            "name": name,
            "email": email,
            "profile": profile,
        },
        "qa": {"enable_fake_job_detection": True},
        "logging": {"level": "INFO", "file": "logs/sentinel.log"},
    }

    config_path.write_text(json.dumps(config, indent=2))
    C.ok("config.json created")

    # Create data directories
    for d in ["data/matches", "data/parsed", "data/fit_gaps", "data/resumes", "data/digests", "logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    # ── Done ──
    print(f"\n  {'=' * 52}")
    print(f"  {C.BOLD}{C.GREEN}SENTINEL is ready!{C.END}")
    print(f"  {'=' * 52}")
    print(f"\n  Configuration: {sel['label']} ({', '.join(sel['pull'])})")
    print(f"  Target roles:  {', '.join(roles)}")
    print(f"  Sources:       40+ company career APIs")
    print()
    print(f"  {C.BOLD}To start the pipeline:{C.END}")
    print(f"    python main.py")
    print()
    print(f"  {C.BOLD}To start the dashboard:{C.END}")
    # `;` works in PowerShell, cmd.exe (with cmd /c), and POSIX shells.
    print(f"    cd ../sentinel-ui ; npm install ; npm run dev")
    print(f"  {C.DIM}    (or just run start.ps1 / start.sh from the project root){C.END}")
    print()
    print(f"  {C.DIM}Results will be saved to data/matches/")
    print(f"  Resumes will be generated in data/resumes/")
    print(f"  Logs are in logs/sentinel.log{C.END}")
    print()


if __name__ == "__main__":
    main()
