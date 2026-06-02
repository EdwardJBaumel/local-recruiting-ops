import { describe, expect, it } from "vitest";
import type { MatchPayload } from "@/types/match";
import { DEFAULT_MATCH_FILTERS, filterMatchRows } from "@/lib/matchFilters";

function row(partial: Partial<MatchPayload> & Pick<MatchPayload, "title" | "company">): MatchPayload {
  return {
    url: "https://example.com/job",
    ...partial,
  };
}

describe("filterMatchRows", () => {
  const base = [
    row({ title: "Open", company: "A", url: "https://a/1" }),
    row({ title: "Starred", company: "B", url: "https://b/1", _starred: true }),
    row({ title: "Seen", company: "C", url: "https://c/1", _seen: true }),
    row({ title: "Dismissed", company: "D", url: "https://d/1", _dismissed: true }),
    row({ title: "Removed", company: "E", url: "https://e/1", _removed: true }),
    row({ title: "Maybe", company: "M", url: "https://m/1", _match_tier: "maybe" }),
    row({
      title: "UK remote",
      company: "F",
      url: "https://f/1",
      location: "Remote - UK",
    }),
  ];

  it("returns match-tier rows with default filters", () => {
    const { rows } = filterMatchRows(base, DEFAULT_MATCH_FILTERS);
    const titles = rows.map((r) => r.title);
    expect(titles).toEqual(["Open", "Starred", "Seen"]);
    expect(titles).not.toContain("Dismissed");
    expect(titles).not.toContain("Removed");
    expect(titles).not.toContain("Maybe");
    expect(titles).not.toContain("UK remote");
  });

  it("starredOnly keeps starred rows", () => {
    const { rows } = filterMatchRows(base, { ...DEFAULT_MATCH_FILTERS, starredOnly: true });
    expect(rows.map((r) => r.title)).toEqual(["Starred"]);
  });

  it("unseenOnly hides reacted rows (_seen)", () => {
    const { rows } = filterMatchRows(base, { ...DEFAULT_MATCH_FILTERS, unseenOnly: true });
    expect(rows.every((r) => !r._seen)).toBe(true);
    expect(rows.map((r) => r.title)).toContain("Open");
    expect(rows.map((r) => r.title)).not.toContain("Seen");
  });

  it("showDismissed includes passed rows but not _removed", () => {
    const { rows } = filterMatchRows(base, { ...DEFAULT_MATCH_FILTERS, showDismissed: true });
    expect(rows.map((r) => r.title)).toContain("Dismissed");
    expect(rows.map((r) => r.title)).not.toContain("Removed");
  });

  it("maybe tier visible when starred", () => {
    const rows = [
      row({ title: "Star maybe", company: "X", url: "https://x/1", _match_tier: "maybe", _starred: true }),
    ];
    const { rows: filtered } = filterMatchRows(rows, DEFAULT_MATCH_FILTERS);
    expect(filtered.map((r) => r.title)).toEqual(["Star maybe"]);
  });

  it("archetype filter", () => {
    const rows = [
      row({ title: "PM", company: "A", url: "https://a/1", archetype: "product" }),
      row({ title: "Eng", company: "B", url: "https://b/1", archetype: "engineering" }),
    ];
    const { rows: filtered } = filterMatchRows(rows, {
      ...DEFAULT_MATCH_FILTERS,
      archetype: "product",
    });
    expect(filtered.map((r) => r.title)).toEqual(["PM"]);
  });

  it("hideForeignRemote drops foreign-remote locations", () => {
    const rows = [
      row({ title: "US remote", company: "X", url: "https://x/1", location: "Remote - US" }),
      row({ title: "Bare remote", company: "Y", url: "https://y/1", location: "Remote" }),
      row({ title: "Canada", company: "Z", url: "https://z/1", location: "Remote - Canada" }),
    ];
    const { rows: filtered, stats } = filterMatchRows(rows, DEFAULT_MATCH_FILTERS);
    expect(filtered.map((r) => r.title)).toEqual(["US remote", "Bare remote"]);
    expect(stats.foreignRemoteDropped).toBe(1);
  });

  it("hideForeignRemote uses work_mode when location is empty", () => {
    const rows = [
      row({ title: "UK via mode", company: "X", url: "https://x/1", work_mode: "Remote - UK" }),
    ];
    const { rows: filtered, stats } = filterMatchRows(rows, DEFAULT_MATCH_FILTERS);
    expect(filtered).toHaveLength(0);
    expect(stats.foreignRemoteDropped).toBe(1);
  });

  it("hideForeignRemote off keeps foreign-remote rows", () => {
    const { rows } = filterMatchRows(base, { ...DEFAULT_MATCH_FILTERS, hideForeignRemote: false });
    expect(rows.map((r) => r.title)).toContain("UK remote");
  });
});
