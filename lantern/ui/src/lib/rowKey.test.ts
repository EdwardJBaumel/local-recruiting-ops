import { describe, it, expect } from "vitest";
import { rowKey } from "@/lib/rowKey";
import type { MatchPayload } from "@/types/match";

// Minimal payload factory — rowKey only reads url/company/title/location.
function match(over: Partial<MatchPayload>): MatchPayload {
  return {
    url: "",
    title: "",
    company: "",
    ...over,
  } as MatchPayload;
}

describe("rowKey", () => {
  it("uses the url when one is present", () => {
    const m = match({
      url: "https://boards.greenhouse.io/acme/jobs/123",
      company: "Acme",
      title: "PM",
      location: "Remote",
    });
    expect(rowKey(m)).toBe("https://boards.greenhouse.io/acme/jobs/123");
  });

  it("is stable — same row yields the same key across calls", () => {
    const m = match({ url: "https://example.com/job/1" });
    expect(rowKey(m)).toBe(rowKey(m));
  });

  it("falls back to company::title::location when url is an empty string", () => {
    const m = match({
      url: "",
      company: "Acme",
      title: "Senior Product Manager",
      location: "San Jose, CA",
    });
    expect(rowKey(m)).toBe("Acme::Senior Product Manager::San Jose, CA");
  });

  it("falls back to the composite key when url is null", () => {
    const m = match({
      // legacy Google entries historically carried url: null
      url: null as unknown as string,
      company: "Google",
      title: "Group PM",
      location: "Mountain View, CA",
    });
    expect(rowKey(m)).toBe("Google::Group PM::Mountain View, CA");
  });

  it("substitutes ? for missing company / title / location parts", () => {
    const m = match({
      url: "",
      company: undefined as unknown as string,
      title: undefined as unknown as string,
      location: undefined,
    });
    expect(rowKey(m)).toBe("?::?::?");
  });

  it("gives two distinct no-url rows distinct keys", () => {
    const a = match({ url: "", company: "Acme", title: "PM", location: "NYC" });
    const b = match({ url: "", company: "Acme", title: "PM", location: "SF" });
    expect(rowKey(a)).not.toBe(rowKey(b));
  });

  it("collides only for genuine duplicates (same company+title+location)", () => {
    const a = match({ url: "", company: "Acme", title: "PM", location: "NYC" });
    const b = match({ url: "", company: "Acme", title: "PM", location: "NYC" });
    // The registry dedupes on exactly this triple, so a collision here
    // means a real duplicate — which is the intended behaviour.
    expect(rowKey(a)).toBe(rowKey(b));
  });
});
