/**
 * Location text classifiers — used by the Matches view to decide
 * whether a posting's location string passes the user's allow/block
 * lists, plus a "hide foreign-only remote" gate for US-based users
 * who don't want to wade through Remote-UK / Remote-Canada postings.
 *
 * Removed (Apr 2026): the pin-on-a-map filter and its supporting
 * CITY_COORDS table, locateJob() / haversineKm() / withinAnyPin().
 * The text allow/block lists turned out to be what users actually
 * used; the geographic-radius UI was decoration. See LocationSection
 * removal commit and `lro/api/core/preferences.py LocationFilter`
 * for the server-side mirror.
 *
 * The classifiers below stay in sync with the backend's
 * `core/geocode.py` `_REMOTE_TOKENS` and `_COUNTRY_ONLY_LOCATIONS` so
 * client-side preview filtering matches what the pipeline actually
 * scored at match time. If you add a token here, mirror it there.
 */
// Tokens that mean "this role is location-agnostic." Mirror of
// _REMOTE_TOKENS in lro/api/core/geocode.py — keep them in sync
// so the live UI re-apply matches what the backend actually filtered.
const REMOTE_TOKENS = [
  "remote", "anywhere", "worldwide", "distributed", "global",
  "wfh", "work from home", "work-from-home",
];

export function isRemoteLocation(location: string | null | undefined): boolean {
  if (!location || typeof location !== "string") return false;
  const lc = location.toLowerCase();
  return REMOTE_TOKENS.some((tok) => lc.includes(tok));
}

/**
 * Country-only location strings — when a posting's `location` is just
 * the name of a country (no city), the city-pin filter shouldn't treat
 * it as "out of region". Mirrors the backend's _COUNTRY_ONLY_LOCATIONS
 * in lro/api/core/preferences.py — keep both lists in sync.
 *
 * The country filter (`hideForeignRemote`, `allowed_countries`) runs
 * upstream of the pin check, so reaching this means the country is
 * already allowed. Without this guard, "United States" jobs were
 * dropped 56% of the time because none of them geocode to a city
 * inside any pin radius.
 */
const COUNTRY_ONLY_LOCATIONS = new Set([
  "united states", "usa", "u.s.", "u.s.a.", "us",
  "canada",
  "united kingdom", "uk", "u.k.", "great britain", "england",
  "ireland",
  "germany", "france", "spain", "italy", "netherlands",
  "australia", "new zealand",
  "india", "japan", "singapore",
  "brazil", "mexico",
  "remote (us)", "remote us", "remote, us", "remote, usa",
  "remote (eu)", "remote eu", "remote, eu",
  "remote (uk)", "remote uk", "remote, uk",
  "north america", "europe", "emea", "apac", "americas", "latam",
]);

export function isCountryOnlyLocation(location: string | null | undefined): boolean {
  if (!location) return false;
  return COUNTRY_ONLY_LOCATIONS.has(location.trim().toLowerCase());
}

// Foreign-country / foreign-region markers. If a location string
// contains a remote token AND one of these tokens (without ALSO
// containing a US marker), it's effectively a non-US-only remote
// posting — those usually require local work authorisation, payroll,
// and tax residency, so they're a false-positive for a US-based
// candidate hoping for "anywhere remote".
//
// We match on whole-word boundaries via regex (not bare .includes)
// because raw substrings like "uk" would false-match "ukraine" and
// "us" would false-match "Houston" or "discuss". The whitespace /
// punctuation guards keep us honest.
const FOREIGN_REGION_PATTERNS: RegExp[] = [
  // United Kingdom + constituent nations
  /\b(uk|u\.k\.|united kingdom|great britain|britain|england|scotland|wales|northern ireland)\b/,
  // Continental Europe (broad)
  /\b(eu|e\.u\.|emea|europe|european|eea|eurozone)\b/,
  // Specific European countries that pop up a lot
  /\b(germany|france|spain|italy|netherlands|holland|belgium|sweden|norway|denmark|finland|poland|ireland|portugal|switzerland|austria)\b/,
  // North America (non-US)
  /\b(canada|canadian|mexico|mexican)\b/,
  // LATAM
  /\b(latam|latin america|brazil|brasil|argentina|chile|colombia|peru|uruguay)\b/,
  // APAC (non-US)
  /\b(apac|asia|asian|india|singapore|japan|korea|china|taiwan|thailand|vietnam|philippines|malaysia|indonesia|hong kong)\b/,
  // ANZ
  /\b(anz|australia|australian|new zealand|nz)\b/,
  // Middle East & Africa
  /\b(uae|dubai|saudi|israel|south africa|africa|mena)\b/,
];

const US_REGION_PATTERNS: RegExp[] = [
  // The literal "US"-ish tokens. \b prevents "discuss"/"Houston"/"plus" matches.
  /\b(us|u\.s\.|usa|u\.s\.a\.|united states|america|american|stateside)\b/,
  // States are too noisy for whole-token detection — most "Remote - US"
  // postings just say "US" or list multiple cities. We don't try to
  // enumerate state names here; if you want fine-grained gating, add
  // the specific state names to the allowed-locations list in
  // Settings → Location (e.g. "California", "Washington State").
];

/**
 * Classify a location string's remote scope:
 *   - "us"        → remote, scoped to (or includes) the United States
 *   - "foreign"   → remote, scoped to a non-US country/region
 *   - "anywhere"  → remote with no country tied to it
 *   - "onsite"    → not remote at all
 *
 * Used by the UI's "hide foreign-only remote" filter so US-based
 * candidates don't have to wade through Remote-UK / Remote-Canada
 * postings that won't sponsor work permits.
 */
export type RemoteRegion = "us" | "foreign" | "anywhere" | "onsite";

export function getRemoteRegion(location: string | null | undefined): RemoteRegion {
  if (!isRemoteLocation(location)) return "onsite";
  const lc = (location ?? "").toLowerCase();
  const hasUs = US_REGION_PATTERNS.some((re) => re.test(lc));
  const hasForeign = FOREIGN_REGION_PATTERNS.some((re) => re.test(lc));
  if (hasUs) return "us";          // mentions US explicitly → US (or hybrid)
  if (hasForeign) return "foreign"; // foreign region only
  return "anywhere";                 // bare "Remote" / "Worldwide" — global
}
