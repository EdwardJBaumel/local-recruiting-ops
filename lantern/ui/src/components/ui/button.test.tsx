import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui/button";

// Smoke tests for the Button primitive — mounts cleanly, renders
// children, threads className/disabled, and the asChild slot pattern
// renders the child element instead of a <button>.

describe("Button", () => {
  it("mounts without throwing", () => {
    expect(() => render(<Button>Click me</Button>)).not.toThrow();
  });

  it("renders its children and is a <button> by default", () => {
    render(<Button>Run Pipeline</Button>);
    const btn = screen.getByRole("button", { name: "Run Pipeline" });
    expect(btn).toBeInTheDocument();
    expect(btn.tagName).toBe("BUTTON");
  });

  it("applies the base variant classes", () => {
    render(<Button>styled</Button>);
    expect(screen.getByRole("button")).toHaveClass("inline-flex");
  });

  it("merges a caller-supplied className", () => {
    render(<Button className="w-full">wide</Button>);
    expect(screen.getByRole("button")).toHaveClass("w-full");
  });

  it("passes through the disabled attribute", () => {
    render(<Button disabled>nope</Button>);
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("renders each variant / size combo without throwing", () => {
    for (const variant of [
      "default",
      "destructive",
      "outline",
      "secondary",
      "ghost",
      "link",
      "accent",
    ] as const) {
      for (const size of ["default", "sm", "lg", "icon"] as const) {
        expect(() =>
          render(
            <Button variant={variant} size={size}>
              {variant}-{size}
            </Button>,
          ),
        ).not.toThrow();
      }
    }
  });

  it("renders the child element directly when asChild is set", () => {
    render(
      <Button asChild>
        <a href="https://example.com">Apply</a>
      </Button>,
    );
    // asChild routes through Radix <Slot> — the rendered node is an
    // anchor, not a button, but it still carries the button classes.
    const link = screen.getByRole("link", { name: "Apply" });
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveClass("inline-flex");
  });
});
