import { defineConfig, createLogger } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Vite proxy forwards `/api/*` to the Python backend on :8099 so the
// browser only has one origin (no CORS dance, no env-vars-per-host
// gymnastics). Backend stays at the same port the launcher uses.
//
// Boot-race note: the launcher starts Vite and the Python backend in
// parallel. Vite is ready in ~100ms; the backend needs a few seconds to
// import torch and load the embedding model onto the GPU. During that
// window every `/api/*` poll hits a dead port. That's expected startup
// noise, not a real failure — so we stay quiet until the grace window
// is over, then surface a genuinely unreachable backend loudly.
const BACKEND_BOOT_GRACE_MS = 20_000;
const devStartedAt = Date.now();
const withinBootGrace = () => Date.now() - devStartedAt < BACKEND_BOOT_GRACE_MS;

// Swallow Vite's built-in "http proxy error" spam during the boot grace
// window; pass everything else (and all post-boot errors) straight through.
const logger = createLogger();
const baseError = logger.error.bind(logger);
logger.error = (msg, opts) => {
  if (withinBootGrace() && msg.includes("http proxy error")) return;
  baseError(msg, opts);
};

export default defineConfig({
  customLogger: logger,
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8099",
        changeOrigin: true,
        // Don't crash the dev server when the backend is briefly down
        // (during the boot race above, or a mid-session restart). Our
        // hook fires once per failed proxied request; throttle it to a
        // single friendly line during boot, then surface real errors.
        configure: (proxy) => {
          let bootNoticeShown = false;
          proxy.on("error", (err) => {
            if (withinBootGrace()) {
              if (!bootNoticeShown) {
                // eslint-disable-next-line no-console
                console.log(
                  "[proxy] waiting for the backend to finish starting…",
                );
                bootNoticeShown = true;
              }
              return;
            }
            // eslint-disable-next-line no-console
            console.warn("[proxy] backend unreachable:", err.message);
          });
        },
      },
    },
  },
});
