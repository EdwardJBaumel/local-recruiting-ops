# SENTINEL Dashboard

Portfolio dashboard for the SENTINEL multi-agent job intelligence system.

## Local Development (with live pipeline data)

```bash
cd sentinel-ui
npm install
npm run dev
```

Make sure the SENTINEL pipeline is running (`python main.py` in the `sentinel/` directory) so the API server is available on port 8099. Vite proxies `/api` requests automatically.

Open http://localhost:3000

## Deploy to Vercel (public portfolio)

```bash
npm install -g vercel
vercel
```

When deployed publicly without the API server, the dashboard automatically shows demo data with a "DEMO" indicator. Live data appears when the pipeline is running locally.

## Deploy to Netlify

```bash
npm run build
# Upload the `dist/` folder to Netlify
```

## Stack

- React 18
- Recharts (charts)
- Vite (build)
- Instrument Serif + Outfit + IBM Plex Mono (typography)
