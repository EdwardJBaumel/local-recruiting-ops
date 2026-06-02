import { defineConfig } from "vitest/config";
import path from "node:path";

// Standalone test config — deliberately NOT extending vite.config.ts.
// That file installs a custom logger and an /api dev-proxy whose
// `configure` hook touches process-level state; none of it is wanted
// (or safe) inside the test runner. We only need two things from it:
// the jsdom DOM environment and the `@/` → ./src path alias, which we
// mirror here verbatim.
export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    // Registers @testing-library/jest-dom matchers (toBeInTheDocument,
    // toHaveTextContent, …) and runs cleanup() after each test.
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
