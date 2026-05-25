# Lantern — code

Lantern is two processes that talk over HTTP on `localhost`:

- [`api/`](api/) — Python backend on port **8099**. Runs the scrape → parse → score → persist pipeline. Owns all on-disk state.
- [`ui/`](ui/) — Vite + React + TypeScript frontend on port **3000**. Reads `/api/*` via Vite's dev proxy. Pure view layer — no server-side state of its own.

The two are split by **port**, not by **deploy**. They run on the same machine in the same dev session; the launcher (`start.ps1` at the repo root) brings them up together. Ctrl-C in the launcher window stops both cleanly.

## Why two processes instead of one

The split is intentional even though Lantern is a single-user tool:

- **The backend has heavy dependencies** (`sentence-transformers`, `requests`, BeautifulSoup, the Ollama Python client). The frontend has none of that. Cold-starting the UI shouldn't pay the import cost of the embedding model.
- **The backend can hot-reload independently** from the UI. Changing a scoring weight in `agents/match.py` doesn't require restarting the dashboard's tab state.
- **TypeScript on the wire** is the lingua franca. The UI is fully typed against the backend's response shapes (`ui/src/types/*.ts`), and the proxy keeps the browser on a single origin so there's no CORS wrangling.

If this ever needs to scale to multiple users, the backend becomes a real service (FastAPI in front of the same agents) and the UI becomes a static SPA pointing at it. The same files survive that move.

## Reading order

If you're new to the codebase:

1. Start with [`api/README.md`](api/README.md) — explains the agent pipeline, the model assignments, and where ghost detection lives.
2. Then [`ui/README.md`](ui/README.md) — explains the three-tab layout, where state lives, and the polling tier discipline.
3. The repo root `README.md` is the product-level pitch (what the tool does and why); the two READMEs above are the engineering view.

## License

MIT — see [/LICENSE](../LICENSE) at the repo root.
