---
title: Lantern
description: Local-first job intelligence — multi-agent pipeline, zero API cost
---

# Lantern

Local-first job intelligence: scrape public ATS feeds, score roles against your resume with embeddings, flag ghost jobs — all on your machine.

**[GitHub](https://github.com/edwardjbaumel/lantern)** · **[README](https://github.com/edwardjbaumel/lantern/blob/master/README.md)** · **[Setup](https://github.com/edwardjbaumel/lantern/blob/master/SETUP.md)** · **[Changelog](https://github.com/edwardjbaumel/lantern/blob/master/CHANGELOG.md)**

---

## Quick start

Requires Python 3.11+, Node 18+, Ollama and at least one model pull.

```powershell
git clone https://github.com/edwardjbaumel/lantern.git
cd lantern
ollama pull qwen3:8b
.\start.ps1
```

Opens [http://127.0.0.1:8099](http://127.0.0.1:8099) — upload a resume in **Settings**, then **Run Pipeline**.

See the [README](https://github.com/edwardjbaumel/lantern/blob/master/README.md) for hardware-specific model guidance.

---

MIT License · [Eddie Baumel](https://www.linkedin.com/in/edwardbaumel/)
