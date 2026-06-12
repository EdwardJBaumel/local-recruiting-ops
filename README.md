# Local Recruiting Ops

Scrapes public job feeds, scores roles against your resume, flags ghost jobs, all on your machine, no API costs.

**[Live site](https://edwardjbaumel.github.io/local-recruiting-ops/)** · [Setup](SETUP.md) · [Changelog](CHANGELOG.md) · [Troubleshooting](TROUBLESHOOTING.md) · MIT License

---

## What it does

Each cycle ingests public ATS feeds, scores survivors against your resume and caps the Matches tab at **80** rows:

1. **Ingests** Greenhouse, Lever, Ashby + Amazon, Workday, RemoteOK, Jobicy (Google optional, off by default)
2. **Scores** with `BAAI/bge-m3` embeddings + hard filters + ghost fold
3. **Flags ghosts** nine explainable signals, not a black-box score
4. **Analyses** the top **8** matches with a local LLM (one-sentence fit/gap)

![Brief tab — market metrics and last cycle funnel](docs/assets/lro-demo-brief.gif)

Results live in a four-tab dashboard: **Brief** (market overview), **Matches** (scored list), **History** (cycle timeline), **Settings** (résumé upload, models, run pipeline).

---

## Requirements

- Python 3.11+, Node 18+, [Ollama](https://ollama.com/download)
- GPU optional but strongly recommended. Match phase is ~1 min on GPU vs ~80 min on CPU

---

## Get started

**1. Clone**

```bash
git clone https://github.com/edwardjbaumel/local-recruiting-ops.git
cd local-recruiting-ops
```

**2. Pull models**

The embedding model (`bge-m3`) uses ~2.3 GB VRAM. LRO loads one LLM at a time per stage, so you do not need every model resident at once.

| VRAM | Pull | Disk |
|---|---|---|
| No GPU / 6–8 GB | `qwen3:4b` | ~2.5 GB — works, slow cycles |
| 8 GB dedicated | `qwen3:8b` | ~5 GB — minimum for parse + analyse |
| **16 GB (e.g. RTX 5070 Ti)** | `qwen3:8b` + `qwen3:14b` + `gemma4:e4b` | ~24 GB on disk — **recommended** |
| 24 GB+ | add `gemma4:26b` or `gemma4:31b` | 18–20 GB each — marginal gain on LRO tasks |

```bash
ollama pull qwen3:8b
ollama pull qwen3:14b
ollama pull gemma4:e4b
```

### Recommended per-task picks (benchmarked on RTX 5070 Ti)

Measured with `scripts/benchmark_models.py` on LRO's real parse / analyse / digest / cover prompts. Latency outliers from concurrent GPU use were stripped.

| Task | Model | Why |
|---|---|---|
| **Parse** (~60 calls/cycle) | `qwen3:8b` or `gemma4:e4b` | Both 100% JSON; qwen ~6.7 s, gemma ~1.4 s per card |
| **Analyse** (top 8) | `qwen3:14b` | 100% JSON, fewest tokens; qwen3:8b works but slower |
| **Digest** | `qwen3:14b` or `gemma4:e4b` | Best prose structure in bench |
| **Cover letter** | `deepseek-r1:8b` or `gemma4:e4b` | Highest prose quality score |

**Not recommended:** `deepseek-r1:8b` for parse (thinking trace breaks JSON), `gemma4:12b` for parse (0% JSON in bench).

**`gemma4:31b` on 16 GB:** runnable but impractical — loads ~14.5 GB VRAM and parse/analyse ran **3–4 min per call** vs **~7–10 s** for `qwen3:14b`. Reserve it for 24 GB+ cards or use `gemma4:26b` MoE (18 GB disk, ~4B active) instead.

Re-run benchmarks: `python scripts/benchmark_models.py`

![Settings tab — local résumé parse and pipeline run](docs/assets/lro-demo-settings.gif)


**3. Launch**

Windows: `.\start.ps1`  
macOS / Linux: `./start.sh`

The launcher handles everything: venv, deps, config, UI build, starting Ollama, opening the browser. **First run takes ~10–20 min.**

**4. Upload your resume and run**

Settings → Resume (upload CV) → click **Run Pipeline**.

Full walkthrough: [SETUP.md](SETUP.md)

---

## Stack

| | |
|---|---|
| Frontend | Vite · React 18 · TypeScript · Tailwind · shadcn/ui |
| Backend | Python · threaded orchestrator |
| LLMs | Ollama — see **Recommended per-task picks** above |
| Embeddings | sentence-transformers · `BAAI/bge-m3` |
| Tests | pytest (223) · vitest · GitHub Actions CI |

---

## Develop and test

```bash
cd lro/api && pip install -r requirements.txt -r requirements-dev.txt && pytest
cd ../ui && npm ci && npm test
```

Hot-reload UI: `$env:LRO_DEV_UI = "1"; .\start.ps1`

---

## License

MIT — see [LICENSE](LICENSE).  
Built by [Eddie Baumel](https://www.linkedin.com/in/edwardbaumel/).
