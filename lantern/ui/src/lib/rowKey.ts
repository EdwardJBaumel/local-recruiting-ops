import type { MatchPayload } from "@/types/match";

/**
 * Stable identifier for a match row.
 *
 * The natural choice is `m.url` — every row should have one — but real
 * data has rough edges:
 *   - Google careers cards historically came back with `url=null`
 *     because our HTML cleaner strips `<a href>` before the LLM sees
 *     the input. Fixed in the fetcher now, but legacy registry
 *     entries still carry null URLs until the next pipeline run.
 *   - Some bespoke sources occasionally drop the URL on malformed
 *     responses.
 *
 * When multiple rows share `url === null`, using URL as the selection
 * key meant clicking ANY null-url row highlighted ALL of them and
 * popped open whichever the matches array iterated first — looked
 * like the click was broken.
 *
 * Falling back to `${company}::${title}::${location}` gives us a
 * deterministic-but-meaningful key. Two rows with the same
 * company+title+location are duplicates anyway (the registry already
 * dedupes on this triple), so collisions imply a real duplicate.
 */
export function rowKey(m: MatchPayload): string {
  if (m.url) return m.url;
  return `${m.company ?? "?"}::${m.title ?? "?"}::${m.location ?? "?"}`;
}
