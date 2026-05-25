# lantern/ui — Vite + React + TypeScript frontend

The dashboard. Three tabs (Brief, Matches, Settings), dark-mode only, runs on **port 3000** in dev. Reads `/api/*` via Vite's dev proxy, which forwards to the Python backend on port 8099.

This is a pure view layer — no server-side state of its own. All persistent state lives behind `/api/*` on the backend; the UI only owns ephemeral session state (selected row, filter toggles, current tab).

## Stack

| Layer | Tool | Why |
|---|---|---|
| Build | Vite 8 | Fastest dev-server start, native ESM, no webpack config to maintain |
| UI | React 18 + TypeScript (strict) | Strict mode catches drift early; types flow from `types/*.ts` to every consumer |
| Styling | Tailwind 3 + shadcn/ui primitives | Hand-written primitives in `components/ui/`, no runtime CSS-in-JS |
| Server state | TanStack Query | Cache + refetch + optimistic mutation in one coherent model |
| UI state | Zustand (with `persist` middleware) | Tiny store, survives reload via localStorage |
| Form state | react-hook-form | Decouples form from server cache; `Controller` bridges shadcn primitives |
| Charts | Recharts (lazy-loaded) | Chunk-split so non-Brief tabs ship without the chart library |
| Location filter | Hand-rolled `MultiSelectLocations` (no library) | Chips + search + free-text fallback. Was Leaflet + a pin map; removed because users actually wanted to type city names, not draw polygons |
| Sanitisation | DOMPurify | JD descriptions ship as HTML; we render through DOMPurify before `dangerouslySetInnerHTML` |

## Layout

```
ui/
├── index.html
├── vite.config.ts        ← `/api` proxy to localhost:8099
├── tailwind.config.js
├── tsconfig.json
│
└── src/
    ├── main.tsx          ← React root + QueryClientProvider + ErrorBoundary
    ├── App.tsx           ← Tabs root that wraps Header (with TabsList) + main (with TabsContent)
    ├── index.css         ← Tailwind directives + a small set of custom CSS classes (.prose-jd etc.)
    │
    ├── api/
    │   └── client.ts     ← single typed fetch wrapper (api.get / api.post / api.postFile / api.delete)
    │
    ├── components/
    │   ├── Header.tsx          ← brand + Run Pipeline + nav tabs (lives inside the header bar)
    │   ├── MatchTable.tsx      ← TanStack Table over the matches array; resizable columns
    │   ├── MatchDetail.tsx     ← right-rail panel — score pills, JD, fit/gap, cover letter generator
    │   ├── GhostBadge.tsx      ← coloured Clear / Caution / Suspect pill
    │   ├── ScoreBar.tsx        ← thin progress bar for the Brief tab's recent matches
    │   ├── SourceHealth.tsx    ← per-source job count + error count grid
    │   ├── settings/           ← one section component per Settings card (Resume, Titles, Scoring, Freshness, Location, Companies, Danger)
    │   └── ui/                 ← shadcn primitives (button, card, badge, input, slider, tabs, separator, textarea, switch, label)
    │
    ├── views/
    │   ├── Brief.tsx     ← market overview: top companies bar chart + top archetypes + recent matches preview
    │   ├── Matches.tsx   ← the headline tab: filter row + MatchTable + conditional MatchDetail rail
    │   └── Settings.tsx  ← single-form orchestration of every config + resume save in one click
    │
    ├── hooks/
    │   ├── useStatus.ts        ← /api/status heartbeat (2s; 15s on failure with backoff)
    │   ├── useMatches.ts       ← cycle-gated polling
    │   ├── useMarket.ts        ← cycle-end triggered refetch
    │   ├── useConfig.ts        ← hydrate-once + saveConfig mutation
    │   ├── useResume.ts        ← upload, re-parse, save profile overrides
    │   ├── useCoverLetter.ts   ← POST /api/cover-letter mutation
    │   ├── useReact.ts         ← optimistic Star / Like / Pass / Apply mutations
    │   ├── useRunPipeline.ts   ← POST /api/run-cycle with explicit error surfacing
    │   ├── useReset.ts         ← danger-zone reset
    │   └── useTenants.ts       ← Greenhouse/Lever/Ashby health probe
    │
    ├── stores/
    │   └── ui.ts         ← Zustand store: currentTab, selectedJobUrl, matchFilters, matchSort
    │
    ├── lib/
    │   ├── utils.ts      ← `cn()` — tailwind-merge + clsx
    │   ├── geocode.ts    ← static city table + haversine + remote-region classifier (mirrored from backend)
    │   ├── companyTier.ts← classifies company → "mega" / "large" / "growth" for tier-aware freshness
    │   ├── jdTrim.ts     ← parses JD HTML, keeps responsibilities/requirements sections, caps to ~1800 chars
    │   └── rowKey.ts     ← stable identifier for a match row (falls back to company::title::location when url is null)
    │
    └── types/
        ├── match.ts      ← MatchPayload — the shape on the wire
        ├── status.ts     ← StatusResponse
        ├── market.ts     ← MarketCycleEntry (matches orchestrator._save_market_intel output)
        └── config.ts     ← AppConfig + ResumeState + ResumeProfile
```

## State discipline

Three layers, each owns one concern. The hard rule: **no layer reads or writes another's state directly**.

| Layer | Owns | Reads from |
|---|---|---|
| TanStack Query | All `/api/*` data — matches, market, config, resume, status | The network |
| Zustand store | UI session state — current tab, selected row, filter toggles | localStorage (via `persist`) |
| react-hook-form | Form state inside Settings | TanStack Query cache (one-time hydration via `reset()`) |

### Polling tier discipline

To avoid hammering the backend (or your own GPU), each TanStack Query is in one of three tiers:

1. **Always-on heartbeat** — `useStatus()` polls every 2 s at rest, 1 s mid-cycle, 15 s on failure (with retry capped at 1).
2. **Cycle-gated** — `useMatches()` and `useMarket()` only refetch on the cycle-end transition (watched via `useStatus().cycle_in_progress`).
3. **Hydrate-once** — `useConfig()` and `useResume()` use `staleTime: Infinity` and `refetchInterval: false`. Updated only via mutation invalidation.

Polling NEVER touches form state — `staleTime: Infinity` on the config query plus a `hasReset` guard in `Settings.tsx` ensures user edits are sticky until the user explicitly saves.

## Dev loop

```bash
# From this directory:
npm install
npm run dev     # → http://localhost:3000

# Or — easier — from the repo root, use the launcher:
.\start.ps1     # brings up backend + UI together
```

The dev server is set to `strictPort: true` on 3000 so a port conflict surfaces immediately rather than silently moving you to 3001.

## Build

```bash
npm run build   # tsc -b && vite build → dist/
npm run preview # serve the dist/ output for a final smoke test
```

## License

MIT — see [/LICENSE](../../LICENSE) at the repo root.
