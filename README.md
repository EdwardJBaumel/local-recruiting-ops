# Lantern

Scrapes public job feeds, scores roles against your resume, flags ghost jobs — all on your machine, no API costs.

[Setup](SETUP.md) · [Troubleshooting](TROUBLESHOOTING.md) · MIT License

---

## What it does

Each cycle (~900–1,000 postings):

1. **Ingests** Greenhouse, Lever, Ashby tenants + Amazon, Google, Workday, RemoteOK, Jobicy
2. **Scores** every posting against your resume with `BAAI/bge-m3` embeddings
3. **Flags ghosts** — nine explainable signals, not a black-box score
4. **Analyses** the top matches with a local LLM (fit/gap rationale, cover letter)

Results live in a four-tab dashboard: **Brief** (market overview), **Matches** (scored list), **History** (cycle timeline), **Settings**.

---

## Requirements

- Python 3.11+, Node 18+, [Ollama](https://ollama.com/download)
- ~5 GB disk for `qwen3:8b` (minimum) · ~14 GB for `qwen3:8b` + `qwen3:14b` (recommended)
- NVIDIA GPU optional but strongly recommended — match phase is ~1 min on GPU vs ~80 min on CPU

---

## Get started

```bash
git clone https://github.com/edwardjbaumel/lantern.git
cd lantern
ollama pull qwen3:8b          # minimum; add qwen3:14b for better analysis
```

**Windows:** `.\start.ps1`  
**macOS / Linux:** `./start.sh`

The launcher creates the venv, installs deps, builds the UI and opens the browser. First run takes ~10–20 min.

Then: **Settings → Resume** (upload CV) → **Run Pipeline**.

Full walkthrough: [SETUP.md](SETUP.md)

---

## Stack

| | |
|---|---|
| Frontend | Vite · React 18 · TypeScript · Tailwind · shadcn/ui |
| Backend | Python · threaded orchestrator |
| LLMs | Ollama (`qwen3:8b`, `qwen3:14b`) |
| Embeddings | sentence-transformers · `BAAI/bge-m3` |
| Tests | pytest (190) · vitest (66) · GitHub Actions CI |

---

## Develop and test

```bash
cd lantern/api && pip install -r requirements.txt -r requirements-dev.txt && pytest
cd ../ui && npm ci && npm test
```

Hot-reload UI: `$env:LANTERN_DEV_UI = "1"; .\start.ps1`

---

## License

MIT — see [LICENSE](LICENSE).  
Built by [Eddie Baumel](https://www.linkedin.com/in/edwardbaumel/).
