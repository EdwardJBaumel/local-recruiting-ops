import { describe, it, expect } from "vitest";
import { trimJobDescription, wasTrimmed } from "@/lib/jdTrim";

// trimJobDescription relies on DOMParser, which jsdom provides — so
// these run in the configured jsdom environment, exercising the real
// parsing path rather than the SSR fallback.

describe("trimJobDescription", () => {
  it("keeps a recognised section heading and its following content", () => {
    const html = [
      "<h2>About Acme</h2>",
      "<p>We are a mission-driven company changing the world.</p>",
      "<h2>Responsibilities</h2>",
      "<ul><li>Own the product roadmap</li><li>Talk to customers</li></ul>",
      "<h2>Equal Opportunity Employer</h2>",
      "<p>Acme is an equal opportunity employer.</p>",
    ].join("");

    const trimmed = trimJobDescription(html);

    // Kept: the Responsibilities heading + its list.
    expect(trimmed).toContain("Responsibilities");
    expect(trimmed).toContain("Own the product roadmap");
    // Dropped: company boilerplate and the EEO disclaimer.
    expect(trimmed).not.toContain("About Acme");
    expect(trimmed).not.toContain("equal opportunity employer");
  });

  it("keeps both a responsibilities and a requirements section", () => {
    const html = [
      "<h3>Our Mission</h3><p>Boilerplate filler.</p>",
      "<h3>What you'll do</h3><p>Drive cross-functional delivery.</p>",
      "<h3>What you'll bring</h3><p>5+ years of product management.</p>",
      "<h3>Benefits</h3><p>Unlimited PTO and snacks.</p>",
    ].join("");

    const trimmed = trimJobDescription(html);

    expect(trimmed).toContain("What you'll do");
    expect(trimmed).toContain("Drive cross-functional delivery");
    expect(trimmed).toContain("What you'll bring");
    expect(trimmed).toContain("5+ years of product management");
    expect(trimmed).not.toContain("Our Mission");
    expect(trimmed).not.toContain("Unlimited PTO");
  });

  it("recognises the bold-paragraph heading pattern (Greenhouse/Lever)", () => {
    const html = [
      "<p><strong>About us</strong></p><p>Filler.</p>",
      "<p><strong>Requirements</strong></p>",
      "<ul><li>Strong written communication</li></ul>",
    ].join("");

    const trimmed = trimJobDescription(html);

    expect(trimmed).toContain("Requirements");
    expect(trimmed).toContain("Strong written communication");
    expect(trimmed).not.toContain("About us");
  });

  it("falls back to the original when no recognised headings exist", () => {
    const html =
      "<p>Just a flat paragraph of prose with no section structure at all.</p>";
    expect(trimJobDescription(html)).toBe(html);
  });

  it("returns the input unchanged for empty / whitespace strings", () => {
    expect(trimJobDescription("")).toBe("");
    expect(trimJobDescription("   ")).toBe("   ");
  });

  it("caps the trimmed output at ~800 visible characters", () => {
    // One huge Responsibilities list — far past the 800-char budget.
    const items = Array.from(
      { length: 60 },
      (_, i) => `<li>Responsibility number ${i} with some descriptive text</li>`,
    ).join("");
    const html = `<h2>Responsibilities</h2><ul>${items}</ul>`;

    const trimmed = trimJobDescription(html);

    // Pull visible text length out of the result.
    const visible = trimmed.replace(/<[^>]+>/g, "");
    // The cap is 800 chars of body; allow generous slack for the
    // heading text + the "… more" footer note.
    expect(visible.length).toBeLessThan(1000);
    // It must still have actually trimmed (be shorter than the input).
    expect(trimmed.length).toBeLessThan(html.length);
    expect(trimmed).toContain("Responsibilities");
  });
});

describe("wasTrimmed", () => {
  it("reports true when the trimmed text is shorter than the original", () => {
    expect(wasTrimmed("a".repeat(100), "a".repeat(40))).toBe(true);
  });

  it("reports false when nothing was removed (fallback returned original)", () => {
    const original = "<p>unchanged</p>";
    expect(wasTrimmed(original, original)).toBe(false);
  });

  it("reports false when the trimmed result is empty", () => {
    expect(wasTrimmed("some original text", "")).toBe(false);
  });

  it("reports false when trimmed is somehow longer (defensive)", () => {
    expect(wasTrimmed("short", "a much longer string")).toBe(false);
  });
});
