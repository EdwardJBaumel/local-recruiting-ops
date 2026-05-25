import { describe, it, expect } from "vitest";
import {
  isRemoteLocation,
  isCountryOnlyLocation,
  getRemoteRegion,
} from "@/lib/geocode";

// NOTE: this version of geocode.ts no longer ships the CITY_COORDS
// table / haversineKm() / locateJob() — the pin-on-a-map filter was
// removed (see the file header). The public surface is the three text
// classifiers below, so that's what we test.

describe("isRemoteLocation", () => {
  it("detects the remote tokens", () => {
    expect(isRemoteLocation("Remote")).toBe(true);
    expect(isRemoteLocation("Remote - US")).toBe(true);
    expect(isRemoteLocation("Anywhere")).toBe(true);
    expect(isRemoteLocation("Worldwide")).toBe(true);
    expect(isRemoteLocation("Fully Distributed")).toBe(true);
    expect(isRemoteLocation("WFH")).toBe(true);
    expect(isRemoteLocation("Work from home")).toBe(true);
  });

  it("is case-insensitive", () => {
    expect(isRemoteLocation("REMOTE")).toBe(true);
    expect(isRemoteLocation("remote (global)")).toBe(true);
  });

  it("returns false for concrete on-site locations", () => {
    expect(isRemoteLocation("San Jose, CA")).toBe(false);
    expect(isRemoteLocation("New York, NY")).toBe(false);
    expect(isRemoteLocation("London, UK")).toBe(false);
  });

  it("returns false for null / undefined / empty / non-string", () => {
    expect(isRemoteLocation(null)).toBe(false);
    expect(isRemoteLocation(undefined)).toBe(false);
    expect(isRemoteLocation("")).toBe(false);
    // @ts-expect-error — exercising the runtime type guard
    expect(isRemoteLocation(42)).toBe(false);
  });
});

describe("isCountryOnlyLocation", () => {
  it("treats bare country names as country-only", () => {
    expect(isCountryOnlyLocation("United States")).toBe(true);
    expect(isCountryOnlyLocation("USA")).toBe(true);
    expect(isCountryOnlyLocation("Canada")).toBe(true);
    expect(isCountryOnlyLocation("United Kingdom")).toBe(true);
    expect(isCountryOnlyLocation("Germany")).toBe(true);
  });

  it("normalises case and surrounding whitespace", () => {
    expect(isCountryOnlyLocation("  united states  ")).toBe(true);
    expect(isCountryOnlyLocation("UnItEd StAtEs")).toBe(true);
  });

  it("treats a city+state string as NOT country-only", () => {
    expect(isCountryOnlyLocation("San Jose, CA")).toBe(false);
    expect(isCountryOnlyLocation("San Francisco, California")).toBe(false);
    expect(isCountryOnlyLocation("Austin, TX")).toBe(false);
  });

  it("returns false for null / undefined / empty", () => {
    expect(isCountryOnlyLocation(null)).toBe(false);
    expect(isCountryOnlyLocation(undefined)).toBe(false);
    expect(isCountryOnlyLocation("")).toBe(false);
  });
});

describe("getRemoteRegion", () => {
  it("classifies non-remote locations as onsite", () => {
    expect(getRemoteRegion("San Jose, CA")).toBe("onsite");
    expect(getRemoteRegion("London")).toBe("onsite");
    expect(getRemoteRegion(null)).toBe("onsite");
    expect(getRemoteRegion("")).toBe("onsite");
  });

  it("classifies US-scoped remote as us", () => {
    expect(getRemoteRegion("Remote - US")).toBe("us");
    expect(getRemoteRegion("Remote (USA)")).toBe("us");
    expect(getRemoteRegion("Remote, United States")).toBe("us");
  });

  it("classifies foreign-scoped remote as foreign", () => {
    expect(getRemoteRegion("Remote - UK")).toBe("foreign");
    expect(getRemoteRegion("Remote - Canada")).toBe("foreign");
    expect(getRemoteRegion("Remote (EMEA)")).toBe("foreign");
    expect(getRemoteRegion("Remote - Germany")).toBe("foreign");
  });

  it("classifies bare remote with no geography as anywhere", () => {
    expect(getRemoteRegion("Remote")).toBe("anywhere");
    expect(getRemoteRegion("Worldwide")).toBe("anywhere");
    expect(getRemoteRegion("Anywhere")).toBe("anywhere");
  });

  it("does not false-match foreign tokens hidden inside words", () => {
    // "us" inside "Houston", "uk" inside "Ukraine" — the classifier
    // uses \b word boundaries precisely to avoid these. A "Remote -
    // Houston" string mentions no whole-word country token, so it
    // falls through to "anywhere".
    expect(getRemoteRegion("Remote - Houston")).toBe("anywhere");
  });

  it("treats a US + foreign mention as US (hybrid wins for a US user)", () => {
    expect(getRemoteRegion("Remote - US or UK")).toBe("us");
  });
});
