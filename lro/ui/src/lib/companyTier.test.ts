import { describe, it, expect } from "vitest";
import {
  classifyCompany,
  DEFAULT_FRESHNESS_WINDOWS,
} from "@/lib/companyTier";

describe("classifyCompany", () => {
  it("classifies mega-tier big tech as mega", () => {
    expect(classifyCompany("Amazon")).toBe("mega");
    expect(classifyCompany("Google")).toBe("mega");
    expect(classifyCompany("NVIDIA")).toBe("mega");
    expect(classifyCompany("Microsoft")).toBe("mega");
  });

  it("classifies decacorns / public mid-caps as large", () => {
    expect(classifyCompany("Stripe")).toBe("large");
    expect(classifyCompany("Databricks")).toBe("large");
    expect(classifyCompany("OpenAI")).toBe("large");
    expect(classifyCompany("Figma")).toBe("large");
  });

  it("falls through to growth for anything unlisted", () => {
    expect(classifyCompany("Some Seed Startup")).toBe("growth");
    expect(classifyCompany("Acme Robotics Inc")).toBe("growth");
    expect(classifyCompany("Local Recruiting Ops")).toBe("growth");
  });

  it("is case-insensitive and tolerates whitespace drift", () => {
    expect(classifyCompany("amazon")).toBe("mega");
    expect(classifyCompany("  STRIPE  ")).toBe("large");
    expect(classifyCompany("OpenAI")).toBe("large");
  });

  it("tolerates the spacing variants the feeds produce", () => {
    // The LARGE set carries both "scale ai" and "scaleai" for exactly
    // this reason.
    expect(classifyCompany("Scale AI")).toBe("large");
    expect(classifyCompany("scaleai")).toBe("large");
  });

  it("returns growth for null / undefined / empty", () => {
    expect(classifyCompany(null)).toBe("growth");
    expect(classifyCompany(undefined)).toBe("growth");
    expect(classifyCompany("")).toBe("growth");
    expect(classifyCompany("   ")).toBe("growth");
  });
});

describe("DEFAULT_FRESHNESS_WINDOWS", () => {
  it("exposes the research-backed per-tier day windows", () => {
    expect(DEFAULT_FRESHNESS_WINDOWS).toEqual({
      mega: 30,
      large: 14,
      growth: 7,
    });
  });

  it("orders the windows mega > large > growth", () => {
    expect(DEFAULT_FRESHNESS_WINDOWS.mega).toBeGreaterThan(
      DEFAULT_FRESHNESS_WINDOWS.large,
    );
    expect(DEFAULT_FRESHNESS_WINDOWS.large).toBeGreaterThan(
      DEFAULT_FRESHNESS_WINDOWS.growth,
    );
  });
});
