/**
 * Defensive formatters for "how old is this posting" displays.
 *
 * Why this exists: the previous inline `Math.floor((Date.now() -
 * Date.parse(iso)) / (1000 * 60 * 60 * 24))` was wrong in two ways:
 *
 *   1. `Date.parse(iso)` returns NaN on any unparseable input. NaN
 *      arithmetic propagates: NaN - number = NaN, Math.floor(NaN/...)
 *      = NaN. The cell rendered "NaNd". A scraper handing back a
 *      slightly malformed date string (or a date in a format like
 *      "April 26, 2026" that JavaScript's parser doesn't understand
 *      consistently across browsers) would silently surface as NaN.
 *
 *   2. No future-date guard. If a posting somehow stamps a future
 *      date (timezone confusion, scraper bug), we'd render
 *      "-3d" — visually identical to a past date with a negative
 *      sign, easily missed.
 *
 * The helpers below normalise both: an unparseable / future / missing
 * date returns null, and the caller renders a single em-dash so the
 * column has one obvious "no signal" tell instead of three (NaN, "—",
 * negative number) the user has to triage.
 */

/**
 * Parse a posted-date string defensively. Returns the Unix-ms
 * timestamp on success, null on any failure (unparseable string,
 * empty input, future-dated posting).
 */
export function parsePostedDate(iso: string | null | undefined): number | null {
  if (!iso || typeof iso !== "string") return null;
  const trimmed = iso.trim();
  if (!trimmed) return null;
  const ts = Date.parse(trimmed);
  if (!Number.isFinite(ts)) return null;
  // Future-dated postings are almost always a parse / timezone bug,
  // not a real "this job opens next month" signal. Treat as unknown.
  if (ts > Date.now() + 86400000 /* 1d slack for clock skew */) return null;
  return ts;
}

/**
 * Whole-days age of a posting from `now`. Returns null on any parse
 * failure or future date — caller renders "—".
 */
export function postedAgeDays(iso: string | null | undefined, now: number = Date.now()): number | null {
  const ts = parsePostedDate(iso);
  if (ts == null) return null;
  return Math.max(0, Math.floor((now - ts) / 86400000));
}

/**
 * Compact human-readable age for the table cell. ~5 chars max so the
 * column can stay tight.
 *
 *   null   → "—"          (no usable date)
 *   0d     → "today"
 *   1d     → "1d"
 *   2-13d  → "Nd"
 *   14-59d → "Nw"          (weeks)
 *   60-364d→ "Nmo"         (months)
 *   365+   → "Ny"          (years)
 */
export function formatPostedAge(iso: string | null | undefined, now: number = Date.now()): string {
  const days = postedAgeDays(iso, now);
  if (days == null) return "—";
  if (days === 0) return "today";
  if (days < 14) return `${days}d`;
  if (days < 60) return `${Math.floor(days / 7)}w`;
  if (days < 365) return `${Math.floor(days / 30)}mo`;
  return `${Math.floor(days / 365)}y`;
}
