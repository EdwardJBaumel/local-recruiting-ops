import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { GhostBadge } from "@/components/GhostBadge";

// Smoke tests — assert the component mounts without throwing and that
// the score-to-tier classification renders the expected label + number.
// Thresholds: clear < 0.30, caution 0.30–0.44, suspect >= 0.45.

describe("GhostBadge", () => {
  it("mounts without throwing", () => {
    expect(() => render(<GhostBadge score={0.1} />)).not.toThrow();
  });

  it("renders the Clear tier for a low score", () => {
    render(<GhostBadge score={0.12} />);
    // Label + rounded 0-100 score in one text node.
    expect(screen.getByText(/Clear · 12/)).toBeInTheDocument();
  });

  it("renders the Caution tier for a mid score", () => {
    render(<GhostBadge score={0.35} />);
    expect(screen.getByText(/Caution · 35/)).toBeInTheDocument();
  });

  it("renders the Suspect tier for a high score", () => {
    render(<GhostBadge score={0.72} />);
    expect(screen.getByText(/Suspect · 72/)).toBeInTheDocument();
  });

  it("respects custom warnAt / flagAt thresholds", () => {
    // With flagAt lowered to 0.20, a 0.25 score should read Suspect.
    render(<GhostBadge score={0.25} warnAt={0.1} flagAt={0.2} />);
    expect(screen.getByText(/Suspect · 25/)).toBeInTheDocument();
  });

  it("rounds the displayed score to a whole number", () => {
    render(<GhostBadge score={0.666} />);
    expect(screen.getByText(/Suspect · 67/)).toBeInTheDocument();
  });
});
