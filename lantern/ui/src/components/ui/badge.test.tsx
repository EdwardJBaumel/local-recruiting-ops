import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "@/components/ui/badge";

// Smoke tests for the Badge primitive — mounts cleanly, renders its
// children, and threads through className + native div attributes.

describe("Badge", () => {
  it("mounts without throwing", () => {
    expect(() => render(<Badge>hello</Badge>)).not.toThrow();
  });

  it("renders its children text", () => {
    render(<Badge>FALLBACK</Badge>);
    expect(screen.getByText("FALLBACK")).toBeInTheDocument();
  });

  it("applies the base variant classes", () => {
    render(<Badge>tag</Badge>);
    // cva base includes the rounded-full pill shape.
    expect(screen.getByText("tag")).toHaveClass("rounded-full");
  });

  it("merges a caller-supplied className", () => {
    render(<Badge className="custom-cls">x</Badge>);
    expect(screen.getByText("x")).toHaveClass("custom-cls");
  });

  it("renders each semantic variant without throwing", () => {
    for (const variant of [
      "default",
      "secondary",
      "destructive",
      "outline",
      "clear",
      "aging",
      "suspect",
    ] as const) {
      expect(() =>
        render(<Badge variant={variant}>{variant}</Badge>),
      ).not.toThrow();
    }
  });

  it("forwards native div attributes like title", () => {
    render(<Badge title="tooltip text">hover me</Badge>);
    expect(screen.getByText("hover me")).toHaveAttribute(
      "title",
      "tooltip text",
    );
  });
});
