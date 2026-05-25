import { describe, it, expect } from "vitest";
import { cn } from "@/lib/utils";

describe("cn", () => {
  it("joins plain class strings", () => {
    expect(cn("flex", "items-center")).toBe("flex items-center");
  });

  it("drops falsy values (false / null / undefined / empty)", () => {
    expect(cn("flex", false, null, undefined, "", "p-2")).toBe("flex p-2");
  });

  it("resolves conflicting tailwind utilities — later class wins", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
    expect(cn("text-sm", "text-lg")).toBe("text-lg");
    expect(cn("bg-red-500", "bg-blue-500")).toBe("bg-blue-500");
  });

  it("keeps non-conflicting utilities from both inputs", () => {
    expect(cn("p-4", "text-lg")).toBe("p-4 text-lg");
  });

  it("supports conditional object syntax (clsx passthrough)", () => {
    expect(cn("base", { active: true, hidden: false })).toBe("base active");
  });

  it("flattens arrays of class values", () => {
    expect(cn(["flex", "gap-2"], "p-1")).toBe("flex gap-2 p-1");
  });

  it("returns an empty string when given nothing usable", () => {
    expect(cn()).toBe("");
    expect(cn(false, null, undefined)).toBe("");
  });

  it("lets a later conditional class override an earlier base class", () => {
    // Common shadcn pattern: base styles + a prop-driven override.
    expect(cn("rounded-md p-2", { "p-6": true })).toBe("rounded-md p-6");
  });
});
