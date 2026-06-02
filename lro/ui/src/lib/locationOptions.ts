/**
 * Curated list of metros, states, regions, and remote-bands that the
 * location filter's multi-select dropdown offers. Each option's
 * `value` is the lowercased substring that gets written to the
 * config's `allowed_locations` / `blocked_locations` arrays. The
 * server-side substring match in `core/preferences.py LocationFilter`
 * uses these strings verbatim — so picking "california" matches any
 * posting whose location text contains "california".
 *
 * Why this list and not free-text
 * -------------------------------
 * Users want "the cities I'd consider" — typing "San Francisco, NYC,
 * Seattle" by hand is error-prone (typos, capitalisation drift) and
 * doesn't suggest options. A curated dropdown with categories +
 * search is the better UX. Power users who want a substring we don't
 * list ("Manchester UK", "Tier 2 city") would have lost that ability
 * with a pure-dropdown approach, so the LocationSection keeps a
 * small free-text field below the dropdowns for one-offs.
 *
 * Why some entries look like duplicates
 * -------------------------------------
 * "san francisco" and "bay area" both exist because some postings
 * tag with one and not the other. "new york" matches both NYC and
 * "New York State"; the user picks the broader signal. "remote" and
 * "remote us" both exist because some postings explicitly scope to
 * US-only remote and the user may want to be specific.
 *
 * Substring gotchas worth knowing
 * -------------------------------
 *   - "uk" would substring-match "Ukraine" and "ukulele". We use the
 *     longer "united kingdom" instead. Same reason "us" isn't an
 *     option — it'd match "Houston" and "discuss" inside text.
 *   - "washington" matches both Washington DC and Washington State —
 *     they're disambiguated by listing each separately with the
 *     longer phrase ("washington dc" vs "washington state") only
 *     where the JD market reliably uses that phrasing.
 */

export type LocationCategory = "us-metro" | "us-state" | "remote" | "international" | "region";

export interface LocationOption {
  /** Lowercased substring matched against the JD's `location` field. */
  value: string;
  /** Display label shown in the dropdown chip / list row. */
  label: string;
  /** Category for grouping headers inside the dropdown. */
  category: LocationCategory;
}

export const CATEGORY_LABELS: Record<LocationCategory, string> = {
  "us-metro": "US Metros",
  "us-state": "US States",
  "remote": "Remote",
  "international": "International",
  "region": "Regions",
};

export const LOCATION_OPTIONS: LocationOption[] = [
  // --- US Metros ---------------------------------------------------------
  // California (Bay Area is a single tag many ATSes use; included
  // alongside specific cities to catch both).
  { value: "san francisco", label: "San Francisco", category: "us-metro" },
  { value: "bay area",      label: "Bay Area",      category: "us-metro" },
  { value: "palo alto",     label: "Palo Alto",     category: "us-metro" },
  { value: "mountain view", label: "Mountain View", category: "us-metro" },
  { value: "menlo park",    label: "Menlo Park",    category: "us-metro" },
  { value: "san jose",      label: "San Jose",      category: "us-metro" },
  { value: "sunnyvale",     label: "Sunnyvale",     category: "us-metro" },
  { value: "santa clara",   label: "Santa Clara",   category: "us-metro" },
  { value: "oakland",       label: "Oakland",       category: "us-metro" },
  { value: "san mateo",     label: "San Mateo",     category: "us-metro" },
  { value: "los angeles",   label: "Los Angeles",   category: "us-metro" },
  { value: "irvine",        label: "Irvine",        category: "us-metro" },
  { value: "santa monica",  label: "Santa Monica",  category: "us-metro" },
  { value: "san diego",     label: "San Diego",     category: "us-metro" },
  // Pacific NW
  { value: "seattle",       label: "Seattle",       category: "us-metro" },
  { value: "bellevue",      label: "Bellevue",      category: "us-metro" },
  { value: "redmond",       label: "Redmond",       category: "us-metro" },
  { value: "portland",      label: "Portland",      category: "us-metro" },
  // Mountain
  { value: "denver",        label: "Denver",        category: "us-metro" },
  { value: "boulder",       label: "Boulder",       category: "us-metro" },
  // Texas
  { value: "austin",        label: "Austin",        category: "us-metro" },
  { value: "dallas",        label: "Dallas",        category: "us-metro" },
  { value: "houston",       label: "Houston",       category: "us-metro" },
  // Midwest / South
  { value: "chicago",       label: "Chicago",       category: "us-metro" },
  { value: "atlanta",       label: "Atlanta",       category: "us-metro" },
  { value: "minneapolis",   label: "Minneapolis",   category: "us-metro" },
  // Northeast
  { value: "new york",      label: "New York",      category: "us-metro" },
  { value: "brooklyn",      label: "Brooklyn",      category: "us-metro" },
  { value: "manhattan",     label: "Manhattan",     category: "us-metro" },
  { value: "jersey city",   label: "Jersey City",   category: "us-metro" },
  { value: "boston",        label: "Boston",        category: "us-metro" },
  { value: "cambridge",     label: "Cambridge",     category: "us-metro" },
  { value: "philadelphia",  label: "Philadelphia",  category: "us-metro" },
  // Mid-Atlantic / South
  { value: "washington dc", label: "Washington DC", category: "us-metro" },
  { value: "arlington",     label: "Arlington VA",  category: "us-metro" },
  { value: "raleigh",       label: "Raleigh",       category: "us-metro" },
  { value: "miami",         label: "Miami",         category: "us-metro" },

  // --- US States ---------------------------------------------------------
  // Picking a state catches every job whose location text mentions
  // it (e.g. "San Francisco, California" or "Remote - California").
  // Combine with specific metros above for jobs that say just the
  // city without the state.
  { value: "california",     label: "California",         category: "us-state" },
  { value: "new york state", label: "New York (State)",   category: "us-state" },
  { value: "texas",          label: "Texas",              category: "us-state" },
  { value: "washington state",label: "Washington (State)",category: "us-state" },
  { value: "massachusetts",  label: "Massachusetts",      category: "us-state" },
  { value: "colorado",       label: "Colorado",           category: "us-state" },
  { value: "oregon",         label: "Oregon",             category: "us-state" },
  { value: "illinois",       label: "Illinois",           category: "us-state" },
  { value: "florida",        label: "Florida",            category: "us-state" },

  // --- Remote bands ------------------------------------------------------
  { value: "remote",     label: "Remote (any)",  category: "remote" },
  { value: "remote us",  label: "Remote (US)",   category: "remote" },
  { value: "anywhere",   label: "Anywhere",      category: "remote" },
  { value: "worldwide",  label: "Worldwide",     category: "remote" },

  // --- International (mostly for block lists) ----------------------------
  // Avoid 2-letter codes ("uk", "us") — they substring-match harmless
  // words. Spell them out.
  { value: "united kingdom", label: "United Kingdom", category: "international" },
  { value: "canada",         label: "Canada",         category: "international" },
  { value: "germany",        label: "Germany",        category: "international" },
  { value: "france",         label: "France",         category: "international" },
  { value: "netherlands",    label: "Netherlands",    category: "international" },
  { value: "spain",          label: "Spain",          category: "international" },
  { value: "ireland",        label: "Ireland",        category: "international" },
  { value: "ukraine",        label: "Ukraine",        category: "international" },
  { value: "russia",         label: "Russia",         category: "international" },
  { value: "india",          label: "India",          category: "international" },
  { value: "china",          label: "China",          category: "international" },
  { value: "japan",          label: "Japan",          category: "international" },
  { value: "singapore",      label: "Singapore",      category: "international" },
  { value: "brazil",         label: "Brazil",         category: "international" },
  { value: "mexico",         label: "Mexico",         category: "international" },
  { value: "argentina",      label: "Argentina",      category: "international" },
  { value: "australia",      label: "Australia",      category: "international" },

  // --- Regional rollups --------------------------------------------------
  // Useful in block lists when a posting says e.g. "Remote - EMEA".
  { value: "emea",          label: "EMEA",          category: "region" },
  { value: "europe",        label: "Europe",        category: "region" },
  { value: "latam",         label: "LATAM",         category: "region" },
  { value: "latin america", label: "Latin America", category: "region" },
  { value: "apac",          label: "APAC",          category: "region" },
  { value: "asia",          label: "Asia",          category: "region" },
];
