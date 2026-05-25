/**
 * Company-size classifier for the freshness-window filter.
 *
 * Why tiers exist: hiring velocity differs wildly by company size.
 *   - **Mega** (Amazon, Google, public big tech): postings are
 *     "evergreen reqs" — one job ad maps to many seats and stays open
 *     30-90+ days while the funnel rotates. A 4-day filter would
 *     hide perfectly viable openings.
 *   - **Large** (decacorns, public mid-caps, Stripe / Databricks /
 *     OpenAI tier): hire fast but with structure; first 7-14 days
 *     captures most of the active interview pool.
 *   - **Growth** (everything else: smaller startups, niche companies,
 *     remote-board postings): the early-bird advantage is real.
 *     ~7× higher response rate in the first 4-7 days than after 30.
 *
 * The lists below are intentionally short and biased toward
 * false-NEGATIVES — better to treat "Stripe" as Large and apply the
 * 14-day window than to mistakenly treat a small Series A as Mega
 * and hide its postings 30 days late. Anything not in MEGA or LARGE
 * falls through to "growth" and gets the aggressive default.
 *
 * Maintenance: bump the lists when a company crosses the threshold
 * (typical signal: $10B+ valuation for LARGE, $50B+ market cap for
 * MEGA). The Settings UI doesn't expose the lists — it exposes the
 * three window VALUES — so users can tune timing without us
 * shipping a per-company override.
 */
export type CompanyTier = "mega" | "large" | "growth";

// Mega-tier: huge public companies + Big Tech. Evergreen reqs, slow
// hiring committees, postings can sit 60+ days and still be active.
const MEGA = new Set([
  "amazon",
  "aws",
  "google",
  "alphabet",
  "youtube",
  "nvidia",
  "adobe",
  "salesforce",
  "ibm",
  "cisco",
  "intel",
  // Not currently scraped (TOS / robots.txt) but listed so if a
  // posting trickles in via aggregator it's classified correctly:
  "apple",
  "microsoft",
  "meta",
  "facebook",
  "oracle",
  "tesla",
  "sap",
  "linkedin",
  "uber",
  "netflix",
]);

// Large-tier: decacorns / well-established public mid-caps. Hire
// quickly but with structured interview loops; 14-day window is the
// sweet spot.
const LARGE = new Set([
  "stripe",
  "databricks",
  "openai",
  "anthropic",
  "scale ai",
  "scaleai",
  "coinbase",
  "airbnb",
  "doordash",
  "pinterest",
  "spotify",
  "cloudflare",
  "lyft",
  "snowflake",
  "datadog",
  "roblox",
  "reddit",
  "robinhood",
  "mongodb",
  "twilio",
  "gitlab",
  "dropbox",
  "okta",
  "affirm",
  "plaid",
  "discord",
  "instacart",
  "samsara",
  "elastic",
  "sofi",
  "block",
  "square",
  "shopify",
  "zoom",
  "atlassian",
  "twitch",
  "duolingo",
  "github",
  "asana",
  "figma",
  "notion",
  "canva",
  "rippling",
  "gusto",
]);

/**
 * Classify a company name into a freshness-window tier. The lookup is
 * case-insensitive and tolerates the common formatting drift we see
 * in feeds ("OpenAI" vs. "openai", "Scale AI" vs. "Scaleai").
 */
export function classifyCompany(company: string | null | undefined): CompanyTier {
  if (!company) return "growth";
  const norm = company.trim().toLowerCase();
  if (!norm) return "growth";
  if (MEGA.has(norm)) return "mega";
  if (LARGE.has(norm)) return "large";
  return "growth";
}

/**
 * Default windows (in days) per tier — research-backed:
 *   - mega:   30 days (evergreen-req model; first month is still active)
 *   - large:  14 days (structured loops, mid-funnel by day 14)
 *   - growth:  7 days (early-bird advantage strongest here)
 */
export const DEFAULT_FRESHNESS_WINDOWS: Record<CompanyTier, number> = {
  mega: 30,
  large: 14,
  growth: 7,
};
