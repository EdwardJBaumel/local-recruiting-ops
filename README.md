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
- GPU optional but strongly recommended — match phase is ~1 min on GPU vs ~80 min on CPU

---

## Get started

**1. Clone**

```bash
git clone https://github.com/edwardjbaumel/lantern.git
cd lantern
```

**2. Pull a model**

The embedding model (`bge-m3`) uses 2.3 GB VRAM on its own, so pick based on what's left:

| VRAM | Model | Disk |
|---|---|---|
| 6 GB or 8 GB unified (Mac M1/M2 base) | `qwen3:4b` | ~2.5 GB |
| 8 GB dedicated or 16 GB+ unified | `qwen3:8b` | ~5 GB |
| 16 GB+ | `qwen3:8b` + `qwen3:14b` | ~14 GB — best quality |
| No GPU | `qwen3:4b` | ~2.5 GB — works, slower cycles |

```bash
ollama pull qwen3:4b    # swap for whichever row fits your machine
```

**3. Launch**

Windows: `.\start.ps1`  
macOS / Linux: `./start.sh`

The launcher handles everything — venv, deps, config, UI build, starting Ollama, opening the browser. **First run takes ~10–20 min.**

**4. Upload your resume and run**

Settings → Resume (upload CV) → click **Run Pipeline**.

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
