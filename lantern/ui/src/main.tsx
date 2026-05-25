import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import App from "./App.tsx";
import { ErrorBoundary } from "@/components/ErrorBoundary";

// StrictMode is back as of the PinMap removal (Apr 2026). It was
// previously OFF because Leaflet stored its map instance on the DOM
// node — StrictMode's double-mount produced "Map container is
// already initialized" on every Settings → Location render. With
// no map mounted anywhere, double-mounting is harmless and the
// dev-time benefit (catches effect-cleanup bugs early) is back.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 1000 * 5,
    },
    mutations: { retry: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
