import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount any React trees rendered during a test so the jsdom document
// is clean for the next one. With globals enabled Vitest + RTL usually
// auto-cleanup, but wiring it explicitly keeps the suite robust if that
// behaviour changes.
afterEach(() => {
  cleanup();
});
