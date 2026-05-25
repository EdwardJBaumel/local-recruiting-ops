import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from "recharts";
import { light as themeLight, dark as themeDark } from "./ui/tokens";
import { Button, Chip, IconButton, Icon } from "./ui/primitives";
import JobMap, { locateJob, haversineKm } from "./JobMap";

const API = "";
// Adaptive polling. When a cycle is in progress we want the Brief tab to
// repaint quickly so stages and rolling counts look live; at rest we back
// off so the server isn't doing useless work for an idle tab.
const POLL_FAST = 2000;
const POLL_IDLE = 8000;

// ─── DEMO DATA ───────────────────────────────────────────────────
// Empty shape used on cold start and when the backend is briefly
// unreachable. Previously the UI seeded fake Stripe/Cloudflare rows
// here, which confused people: "why does my app have Stripe matches
// when I just ran a cycle with zero real matches?" Registry now
// carries previous real matches across restarts - no fallback needed.
const D = {
  matches: [],
  fitGaps: [],
  decisions: { decisions: [], reactions: {} },
  market: [],
  digests: [],
  config: {
    ingest: { role_keywords: [] },
    match: { threshold: 0.55, profile_text: "" },
    parse: { model: "qwen2.5:14b" },
    cycle_interval_minutes: 30,
    preferences: { work_modes: ["remote", "hybrid", "onsite"], allowed_locations: [], blocked_locations: [], salary_floor_usd: 0, salary_weight: 0.15 },
  },
  tier1: null,
};

// ─── THEME ───────────────────────────────────────────────────────
// Tokens live in ./ui/tokens.js. They're WCAG 2.1 AA compliant and
// expose the same flat keys this file has always used (`bg`, `text`,
// `accent`, etc.) PLUS new `danger`/`dangerBg`/`info`/`infoBg` pairs
// and a `tones()` helper for the new primitives. Re-exported as
// `light`/`dark` so the rest of this file keeps referencing them.
const light = themeLight;
const dark = themeDark;

// ─── HELPER SPRITE ───────────────────────────────────────────────
// Renders the chosen helper sprite. We serve animated GIFs out of
// sentinel-ui/public/sprites/ (Vite copies them verbatim to dist/) and
// the browser handles looping natively - no JS frame-cycling required.
// `image-rendering: pixelated` keeps the upscale crisp for small
// source GIFs. If the asset 404s (half-configured manifest, file not
// dropped in yet) we fall back to a coloured square with the label
// so the dashboard never breaks.
function HelperSprite({ assetUrl, label, size = 96, fallbackBg = "#c44d2a" }) {
  const [errored, setErrored] = useState(false);
  if (!assetUrl || errored) {
    return (
      <div style={{
        width: size, height: size, margin: "0 auto",
        background: fallbackBg, borderRadius: "6px",
        display: "flex", alignItems: "center", justifyContent: "center",
        color: "#faf8f5", fontFamily: "'IBM Plex Mono', monospace",
        fontSize: "11px", letterSpacing: "1px", fontWeight: 600,
      }}>
        {(label || "?").slice(0, 6)}
      </div>
    );
  }
  return (
    <img
      src={assetUrl}
      alt={label || "helper"}
      width={size}
      height={size}
      onError={() => setErrored(true)}
      style={{
        imageRendering: "pixelated",
        display: "block",
        margin: "0 auto",
        width: `${size}px`,
        height: `${size}px`,
      }}
    />
  );
}

// Turns a raw job-description string into a tree of paragraphs and
// bullet lists. Most ATS feeds hand us a wall of HTML-stripped text
// with inconsistent whitespace: sometimes double-newlines, sometimes
// none, sometimes bullet glyphs (• ●), sometimes just "·" or "-". We
// normalise + segment so the panel doesn't show a 600-word blob.
//
// Heuristics (fail soft - never throw, always return *something*):
//   1. Normalise weird whitespace: nbsp -> space, CRLF -> LF, collapse
//      runs of 3+ spaces into a sentence break ("... perf.   Built ..."
//      becomes two sentences).
//   2. Split the text into blocks on blank lines.
//   3. Each block: if ≥2 lines start with a bullet glyph, treat the
//      whole block as a <ul>; else render as a <p> with internal line
//      breaks preserved.
//   4. If the whole input is ONE giant paragraph (no blank lines at
//      all), fall back to sentence-grouping: break after every 2-3
//      sentences so the reader gets paragraph-sized chunks.
//   5. Bold-ish tokens (ALL-CAPS headers like "REQUIREMENTS:" or
//      markdown-style `**foo**`) get a bold span.
// Strip HTML tags and decode entities while preserving block-level
// structure. Real-world JD inputs come from multiple sources: some are
// plain text already, some ship raw HTML (gmail_quote divs, <br>, <ul>)
// because the crawler didn't unwrap the body. Running an HTML tag
// pre-pass on clean text is a no-op — input with no < / > survives
// unchanged — but when HTML is present this turns it into paragraphs
// and bullets our downstream detector understands.
function stripHtml(raw) {
  if (!raw) return "";
  const s = String(raw);
  // Fast path: no tags detected at all. Avoids spinning up DOMParser
  // for plain-text inputs (the majority of cards).
  if (!/<[a-z!/]/i.test(s)) {
    // Still decode a handful of common entities in case they leak in.
    return s
      .replace(/&nbsp;/g, " ")
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'");
  }
  try {
    const doc = new DOMParser().parseFromString(s, "text/html");
    // Kill script/style outright — they never belong in a JD preview.
    doc.querySelectorAll("script, style, noscript").forEach(n => n.remove());
    // Walk the DOM and build a text string that preserves block breaks.
    const BLOCK = new Set([
      "p","div","section","article","header","footer","main","aside",
      "h1","h2","h3","h4","h5","h6","ul","ol","table","tr","thead","tbody"
    ]);
    const LINE = new Set(["br","hr","td","th"]);
    const BULLET = new Set(["li"]);
    let out = "";
    const walk = (node) => {
      if (node.nodeType === 3) { out += node.nodeValue; return; }
      if (node.nodeType !== 1) return;
      const tag = node.tagName.toLowerCase();
      if (LINE.has(tag)) { out += "\n"; }
      else if (BULLET.has(tag)) { out += "\n• "; }
      else if (BLOCK.has(tag)) { out += "\n\n"; }
      for (const child of node.childNodes) walk(child);
      if (BLOCK.has(tag)) out += "\n\n";
    };
    walk(doc.body || doc.documentElement);
    return out;
  } catch {
    // Fallback: regex strip. Not perfect, but always terminating.
    return s
      .replace(/<(br|hr)\s*\/?>/gi, "\n")
      .replace(/<\/(p|div|li|h[1-6]|ul|ol|tr|table)>/gi, "\n\n")
      .replace(/<li[^>]*>/gi, "\n• ")
      .replace(/<[^>]+>/g, "")
      .replace(/&nbsp;/g, " ")
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'");
  }
}

// Scrub the corporate "about the company" paragraph that ATSs inject at
// the top of every JD. Airbnb prepends "Airbnb was born in 2007 when two
// hosts..."; Stripe leads with "Stripe is a financial infrastructure..."
// etc. These paragraphs carry no role signal and push the actual JD off
// the fold. Strategy: if the opening chunk matches the boilerplate shape
// AND we can locate a role-specific section header after it, return the
// text from that header onward. Otherwise, leave the raw text alone.
// Section-header regex. Not line-anchored any more: many ATS exports
// collapse all whitespace into single spaces, which meant the old
// `/^...$/m` version never matched and the boilerplate leaked through.
// Now we match the header as a phrase anywhere in the first 2500 chars,
// so long as it's preceded by a word boundary and followed by a colon
// or end-of-segment punctuation.
const SECTION_HEADERS_RE = /\b(the\s+(role|team|community|opportunity|position)|a\s+typical\s+day|what\s+you['']ll\s+do|key\s+responsibilities|responsibilities|your\s+(expertise|role|background)|qualifications|requirements|who\s+you\s+are|about\s+(the\s+role|this\s+role|the\s+position|the\s+job)|job\s+description|role\s+overview|overview|minimum\s+qualifications|preferred\s+qualifications|what\s+we['']re\s+looking\s+for|what\s+we\s+offer|in\s+this\s+role|day\s+to\s+day)\b/i;
// Boilerplate hints: the company-blurb tells. Expanded to cover more
// common opening patterns we've seen leak through — "mission-driven",
// "trusted by X companies", founding anecdotes, values statements.
const BOILERPLATE_HINT_RE = /\b(was\s+born|was\s+founded|founded\s+in\s+\d{4}|is\s+a\s+(leading|global|fast-growing|fast-paced|world-class|trusted)|is\s+the\s+(leading|world['']s|largest|premier)|we['']re\s+on\s+a\s+mission|our\s+mission\s+is|mission[\s-]driven|we\s+believe\s+(that|in)|transforms?\s+how|headquarter(ed|s)|trusted\s+by\s+(millions|thousands|hundreds|\d)|at\s+[A-Z][a-zA-Z]+,\s+we\s+|our\s+(vision|values|purpose)|join\s+(us|our\s+team)|our\s+(people|team|culture)\s+(are|is))\b/i;
function stripCompanyBoilerplate(text) {
  if (!text || text.length < 400) return text;
  // Look for the first role-section header in the first 2500 chars.
  const slice = text.slice(0, 2500);
  const match = slice.match(SECTION_HEADERS_RE);
  if (!match) return text;
  const cutIdx = slice.indexOf(match[0]);
  if (cutIdx <= 0) return text;
  const preamble = slice.slice(0, cutIdx);
  // Strip if EITHER:
  //  (a) the preamble contains explicit boilerplate hints (old rule), OR
  //  (b) the preamble is >= 250 chars AND references the company by
  //      name/brand-like capitalised phrase — a strong signal the intro
  //      isn't role-specific. This catches cases where the LLM's opening
  //      lacks a tell phrase but is still 300 words of corporate intro.
  if (BOILERPLATE_HINT_RE.test(preamble)) return text.slice(cutIdx).trim();
  if (preamble.length >= 250 && /\b(our\s+(company|firm|organization)|the\s+(company|firm|organization)|about\s+us)\b/i.test(preamble)) {
    return text.slice(cutIdx).trim();
  }
  return text;
}

// Human-friendly seniority label. Backend taxonomy is lowercase keys
// ("mid", "senior", "staff"); UI renders "Mid-level", "Senior", etc.
// ─── COUNTRY CODE → FULL NAME ────────────────────────────────────
// Used anywhere we'd otherwise display the raw 2-letter ISO code.
// Unknown codes pass through (better than blanking out) so rare
// countries we haven't hand-named still render.
const COUNTRY_NAMES = {
  US: "United States", CA: "Canada", GB: "United Kingdom", IE: "Ireland",
  DE: "Germany", FR: "France", NL: "Netherlands", ES: "Spain", IT: "Italy",
  SE: "Sweden", NO: "Norway", DK: "Denmark", FI: "Finland", CH: "Switzerland",
  AT: "Austria", BE: "Belgium", PT: "Portugal", PL: "Poland", CZ: "Czechia",
  AU: "Australia", NZ: "New Zealand", SG: "Singapore", JP: "Japan",
  KR: "South Korea", IN: "India", BR: "Brazil", MX: "Mexico",
  AR: "Argentina", IL: "Israel", AE: "UAE", ZA: "South Africa",
};
function countryName(code) {
  if (!code) return "";
  const up = String(code).toUpperCase();
  return COUNTRY_NAMES[up] || up;
}

// ─── YEARS-OF-EXPERIENCE EXTRACTOR ───────────────────────────────
// Runs on the JD body to surface "requires X years" as a chip on
// the match card. Patterns it catches:
//   "5+ years of experience"      → 5+
//   "5-7 years"                   → 5-7
//   "minimum 3 years"             → 3+
//   "at least 8 years"            → 8+
// Returns a short display string or "" if nothing confident.
function extractYoE(text) {
  if (!text || typeof text !== "string") return "";
  // Range first (5-7 years) so we don't match just "5" in "5-7".
  const mRange = text.match(/\b(\d{1,2})\s*[-–to]+\s*(\d{1,2})\s+years?\b/i);
  if (mRange) return `${mRange[1]}-${mRange[2]} yrs`;
  // "5+ years" or "at least 5 years" or "minimum 5 years"
  const mPlus = text.match(/\b(?:at\s+least|minimum(?:\s+of)?|min\.?)\s+(\d{1,2})\s+years?\b/i);
  if (mPlus) return `${mPlus[1]}+ yrs`;
  const mPlus2 = text.match(/\b(\d{1,2})\+\s+years?\b/i);
  if (mPlus2) return `${mPlus2[1]}+ yrs`;
  // Bare "X years of experience"
  const mBare = text.match(/\b(\d{1,2})\s+years?\s+(?:of\s+)?(?:experience|exp\b|industry)/i);
  if (mBare) {
    const n = parseInt(mBare[1], 10);
    if (n >= 1 && n <= 25) return `${n}+ yrs`;
  }
  return "";
}

function prettySeniority(raw) {
  if (!raw) return "";
  const s = String(raw).trim().toLowerCase();
  const map = {
    "intern": "Intern", "internship": "Intern",
    "entry": "Entry-level", "entry-level": "Entry-level", "entry level": "Entry-level",
    "junior": "Entry-level", "new grad": "Entry-level", "new-grad": "Entry-level",
    "graduate": "Entry-level", "associate": "Entry-level", "apm": "APM",
    "mid": "Mid-level", "mid-level": "Mid-level", "mid level": "Mid-level",
    "senior": "Senior-level", "sr": "Senior-level", "lead": "Lead",
    "staff": "Staff", "principal": "Principal",
    "director": "Director", "head of": "Head of",
    "vp": "VP", "cxo": "Chief",
  };
  return map[s] || (raw.charAt(0).toUpperCase() + raw.slice(1));
}

// Role archetype label. Backend emits a slug (pm / tpm / platform_pm /
// ai_pm / ops_pm / growth_pm / director / other / unclassified) from
// sentinel/agents/archetype.py ARCHETYPES. If the dict there changes,
// add a short label here and the chip updates.
function prettyArchetype(raw) {
  if (!raw) return "";
  const s = String(raw).trim().toLowerCase();
  const map = {
    "pm": "Core PM",
    "tpm": "TPM",
    "platform_pm": "Platform PM",
    "ai_pm": "AI PM",
    "ops_pm": "Product Ops",
    "growth_pm": "Growth PM",
    "director": "Director+",
    "other": "Adjacent",
    "unclassified": "", // hide chip when we couldn't classify
  };
  if (map[s] !== undefined) return map[s];
  // Unknown slug (e.g. newly added bucket the UI doesn't know yet) →
  // title-case the slug so we never show raw snake_case to the user.
  return s.split("_").map(w => w ? w[0].toUpperCase() + w.slice(1) : w).join(" ");
}

// Compensation strings from ATSes are a mess: "The United States base range
// for this position is $147,595–$210,850 USD, plus equity. The benefits..."
// Pull just the numeric range (or single number) for the column/chip. Return
// { base, extras } where `extras` is a short tag like "+ equity" when we
// spot it in the remainder. Nothing found → { base: "", extras: "" } and
// callers can fall back to the raw string.
function prettySalary(raw) {
  if (!raw) return { base: "", extras: "" };
  const s = String(raw).replace(/\s+/g, " ").trim();
  // Money token: $147,595 | $147595 | $210k | $1.2M | 147000 etc.
  const MONEY = "\\$?\\s*[\\d]{1,3}(?:[,\\d]{0,10})?(?:\\.\\d+)?\\s*[kKmM]?";
  // Range with en-dash, em-dash, hyphen, "to", or "-".
  const rangeRe = new RegExp(
    "(" + MONEY + ")\\s*(?:[–—\\-]|to)\\s*(" + MONEY + ")",
    "i"
  );
  const rangeMatch = s.match(rangeRe);
  let base = "";
  if (rangeMatch) {
    const low = rangeMatch[1].trim();
    const high = rangeMatch[2].trim();
    // Ensure both sides have a $; ATS strings often only prefix the first.
    const lowFmt = low.startsWith("$") ? low : "$" + low.replace(/^\$?\s*/, "");
    const highFmt = high.startsWith("$") ? high : "$" + high.replace(/^\$?\s*/, "");
    base = `${lowFmt}–${highFmt}`;
  } else {
    // Single value fallback.
    const singleRe = new RegExp("(" + MONEY + ")", "i");
    const single = s.match(singleRe);
    if (single && /\d/.test(single[1])) {
      const v = single[1].trim();
      base = v.startsWith("$") ? v : "$" + v.replace(/^\$?\s*/, "");
    }
  }
  if (!base) return { base: "", extras: "" };
  // Collapse internal whitespace in the number.
  base = base.replace(/\s+/g, "");
  // Detect common compensation extras in the remainder.
  const extrasBits = [];
  if (/\bequity\b/i.test(s)) extrasBits.push("equity");
  if (/\bbonus\b/i.test(s)) extrasBits.push("bonus");
  if (/\brsus?\b/i.test(s)) extrasBits.push("RSUs");
  if (/\bstock\b/i.test(s) && !/\brsus?\b/i.test(s)) extrasBits.push("stock");
  if (/\bbenefits?\b/i.test(s) && extrasBits.length === 0) extrasBits.push("benefits");
  const extras = extrasBits.length ? "+ " + extrasBits.join(" + ") : "";
  return { base, extras };
}

function formatJobText(raw) {
  if (!raw) return [];
  const BULLET_RE = /^\s*(?:[•●▪►■□○◦·]|[-*+])\s+/;
  const cleaned = stripCompanyBoilerplate(stripHtml(raw))
    .replace(/\r\n/g, "\n")
    .replace(/\u00a0/g, " ")
    // Double-spaces are a tell-tale of HTML-stripped content - turn runs
    // of 3+ into a sentence break so wall-of-text inputs get paragraphs.
    .replace(/ {3,}/g, "\n\n")
    // Collapse 3+ newlines (DOM walk can emit stacks) to a single
    // paragraph break.
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  // Split on 1+ blank lines. If that gives us only one "block", fall
  // back to sentence grouping below.
  let blocks = cleaned.split(/\n\s*\n+/).map(b => b.trim()).filter(Boolean);
  if (blocks.length === 1 && cleaned.length > 500) {
    // Sentence-group fallback: split on ". " or "! " or "? " followed
    // by a capital, group every 2-3 sentences into a paragraph.
    const sentences = cleaned.split(/(?<=[.!?])\s+(?=[A-Z])/);
    blocks = [];
    for (let i = 0; i < sentences.length; i += 3) {
      blocks.push(sentences.slice(i, i + 3).join(" "));
    }
  }

  return blocks.map(block => {
    const lines = block.split(/\n/).map(l => l.trim()).filter(Boolean);
    const bulletLines = lines.filter(l => BULLET_RE.test(l));
    if (bulletLines.length >= 2 && bulletLines.length >= lines.length * 0.6) {
      return { kind: "ul", items: lines.map(l => l.replace(BULLET_RE, "").trim()) };
    }
    return { kind: "p", text: lines.join(" ") };
  });
}

// Split a paragraph string into renderable runs: inline-bold for
// `**foo**` and `ALL-CAPS:` headers, plain text for the rest.
function formatInline(text) {
  if (!text) return [];
  // Handle **bold** first so the header regex doesn't swallow it.
  const parts = [];
  const boldRe = /\*\*([^*]+)\*\*/g;
  let lastIdx = 0; let match;
  while ((match = boldRe.exec(text))) {
    if (match.index > lastIdx) parts.push({ t: text.slice(lastIdx, match.index) });
    parts.push({ t: match[1], bold: true });
    lastIdx = match.index + match[0].length;
  }
  if (lastIdx < text.length) parts.push({ t: text.slice(lastIdx) });
  // Second pass: ALL-CAPS headers ending with ":" (e.g. "REQUIREMENTS:").
  const out = [];
  const HEADER_RE = /\b([A-Z][A-Z &/]{2,}:)/g;
  parts.forEach(part => {
    if (part.bold) { out.push(part); return; }
    let s = part.t; let idx = 0; let m;
    while ((m = HEADER_RE.exec(s))) {
      if (m.index > idx) out.push({ t: s.slice(idx, m.index) });
      out.push({ t: m[1], bold: true });
      idx = m.index + m[0].length;
    }
    if (idx < s.length) out.push({ t: s.slice(idx) });
  });
  return out;
}

// Render a formatted JD. `text` is the raw string, `theme` is passed so
// the component can pick colours - keeps it portable across the
// inspector panel, the triage card expansion, etc.
function FormattedJobText({ text, theme }) {
  const t = theme;
  const blocks = useMemo(() => formatJobText(text), [text]);
  if (!blocks.length) return null;
  return (
    <div style={{ fontSize: "13px", lineHeight: 1.65, color: t.textMid }}>
      {blocks.map((b, i) => b.kind === "ul" ? (
        <ul key={i} style={{ margin: "0 0 12px 18px", padding: 0 }}>
          {b.items.map((item, j) => (
            <li key={j} style={{ marginBottom: "4px" }}>
              {formatInline(item).map((r, k) => r.bold
                ? <strong key={k} style={{ color: t.text }}>{r.t}</strong>
                : <span key={k}>{r.t}</span>)}
            </li>
          ))}
        </ul>
      ) : (
        <p key={i} style={{ margin: "0 0 12px 0" }}>
          {formatInline(b.text).map((r, k) => r.bold
            ? <strong key={k} style={{ color: t.text }}>{r.t}</strong>
            : <span key={k}>{r.t}</span>)}
        </p>
      ))}
    </div>
  );
}

// Maps the accountability-pet mood (derived from hunger timer) to a
// backend saying bucket. Keeps sayings context-appropriate without the
// component caring how mood is computed.
function moodToSayingBucket(mood) {
  if (mood === "ECSTATIC") return "match_found";
  if (mood === "NEW" || mood === "HUNGRY" || mood === "STARVING") return "encourage";
  return "idle";
}

// ─── HELPERS ─────────────────────────────────────────────────────
const buildCompanyChart = (m, mk) => {
  const c = {}; m.forEach(j => { c[j.company] = (c[j.company] || 0) + 1; });
  if (mk?.length) Object.entries(mk[mk.length - 1]?.company_volume || {}).forEach(([k, v]) => { if (!c[k]) c[k] = v; });
  return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([company, count]) => ({ company, count }));
};
const buildRemoteChart = mk => {
  if (!mk?.length) return [{ n: "Remote", v: 42 }, { n: "Hybrid", v: 31 }, { n: "Onsite", v: 27 }];
  const w = mk[mk.length - 1]?.work_model || {};
  return [{ n: "Remote", v: w.remote || 0 }, { n: "Hybrid", v: w.hybrid || 0 }, { n: "Onsite", v: (w.onsite || 0) + (w.unknown || 0) }].filter(d => d.v > 0);
};
const reactionKey = (title, company) => `${(title || "").trim().toLowerCase()}||${(company || "").trim().toLowerCase()}`;
// Display-only score. Backend emits _match_score_display as a calibrated
// percentage (piecewise-linear stretch of the raw cosine) so the UI can
// show a useful 5-98% spread instead of everything bunched around 50%.
// Falls back to the raw _match_score for back-compat with existing
// registry rows that predate calibration.
const displayScoreOf = m => (m && (m._match_score_display ?? m._match_score ?? m._score)) || 0;
const formatSeconds = s => {
  if (s == null) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  const m = Math.floor(s / 60); const r = Math.round(s % 60);
  return `${m}m ${r}s`;
};
const formatMs = ms => (ms == null ? "—" : ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(1)}s`);

// ─── COMPONENTS ──────────────────────────────────────────────────
const Tip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return <div style={{ background: "#1a1816", color: "#faf8f5", borderRadius: "4px", padding: "8px 12px", fontSize: "12px", boxShadow: "0 4px 12px rgba(0,0,0,0.2)" }}>
    <div style={{ fontWeight: 600, marginBottom: "2px" }}>{label}</div>
    {payload.map((p, i) => <div key={i} style={{ color: "#d4c5b0" }}>{p.name}: {p.value}</div>)}
  </div>;
};

const PIE_C_LIGHT = ["#c44d2a", "#5b7a5e", "#8a7e72"];
const PIE_C_DARK = ["#e0683e", "#7aaa7e", "#7a7168"];

// Derive the four provenance-aware tier cutoffs from a single headline
// threshold. Mirrors agents/match.py so the UI shows the same numbers the
// backend will actually apply. Kept as a standalone function so both the
// Settings and Wizard explainers can reuse it without prop drilling.
function deriveTierCutoffs(threshold) {
  const clamp = v => Math.max(0, Math.min(1, v));
  return {
    embed: { match: clamp(threshold + 0.05), maybe: clamp(threshold - 0.10) },
    llm:   { match: clamp(threshold + 0.10), maybe: clamp(threshold) },
  };
}

// ThresholdExplainer renders a worked example showing how the headline
// threshold turns into the Match and Maybe cutoffs, and classifies a
// synthetic job against them. Used in both Settings and the Wizard so the
// user sees the same numbers in both places. Receives `theme` because this
// component lives above the Sentinel component and can't read the theme
// object from the outer scope.
function ThresholdExplainer({ threshold, salaryFloor, yearsExp, salaryWeight, yearsWeight, matchModel, embedModel, theme }) {
  const t = theme;
  const cutoffs = deriveTierCutoffs(threshold);
  // Synthetic worked example: a posting that's close to the line so the
  // adjustments actually change the tier. Numbers chosen to feel realistic,
  // not cherry-picked to tip a particular way.
  const baseSimilarity = 0.62;
  const postingSalary = salaryFloor > 0 ? Math.round(salaryFloor * 1.08) : 165000;
  const postingYears = Math.max(0, (yearsExp || 0) - 1);
  const salaryHit = salaryFloor > 0 && postingSalary >= salaryFloor ? (salaryWeight || 0) : 0;
  const yearsHit = yearsExp > 0 && postingYears >= yearsExp - 2 ? 0 : -(yearsWeight || 0);
  const adjusted = Math.max(0, Math.min(1, baseSimilarity + salaryHit + yearsHit));
  const provLabel = embedModel ? "embeddings" : "LLM";
  const provKey = embedModel ? "embed" : "llm";
  const cut = cutoffs[provKey];
  const tier = adjusted >= cut.match ? "MATCH" : adjusted >= cut.maybe ? "MAYBE" : "DROPPED";
  const tierColor = tier === "MATCH" ? t.accent : tier === "MAYBE" ? (t.warn || "#b88a2e") : t.textFaint;
  const modelLabel = embedModel ? `embeddings (${embedModel})` : `${matchModel || "LLM"}`;
  const pct = v => `${(v * 100).toFixed(0)}%`;
  const signed = v => (v >= 0 ? "+" : "") + (v * 100).toFixed(0) + "%";
  return (
    <div style={{ marginTop: "12px", background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "14px 16px" }}>
      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "10px" }}>
        Worked example at {pct(threshold)}
      </div>
      <div style={{ fontSize: "12px", color: t.textMid, lineHeight: 1.6, marginBottom: "10px" }}>
        Scoring via <span style={{ color: t.text, fontWeight: 600 }}>{modelLabel}</span>. Match and Maybe cutoffs are derived from the headline number: embeddings cluster tight so the split is narrower, {`LLMs`} spread out so the split is wider.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "14px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px" }}>
        <div style={{ padding: "8px 10px", background: t.bg, borderRadius: "3px", border: `1px solid ${t.border}` }}>
          <div style={{ color: t.textDim, marginBottom: "4px" }}>EMBEDDINGS PATH</div>
          <div style={{ color: t.text }}>Match ≥ {pct(cutoffs.embed.match)}</div>
          <div style={{ color: t.textMid }}>Maybe ≥ {pct(cutoffs.embed.maybe)}</div>
        </div>
        <div style={{ padding: "8px 10px", background: t.bg, borderRadius: "3px", border: `1px solid ${t.border}` }}>
          <div style={{ color: t.textDim, marginBottom: "4px" }}>LLM PATH</div>
          <div style={{ color: t.text }}>Match ≥ {pct(cutoffs.llm.match)}</div>
          <div style={{ color: t.textMid }}>Maybe ≥ {pct(cutoffs.llm.maybe)}</div>
        </div>
      </div>
      <div style={{ fontSize: "12px", color: t.textMid, lineHeight: 1.7 }}>
        <div style={{ color: t.textDim, fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1px", textTransform: "uppercase", marginBottom: "6px" }}>Sample posting</div>
        <div>a. Base similarity to your resume: <span style={{ color: t.text, fontWeight: 600 }}>{pct(baseSimilarity)}</span></div>
        <div>b. Salary adjustment ({postingSalary >= (salaryFloor || 0) ? "above" : "below"} your floor): <span style={{ color: t.text, fontWeight: 600 }}>{signed(salaryHit)}</span></div>
        <div>c. Experience adjustment (posting wants {postingYears} years, you have {yearsExp || 0}): <span style={{ color: t.text, fontWeight: 600 }}>{signed(yearsHit)}</span></div>
        <div style={{ marginTop: "4px", paddingTop: "6px", borderTop: `1px dashed ${t.border}` }}>
          d. Adjusted total: <span style={{ color: t.text, fontWeight: 600 }}>{pct(adjusted)}</span>
          <span style={{ marginLeft: "10px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", padding: "2px 8px", background: t.accentBg, color: tierColor, borderRadius: "3px", fontWeight: 700, letterSpacing: "1px" }}>{tier}</span>
        </div>
      </div>
    </div>
  );
}

export default function Sentinel() {
  const [view, setView] = useState("brief");
  // Theme: honour the OS prefers-color-scheme on first mount, dark by
  // default when the user has no preference. The manual toggle still
  // works; we just stop overriding the OS hint at launch.
  const [isDark, setIsDark] = useState(() => {
    try {
      const mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)");
      if (mq && mq.matches) return false;
    } catch { /* matchMedia unavailable - fall through to dark default */ }
    return true;
  });
  const [live, setLive] = useState(false);
  const [pipelineRunning, setPipelineRunning] = useState(false);

  // ─── SOUND FX (Halo-style kill-streak feedback) ──────────────────
  // Synthesised via Web Audio so there are no binary assets to ship.
  // Kept inside the component so the AudioContext is lazily allocated
  // (Chrome blocks it until the first user gesture) and automatically
  // garbage-collected when the dashboard tab closes. Respects a
  // localStorage mute flag so the user can kill it entirely from
  // DevTools: `localStorage.setItem('sentinel.mute', '1')`.
  const audioCtxRef = useRef(null);
  const [soundOn, setSoundOn] = useState(() => {
    try { return localStorage.getItem("sentinel.mute") !== "1"; }
    catch { return true; }
  });
  const getAudio = () => {
    if (typeof window === "undefined") return null;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;
    if (!audioCtxRef.current) audioCtxRef.current = new Ctor();
    // Suspended contexts (tab was backgrounded) need resume() to play.
    if (audioCtxRef.current.state === "suspended") {
      audioCtxRef.current.resume().catch(() => {});
    }
    return audioCtxRef.current;
  };
  // Tiny polyphonic synth: schedules N oscillator notes over `dur` with
  // an ADSR-ish envelope so quick keep/skip blips don't click. `waveform`
  // picks the timbre - triangle for soft decisions, square for combo.
  const playTone = (freqs, dur = 0.12, waveform = "triangle", vol = 0.18) => {
    const ctx = getAudio();
    if (!ctx || !soundOn) return;
    const now = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.setValueAtTime(0, now);
    master.gain.linearRampToValueAtTime(vol, now + 0.01);
    master.gain.exponentialRampToValueAtTime(0.0001, now + dur);
    master.connect(ctx.destination);
    (Array.isArray(freqs) ? freqs : [freqs]).forEach((f, i) => {
      const o = ctx.createOscillator();
      o.type = waveform;
      // Slight stagger on chords so polyphony has a strum feel instead
      // of slabby unison that reads as a square wave.
      const t0 = now + i * 0.012;
      o.frequency.setValueAtTime(f, t0);
      o.connect(master);
      o.start(t0);
      o.stop(t0 + dur);
    });
  };
  // Combo-tier announcement: arpeggio + sustained chord. Scales in
  // duration and pitch with tier. Tier 0 is a no-op guard.
  const playComboFanfare = (tier) => {
    if (!tier || tier < 2) return;
    // Base in C major pentatonic; higher tier, brighter + more notes.
    const roots = { 2: 523, 3: 587, 5: 659, 7: 740, 10: 880, 15: 988, 20: 1046, 30: 1175 };
    const root = roots[tier] || (440 + tier * 20);
    // Arpeggio sweep
    const arp = [root, root * 1.25, root * 1.5, root * 2];
    arp.forEach((f, i) => setTimeout(
      () => playTone(f, 0.18, "square", 0.12), i * 60
    ));
    // Sustained chord after the sweep
    setTimeout(() => playTone(
      [root, root * 1.25, root * 1.5], 0.42, "triangle", 0.16
    ), arp.length * 60 + 40);
  };
  // Quick-action clicks for keep/skip. Two-note blip so it's satisfying
  // without being loud. Keep = rising (yes), skip = falling (no).
  const playComboSound = (kind) => {
    if (kind === "keep") {
      playTone(880, 0.05, "triangle", 0.09);
      setTimeout(() => playTone(1175, 0.06, "triangle", 0.09), 35);
    } else if (kind === "skip") {
      playTone(440, 0.05, "triangle", 0.07);
      setTimeout(() => playTone(370, 0.06, "triangle", 0.07), 35);
    } else if (kind === "match") {
      // Three-note ascending "new mail" jingle for a fresh match.
      [659, 784, 988].forEach((f, i) => setTimeout(
        () => playTone(f, 0.14, "triangle", 0.14), i * 80
      ));
    }
  };
  // Mute toggle persisted in localStorage. Exposed via window so the
  // user can hit it from DevTools without us adding a settings page
  // for a single boolean.
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.__sentinelSetSound = (on) => {
      setSoundOn(!!on);
      try { localStorage.setItem("sentinel.mute", on ? "0" : "1"); } catch {}
    };
    return () => { delete window.__sentinelSetSound; };
  }, []);
  const [selectedJob, setSelectedJob] = useState(null);
  const [ready, setReady] = useState(false);

  // Initialise empty. We used to seed these from D (demo data) so the app
  // looked populated on first open, but that caused the Command Center to
  // show fake Stripe/Cloudflare rows even when the pipeline was LIVE with
  // no real matches yet. Now we reflect reality: empty until the backend
  // says otherwise, with a demo fallback only when not live (see poll()).
  const [matches, setMatches] = useState([]);
  // Story bank markdown content + metadata. Fetched on demand when the
  // Stories tab is opened. Backend: GET /api/story-bank returns
  // { path, exists, text, size_bytes }.
  const [storyBank, setStoryBank] = useState({ text: "", path: "", exists: false, loading: false });
  // Matches-tab filters. Defaults are efficiency-driven: hide dismissed
  // (the whole point of the feature), show everything else. A time-window
  // filter kicks in only when the registry gets large enough to matter.
  const [matchFilters, setMatchFilters] = useState({
    showDismissed: false,
    starredOnly: false,
    unseenOnly: false,
    windowDays: 0,         // 0 = all time; set to 14 when registry > 1000
    // null = no archetype filter (show all). Any archetype slug from
    // the backend (pm / tpm / ai_pm / ...) to limit the list to that
    // bucket. Drives the archetype chip row below the main filters.
    archetype: null,
  });
  // Column sort state for the Matches table. Default is score desc (the
  // server already returns that, so no extra work). Clicking a header
  // cycles desc -> asc -> back to desc. Pinned rows stick to the top
  // regardless of sort.
  const [matchSort, setMatchSort] = useState({ key: "score", dir: "desc" });
  const [fitGaps, setFitGaps] = useState([]);
  const [decisions, setDecisions] = useState({ decisions: [], reactions: {} });
  // Resource snapshot for the Brief-tab panel. Refetched on Brief entry
  // and every 30s while that tab is open - the underlying data (cycle
  // times, GPU probe) changes on that cadence, so the poll is cheap.
  const [resources, setResources] = useState(null);
  const [market, setMarket] = useState([]);
  const [tier1, setTier1] = useState(null);
  const [tier2, setTier2] = useState(null);
  const [digests, setDigests] = useState([]);
  const [config, setConfig] = useState(D.config);
  const [expandedGap, setExpandedGap] = useState(0);
  const [status, setStatus] = useState({});
  // Mirror of status readable inside the polling loop without restarting
  // the recursive setTimeout every time status changes (which would cause
  // the interval to reset each render).
  const statusRef = useRef({});
  // Hydrate-once guards. /api/config, /api/resume, /api/setup-state, and
  // /api/decisions are user-driven state that ONLY this UI mutates —
  // polling them every 2 s clobbers in-flight typing in Settings/Profile
  // inputs. Hydrate once at boot, then trust local state until a save
  // (which already updates locally) or a manual refresh refetches.
  const configHydratedRef = useRef(false);
  const resumeHydratedRef = useRef(false);
  const setupHydratedRef = useRef(false);
  const decisionsHydratedRef = useRef(false);
  // Cycle-end transition detector. We poll /api/matches /fit-gaps
  // /market /digests only while a cycle is running; on the falling
  // edge of cycle_in_progress we pull one more time so the final
  // state lands, then go quiet.
  const wasInProgressRef = useRef(false);

  // Settings state
  const [keywords, setKeywords] = useState("");
  const [threshold, setThreshold] = useState(0.55);
  const [parseModel, setParseModel] = useState("qwen3:8b");
  const [matchModel, setMatchModel] = useState("qwen3:8b");
  // Work mode is now a set: any combination of remote / hybrid / onsite.
  // All three on (the default) = "any location whatsoever". This replaced
  // the old allow_remote / remote_only boolean pair which couldn't express
  // "hybrid OK, fully onsite not OK".
  const [workModes, setWorkModes] = useState(["remote", "hybrid", "onsite"]);
  const [allowedLocations, setAllowedLocations] = useState("");
  const [blockedLocations, setBlockedLocations] = useState("");
  // Geographic pin filter — array of [lat, lon] points. A job passes
  // the filter if it's within `locationRadiusKm` of ANY pin (union, not
  // intersection — so SF + NYC + London at 50 km each gives you all
  // three areas). Hard filter: persisted to preferences and applied
  // server-side during the match stage. The client also re-applies on
  // the matches table so changes take effect without a re-cycle.
  const [locationPins, setLocationPins] = useState([]); // [[lat, lon], ...]
  const [locationRadiusKm, setLocationRadiusKm] = useState(50);
  // Country hard-filter. Multi-select ISO-2 codes. If non-empty,
  // any job NOT classified as one of these gets dropped pre-scoring.
  // strictUnknownCountry=true also drops jobs we can't classify at all.
  const [allowedCountries, setAllowedCountries] = useState(["US", "IE"]);
  const [strictUnknownCountry, setStrictUnknownCountry] = useState(true);
  const [allowRemoteAnyCountry, setAllowRemoteAnyCountry] = useState(true);
  // Pipeline cadence. Expressed in minutes; backend clamps 5-240.
  const [cycleInterval, setCycleInterval] = useState(30);
  const [salaryFloor, setSalaryFloor] = useState(0);
  const [salaryWeight, setSalaryWeight] = useState(0.15);
  // Experience preferences (mirrors the wizard). Drives the ExperienceFilter
  // + ExperienceScorer on the backend. Empty/zero defaults keep it inactive
  // until the user sets them.
  const [yearsExperience, setYearsExperience] = useState(0);
  const [currentLevel, setCurrentLevel] = useState("");
  const [yearsWeight, setYearsWeight] = useState(0.04);
  const [trapdoorEnabled, setTrapdoorEnabled] = useState(true);
  const [fakeAggressiveness, setFakeAggressiveness] = useState("balanced"); // low | balanced | strict
  // Ghost-score fold controls. ghost_weight scales the penalty applied to
  // the match score when a posting looks suspicious; 0 disables the fold
  // entirely (legacy "raw fit" scoring). flag/warn thresholds drive the
  // badge bands in the Matches table. All three are hot-applied via
  // /api/config → fake_detection.* (see server.py / MatchAgent setters).
  const [ghostWeight, setGhostWeight] = useState(0.35);
  const [ghostFlagThreshold, setGhostFlagThreshold] = useState(0.45);
  const [ghostWarnThreshold, setGhostWarnThreshold] = useState(0.30);
  // Scrape targets. Tenant lists + big-tech toggles. All state mirrors
  // config.ingest.* and posts back through /api/config under {ingest:{...}}.
  // greenhouseCompanies / leverCompanies: string[] of slugs.
  // ashbyCompanies: [display, slug][] (Ashby's public board uses a slug but
  // we preserve the display casing for the UI).
  const [greenhouseCompanies, setGreenhouseCompanies] = useState([]);
  const [leverCompanies, setLeverCompanies] = useState([]);
  const [ashbyCompanies, setAshbyCompanies] = useState([]);
  const [newGreenhouseSlug, setNewGreenhouseSlug] = useState("");
  const [newLeverSlug, setNewLeverSlug] = useState("");
  const [newAshbyDisplay, setNewAshbyDisplay] = useState("");
  const [newAshbySlug, setNewAshbySlug] = useState("");
  // Per-slug test results. Keyed by `${kind}:${slug}` → { ok, jobs_found,
  // sample_title, error, status_code, ts }.
  const [tenantTests, setTenantTests] = useState({});
  const [tenantTestBusy, setTenantTestBusy] = useState({}); // {key: true}
  // Big-tech toggles. Tier classification:
  //   FAST (plain HTTP, runs every cycle): Amazon, Google, Nvidia,
  //     Tesla, Adobe, Salesforce, Oracle, IBM, Cisco, Intel — all
  //     either Workday-based or have static job-listing JSON endpoints.
  //   SLOW (Playwright, runs only on Run Scraper): Apple, Meta,
  //     Microsoft, Netflix, LinkedIn — SPA / anti-bot / login walls.
  const [enableApple, setEnableApple] = useState(true);
  const [enableAmazon, setEnableAmazon] = useState(true);
  const [enableGoogle, setEnableGoogle] = useState(true);
  const [enableMeta, setEnableMeta] = useState(true);
  const [enableMicrosoft, setEnableMicrosoft] = useState(true);
  const [enableNetflix, setEnableNetflix] = useState(true);
  const [enableNvidia, setEnableNvidia] = useState(true);
  const [enableTesla, setEnableTesla] = useState(true);
  const [enableLinkedin, setEnableLinkedin] = useState(true);
  const [enableAdobe, setEnableAdobe] = useState(true);
  const [enableSalesforce, setEnableSalesforce] = useState(true);
  const [enableOracle, setEnableOracle] = useState(true);
  const [enableIbm, setEnableIbm] = useState(true);
  const [enableCisco, setEnableCisco] = useState(true);
  const [enableIntel, setEnableIntel] = useState(true);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportMsg, setExportMsg] = useState("");
  // Surfaced when Run Pipeline / Run Scraper are rejected (cycle in
  // progress, setup not complete, backend down). Auto-clears in 6s.
  const [runMsg, setRunMsg] = useState("");
  const [settingsSaved, setSettingsSaved] = useState(false);
  // Tailor-resume button state. Keyed by match url so two concurrent
  // clicks on different roles don't overwrite each other's status.
  // Shape: { [url]: { busy: bool, path?: string, error?: string } }
  const [tailorState, setTailorState] = useState({});

  // Resume state
  const [resumeState, setResumeState] = useState({ has_resume: false, metadata: {}, additional_notes_len: 0 });
  const [additionalNotes, setAdditionalNotes] = useState("");
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeMsg, setResumeMsg] = useState("");
  const [resumeProfile, setResumeProfile] = useState(null);
  const [reparseBusy, setReparseBusy] = useState(false);
  const fileInputRef = useRef(null);

  // Wizard
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardDismissed, setWizardDismissed] = useState(false);
  // Setup state from /api/setup-state. Drives the wizard auto-open and
  // gates the Run Pipeline button on fresh installs. Returning users
  // with setup_completed=true never see the modal again.
  // `loaded` guards the wizard auto-open from racing the first poll.
  // Without it, setup_completed defaults to false while the fetch is in
  // flight, the auto-open effect fires on that render, and the wizard
  // pops up even when user.json says setup is complete.
  const [setupState, setSetupState] = useState({ setup_completed: false, user: {}, loaded: false });
  // Preflight + prewarm snapshots. Populated via /api/preflight and
  // /api/prewarm when the wizard is visible so the user gets real-time
  // ticks/crosses while they fill in identity.
  const [preflight, setPreflight] = useState(null);
  const [prewarm, setPrewarm] = useState(null);

  // Chat - now a bottom-docked persistent drawer. History hydrates from
  // localStorage so conversations survive reloads (capped at 100 turns to
  // keep payloads sane on the /api/chat side). chatOpen toggles the drawer.
  const [chatMessages, setChatMessages] = useState(() => {
    try {
      const raw = localStorage.getItem("sentinel.chatMessages");
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.slice(-100) : [];
    } catch { return []; }
  });
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const chatScrollRef = useRef(null);

  // "Why this match?" rationale cache. Keyed by `${company}||${title}||${url}`
  // so switching between jobs in the detail panel never shows the wrong
  // rationale. A single rationaleBusy flag is fine - only one in flight
  // at a time since the UI only shows one detail panel.
  const [rationales, setRationales] = useState({});
  const [rationaleBusy, setRationaleBusy] = useState(false);

  // Cover letters keyed by match identity. Keeping them keyed means
  // flipping between jobs doesn't wipe a draft the user hasn't saved
  // and a regenerate starts from the previous version the user saw.
  const [coverLetters, setCoverLetters] = useState({}); // key -> { text, saved_to, tone }
  const [coverLetterBusy, setCoverLetterBusy] = useState(false);
  const [coverLetterError, setCoverLetterError] = useState("");
  const [coverLetterTone, setCoverLetterTone] = useState("professional");
  const [coverLetterNote, setCoverLetterNote] = useState("");
  const [coverLetterCopied, setCoverLetterCopied] = useState(false);
  const [rationaleError, setRationaleError] = useState("");

  // History tab: cycle timeline + live log tail. Both polled only while
  // the tab is open so the default Brief view doesn't pay for them.
  const [cycleHistory, setCycleHistory] = useState([]);
  const [logs, setLogs] = useState({ available: false, lines: [] });
  const [logLevel, setLogLevel] = useState("INFO");
  const [logBusy, setLogBusy] = useState(false);

  // Triage tab: keyboard-driven keep/skip queue. We cursor through
  // un-reacted matches one at a time. "Maybe" just advances without
  // writing a reaction - users can revisit the job later.
  const [triageIndex, setTriageIndex] = useState(0);
  const [triageLearned, setTriageLearned] = useState({ samples: { keeps: 0, skips: 0 }, suggestions: [] });
  // Blitz session stats - live scoreboard. Resets on page reload so the
  // dopamine is per-session, not cumulative across days.
  const [blitzStats, setBlitzStats] = useState({
    keeps: 0, skips: 0, maybes: 0,
    streak: 0, bestStreak: 0,
    lastDecisionAt: 0,     // ms timestamp of last action, for avg-time calc
    totalDecisionMs: 0,    // sum of gaps between consecutive decisions
    decisionCount: 0,      // denominator for avg time
  });
  // Last action for the slot-machine card flash: "keep" | "skip" | "maybe".
  // Cleared after the animation ends so the next action retriggers cleanly.
  const [blitzFlash, setBlitzFlash] = useState(null);
  // Pip: tiny accountability pet that lives in the Blitz sidebar. Gets
  // fed on every keep + skip (committed decision). Maybe is neutral -
  // Pip is principled. State persists so when you come back after a week
  // of not applying, Pip is visibly starving. Guilt as a feature.
  const [pip, setPip] = useState(() => {
    try {
      const raw = localStorage.getItem("sentinel.pip");
      if (!raw) return { lastFedAt: 0, totalFeeds: 0 };
      const parsed = JSON.parse(raw);
      return {
        lastFedAt: Number(parsed.lastFedAt) || 0,
        totalFeeds: Number(parsed.totalFeeds) || 0,
      };
    } catch { return { lastFedAt: 0, totalFeeds: 0 }; }
  });
  // Tick once a minute so "last fed 3 min ago" stays current without a
  // render storm. 60s granularity is plenty for a hunger timer.
  const [pipNow, setPipNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setPipNow(Date.now()), 60000);
    return () => clearInterval(id);
  }, []);
  // Pulse flag for the feed animation - flips briefly when Pip eats.
  const [pipBounce, setPipBounce] = useState(0);

  // ─── HELPER (Joby et al) ─────────────────────────────────────
  // The cosmetic companion. Fetches the user's chosen sprite + metadata
  // from the backend. Sprite is an animated GIF served out of
  // sentinel-ui/public/sprites/ so looping is browser-native - no JS
  // frame logic needed. Saying rotates every 9s from a backend-provided
  // catalogue, picked by the current hunger-mood so the line feels
  // contextually aware.
  //
  // We seed `helper` with a default Joby manifest so the sprite renders
  // instantly on first paint, even if the backend is still booting or
  // unreachable. The dev launcher opens the browser ~2s before the
  // Python server is listening, so a one-shot fetch on mount races and
  // loses; the backend is then hit with exponential backoff until it
  // answers. Known sprite filenames are duplicated here from the
  // backend manifest - kept in sync manually, low churn.
  const HELPER_FALLBACK = {
    name: "Joby",
    sprite: "joby",
    label: "Joby",
    eyes: "dots",
    accessory: "none",
    asset_url: "/sprites/joby_idle.gif",
    assets: {
      idle: "/sprites/joby_idle.gif",
      wave: "/sprites/joby_wave.gif",
      bounce: "/sprites/joby_bounce.gif",
      celebrate: "/sprites/joby_celebrate.gif",
      sleep: "/sprites/joby_sleep.gif",
    },
    credit: "built-in default",
  };
  const [helper, setHelper] = useState(HELPER_FALLBACK);
  const [helperSayings, setHelperSayings] = useState({});
  const [helperSayingIdx, setHelperSayingIdx] = useState(0);

  // Retry-with-backoff fetch for /api/helper. First attempt fires
  // immediately; if the backend isn't up (ECONNREFUSED during launcher
  // boot) we back off and retry. Gives up after ~30s total which is
  // well past any realistic warm-up for a local server. Until we get
  // a real answer the fallback manifest above keeps the sprite rendered.
  useEffect(() => {
    let cancelled = false;
    const delays = [0, 400, 800, 1500, 2500, 4000, 6000, 6000, 6000];
    (async () => {
      for (const delay of delays) {
        if (cancelled) return;
        if (delay) await new Promise((r) => setTimeout(r, delay));
        if (cancelled) return;
        try {
          const r = await fetch(`${API}/api/helper`);
          if (!r.ok) continue;
          const d = await r.json();
          if (d && d.asset_url) {
            // Merge: backend wins, but keep fallback fields for anything
            // the backend didn't ship (future-proof against a slim payload).
            setHelper({ ...HELPER_FALLBACK, ...d });
            return;
          }
        } catch { /* keep retrying */ }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Warm the sayings cache for the four buckets we actually use. Same
  // backoff strategy as /api/helper - the initial mount hits during the
  // backend-boot gap so the naive single fetch would leave us with an
  // empty catalogue and stale fallback lines forever.
  useEffect(() => {
    let cancelled = false;
    const moods = ["idle", "match_found", "encourage", "celebrate",
                   "pet_wave", "pet_bounce", "pet_celebrate"];
    const delays = [0, 500, 1500, 3000, 6000, 6000];
    (async () => {
      for (const delay of delays) {
        if (cancelled) return;
        if (delay) await new Promise((r) => setTimeout(r, delay));
        if (cancelled) return;
        const results = await Promise.all(moods.map((m) =>
          fetch(`${API}/api/helper/sayings?mood=${m}`)
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null)
        ));
        const next = {};
        results.forEach((r) => {
          if (r && r.mood && Array.isArray(r.sayings)) next[r.mood] = r.sayings;
        });
        if (Object.keys(next).length === moods.length) {
          setHelperSayings(next);
          return;
        }
        // Partial success still counts - stash what we got and keep trying.
        if (Object.keys(next).length) setHelperSayings(next);
      }
    })();
    return () => { cancelled = true; };
  }, []);
  // Saying rotation. 9s gives the user time to read without feeling
  // stuck on one line. Index is a counter so we can pick len-aware.
  useEffect(() => {
    const id = setInterval(() => setHelperSayingIdx((i) => i + 1), 9000);
    return () => clearInterval(id);
  }, []);

  // ─── HELPER MILESTONES ──────────────────────────────────────
  // One-shot celebration events. When a lifetime counter first crosses
  // a threshold, flash the helper's `celebrate` GIF for ~5s and show a
  // toast. Achieved IDs are stored in localStorage so we never fire
  // twice for the same threshold. List is ordered highest-first so if
  // two unlock in the same tick (rare but possible on a big import)
  // we surface the impressive one.
  const MILESTONES = [
    // keeps
    { id: "keeps_500", threshold: (lt) => lt.keeps >= 500, title: "500 saved", detail: "Half a thousand roles kept. You've built an archive." },
    { id: "keeps_250", threshold: (lt) => lt.keeps >= 250, title: "250 saved", detail: "Quarter of a thousand. Filing cabinet energy." },
    { id: "keeps_100", threshold: (lt) => lt.keeps >= 100, title: "100 saved", detail: "Triple digits. Certified picky." },
    { id: "keeps_50",  threshold: (lt) => lt.keeps >= 50,  title: "50 saved",  detail: "You're a proper shortlist maker now." },
    { id: "keeps_10",  threshold: (lt) => lt.keeps >= 10,  title: "10 saved",  detail: "The shortlist takes shape." },
    { id: "first_keep",threshold: (lt) => lt.keeps >= 1,   title: "First save!", detail: "You saved your first role. The game begins." },
    // streaks
    { id: "streak_30", threshold: (lt) => (lt.bestStreak || 0) >= 30, title: "30-day streak", detail: "A full month of showing up. Ridiculous." },
    { id: "streak_14", threshold: (lt) => (lt.bestStreak || 0) >= 14, title: "14-day streak", detail: "Two weeks unbroken. The habit is real." },
    { id: "streak_7",  threshold: (lt) => (lt.bestStreak || 0) >= 7,  title: "7-day streak",  detail: "A full week of showing up." },
    { id: "streak_3",  threshold: (lt) => (lt.bestStreak || 0) >= 3,  title: "3-day streak",  detail: "Momentum building." },
    // combos
    { id: "combo_10",  threshold: (lt) => (lt.bestCombo  || 0) >= 10, title: "10-combo",      detail: "Ten decisions in under a minute. Focused." },
    { id: "combo_5",   threshold: (lt) => (lt.bestCombo  || 0) >= 5,  title: "5-combo",       detail: "A clean run of five. The flow state is close." },
    // days
    { id: "days_30",   threshold: (lt) => (lt.days?.length || 0) >= 30, title: "30 active days", detail: "A month's worth of triage sessions logged." },
    { id: "days_7",    threshold: (lt) => (lt.days?.length || 0) >= 7,  title: "7 active days",  detail: "A week of real use. This is the tool working." },
  ];

  // Current transient helper-state override (null = no override, falls
  // back to mood-derived state). Set to "celebrate" for the duration of
  // a milestone toast. A ref holds the clear-timer so back-to-back
  // unlocks don't cancel each other mid-animation.
  const [helperOverrideState, setHelperOverrideState] = useState(null);
  const [helperMilestone, setHelperMilestone] = useState(null);
  const milestoneTimerRef = useRef(null);

  // ─── PET METER (arrow-key happiness) ─────────────────────────
  // Tier thresholds kept client-side in a constant rather than fetched
  // from /api/helper/options - we want the keydown handler to work even
  // if the backend never answers. Backend is still source-of-truth for
  // sayings; if its saying list isn't loaded yet we fall back to a tiny
  // local pool so the first arrow press is never silent.
  const PET_TIERS = [
    { state: "celebrate", min: 6, mood: "pet_celebrate" },
    { state: "bounce",    min: 3, mood: "pet_bounce" },
    { state: "wave",      min: 1, mood: "pet_wave" },
  ]; // ordered HIGHEST first so find() picks the top matching tier
  const PET_FALLBACK_LINES = {
    pet_wave: ["oh hi", "*wiggles*", "hey you"],
    pet_bounce: ["yes yes yes", "MORE please", "boingy boingy"],
    pet_celebrate: ["AMAZING", "BEST DAY EVER", "YIPPEEEEE"],
  };
  const PET_DECAY_MS = 3500;   // each point of meter decays after this
  const PET_MAX = 12;          // ceiling - stops mashing from going absurd
  const PET_COOLDOWN_MS = 140; // min gap between registered arrow presses
  // Meter is stored in a ref (we update it per-keystroke and don't want a
  // re-render on every tick). The tier + current line are state so the UI
  // actually updates.
  const petMeterRef = useRef(0);
  const petLastPressRef = useRef(0);
  const petDecayTimerRef = useRef(null);
  const [petTier, setPetTier] = useState(null);   // { state, mood } or null
  const [petLine, setPetLine] = useState("");
  const petLineSeqRef = useRef(0);                // monotonic so React remounts bubble

  // Pick a random line from the current tier's sayings pool, avoiding the
  // one we just showed if possible. Keeps rapid mashing from repeating.
  const petLastLineRef = useRef("");
  const pickPetLine = (mood) => {
    const pool = (helperSayings[mood] && helperSayings[mood].length)
      ? helperSayings[mood]
      : PET_FALLBACK_LINES[mood] || ["..."];
    if (pool.length === 1) return pool[0];
    let candidate = pool[Math.floor(Math.random() * pool.length)];
    if (candidate === petLastLineRef.current) {
      candidate = pool[(pool.indexOf(candidate) + 1) % pool.length];
    }
    petLastLineRef.current = candidate;
    return candidate;
  };

  // Derive the active tier from the current meter value and apply it.
  // Called after every bump + whenever the meter decays.
  const applyPetMeter = () => {
    const v = petMeterRef.current;
    const tier = PET_TIERS.find((t) => v >= t.min);
    if (!tier) {
      setPetTier(null);
      return;
    }
    setPetTier(tier);
  };

  // Schedule the next decay tick. Decay happens one point at a time so
  // the sprite walks back down through tiers (celebrate -> bounce ->
  // wave -> idle) rather than snapping to idle after a cooldown.
  const scheduleDecay = () => {
    if (petDecayTimerRef.current) return;
    petDecayTimerRef.current = setTimeout(function tick() {
      petDecayTimerRef.current = null;
      if (petMeterRef.current <= 0) return;
      petMeterRef.current -= 1;
      applyPetMeter();
      if (petMeterRef.current > 0) {
        petDecayTimerRef.current = setTimeout(tick, PET_DECAY_MS);
      }
    }, PET_DECAY_MS);
  };

  // Arrow-key keydown handler. Bumps the meter, refreshes the speech
  // bubble with a tier-appropriate line, and (re)arms the decay timer.
  // Milestone overrides win - don't stomp a milestone toast mid-animation.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown" &&
          e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      // Don't fight text inputs - if the user is typing somewhere, let
      // the arrow keys move the caret.
      const tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || e.target?.isContentEditable) return;
      // Milestone takes priority - suppress pet reactions while it's up.
      if (helperMilestone) return;
      const now = Date.now();
      if (now - petLastPressRef.current < PET_COOLDOWN_MS) return;
      petLastPressRef.current = now;
      petMeterRef.current = Math.min(PET_MAX, petMeterRef.current + 1);
      applyPetMeter();
      // Refresh the line based on the tier we just reached.
      const v = petMeterRef.current;
      const tier = PET_TIERS.find((t) => v >= t.min);
      if (tier) {
        petLineSeqRef.current += 1;
        setPetLine(pickPetLine(tier.mood));
      }
      scheduleDecay();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [helperMilestone, helperSayings]);

  // Clean up decay timer on unmount.
  useEffect(() => () => {
    if (petDecayTimerRef.current) clearTimeout(petDecayTimerRef.current);
  }, []);

  // (Milestone scan effect is defined below, after `blitzLifetime` is
  // declared — JS temporal dead zone means we can't reference it up here.)

  // Debug hook: expose a manual trigger on window so you can preview
  // any milestone without grinding keeps. Only attached in dev builds.
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.__sentinelTriggerMilestone = (id) => {
      const m = MILESTONES.find((x) => x.id === id) || MILESTONES[0];
      setHelperMilestone(m);
      setHelperOverrideState("celebrate");
      if (milestoneTimerRef.current) clearTimeout(milestoneTimerRef.current);
      milestoneTimerRef.current = setTimeout(() => {
        setHelperMilestone(null);
        setHelperOverrideState(null);
      }, 5200);
    };
    return () => { delete window.__sentinelTriggerMilestone; };
  }, []);

  // ─── HELPER BURSTS (short contextual animations) ────────────────
  // A "burst" is a brief animation that plays over the top of whatever
  // mood / pet-tier state is active - nod after a keep, shake after a
  // skip, think while a cycle is running, blink/look as random garnish.
  // Stored as { state, mood, line, until } or null. The derivedState
  // computation checks `until` against Date.now() so we don't need a
  // timer to cancel - React just re-renders past the expiry.
  const [helperBurst, setHelperBurst] = useState(null);
  const helperBurstTimerRef = useRef(null);

  // Fire a burst. `mood` pulls a random line from helperSayings so the
  // speech bubble stays fresh. Call with persistUntilCleared=true for
  // the "think" state that should dwell for the full cycle duration;
  // otherwise the burst auto-expires after durationMs.
  const triggerBurst = (state, mood, durationMs = 600) => {
    // Don't clobber a milestone or an active pet tier with a low-priority
    // burst - let the louder animation play out.
    if (helperMilestone) return;
    const pool = (helperSayings[mood] && helperSayings[mood].length)
      ? helperSayings[mood] : null;
    const line = pool ? pool[Math.floor(Math.random() * pool.length)] : "";
    const until = durationMs > 0 ? Date.now() + durationMs : Infinity;
    setHelperBurst({ state, mood, line, until, id: Date.now() + Math.random() });
    if (helperBurstTimerRef.current) clearTimeout(helperBurstTimerRef.current);
    if (durationMs > 0) {
      helperBurstTimerRef.current = setTimeout(() => {
        setHelperBurst((b) => (b && Date.now() >= b.until ? null : b));
        helperBurstTimerRef.current = null;
      }, durationMs + 30);
    }
  };

  // Random liveness garnish - every ~9s during genuine idle, play a
  // tiny blink; every ~22s, play a slightly longer look. Skips garnish
  // if a higher-priority animation is active so we never fight the UI.
  useEffect(() => {
    const BLINK_EVERY = 9000;
    const LOOK_EVERY  = 22000;
    let mounted = true;
    const tick = () => {
      if (!mounted) return;
      // Abort during anything louder: milestone, burst, pet tier, or
      // non-idle mood. We poll the refs/state via closures at call time
      // through the useState setters; simpler is just to read petTier
      // and helperMilestone directly - they're in scope.
      const anythingLouder = helperMilestone || petTier || helperBurst;
      if (!anythingLouder && document.visibilityState === "visible") {
        const pick = Math.random();
        if (pick < 0.55) triggerBurst("blink", null, 300);
        else if (pick < 0.80) triggerBurst("look", null, 660);
        // remaining 20%: do nothing, keep the rhythm varied
      }
    };
    // Randomise the first tick so multiple page loads don't sync-blink.
    const kick = setTimeout(() => {
      tick();
      const id = setInterval(tick, BLINK_EVERY + Math.random() * 3000);
      // Store so we clear on cleanup.
      kick._id = id;
    }, 3000 + Math.random() * 4000);
    return () => {
      mounted = false;
      clearTimeout(kick);
      if (kick._id) clearInterval(kick._id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [helperMilestone, petTier, helperBurst]);

  // Dev hook: trigger any burst from the console for preview.
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.__sentinelTriggerBurst = triggerBurst;
    return () => { delete window.__sentinelTriggerBurst; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [helperSayings]);

  // While a pipeline cycle is running, Joby plays 'think' indefinitely.
  // When the cycle ends, clear the burst so the sprite returns to the
  // mood-derived default. We use a durationMs of 0 to mean "don't
  // auto-expire" - the cleanup below handles the handoff.
  useEffect(() => {
    if (pipelineRunning) {
      triggerBurst("think", "cycle_working", 0);
    } else {
      // Only clear if the active burst is our think burst (don't stomp
      // a nod/shake that happened mid-transition).
      setHelperBurst((b) => (b && b.state === "think" ? null : b));
      if (helperBurstTimerRef.current) {
        clearTimeout(helperBurstTimerRef.current);
        helperBurstTimerRef.current = null;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipelineRunning]);

  // Lifetime stats that persist across sessions. This is the "you've
  // triaged 487 roles with Pip" kind of number - meant to feel rewarding
  // over weeks. Kept in localStorage so it survives reloads.
  const [blitzLifetime, setBlitzLifetime] = useState(() => {
    try {
      const raw = localStorage.getItem("sentinel.blitz.lifetime");
      if (!raw) return { keeps: 0, skips: 0, maybes: 0, bestStreak: 0, bestCombo: 0, days: [] };
      const parsed = JSON.parse(raw);
      return {
        keeps: Number(parsed.keeps) || 0,
        skips: Number(parsed.skips) || 0,
        maybes: Number(parsed.maybes) || 0,
        bestStreak: Number(parsed.bestStreak) || 0,
        bestCombo: Number(parsed.bestCombo) || 0,
        days: Array.isArray(parsed.days) ? parsed.days.slice(-365) : [],
      };
    } catch { return { keeps: 0, skips: 0, maybes: 0, bestStreak: 0, bestCombo: 0, days: [] }; }
  });
  // Milestone scan. Runs every time lifetime stats change. Cheap enough
  // (14 predicate checks) that we don't bother memoising. Loads the
  // achieved set fresh each scan so hot reloads don't re-fire on start.
  // Declared after `blitzLifetime` so the deps array doesn't hit the TDZ.
  useEffect(() => {
    let achieved;
    try {
      achieved = JSON.parse(localStorage.getItem("sentinel.milestones.achieved") || "{}");
    } catch { achieved = {}; }
    const unlocked = MILESTONES.find((m) => !achieved[m.id] && m.threshold(blitzLifetime));
    if (!unlocked) return;
    achieved[unlocked.id] = Date.now();
    try {
      localStorage.setItem("sentinel.milestones.achieved", JSON.stringify(achieved));
    } catch { /* quota / private mode: non-fatal */ }
    setHelperMilestone(unlocked);
    setHelperOverrideState("celebrate");
    if (milestoneTimerRef.current) clearTimeout(milestoneTimerRef.current);
    milestoneTimerRef.current = setTimeout(() => {
      setHelperMilestone(null);
      setHelperOverrideState(null);
      milestoneTimerRef.current = null;
    }, 5200);
  }, [blitzLifetime.keeps, blitzLifetime.bestStreak, blitzLifetime.bestCombo,
       blitzLifetime.days?.length]);
  // Rolling window of recent decision timestamps for combo detection.
  // A combo is N keeps/skips within a time window - e.g. 10 in 60s fires
  // "ON A ROLL". Kept out of state because it mutates on every keystroke
  // and we don't need a re-render when a new timestamp is pushed.
  const blitzRecentRef = useRef([]);
  // Last combo tier fired; prevents the overlay retriggering every
  // keystroke once you're past a threshold. Resets when the rolling
  // window drops below the threshold again.
  const [blitzComboTier, setBlitzComboTier] = useState(0);
  // Surprise overlay payload: { label, subtitle, ts }. Cleared ~1.5s
  // after set. Rendered as a fullscreen centered flash so it feels earned.
  const [blitzSurprise, setBlitzSurprise] = useState(null);

  const t = isDark ? dark : light;
  const pieC = isDark ? PIE_C_DARK : PIE_C_LIGHT;

  // ─── POLLING ──────────────────────────────────────────────────
  const poll = useCallback(async () => {
    const safeJson = (path) =>
      fetch(`${API}${path}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);

    const st = await safeJson("/api/status");
    if (!st) {
      // Backend unreachable. Don't flash placeholder data - just mark
      // live=false and keep whatever the UI last showed so a transient
      // server blip doesn't wipe out a populated view. On cold start
      // the initial useState empties already render as empty state.
      setLive(false);
      return;
    }
    setLive(true);
    setStatus(st);
    statusRef.current = st;
    const inProgress = !!st.cycle_in_progress;
    const cycleEnded = wasInProgressRef.current && !inProgress;
    wasInProgressRef.current = inProgress;
    setPipelineRunning(inProgress);

    // Tier 1 — always: status only (already done above).
    // Tier 2 — hydrate-once: config / resume / setup-state / decisions.
    // Tier 3 — cycle-gated: matches / fit-gaps / market(s) / digests.
    //   Poll while a cycle runs so rows stream in; pull one more time
    //   on the cycle-end transition; quiet at rest.
    const needsBoot = !configHydratedRef.current || !resumeHydratedRef.current
                   || !setupHydratedRef.current || !decisionsHydratedRef.current;
    const needsCycle = inProgress || cycleEnded;

    if (!needsBoot && !needsCycle) {
      return; // steady state — nothing to fetch
    }

    const reqs = {};
    if (needsCycle) {
      reqs.m = safeJson("/api/matches");
      reqs.f = safeJson("/api/fit-gaps");
      reqs.mk = safeJson("/api/market");
      reqs.t1 = safeJson("/api/market-tier1");
      reqs.t2 = safeJson("/api/market-tier2");
      reqs.dg = safeJson("/api/digests");
    }
    if (!configHydratedRef.current)    reqs.cfg = safeJson("/api/config");
    if (!resumeHydratedRef.current)    reqs.res = safeJson("/api/resume");
    if (!setupHydratedRef.current)     reqs.su  = safeJson("/api/setup-state");
    if (!decisionsHydratedRef.current) reqs.d   = safeJson("/api/decisions");

    const keys = Object.keys(reqs);
    const vals = await Promise.all(keys.map((k) => reqs[k]));
    const r = Object.fromEntries(keys.map((k, i) => [k, vals[i]]));

    if ("su" in r) {
      if (r.su && typeof r.su === "object") {
        setSetupState({
          setup_completed: !!r.su.setup_completed,
          user: r.su.user || {},
          loaded: true,
        });
      } else {
        setSetupState((prev) => ({ ...prev, loaded: true }));
      }
      setupHydratedRef.current = true;
    }
    // Re-bind locals to the keys downstream code expects.
    const m = "m" in r ? r.m : null;
    const f = "f" in r ? r.f : null;
    const d = "d" in r ? r.d : null;
    const mk = "mk" in r ? r.mk : null;
    const t1 = "t1" in r ? r.t1 : null;
    const t2 = "t2" in r ? r.t2 : null;
    const dg = "dg" in r ? r.dg : null;
    const cfg = "cfg" in r ? r.cfg : null;
    const res = "res" in r ? r.res : null;
    // When live, always reflect reality including empty state. Do NOT fall
    // back to demo rows; the user needs to see that the pipeline produced
    // nothing so they know to run a cycle or loosen filters.
    // Before overwriting, note if the match count grew so we can fire the
    // helper's 'eat' burst + a short jingle on fresh matches. We compare
    // lengths as a heuristic - good enough since the endpoint returns
    // newest-first and we don't need exact delta tracking here.
    if ("m" in r) {
      const incomingMatches = Array.isArray(m) ? m : [];
      const prevMatchCount = matches.length;
      setMatches(incomingMatches);
      if (incomingMatches.length > prevMatchCount && prevMatchCount > 0) {
        // Only fire on genuine growth. One jingle per poll to avoid
        // audio spam on big backfills.
        triggerBurst("eat", "match_eat", 500);
        playComboSound("match");
      }
    }
    if ("f" in r)  setFitGaps(Array.isArray(f) ? f : []);
    if ("d" in r) {
      setDecisions(d && typeof d === "object" ? d : { decisions: [], reactions: {} });
      decisionsHydratedRef.current = true;
    }
    if ("mk" in r) setMarket(Array.isArray(mk) ? mk : []);
    if ("t1" in r) setTier1(t1 || null);
    if ("t2" in r) setTier2(t2 || null);
    if ("dg" in r) setDigests(Array.isArray(dg) ? dg : []);
    if (cfg?.ingest) {
      setConfig(cfg);
      setKeywords((cfg.ingest?.role_keywords || []).join(", "));
      setThreshold(cfg.match?.threshold ?? 0.55);
      setParseModel(cfg.parse?.model || "qwen2.5:14b");
      setMatchModel(cfg.match?.model || "qwen3:14b");
      const p = cfg.preferences || {};
      // Prefer the new work_modes list, but fall back to the legacy
      // allow_remote / remote_only pair so older saved configs hydrate
      // into a sensible state.
      if (Array.isArray(p.work_modes)) {
        const valid = p.work_modes.filter(m => ["remote", "hybrid", "onsite"].includes(m));
        setWorkModes(valid.length ? valid : ["remote", "hybrid", "onsite"]);
      } else {
        const allow = p.allow_remote !== false;
        const only  = !!p.remote_only;
        if (only && allow)      setWorkModes(["remote"]);
        else if (!allow)        setWorkModes(["hybrid", "onsite"]);
        else                    setWorkModes(["remote", "hybrid", "onsite"]);
      }
      setAllowedLocations((p.allowed_locations || []).join(", "));
      setBlockedLocations((p.blocked_locations || []).join(", "));
      // Geographic pin filter. Accept the new array shape, and back-
      // compat the previous singleton (`location_pin_lat` + `_lon`)
      // shape so older saved configs still hydrate.
      if (Array.isArray(p.location_pin_areas)) {
        const pins = p.location_pin_areas
          .filter(a => a && typeof a.lat === "number" && typeof a.lon === "number")
          .map(a => [a.lat, a.lon]);
        setLocationPins(pins);
      } else if (typeof p.location_pin_lat === "number" && typeof p.location_pin_lon === "number") {
        setLocationPins([[p.location_pin_lat, p.location_pin_lon]]);
      } else {
        setLocationPins([]);
      }
      if (typeof p.location_pin_radius_km === "number") {
        setLocationRadiusKm(Math.max(5, Math.min(2000, p.location_pin_radius_km)));
      }
      // Country filter state (new).
      if (Array.isArray(p.allowed_countries)) {
        setAllowedCountries(p.allowed_countries.map(c => String(c).toUpperCase()).filter(Boolean));
      }
      if (typeof p.strict_unknown_country === "boolean") {
        setStrictUnknownCountry(p.strict_unknown_country);
      }
      if (typeof p.allow_remote_any_country === "boolean") {
        setAllowRemoteAnyCountry(p.allow_remote_any_country);
      }
      setCycleInterval(Math.max(5, Math.min(240, Number(cfg.cycle_interval_minutes) || 30)));
      setSalaryFloor(p.salary_floor_usd ?? 0);
      setSalaryWeight(p.salary_weight ?? 0.15);
      setYearsExperience(p.years_experience ?? 0);
      setCurrentLevel(p.current_level || "");
      setYearsWeight(p.years_weight ?? 0.04);
      setTrapdoorEnabled(p.trapdoor_enabled !== false);
      const fd = cfg.fake_detection || {};
      const agg = fd.aggressiveness || fd.preset;
      if (agg === "low" || agg === "balanced" || agg === "strict") {
        setFakeAggressiveness(agg);
      } else {
        setFakeAggressiveness("balanced");
      }
      // Hydrate ghost-fold knobs. Missing keys = defaults (the server also
      // defaults, so old configs keep working).
      if (typeof fd.ghost_weight === "number") {
        setGhostWeight(Math.max(0, Math.min(1, fd.ghost_weight)));
      }
      if (typeof fd.flag_threshold === "number") {
        setGhostFlagThreshold(Math.max(0, Math.min(1, fd.flag_threshold)));
      }
      if (typeof fd.warn_threshold === "number") {
        setGhostWarnThreshold(Math.max(0, Math.min(1, fd.warn_threshold)));
      }
      // Ingest tenants + big-tech toggles. Empty defaults = "user hasn't
      // configured yet" — the UI renders an encouraging empty state.
      const ing = cfg.ingest || {};
      if (Array.isArray(ing.greenhouse_companies)) {
        setGreenhouseCompanies(ing.greenhouse_companies.map(s => String(s).trim().toLowerCase()).filter(Boolean));
      }
      if (Array.isArray(ing.lever_companies)) {
        setLeverCompanies(ing.lever_companies.map(s => String(s).trim().toLowerCase()).filter(Boolean));
      }
      if (Array.isArray(ing.ashby_companies)) {
        setAshbyCompanies(ing.ashby_companies
          .filter(row => Array.isArray(row) && row.length >= 2)
          .map(([display, slug]) => [String(display || slug).trim(), String(slug).trim().toLowerCase()])
          .filter(([, slug]) => slug));
      }
      if (typeof ing.enable_apple === "boolean") setEnableApple(ing.enable_apple);
      if (typeof ing.enable_amazon === "boolean") setEnableAmazon(ing.enable_amazon);
      if (typeof ing.enable_google === "boolean") setEnableGoogle(ing.enable_google);
      if (typeof ing.enable_meta === "boolean") setEnableMeta(ing.enable_meta);
      if (typeof ing.enable_microsoft === "boolean") setEnableMicrosoft(ing.enable_microsoft);
      if (typeof ing.enable_netflix === "boolean") setEnableNetflix(ing.enable_netflix);
      if (typeof ing.enable_nvidia === "boolean") setEnableNvidia(ing.enable_nvidia);
      if (typeof ing.enable_tesla === "boolean") setEnableTesla(ing.enable_tesla);
      if (typeof ing.enable_linkedin === "boolean") setEnableLinkedin(ing.enable_linkedin);
      if (typeof ing.enable_adobe === "boolean") setEnableAdobe(ing.enable_adobe);
      if (typeof ing.enable_salesforce === "boolean") setEnableSalesforce(ing.enable_salesforce);
      if (typeof ing.enable_oracle === "boolean") setEnableOracle(ing.enable_oracle);
      if (typeof ing.enable_ibm === "boolean") setEnableIbm(ing.enable_ibm);
      if (typeof ing.enable_cisco === "boolean") setEnableCisco(ing.enable_cisco);
      if (typeof ing.enable_intel === "boolean") setEnableIntel(ing.enable_intel);
      configHydratedRef.current = true;
    }
    if ("res" in r && res && typeof res === "object") {
      setResumeState({
        has_resume: !!res.has_resume,
        metadata: res.metadata || {},
        additional_notes_len: res.additional_notes_len || 0,
      });
      resumeHydratedRef.current = true;
    }
  }, [matches.length]);

  // Hydrate notes once per resume-upload cycle to avoid clobbering typing.
  const notesLoadedFor = useRef(null);
  useEffect(() => {
    const key = resumeState.has_resume ? (resumeState.metadata?.uploaded_at || "present") : "absent";
    if (notesLoadedFor.current === key) return;
    notesLoadedFor.current = key;
    fetch(`${API}/api/resume?full=1`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && typeof data.additional_notes === "string") setAdditionalNotes(data.additional_notes);
      }).catch(() => {});
  }, [resumeState.has_resume, resumeState.metadata?.uploaded_at]);

  // Fetch the structured profile when a resume exists. Re-runs on upload
  // because the backend invalidates the cache on POST /api/resume.
  // If the server says status=needs_parse, kick off the blocking parse via
  // the reparse endpoint so the user sees the busy state rather than a hang.
  const profileLoadedFor = useRef(null);
  useEffect(() => {
    if (!live) return;
    if (!resumeState.has_resume) {
      setResumeProfile(null);
      profileLoadedFor.current = null;
      return;
    }
    const key = resumeState.metadata?.uploaded_at || "present";
    if (profileLoadedFor.current === key) return;
    profileLoadedFor.current = key;
    fetch(`${API}/api/resume/profile`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) { setResumeProfile(null); return; }
        if (data.profile) {
          setResumeProfile(data.profile);
          return;
        }
        // Auto-parse when the server has no cache. reparseResume handles
        // busy state + error messaging; we want this to fire once, not
        // spam the endpoint, so the profileLoadedFor ref above guards it.
        if (data.status === "needs_parse") {
          reparseResume();
        } else {
          setResumeProfile(null);
        }
      })
      .catch(() => setResumeProfile(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, resumeState.has_resume, resumeState.metadata?.uploaded_at]);

  const reparseResume = async () => {
    if (!live || reparseBusy) return;
    setReparseBusy(true);
    setResumeMsg("Re-parsing resume...");
    try {
      const r = await fetch(`${API}/api/resume/reparse`, { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (r.ok && data.ok && data.profile) {
        setResumeProfile(data.profile);
        setResumeMsg(data.profile._fallback
          ? "Re-parsed with keyword fallback (Ollama unreachable)."
          : "Profile re-parsed.");
        setTimeout(() => setResumeMsg(""), 3000);
      } else {
        setResumeMsg(data.error || "Re-parse failed.");
      }
    } catch (e) {
      setResumeMsg(`Re-parse failed: ${e?.message || e}`);
    } finally {
      setReparseBusy(false);
    }
  };

  useEffect(() => {
    setTimeout(() => setReady(true), 50);
    let cancelled = false;
    let timeoutId = null;
    const tick = async () => {
      if (cancelled) return;
      await poll();
      if (cancelled) return;
      // Active = server reports a cycle in progress OR the progress stage
      // is anything other than idle. Either way, poll fast so the Brief
      // tab paints phase transitions without waiting for the slow tick.
      const s = statusRef.current || {};
      const stage = s.progress?.stage;
      const active = !!s.cycle_in_progress || (stage && stage !== "idle");
      const delay = active ? POLL_FAST : POLL_IDLE;
      timeoutId = setTimeout(tick, delay);
    };
    tick();
    return () => {
      cancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [poll]);

  // Auto-open wizard once when we're live and the user_store says setup
  // hasn't been completed. This is the authoritative signal - user.json
  // persists across restarts, so returning users never see it twice.
  // Dismissal is remembered for this session so the user can close and
  // re-open from Settings without it popping back immediately.
  useEffect(() => {
    if (wizardDismissed || wizardOpen) return;
    if (!live) return;
    // Critical: only auto-open after the first successful setup-state
    // fetch. Without this guard, the default setup_completed=false wins
    // the race against the fetch and the wizard pops up even for
    // returning users whose user.json already says setup is complete.
    if (!setupState.loaded) return;
    if (!setupState.setup_completed) setWizardOpen(true);
  }, [live, setupState.setup_completed, setupState.loaded, wizardDismissed, wizardOpen]);

  // While the wizard is open, poll preflight + prewarm so the user sees
  // ticks/crosses update as Ollama / sentence-transformers finish
  // loading. Stops polling as soon as the wizard closes. Keeps traffic
  // to zero once the user is past setup.
  useEffect(() => {
    if (!wizardOpen || !live) return;
    let cancelled = false;
    const tick = async () => {
      const [pf, pw] = await Promise.all([
        fetch(`${API}/api/preflight`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        fetch(`${API}/api/prewarm`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      if (cancelled) return;
      if (pf) setPreflight(pf);
      if (pw) setPrewarm(pw);
    };
    tick();
    const id = setInterval(tick, 2000);
    // Kick prewarm if nothing is running yet.
    fetch(`${API}/api/prewarm`, { method: "POST" }).catch(() => {});
    return () => { cancelled = true; clearInterval(id); };
  }, [wizardOpen, live]);

  // Auto-scroll chat to bottom when messages change.
  useEffect(() => {
    if (chatScrollRef.current) chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
  }, [chatMessages, chatBusy]);

  // Persist chat history across reloads. Trim to last 100 turns so local-
  // storage doesn't bloat after weeks of daily use.
  useEffect(() => {
    try {
      localStorage.setItem("sentinel.chatMessages", JSON.stringify(chatMessages.slice(-100)));
    } catch { /* quota or private-mode: drop silently */ }
  }, [chatMessages]);

  const runCycle = async () => {
    try {
      const r = await fetch(`${API}/api/run-cycle`, { method: "POST" });
      const d = await r.json();
      if (d.ok) {
        setPipelineRunning(true);
      } else {
        // Surface the rejection (e.g. "cycle already in progress",
        // "setup not complete") instead of silently swallowing — the
        // old behaviour made the button feel "stuck."
        setRunMsg(d.error || "Could not start pipeline.");
        setTimeout(() => setRunMsg(""), 6000);
      }
    } catch (e) {
      setRunMsg(`Could not reach the backend: ${e.message || e}`);
      setTimeout(() => setRunMsg(""), 6000);
    }
  };

  // Run Scraper — Playwright SPA fetchers (Apple / Meta / Microsoft). Runs
  // rarely (minutes per cycle) so it has its own button separate from the
  // fast pipeline. Backend gates on the same cycle slot, so if a pipeline
  // is already running this returns 409. UI disables the button while any
  // cycle is in flight.
  const runScraper = async () => {
    try {
      const r = await fetch(`${API}/api/run-scraper`, { method: "POST" });
      const d = await r.json();
      if (d.ok) {
        setPipelineRunning(true);
      } else {
        setRunMsg(d.error || "Could not start scraper.");
        setTimeout(() => setRunMsg(""), 6000);
      }
    } catch (e) {
      setRunMsg(`Could not reach the backend: ${e.message || e}`);
      setTimeout(() => setRunMsg(""), 6000);
    }
  };

  // Run Both — fast tier THEN slow tier in ONE cycle, back-to-back. The
  // orchestrator walks the tiers tuple in order so we don't need two
  // separate thread spawns on the client. From the user's perspective it's
  // "run everything". Expect this to take minutes, not seconds.
  const runBoth = async () => {
    try {
      const r = await fetch(`${API}/api/run-all`, { method: "POST" });
      const d = await r.json();
      if (d.ok) setPipelineRunning(true);
    } catch {}
  };

  // Reset History — nuke per-cycle run data while keeping resume / prefs
  // / tracker / decisions. Backed by /api/reset-history which delegates
  // to core.reset_history (one allow-list, one path guard). Two-step UX:
  // user clicks once to ARM, clicks again within 5s to CONFIRM. Prevents
  // accidental nukes without a modal.
  const [resetArmed, setResetArmed] = useState(false);
  const [resetStatus, setResetStatus] = useState(null);
  const resetHistory = async () => {
    if (!resetArmed) {
      setResetArmed(true);
      setResetStatus(null);
      setTimeout(() => setResetArmed(false), 5000);
      return;
    }
    setResetArmed(false);
    setResetStatus({ busy: true });
    try {
      const r = await fetch(`${API}/api/reset-history`, { method: "POST" });
      const d = await r.json();
      setResetStatus({ busy: false, ...d });
    } catch (e) {
      setResetStatus({ busy: false, ok: false, error: String(e) });
    }
  };

  // Request a tailored PDF resume for one specific match. Server does
  // the LLM tailoring + PDF render and returns the file path. We don't
  // auto-download -- user opens it from disk (paths show in the button
  // label so a non-technical user can find it).
  // Call /api/tailor-resume with the job payload. The server pulls
  // profile + resume text from disk itself, so we only need to send
  // the JOB. Response gives us back two file BASENAMES (html_file,
  // pdf_file) that we turn into /api/resumes/download?file=... links
  // the user can click to open in a new tab.
  const tailorResume = async (job) => {
    if (!job) return;
    const key = job.url || `${job.company}:${job.title}`;
    setTailorState(s => ({ ...s, [key]: { busy: true } }));
    try {
      const r = await fetch(`${API}/api/tailor-resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: job.title || "",
          company: job.company || "",
          url: job.url || "",
          description: job.description || "",
          technologies: job.technologies || [],
        }),
      });
      const d = await r.json();
      if (d.ok) {
        setTailorState(s => ({ ...s, [key]: {
          busy: false,
          htmlFile: d.html_file || null,
          pdfFile: d.pdf_file || null,
          pdfMethod: d.pdf_method || null,
          summary: d.summary || "",
        } }));
      } else {
        setTailorState(s => ({ ...s, [key]: { busy: false, error: d.error || "failed" } }));
      }
    } catch (e) {
      setTailorState(s => ({ ...s, [key]: { busy: false, error: String(e) } }));
    }
  };

  const saveSettings = async () => {
    try {
      await fetch(`${API}/api/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          role_keywords: keywords.split(",").map(k => k.trim()).filter(Boolean),
          threshold: parseFloat(threshold),
          cycle_interval_minutes: Math.max(5, Math.min(240, Number(cycleInterval) || 30)),
          models: { parse: parseModel, match: matchModel },
          preferences: {
            work_modes: workModes.filter(m => ["remote", "hybrid", "onsite"].includes(m)),
            allowed_locations: allowedLocations.split(",").map(s => s.trim()).filter(Boolean),
            blocked_locations: blockedLocations.split(",").map(s => s.trim()).filter(Boolean),
            location_pin_areas: locationPins.map(([lat, lon]) => ({ lat, lon })),
            location_pin_radius_km: locationRadiusKm,
            allowed_countries: allowedCountries,
            country_mode: allowedCountries.length > 0 ? "hard" : "soft",
            strict_unknown_country: !!strictUnknownCountry,
            allow_remote_any_country: !!allowRemoteAnyCountry,
            salary_floor_usd: Number(salaryFloor) || 0,
            salary_weight: Number(salaryWeight) || 0,
            years_experience: Number(yearsExperience) || 0,
            current_level: currentLevel || "",
            years_weight: Number(yearsWeight) || 0.04,
            trapdoor_enabled: !!trapdoorEnabled,
          },
          fake_detection: {
            aggressiveness: ["low", "balanced", "strict"].includes(fakeAggressiveness)
              ? fakeAggressiveness
              : "balanced",
            ghost_weight: Math.max(0, Math.min(1, Number(ghostWeight) || 0)),
            flag_threshold: Math.max(0, Math.min(1, Number(ghostFlagThreshold) || 0.45)),
            warn_threshold: Math.max(0, Math.min(1, Number(ghostWarnThreshold) || 0.30)),
          },
          ingest: {
            greenhouse_companies: greenhouseCompanies,
            lever_companies: leverCompanies,
            ashby_companies: ashbyCompanies,
            enable_apple: !!enableApple,
            enable_amazon: !!enableAmazon,
            enable_google: !!enableGoogle,
            enable_meta: !!enableMeta,
            enable_microsoft: !!enableMicrosoft,
            enable_netflix: !!enableNetflix,
            enable_nvidia: !!enableNvidia,
            enable_tesla: !!enableTesla,
            enable_linkedin: !!enableLinkedin,
            enable_adobe: !!enableAdobe,
            enable_salesforce: !!enableSalesforce,
            enable_oracle: !!enableOracle,
            enable_ibm: !!enableIbm,
            enable_cisco: !!enableCisco,
            enable_intel: !!enableIntel,
          },
        }),
      });
      setSettingsSaved(true);
      setTimeout(() => setSettingsSaved(false), 2000);
    } catch {}
  };

  // Test a tenant slug against its ATS endpoint. Hits /api/ingest/test on
  // the backend which does the HTTP round-trip and returns job count +
  // sample title. Keyed by `${kind}:${slug}` in tenantTests state so each
  // row can render its own status.
  const testTenant = async (kind, slug, display) => {
    const key = `${kind}:${slug}`;
    setTenantTestBusy(prev => ({ ...prev, [key]: true }));
    try {
      const r = await fetch(`${API}/api/ingest/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, slug, display: display || slug }),
      });
      const d = await r.json();
      setTenantTests(prev => ({ ...prev, [key]: { ...d, ts: Date.now() } }));
    } catch (e) {
      setTenantTests(prev => ({ ...prev, [key]: { ok: false, error: String(e), ts: Date.now() } }));
    } finally {
      setTenantTestBusy(prev => ({ ...prev, [key]: false }));
    }
  };

  // Local-state helpers for the three tenant lists. Each normalises
  // (lowercase, trim, dedupe) and auto-saves via saveSettings on the
  // next blur/submit so the user doesn't have to remember to hit save.
  const addGreenhouseSlug = () => {
    const slug = newGreenhouseSlug.trim().toLowerCase();
    if (!slug || greenhouseCompanies.includes(slug)) return;
    setGreenhouseCompanies([...greenhouseCompanies, slug].sort());
    setNewGreenhouseSlug("");
  };
  const removeGreenhouseSlug = (slug) => setGreenhouseCompanies(greenhouseCompanies.filter(s => s !== slug));
  const addLeverSlug = () => {
    const slug = newLeverSlug.trim().toLowerCase();
    if (!slug || leverCompanies.includes(slug)) return;
    setLeverCompanies([...leverCompanies, slug].sort());
    setNewLeverSlug("");
  };
  const removeLeverSlug = (slug) => setLeverCompanies(leverCompanies.filter(s => s !== slug));
  const addAshbyTenant = () => {
    const slug = newAshbySlug.trim().toLowerCase();
    const display = (newAshbyDisplay.trim() || slug);
    if (!slug || ashbyCompanies.some(([, s]) => s === slug)) return;
    setAshbyCompanies([...ashbyCompanies, [display, slug]].sort((a, b) => a[1].localeCompare(b[1])));
    setNewAshbyDisplay("");
    setNewAshbySlug("");
  };
  const removeAshbyTenant = (slug) => setAshbyCompanies(ashbyCompanies.filter(([, s]) => s !== slug));

  // ─── EXPORT HANDLER ───────────────────────────────────────────
  // Pulls /api/export (a zip) and triggers a browser download. The
  // server builds the zip in-memory from config.json and the data/
  // tree, so the user walks away with a portable copy of their whole
  // SENTINEL state: preferences, resume, match history, parsed jobs,
  // digests. Useful for backups or feeding to another LLM.
  const exportBundle = async () => {
    if (exportBusy) return;
    setExportBusy(true);
    setExportMsg("");
    try {
      const r = await fetch(`${API}/api/export`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const stamp = new Date().toISOString().replace(/[:T]/g, "-").slice(0, 19);
      // Prefer the server-provided filename if present.
      const cd = r.headers.get("content-disposition") || "";
      const m = /filename="?([^";]+)"?/i.exec(cd);
      const filename = m?.[1] || `sentinel-export-${stamp}.zip`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      const kb = (blob.size / 1024).toFixed(1);
      setExportMsg(`Exported ${filename} (${kb} kB).`);
    } catch (e) {
      setExportMsg(`Export failed: ${e?.message || e}`);
    } finally {
      setExportBusy(false);
    }
  };

  // ─── RESUME HANDLERS ──────────────────────────────────────────
  const uploadResume = (file) => {
    if (!file) return;
    setResumeBusy(true); setResumeMsg("");
    const reader = new FileReader();
    reader.onerror = () => { setResumeBusy(false); setResumeMsg("Could not read file."); };
    reader.onload = async () => {
      try {
        const dataUrl = reader.result || "";
        const r = await fetch(`${API}/api/resume`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: file.name, content_base64: String(dataUrl) }),
        });
        const data = await r.json().catch(() => ({}));
        if (r.ok && data.ok) {
          setResumeState((s) => ({ ...s, has_resume: true, metadata: data.metadata || {} }));
          setResumeMsg(`Uploaded ${data.metadata?.filename || file.name} (${data.metadata?.char_count || 0} chars extracted).`);
          notesLoadedFor.current = null;
        } else {
          setResumeMsg(data.error || "Upload failed.");
        }
      } catch (e) {
        setResumeMsg(`Upload failed: ${e?.message || e}`);
      } finally {
        setResumeBusy(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    };
    reader.readAsDataURL(file);
  };

  const saveNotes = async () => {
    setResumeBusy(true); setResumeMsg("");
    try {
      const r = await fetch(`${API}/api/resume/notes`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes: additionalNotes }),
      });
      const data = await r.json().catch(() => ({}));
      if (r.ok && data.ok) {
        setResumeState((s) => ({ ...s, additional_notes_len: additionalNotes.length }));
        setResumeMsg("Notes saved.");
        setTimeout(() => setResumeMsg(""), 2000);
      } else {
        setResumeMsg(data.error || "Could not save notes.");
      }
    } catch (e) {
      setResumeMsg(`Could not save notes: ${e?.message || e}`);
    } finally { setResumeBusy(false); }
  };

  const clearResume = async () => {
    setResumeBusy(true); setResumeMsg("");
    try {
      const r = await fetch(`${API}/api/resume/clear`, { method: "POST" });
      if (r.ok) {
        setResumeState({ has_resume: false, metadata: {}, additional_notes_len: 0 });
        setAdditionalNotes(""); notesLoadedFor.current = "absent";
        setResumeMsg("Resume cleared.");
        setTimeout(() => setResumeMsg(""), 2000);
      } else { setResumeMsg("Could not clear resume."); }
    } catch (e) { setResumeMsg(`Could not clear resume: ${e?.message || e}`); }
    finally { setResumeBusy(false); }
  };

  // ─── REACTION HANDLERS ────────────────────────────────────────
  const reactionFor = (m) => decisions?.reactions?.[reactionKey(m.title, m.company)]?.action;
  const setReaction = async (m, next) => {
    // Toggle off if user clicks the same button again.
    const current = reactionFor(m);
    const action = current === next ? "clear" : next;
    // Optimistic update.
    setDecisions((prev) => {
      const reactions = { ...(prev?.reactions || {}) };
      const k = reactionKey(m.title, m.company);
      if (action === "clear") delete reactions[k];
      else reactions[k] = { action, title: m.title, company: m.company, url: m.url || "", score: m._match_score || 0, ts: new Date().toISOString() };
      return { ...prev, reactions };
    });
    try {
      await fetch(`${API}/api/reactions`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: m.title, company: m.company, action, url: m.url || "", score: m._match_score || 0 }),
      });
    } catch {}
  };

  const liked = useMemo(
    () => matches.filter((m) => reactionFor(m) === "up"),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [matches, decisions]
  );

  // ─── REGISTRY STATE HANDLERS (seen / dismissed / starred) ───────
  // Optimistic-update-then-POST pattern, same as reactions. Backend is
  // authoritative on next /api/matches poll, so a 500 self-heals on
  // refresh rather than leaving the UI lying.
  const setMatchState = async (m, field, value) => {
    if (!m || !["seen", "dismissed", "starred", "removed"].includes(field)) return;
    const key = m._registry_key;
    // Optimistic: patch the in-memory match list.
    setMatches((prev) => prev.map((x) => {
      if (x === m || (x.title === m.title && x.company === m.company)) {
        return { ...x, [`_${field}`]: value };
      }
      return x;
    }));
    try {
      const body = key
        ? { key, field, value }
        : { title: m.title, company: m.company, location: m.location || "", field, value };
      await fetch(`${API}/api/matches/state`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch {}
  };

  // seen auto-flips on any interaction with a row. This is the design
  // call we explicitly made: don't flip on render (too aggressive), do
  // flip on expand/action (matches the "I looked at this" intent).
  const markSeenIfNeeded = (m) => {
    if (m && !m._seen) setMatchState(m, "seen", true);
  };

  // "Age" column. Prefer the job's actual posted_date; fall back to
  // when SENTINEL first saw it. Returns a compact string like "3d" or
  // "new" (0 days) or "—" if we have nothing to anchor against.
  const ageFor = (m) => {
    const iso = m.posted_date || m._first_seen_at;
    if (!iso) return "—";
    const ts = Date.parse(iso);
    if (!Number.isFinite(ts)) return "—";
    const days = Math.max(0, Math.floor((Date.now() - ts) / 86400000));
    if (days === 0) return "new";
    if (days < 30) return `${days}d`;
    if (days < 365) return `${Math.floor(days / 30)}mo`;
    return `${Math.floor(days / 365)}y`;
  };

  // Compact remote/hybrid/onsite chip. Upstream field is free-form, so
  // we canonicalise a few common phrasings and fall back to "?" when
  // the scraper couldn't tell.
  const remoteLabelFor = (m) => {
    const raw = (m.remote || m.work_mode || "").toString().toLowerCase().trim();
    if (!raw || raw === "unknown" || raw === "unspecified") return "?";
    if (raw.includes("hybrid")) return "Hybrid";
    if (raw.includes("remote")) return "Remote";
    if (raw.includes("onsite") || raw.includes("on-site") || raw.includes("in office") || raw.includes("in-office")) return "Onsite";
    return raw.charAt(0).toUpperCase() + raw.slice(1);
  };

  // Short "gist" preview. Prefer the LLM's why-it-matches reasoning
  // when it exists and is meaningful; otherwise crop the first line of
  // the scraped description. Kept short so the column stays tidy.
  const previewFor = (m) => {
    const reasoning = (m._match_reasoning || "").toString().trim();
    // The embedding path writes a placeholder "embedding similarity" -
    // skip that, it's noise. Anything longer we trust.
    if (reasoning && reasoning.length > 24) {
      return reasoning.length > 90 ? reasoning.slice(0, 87) + "…" : reasoning;
    }
    const desc = (m.description || "").toString().replace(/\s+/g, " ").trim();
    if (!desc) return "";
    return desc.length > 90 ? desc.slice(0, 87) + "…" : desc;
  };

  // Filtered view for the Matches tab. Default hides dismissed; the
  // other filters are opt-in. Keep this cheap - one pass over the list,
  // no reshuffling unless a filter is active.
  const visibleMatches = useMemo(() => {
    const f = matchFilters || {};
    const nowMs = Date.now();
    const winMs = (f.windowDays || 0) * 86400000;
    // Single Matches list now - we used to split match/maybe into two
    // tabs, but Eddie wants borderline hits visible alongside high-score
    // ones so nothing is hidden behind a secondary tab. Tier info is
    // still on the payload (`_match_tier`) for future sorting.
    const filtered = matches.filter((m) => {
      // Removed (expired postings) always hidden — no toggle, they are
      // meant to stay gone.
      if (m._removed) return false;
      if (!f.showDismissed && m._dismissed) return false;
      if (f.starredOnly && !m._starred) return false;
      if (f.unseenOnly && m._seen) return false;
      // Archetype filter — matches whose archetype slug equals the
      // selected bucket. "unclassified" lets users see roles the
      // classifier couldn't bucket. Absent archetype = treat as
      // unclassified for filter purposes.
      if (f.archetype) {
        const arch = m.archetype || "unclassified";
        if (arch !== f.archetype) return false;
      }
      if (winMs > 0) {
        const iso = m._last_seen_at || m._first_seen_at || m.posted_date;
        if (iso) {
          const ts = Date.parse(iso);
          if (Number.isFinite(ts) && (nowMs - ts) > winMs) return false;
        }
      }
      // Inclusion filter — UNION of pins and the allowed_locations
      // text list (mirrors the server's LocationFilter semantics). A
      // job passes if any pin radius covers it OR it substring-matches
      // any text entry. Ungeocodable locations always pass the pin
      // half (benefit of the doubt). Both empty = no inclusion filter.
      if (locationPins.length > 0 || allowedLocations.trim()) {
        const loc = (m.location || m._location || "").toLowerCase();
        const coords = locateJob(loc);
        const pinPasses = locationPins.length > 0
          ? (coords ? locationPins.some(p => haversineKm(p, coords) <= locationRadiusKm) : true)
          : false;
        const allowList = allowedLocations.split(",").map(s => s.trim().toLowerCase()).filter(Boolean);
        const textPasses = allowList.length > 0
          ? (loc ? allowList.some(a => loc.includes(a)) : true)
          : false;
        if (!pinPasses && !textPasses) return false;
      }
      return true;
    });
    // Apply column sort. Pinned rows always float to the top regardless
    // of sort key. Within each bucket we sort by the requested key.
    const sortKey = matchSort.key || "score";
    const sortDir = matchSort.dir === "asc" ? 1 : -1;
    const scoreOf = (m) => (m._match_score ?? m._score) || 0;
    const dateOf = (m) => {
      const iso = m.posted_date || m._first_seen_at || "";
      const ts = iso ? Date.parse(iso) : NaN;
      return Number.isFinite(ts) ? ts : 0;
    };
    const ghostOf = (m) => (m._fake && typeof m._fake.score === "number") ? m._fake.score : -1;
    const strOf = (v) => (v || "").toString().toLowerCase();
    const getKey = (m) => {
      switch (sortKey) {
        case "role": return strOf(m.title);
        case "company": return strOf(m.company);
        case "location": return strOf(m.location);
        case "posted": return dateOf(m);
        case "ghost": return ghostOf(m);
        case "score":
        default: return scoreOf(m);
      }
    };
    const cmp = (a, b) => {
      const ka = getKey(a), kb = getKey(b);
      if (ka < kb) return -1 * sortDir;
      if (ka > kb) return  1 * sortDir;
      return 0;
    };
    const starred = [];
    const rest = [];
    for (const m of filtered) (m._starred ? starred : rest).push(m);
    starred.sort(cmp);
    rest.sort(cmp);
    return starred.concat(rest);
  }, [matches, matchFilters, matchSort, view, locationPins, locationRadiusKm, allowedLocations]);

  // Lightweight registry counts for the filter bar. Computed from the
  // already-loaded matches array - no extra network call.
  const registryCounts = useMemo(() => {
    const total = matches.length;
    let unseen = 0, dismissed = 0, starred = 0;
    for (const m of matches) {
      if (!m._seen) unseen++;
      if (m._dismissed) dismissed++;
      if (m._starred) starred++;
    }
    return { total, unseen, dismissed, starred };
  }, [matches]);

  // Archetype bucket counts for the filter chip row. We count over the
  // SAME pool registryCounts does (all non-removed matches, pre-filter)
  // so the number next to each chip represents the max achievable if
  // the user clicks it. Chips with count 0 are hidden to keep the bar
  // tidy when a user hasn't run an analysis that hit every bucket yet.
  const archetypeCounts = useMemo(() => {
    const counts = {};
    for (const m of matches) {
      if (m._removed) continue;
      const slug = m.archetype || "unclassified";
      counts[slug] = (counts[slug] || 0) + 1;
    }
    return counts;
  }, [matches]);

  // ─── RATIONALE HANDLERS ───────────────────────────────────────
  // "Why this match?" button on the detail panel. The backend caches by
  // (company,title,url); we also cache client-side so a repeat panel
  // open is free and the button text can flip to "Regenerate" instantly.
  const rationaleKeyFor = (m) =>
    `${(m?.company || "").toLowerCase()}||${(m?.title || "").toLowerCase()}||${(m?.url || "").toLowerCase()}`;

  const fetchRationale = async (m, force = false) => {
    if (!m || rationaleBusy) return;
    setRationaleBusy(true); setRationaleError("");
    try {
      const r = await fetch(`${API}/api/match/rationale`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: m, force }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        setRationaleError(data.error || `HTTP ${r.status}`);
        return;
      }
      setRationales((prev) => ({ ...prev, [rationaleKeyFor(m)]: data.rationale }));
    } catch (e) {
      setRationaleError(e?.message || String(e));
    } finally {
      setRationaleBusy(false);
    }
  };

  // Clear the transient error when switching jobs so a previous error
  // doesn't bleed onto the new card.
  useEffect(() => { setRationaleError(""); }, [selectedJob?.title, selectedJob?.company]);

  // Reset cover-letter transient UI when the selected job changes. The
  // generated letters themselves are keyed by the match so we keep those
  // across switches - only the status/copy flags reset.
  useEffect(() => {
    setCoverLetterError("");
    setCoverLetterCopied(false);
    setCoverLetterNote("");
  }, [selectedJob?.title, selectedJob?.company]);

  // Generate (or regenerate) a cover letter for the currently-selected
  // match. The backend pulls the cached resume profile and hits local
  // Ollama - expect ~10 to 60s depending on model and JD length.
  const generateCoverLetter = async (m) => {
    if (!m || coverLetterBusy) return;
    setCoverLetterBusy(true);
    setCoverLetterError("");
    setCoverLetterCopied(false);
    try {
      const r = await fetch(`${API}/api/cover-letter`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job: {
            title: m.title,
            company: m.company,
            location: m.location || "",
            seniority: m.seniority || "",
            technologies: m.technologies || [],
            description: m.description || "",
            url: m.url || "",
            _match_score: m._match_score ?? m._score ?? 0,
          },
          tone: coverLetterTone,
          custom_note: coverLetterNote,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        setCoverLetterError(data.error || `HTTP ${r.status}`);
        return;
      }
      setCoverLetters((prev) => ({
        ...prev,
        [rationaleKeyFor(m)]: {
          text: data.text || "",
          saved_to: data.saved_to || null,
          tone: data.tone || coverLetterTone,
          model: data.model || "",
        },
      }));
    } catch (e) {
      setCoverLetterError(e?.message || String(e));
    } finally {
      setCoverLetterBusy(false);
    }
  };

  const copyCoverLetter = async (text) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCoverLetterCopied(true);
      setTimeout(() => setCoverLetterCopied(false), 1500);
    } catch {
      // Clipboard can fail under insecure contexts - fall back to a
      // textarea + execCommand so the button still does something.
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed"; ta.style.top = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        setCoverLetterCopied(true);
        setTimeout(() => setCoverLetterCopied(false), 1500);
      } catch { /* give up quietly */ }
    }
  };

  // Auto-mark the detail-pane job as seen - this covers the case where
  // the user opened the pane from the Brief tab or elsewhere without
  // clicking through the row first.
  useEffect(() => {
    if (selectedJob && !selectedJob._seen && selectedJob._registry_key) {
      setMatchState(selectedJob, "seen", true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedJob?._registry_key]);

  // History tab poller. Only runs when `view === "history"` so other
  // tabs don't pay for log tail reads. Cycle history is cheap; log tail
  // re-reads up to 2 MB of file on the server each tick so we poll
  // relatively slowly (5s) and let the user hit "Refresh" for immediate
  // feedback after a cycle.
  const refreshHistory = useCallback(async () => {
    setLogBusy(true);
    try {
      const [ch, lg] = await Promise.all([
        fetch(`${API}/api/cycle-history?n=50`).then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`${API}/api/logs?n=300&level=${encodeURIComponent(logLevel)}`).then((r) => r.ok ? r.json() : null).catch(() => null),
      ]);
      if (Array.isArray(ch)) setCycleHistory(ch);
      if (lg && typeof lg === "object") setLogs(lg);
    } finally { setLogBusy(false); }
  }, [logLevel]);

  useEffect(() => {
    if (view !== "history" || !live) return;
    refreshHistory();
    const id = setInterval(refreshHistory, 5000);
    return () => clearInterval(id);
  }, [view, live, refreshHistory]);

  // Story bank fetcher. Only runs when the Stories tab is active --
  // the bank file is read from disk server-side so we don't want to
  // fire it on every tab view. One-shot fetch on open + a manual
  // Refresh button. No polling.
  const refreshStoryBank = useCallback(async () => {
    setStoryBank((prev) => ({ ...prev, loading: true }));
    try {
      const r = await fetch(`${API}/api/story-bank`);
      const d = await r.json();
      setStoryBank({
        text: d.text || "",
        path: d.path || "",
        exists: !!d.exists,
        loading: false,
      });
    } catch (e) {
      setStoryBank((prev) => ({ ...prev, loading: false }));
    }
  }, []);

  useEffect(() => {
    if (view !== "stories") return;
    refreshStoryBank();
  }, [view, refreshStoryBank]);

  // Triage queue: only un-reacted matches, ordered by the
  // ghost-adjusted display score so the Blitz cursor surfaces the
  // best-first job — not whatever raw fit-score came back from the
  // matcher. Keeping suspect-tier jobs near the bottom mirrors the
  // ordering on the Matches tab. Memoised so the same list survives
  // re-renders while we keyboard-cursor through; regenerates when
  // matches/reactions change (e.g. after a reaction is recorded).
  const triageQueue = useMemo(() => {
    const unreacted = matches.filter((m) => !reactionFor(m));
    // Sort by adjusted score descending. Ties broken by posted date
    // (newest first) so two 80% matches don't fight for the front of
    // the queue arbitrarily.
    unreacted.sort((a, b) => {
      const da = displayScoreOf(a);
      const db = displayScoreOf(b);
      if (da !== db) return db - da;
      const ta = Date.parse(a.posted_date || a._first_seen_at || "") || 0;
      const tb = Date.parse(b.posted_date || b._first_seen_at || "") || 0;
      return tb - ta;
    });
    return unreacted;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matches, decisions]);

  // Clamp triageIndex whenever the queue shortens (a skip reaction shrinks
  // it by one; the cursor should stay pointed at "the next unreacted job").
  useEffect(() => {
    if (triageIndex >= triageQueue.length) setTriageIndex(Math.max(0, triageQueue.length - 1));
  }, [triageQueue.length, triageIndex]);

  // Reset cursor + refresh learned keywords when opening the tab.
  useEffect(() => {
    if (view !== "triage") return;
    setTriageIndex(0);
    if (live) {
      fetch(`${API}/api/triage/learned`).then((r) => r.ok ? r.json() : null).catch(() => null)
        .then((data) => { if (data) setTriageLearned(data); });
    }
  }, [view, live]);

  // Decisions tab: force a fresh /api/decisions fetch on entry so reactions
  // just made in Blitz show up without waiting for the next poll tick.
  // Previously a user would flip a card in Blitz, switch to Decisions and
  // see the old snapshot until the 15s poll elapsed.
  useEffect(() => {
    if (view !== "log" || !live) return;
    fetch(`${API}/api/decisions`).then((r) => r.ok ? r.json() : null).catch(() => null)
      .then((data) => { if (data && typeof data === "object") setDecisions(data); });
  }, [view, live]);

  // Brief-tab resource panel: fetch on entry and while the tab is
  // open. Backend endpoint is cheap (reads two small JSONs + a 1.5s
  // capped nvidia-smi probe) so polling every 30s is fine.
  useEffect(() => {
    if (view !== "brief" || !live) return;
    let cancelled = false;
    const pull = () => {
      fetch(`${API}/api/resources`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null)
        .then((data) => {
          if (!cancelled && data && typeof data === "object" && !data.error) {
            setResources(data);
          }
        });
    };
    pull();
    const id = setInterval(pull, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, [view, live]);

  const triageAct = async (action) => {
    // action in {"keep","skip","maybe"}. Keep + Skip flow through the
    // existing reactions endpoint so the Liked tab / decision log stay
    // authoritative. Maybe just advances the cursor.
    const current = triageQueue[triageIndex];
    if (!current) return;

    // Scoreboard update. Streak ticks on keep OR skip (a committed
    // decision), maybe is neutral and does NOT break the streak. Decision
    // gaps under 30s count toward the running average; longer gaps mean
    // the user walked away and shouldn't penalise the timer.
    const now = Date.now();
    setBlitzStats((s) => {
      const next = { ...s };
      if (action === "keep") next.keeps += 1;
      else if (action === "skip") next.skips += 1;
      else if (action === "maybe") next.maybes += 1;
      if (action === "keep" || action === "skip") {
        next.streak = s.streak + 1;
        next.bestStreak = Math.max(s.bestStreak, next.streak);
      }
      if (s.lastDecisionAt && (now - s.lastDecisionAt) < 30000) {
        next.totalDecisionMs = s.totalDecisionMs + (now - s.lastDecisionAt);
        next.decisionCount = s.decisionCount + 1;
      }
      next.lastDecisionAt = now;
      return next;
    });
    // Trigger the slide-out animation keyed on the action direction.
    // The view reads blitzFlash to pick direction + colour. Auto-clears
    // after 180ms so the next action retriggers cleanly.
    setBlitzFlash({ action, ts: now });
    setTimeout(() => setBlitzFlash((f) => (f && f.ts === now ? null : f)), 200);

    // Pip's feeding rules: keep + skip both feed. Maybe is neutral -
    // Pip rewards commitment, not ambivalence.
    if (action === "keep" || action === "skip") {
      setPip((p) => {
        const next = { lastFedAt: now, totalFeeds: p.totalFeeds + 1 };
        try { localStorage.setItem("sentinel.pip", JSON.stringify(next)); } catch {}
        return next;
      });
      setPipBounce((n) => n + 1);
      setTimeout(() => setPipBounce((n) => n), 300); // trigger a re-render tick
    }

    // Helper reaction burst. Nod on keep, shake on skip. Maybe stays
    // neutral - no animation, matches the "Pip rewards commitment" rule.
    if (action === "keep") {
      triggerBurst("nod", "decision_keep", 520);
      playComboSound("keep"); // small satisfying click
    } else if (action === "skip") {
      triggerBurst("shake", "decision_skip", 480);
      playComboSound("skip");
    }

    // Lifetime counters + day-log for the accountability story.
    if (action === "keep" || action === "skip" || action === "maybe") {
      setBlitzLifetime((lt) => {
        const todayIso = new Date().toISOString().slice(0, 10);
        const days = lt.days.includes(todayIso) ? lt.days : [...lt.days, todayIso].slice(-365);
        const next = {
          keeps: lt.keeps + (action === "keep" ? 1 : 0),
          skips: lt.skips + (action === "skip" ? 1 : 0),
          maybes: lt.maybes + (action === "maybe" ? 1 : 0),
          bestStreak: lt.bestStreak,  // updated below if beaten
          bestCombo: lt.bestCombo,    // ditto
          days,
        };
        try { localStorage.setItem("sentinel.blitz.lifetime", JSON.stringify(next)); } catch {}
        return next;
      });
    }

    // Combo detection: rolling 60s window of committed decisions (not
    // maybes). Fires a surprise overlay at 5 / 10 / 20 / 30 in-window
    // decisions, with escalating flavour copy. The tier gate stops the
    // overlay from retriggering every keystroke once the threshold is met.
    if (action === "keep" || action === "skip") {
      const WINDOW_MS = 60000;
      const recent = blitzRecentRef.current.filter((ts) => now - ts < WINDOW_MS);
      recent.push(now);
      blitzRecentRef.current = recent;
      const count = recent.length;
      const tiers = [
        { n: 30, label: "LEGENDARY", sub: "30 in a minute. Recruiters hate this one trick." },
        { n: 20, label: "UNSTOPPABLE", sub: "20 in 60s. Pip is in tears of joy." },
        { n: 10, label: "ON A ROLL", sub: "10 in 60s. Keep the momentum." },
        { n: 5,  label: "WARMED UP", sub: "5 in 60s. Starting to flow." },
      ];
      let newTier = 0;
      for (const tier of tiers) {
        if (count >= tier.n) { newTier = tier.n; break; }
      }
      if (newTier > blitzComboTier) {
        const chosen = tiers.find((tt) => tt.n === newTier);
        setBlitzSurprise({ label: chosen.label, sub: chosen.sub, ts: now });
        setBlitzComboTier(newTier);
        setBlitzLifetime((lt) => {
          const next = { ...lt, bestCombo: Math.max(lt.bestCombo, newTier) };
          try { localStorage.setItem("sentinel.blitz.lifetime", JSON.stringify(next)); } catch {}
          return next;
        });
        // HALO-STYLE FANFARE. Fires on every new tier crossing; pitch
        // and duration scale with `newTier`. Also bounces Joby to
        // 'celebrate' for a full second so the sound has a visual mate.
        playComboFanfare(newTier);
        triggerBurst("celebrate", "pet_celebrate", 1000);
        setTimeout(() => setBlitzSurprise((s) => (s && s.ts === now ? null : s)), 1600);
      } else if (count < 5) {
        // Window drained - reset the gate so combos can retrigger.
        if (blitzComboTier !== 0) setBlitzComboTier(0);
      }
    }

    // Lifetime streak + session-best update (piggybacks on setBlitzStats
    // already having computed the new streak above).
    if (action === "keep" || action === "skip") {
      setBlitzLifetime((lt) => {
        // Use the freshest streak by recomputing from session state.
        const sessionStreak = blitzStats.streak + 1;  // before the upcoming state flush
        if (sessionStreak > lt.bestStreak) {
          const next = { ...lt, bestStreak: sessionStreak };
          try { localStorage.setItem("sentinel.blitz.lifetime", JSON.stringify(next)); } catch {}
          return next;
        }
        return lt;
      });
    }

    if (action === "maybe") {
      setTriageIndex((i) => i + 1);
      return;
    }
    const reactAs = action === "keep" ? "up" : "down";
    await setReaction(current, reactAs);
    // Re-fetch learned keywords after any real reaction so the sidebar
    // reflects the new signal.
    if (live) {
      fetch(`${API}/api/triage/learned`).then((r) => r.ok ? r.json() : null).catch(() => null)
        .then((data) => { if (data) setTriageLearned(data); });
    }
    // Do NOT advance triageIndex here - the reaction shortens triageQueue,
    // so the same index now points at the NEXT job. Clamping effect handles
    // the end-of-queue case.
  };

  // Keyboard shortcuts: arrow-key-first for speed (← skip, → keep, ↓ maybe).
  // K/S/M aliases retained for muscle memory. ↑ steps back one card for
  // corrections. Only active while the Blitz tab is visible; ignores keys
  // when focus is in an input or textarea.
  useEffect(() => {
    if (view !== "triage") return;
    const onKey = (e) => {
      const tag = (e.target?.tagName || "").toUpperCase();
      if (tag === "INPUT" || tag === "TEXTAREA" || e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "arrowright" || k === "k" || k === "l") { e.preventDefault(); triageAct("keep"); }
      else if (k === "arrowleft" || k === "s" || k === "j") { e.preventDefault(); triageAct("skip"); }
      else if (k === "arrowdown" || k === "m" || k === " ") { e.preventDefault(); triageAct("maybe"); }
      else if (k === "arrowup") { e.preventDefault(); setTriageIndex((i) => Math.max(0, i - 1)); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, triageQueue.length, triageIndex]);

  // ─── CHAT HANDLERS ────────────────────────────────────────────
  // Chat now travels with a `context` payload so the local model knows
  // which screen the user is on and which job (if any) is expanded.
  // The backend renders this as a "=== CURRENT VIEW ===" block inside
  // the system prompt - cheap to send, meaningful lift in answer quality.
  const sendChat = async () => {
    const text = chatInput.trim();
    if (!text || chatBusy) return;
    const next = [...chatMessages, { role: "user", content: text }];
    setChatMessages(next); setChatInput(""); setChatBusy(true);
    const context = {
      view,
      selectedJob: selectedJob ? {
        title: selectedJob.title,
        company: selectedJob.company,
        location: selectedJob.location,
        match_score: selectedJob.match_score,
        remote: selectedJob.remote,
      } : null,
      filters: matchFilters,
      visible_match_count: matchTier.length,
    };
    try {
      const r = await fetch(`${API}/api/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next, context }),
      });
      const data = await r.json().catch(() => ({}));
      const reply = data.reply || data.error || "(no reply)";
      setChatMessages((prev) => [...prev, { role: "assistant", content: reply }]);
    } catch (e) {
      setChatMessages((prev) => [...prev, { role: "assistant", content: `(chat error: ${e?.message || e})` }]);
    } finally { setChatBusy(false); }
  };

  const clearChat = () => {
    if (chatMessages.length === 0) return;
    if (window.confirm("Clear chat history? This can't be undone.")) {
      setChatMessages([]);
    }
  };

  const companyData = buildCompanyChart(matches, market);
  const remoteData = buildRemoteChart(market);
  const fitGapForJob = (title, company) => fitGaps.find(f => f.title === title && f.company === company);

  const decisionList = decisions?.decisions || [];
  const reactionsList = Object.values(decisions?.reactions || {});

  // Single Matches bucket - previously we split match-tier vs maybe-tier,
  // but Eddie wants everything surfaced in one list so borderline hits
  // aren't hidden behind a secondary tab. Star/dismiss on individual
  // rows carries the signal instead.
  const matchTier = matches;
  const tabs = [
    { id: "brief", label: "Brief" },
    { id: "matches", label: `Matches (${matchTier.length})` },
    { id: "triage", label: "Blitz" },
    { id: "market", label: "Market" },
    { id: "stories", label: "Stories" },
    { id: "log", label: "Decisions" },
    { id: "history", label: "History" },
    { id: "profile", label: "Profile" },
    { id: "pipeline", label: "Settings" },
  ];

  // Brief metrics: match count, avg cycle time, median match latency, verified rate.
  // "Verified" = share of current matches NOT flagged suspect by the ghost-job
  // detector. Showcases the detector and is genuinely live - unlike source
  // count, which is effectively config-derived and barely moves.
  const matchMedianMs = status?.match?.median_latency_ms;
  const avgCycleSec = status?.avg_cycle_seconds;
  const avgScrapeSec = status?.avg_scrape_seconds;
  const avgPipelineSec = status?.avg_pipeline_seconds;
  const verifiedRate = useMemo(() => {
    if (!matches.length) return "—";
    const clean = matches.filter(m => !m._fake?.is_suspect).length;
    return `${Math.round(clean / matches.length * 100)}%`;
  }, [matches]);

  // Live cycle progress (populated by orchestrator._set_stage on each phase
  // boundary, surfaced via /api/status.progress). When cycle_in_progress is
  // false AND stage is "idle" we hide the card so the Brief tab stays quiet
  // at rest. We still render it when cycle_in_progress toggles even before
  // the first _set_stage call so the user gets immediate feedback.
  const progress = status?.progress;
  const progressActive = !!status?.cycle_in_progress || (progress && progress.stage && progress.stage !== "idle");
  // "Last cycle X ago" chip. We look at last_cycle_ts (written by
  // _record_cycle_duration) rather than _pipeline_state.last_cycle because
  // the latter is a dict of stats and doesn't include a timestamp.
  const lastCycleAgo = useMemo(() => {
    const ts = status?.last_cycle_ts;
    if (!ts) return null;
    try {
      const ms = Date.now() - new Date(ts).getTime();
      if (ms < 0) return null;
      const s = Math.round(ms / 1000);
      if (s < 60) return `${s}s ago`;
      const m = Math.round(s / 60);
      if (m < 60) return `${m}m ago`;
      const h = Math.round(m / 60);
      return `${h}h ago`;
    } catch { return null; }
  }, [status?.last_cycle_ts]);
  // Source count still shown in Settings; kept here for that view.
  const sourceCount = useMemo(() => {
    const ing = config?.ingest || {};
    const n =
      (ing.greenhouse_companies?.length || 0) +
      (ing.lever_companies?.length || 0) +
      (ing.ashby_companies?.length || 0) +
      (ing.enable_apple ? 1 : 0) +
      (ing.enable_amazon ? 1 : 0) +
      (ing.enable_google ? 1 : 0) +
      (ing.enable_meta ? 1 : 0) +
      (ing.enable_microsoft ? 1 : 0) +
      2; // RemoteOK, Jobicy
    return n || "—";
  }, [config]);

  // ─── RENDER ────────────────────────────────────────────────────
  return (
    <div style={{ fontFamily: "'Outfit', 'Helvetica Neue', sans-serif", background: t.bg, color: t.text, minHeight: "100vh", opacity: ready ? 1 : 0, transition: "opacity 0.4s, background 0.3s, color 0.3s" }}>
      <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Outfit:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
      {!isDark && <div style={{ position: "fixed", inset: 0, opacity: t.grain, backgroundImage: t.paper, pointerEvents: "none", zIndex: 0 }} />}

      <div className="sentinel-shell" style={{ position: "relative", zIndex: 1, width: "100%", maxWidth: "min(1280px, 100%)", margin: "0 auto", padding: "0 clamp(12px, 3vw, 32px)", paddingBottom: chatOpen ? "min(480px, 60vh)" : "60px", display: "flex", flexDirection: "column", minHeight: "100vh" }}>

        {/* ── HEADER ── */}
        <header className="sentinel-header" style={{ padding: "40px 0 28px", borderBottom: `3px solid ${t.text}`, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", flexWrap: "wrap" }}>
          <div>
            {/* Three-state status chip: DISCONNECTED when the backend
                can't be reached, PIPELINE RUNNING while a cycle is in
                progress, LIVE when the backend is up and idle. Previous
                two-state chip was ambiguous - a "live" dot during a
                10-minute cycle didn't tell you anything was happening. */}
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", letterSpacing: "2px", textTransform: "uppercase", color: t.textDim }}>Sentinel</div>
              {(() => {
                const statusColour = !live ? t.accent : pipelineRunning ? (t.warn || t.accent) : t.good;
                const statusLabel = !live ? "DISCONNECTED" : pipelineRunning ? "PIPELINE RUNNING" : "LIVE";
                return (
                  <>
                    <div style={{
                      width: "6px", height: "6px", borderRadius: "50%",
                      background: statusColour,
                      animation: pipelineRunning && live ? "sentinelPulse 1.4s ease-in-out infinite" : "none",
                    }} />
                    <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: statusColour }}>{statusLabel}</span>
                  </>
                );
              })()}
            </div>
            <h1 style={{ fontFamily: "'Instrument Serif', Georgia, serif", fontSize: "42px", fontWeight: 400, margin: 0, letterSpacing: "-1px" }}>Command Center</h1>
          </div>
          {(() => {
            // ── Run-cycle control group ───────────────────────────────
            // THREE buttons that trigger cycles, laid out left-to-right
            // in importance order:
            //
            //   [ Run Both ] [ Run Pipeline ] [ Run Scraper ]
            //    primary       secondary        secondary
            //    (orange)      (outlined)       (outlined)
            //
            // State rules (applied uniformly to all three via runBtnStyle):
            //   IDLE + ready     → the button's own "variant" styling
            //   THIS is running  → highlighted (warnBg + warn text)
            //   OTHER is running → disabled-neutral (bgAlt + faint text)
            //   not live / no-setup → disabled-neutral
            //
            // Only the button whose tiers actually match current_tiers
            // shows a "Running..." label; the others keep their idle
            // label but go disabled. That way the user ALWAYS sees the
            // active label in exactly one place -- no more double
            // "Running Both..." on two buttons at once.
            const currentTiers = status?.current_tiers || [];
            const tiersMatch = (wanted) =>
              pipelineRunning &&
              wanted.length === currentTiers.length &&
              wanted.every(x => currentTiers.includes(x));

            const runBtnStyle = (variant, isActive) => {
              // Base: font, padding, transition -- identical on all three
              // so they can never drift. Only the color layer changes.
              const base = {
                fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px",
                fontWeight: 600, letterSpacing: "1px", textTransform: "uppercase",
                borderRadius: "4px", padding: "10px 18px",
                transition: "all 0.2s",
                cursor: (pipelineRunning || !live) ? "default" : "pointer",
                opacity: !live ? 0.5 : 1,
              };
              // Active (this button's cycle is running): warn highlight.
              if (isActive) {
                return { ...base, background: t.warnBg, color: t.warn,
                         border: `1px solid ${t.warn}` };
              }
              // Another cycle running (or not live / no setup): disabled.
              if (pipelineRunning || !live || !setupState.setup_completed) {
                return { ...base, background: t.bgAlt, color: t.textFaint,
                         border: `1px solid ${t.border}`,
                         ...(!setupState.setup_completed && live
                             ? { borderStyle: "dashed" } : {}) };
              }
              // Idle + ready: per-variant styling.
              if (variant === "primary") {
                return { ...base, background: t.accent, color: "#fff",
                         border: `1px solid ${t.accent}` };
              }
              // secondary
              return { ...base, background: "transparent", color: t.text,
                       border: `1px solid ${t.border}` };
            };

            const clickGuarded = (fn) => () => {
              if (!setupState.setup_completed) {
                setWizardOpen(true); setWizardDismissed(false); return;
              }
              fn();
            };

            const bothActive = tiersMatch(["fast", "slow"]);
            const fastActive = tiersMatch(["fast"]);
            const slowActive = tiersMatch(["slow"]);

            // All three buttons go through <Button> now. `active` prop
            // forces the warn/amber treatment so there's zero ambiguity
            // about which action is currently running. Disabled state
            // is handled by the primitive (neutral gray) so dismiss and
            // idle look identical across the three.
            const anyDisabled = pipelineRunning || !live || !setupState.setup_completed;
            return (
              <div style={{ display: "flex", alignItems: "center", gap: "8px", paddingTop: "8px" }}>
                <Button t={t}
                  tone="accent"
                  size="md"
                  active={bothActive}
                  running={bothActive}
                  disabled={anyDisabled}
                  onClick={clickGuarded(runBoth)}
                  title={!setupState.setup_completed ? "Finish setup first"
                    : pipelineRunning ? "Cycle in progress"
                    : "Run fast pipeline then slow scrapers in one cycle"}>
                  {bothActive ? "Running Both..."
                    : !setupState.setup_completed && live ? "Finish Setup"
                    : "Run Both"}
                </Button>

                <Button t={t}
                  tone="neutral"
                  size="md"
                  active={fastActive}
                  running={fastActive}
                  disabled={anyDisabled}
                  onClick={clickGuarded(runCycle)}
                  title={!setupState.setup_completed ? "Finish setup first"
                    : pipelineRunning ? "Cycle in progress"
                    : "Run fast ATS pipeline only (seconds)"}>
                  {fastActive ? "Running..." : "Run Pipeline"}
                </Button>

                <Button t={t}
                  tone="neutral"
                  size="md"
                  active={slowActive}
                  running={slowActive}
                  disabled={anyDisabled}
                  onClick={clickGuarded(runScraper)}
                  title={!setupState.setup_completed ? "Finish setup first"
                    : pipelineRunning ? "Cycle in progress"
                    : "Run Playwright scrapers only (minutes; Apple/Meta/MS)"}>
                  {slowActive ? "Scraping..." : "Run Scraper"}
                </Button>

                <button onClick={() => setIsDark(!isDark)} style={{
                  background: "none", border: `1px solid ${t.border}`,
                  borderRadius: "4px", padding: "8px 10px", cursor: "pointer",
                  color: t.textDim, fontSize: "14px", marginLeft: "4px",
                }}>
                  {isDark ? "☀" : "☽"}
                </button>
                {runMsg && (
                  <div style={{
                    marginLeft: "12px",
                    fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px",
                    color: t.warn || t.accent,
                    background: t.bgAlt, border: `1px solid ${t.warn || t.accent}`,
                    borderRadius: "4px", padding: "6px 10px",
                  }}>
                    {runMsg}
                  </div>
                )}
              </div>
            );
          })()}
        </header>

        {/* ── NAV ── */}
        <nav style={{ display: "flex", gap: 0, borderBottom: `1px solid ${t.border}`, flexWrap: "wrap" }}>
          {tabs.map(tab => (
            <button key={tab.id} onClick={() => { setView(tab.id); setSelectedJob(null); }} style={{
              fontFamily: "'Outfit', sans-serif", fontSize: "13px", fontWeight: view === tab.id ? 600 : 400,
              color: view === tab.id ? t.text : t.textDim,
              background: "none", border: "none",
              borderBottom: view === tab.id ? `2px solid ${t.accent}` : "2px solid transparent",
              padding: "14px 18px", cursor: "pointer", transition: "all 0.15s",
            }}>{tab.label}</button>
          ))}
        </nav>

        {/* ── MAIN CONTENT ── */}
        <div style={{ flex: 1, display: "flex", paddingTop: "32px", paddingBottom: "60px", gap: "32px" }}>

          <div style={{ flex: 1, minWidth: 0 }}>

            {/* ── BRIEF ── */}
            {view === "brief" && (<>
              {/* Mission statement */}
              <div style={{ borderLeft: `3px solid ${t.accent}`, paddingLeft: "16px", marginBottom: "32px" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "6px" }}>Mission</div>
                <p style={{ fontFamily: "'Instrument Serif', serif", fontSize: "22px", lineHeight: 1.4, margin: 0, color: t.text, fontStyle: "italic" }}>
                  Your personalised, private, and free recruiter.
                </p>
              </div>


              {/* Ollama model-fallback banner. When a configured model
                  returned 404 at runtime the pipeline quietly fell back
                  to a smaller one; surface the substitution here so the
                  user can either pull the missing model or update the
                  config to stop fighting it. Only renders when there's
                  actually a substitution in play — otherwise invisible. */}
              {status?.model_fallback?.substitutes && Object.keys(status.model_fallback.substitutes).length > 0 && (
                <div style={{
                  marginBottom: "28px", padding: "12px 16px",
                  background: t.bgAlt, border: `1px solid ${t.warn || t.accent}`, borderRadius: "6px",
                  fontSize: "12px", color: t.textMid, lineHeight: 1.6,
                }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 700, letterSpacing: "1px", color: t.warn || t.accent, marginBottom: "4px" }}>
                    MODEL FALLBACK ACTIVE
                  </div>
                  {Object.entries(status.model_fallback.substitutes).map(([missing, sub]) => (
                    <div key={missing} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px" }}>
                      <code style={{ color: t.text }}>{missing}</code> → <code style={{ color: t.accent }}>{sub}</code>
                    </div>
                  ))}
                  <div style={{ marginTop: "6px" }}>
                    Pull the missing model (<code style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px" }}>ollama pull {Object.keys(status.model_fallback.substitutes)[0]}</code>) or change the model in Settings → Model Configuration.
                  </div>
                </div>
              )}

              {/* Idle-state "last ran" card. Occupies the same slot as the
                  in-progress card so the Brief tab always tells the user
                  whether the pipeline is fresh. When a cycle is running
                  this block is skipped in favour of the live progress card
                  below. Keeps the "am I looking at stale data?" anxiety to
                  a minimum without adding a second persistent indicator. */}
              {!progressActive && lastCycleAgo && (
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", border: `1px solid ${t.border}`, background: t.bgAlt, borderRadius: "6px", padding: "14px 20px", marginBottom: "28px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <div style={{ width: "8px", height: "8px", borderRadius: "50%", background: t.textFaint }} />
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>Idle</div>
                  </div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.textDim }}>
                    Last ran {lastCycleAgo}
                    {status?.next_cycle_in && <span style={{ color: t.textFaint }}> · next in {status.next_cycle_in}</span>}
                  </div>
                </div>
              )}

              {/* Live progress card - only shown while a cycle is running.
                  Server pushes stage + rolling counts through /api/status,
                  poll ticks every 2s when active, 8s when idle. */}
              {progressActive && (
                <div style={{ border: `1px solid ${t.border}`, background: t.bgAlt, borderRadius: "6px", padding: "18px 20px", marginBottom: "28px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <div style={{ width: "8px", height: "8px", borderRadius: "50%", background: t.accent, boxShadow: `0 0 0 4px ${t.accentBg}`, animation: "pulse 1.4s ease-in-out infinite" }} />
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>Cycle {progress?.cycle || status?.last_cycle?.cycle || "—"} in progress</div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.textDim }}>
                        {progress?.stage_label || "Starting"} {progress?.stage_index ? `${progress.stage_index}/${progress.stage_count || 8}` : ""}
                      </div>
                      {/* Which model is actually doing the work right now.
                          The backend publishes progress.models keyed by stage
                          so the UI can show "parse (qwen2.5:14b)" without
                          hard-coding model names here. */}
                      {progress?.stage && progress?.models?.[progress.stage] && (
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textFaint, marginTop: "2px" }}>
                          via {progress.models[progress.stage]}
                        </div>
                      )}
                    </div>
                  </div>
                  {/* Progress bar */}
                  <div style={{ height: "4px", background: t.border, borderRadius: "2px", overflow: "hidden", marginBottom: "14px" }}>
                    <div style={{
                      width: `${Math.min(100, ((progress?.stage_index || 0) / (progress?.stage_count || 8)) * 100)}%`,
                      height: "100%", background: t.accent, transition: "width 0.4s ease",
                    }} />
                  </div>
                  {/* Rolling counts - only show non-zero so idle phases don't shout.
                      Scoring gets its own X/Y pill because it ticks every posting
                      and is the phase where the user is most likely to be staring
                      at the screen wondering how long this will take. */}
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "18px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px" }}>
                    {progress?.counts?.scored_total > 0 && (
                      <div>
                        <span style={{ color: t.textDim }}>Scoring: </span>
                        <span style={{ color: t.text, fontWeight: 600 }}>{progress.counts.scored || 0}/{progress.counts.scored_total}</span>
                      </div>
                    )}
                    {[
                      ["Ingested", progress?.counts?.ingested],
                      ["Parsed", progress?.counts?.parsed],
                      ["QA pass", progress?.counts?.qa_pass],
                      ["Ghost blocked", progress?.counts?.fake_blocked],
                      ["New jobs", progress?.counts?.new_jobs],
                      ["Matches", progress?.counts?.matches],
                      ["Fit-gaps", progress?.counts?.fit_gaps],
                      ["Resumes", progress?.counts?.resumes],
                    ].filter(([, v]) => v != null && v > 0).map(([label, v]) => (
                      <div key={label}>
                        <span style={{ color: t.textDim }}>{label}: </span>
                        <span style={{ color: t.text, fontWeight: 600 }}>{v}</span>
                      </div>
                    ))}
                    {!Object.values(progress?.counts || {}).some(v => v > 0) && !(progress?.counts?.scored_total > 0) && (
                      <div style={{ color: t.textDim }}>Warming up...</div>
                    )}
                  </div>
                </div>
              )}

              {/* Pulse keyframe for the live progress dot above */}
              <style>{`@keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.35 } }`}</style>

              {/* Brief-tile metric strip. "Avg cycle" used to be a single
                  tile that lumped scraper time and pipeline/LLM time
                  together, which hid whether slowness was ingest-side
                  (scraper 404s, Playwright retries) or LLM-side (model
                  spill to CPU, cold-start latency). We now show each
                  bookend as a standalone tile and keep the total
                  on-hover as a tooltip so the at-a-glance story is
                  "scrape X, pipeline Y" and power users can still see
                  the sum. */}
              <div data-responsive="metric-strip" style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "1px", background: t.border, marginBottom: "40px" }}>
                {[
                  { n: matchTier.length, l: "Matches" },
                  { n: verifiedRate, l: "Verified" },
                  { n: formatSeconds(avgScrapeSec), l: "Avg scrape", hint: "Scrape-only time (ingest phase only). Rising? Check dead ATS slugs or big-tech SPA toggles." },
                  { n: formatSeconds(avgPipelineSec), l: "Avg pipeline", hint: "Parse + QA + match + analyze + resume. Rising? Check model fallback banner or drop to smaller models." },
                  { n: formatMs(matchMedianMs), l: "Match latency" },
                ].map((s, i) => (
                  <div key={i} title={s.hint || `Total cycle: ${formatSeconds(avgCycleSec)}`} style={{ background: t.bg, padding: "24px 20px" }}>
                    <div style={{ fontFamily: "'Instrument Serif', serif", fontSize: "32px" }}>{s.n}</div>
                    <div style={{ fontSize: "12px", fontWeight: 600, marginTop: "2px" }}>{s.l}</div>
                  </div>
                ))}
              </div>

              {/* Per-source ingest breakdown (#89). Surfaces "where did
                  jobs come from last cycle" so thin match counts can be
                  traced to a specific quiet source. Top 8 sources by
                  job count; errors-only rows show their error count. */}
              {status?.ingest_sources?.sources && Object.keys(status.ingest_sources.sources).length > 0 && (
                <div style={{
                  border: `1px solid ${t.border}`, borderRadius: "4px",
                  padding: "14px 16px", marginBottom: "32px", background: t.bgAlt,
                }}>
                  <div style={{
                    fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px",
                    letterSpacing: "1.5px", textTransform: "uppercase",
                    color: t.textDim, fontWeight: 600, marginBottom: "10px",
                  }}>
                    Ingest sources (last cycle)
                  </div>
                  <div style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                    gap: "6px 16px", fontSize: "12px",
                  }}>
                    {Object.entries(status.ingest_sources.sources)
                      .slice(0, 16)
                      .map(([label, counts]) => {
                        const jobs = counts?.jobs || 0;
                        const errs = counts?.errors || 0;
                        return (
                          <div key={label} style={{ display: "flex", justifyContent: "space-between", gap: "10px" }}>
                            <span style={{ color: jobs > 0 ? t.text : t.textDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
                            <span style={{ color: jobs > 0 ? t.text : (errs > 0 ? (t.warn || t.textDim) : t.textDim), fontWeight: 600 }}>
                              {jobs}{errs ? ` (${errs} err)` : ""}
                            </span>
                          </div>
                        );
                      })}
                  </div>
                </div>
              )}

              {/* Dead-slug banner (#93). Tells the user which ATS slugs
                  returned 404 on the last ingest so they know why match
                  counts might be thin without grepping the log. Only
                  renders when the backend actually reported dead slugs. */}
              {Array.isArray(status?.dead_slugs) && status.dead_slugs.length > 0 && (
                <div style={{
                  border: `1px solid ${t.warn || t.border}`, borderLeft: `3px solid ${t.warn || t.accent}`,
                  background: t.warnBg || t.bgAlt, color: t.text,
                  borderRadius: "4px", padding: "12px 16px", marginBottom: "32px",
                  fontSize: "13px", lineHeight: 1.45,
                }}>
                  <div style={{ fontWeight: 600, marginBottom: "4px" }}>
                    {status.dead_slugs.length} ATS slug{status.dead_slugs.length === 1 ? "" : "s"} returned 404 last cycle
                  </div>
                  <div style={{ color: t.textDim }}>
                    {status.dead_slugs.slice(0, 6).map(d => `${d.source}:${d.slug}`).join(", ")}
                    {status.dead_slugs.length > 6 ? `, +${status.dead_slugs.length - 6} more` : ""}
                  </div>
                  <div style={{ color: t.textDim, marginTop: "6px", fontSize: "12px" }}>
                    Remove these from <code>config.json</code> or re-probe with <code>python scripts/probe_slugs.py --extras</code>.
                  </div>
                </div>
              )}

              {/* The old "Last cycle X ago" footer under the metrics grid
                  was removed once the idle-state "Last ran" card took over
                  that signal in the same slot as the in-progress indicator.
                  Keep this comment so we don't re-add a second duplicate. */}

              {/* Resource panel (#Tier2). Shows what SENTINEL is costing
                  the machine right now: GPU VRAM (if nvidia-smi is
                  available), process RAM, match-mode latency, median
                  cycle wall-clock. Backed by /api/resources. Every
                  field is optional - if the probe failed we just omit
                  that pill rather than faking numbers. */}
              {resources && (
                <div style={{
                  border: `1px solid ${t.border}`, background: t.bgAlt,
                  borderRadius: "4px", padding: "14px 20px", marginBottom: "32px",
                }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "10px" }}>
                    Resources
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "22px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px" }}>
                    {resources.gpu && (
                      <div>
                        <span style={{ color: t.textDim }}>GPU: </span>
                        <span style={{ color: t.text, fontWeight: 600 }}>
                          {resources.gpu.used_mib}/{resources.gpu.total_mib} MiB
                          {resources.gpu.used_pct != null && ` (${resources.gpu.used_pct}%)`}
                        </span>
                        {resources.gpu.name && (
                          <div style={{ color: t.textFaint, fontSize: "10px" }}>{resources.gpu.name}</div>
                        )}
                      </div>
                    )}
                    {resources.memory && (
                      <div>
                        <span style={{ color: t.textDim }}>RAM: </span>
                        <span style={{ color: t.text, fontWeight: 600 }}>
                          {Math.round(resources.memory.rss_mib)} MiB
                        </span>
                        <div style={{ color: t.textFaint, fontSize: "10px" }}>
                          system {resources.memory.system_used_pct}%
                        </div>
                      </div>
                    )}
                    {resources.cycles?.median_seconds != null && (
                      <div>
                        <span style={{ color: t.textDim }}>Cycle: </span>
                        <span style={{ color: t.text, fontWeight: 600 }}>
                          {Math.round(resources.cycles.median_seconds)}s median
                        </span>
                        <div style={{ color: t.textFaint, fontSize: "10px" }}>
                          {resources.cycles.count} cycles run
                        </div>
                      </div>
                    )}
                    {resources.match?.mode && (
                      <div>
                        <span style={{ color: t.textDim }}>Match: </span>
                        <span style={{ color: t.text, fontWeight: 600 }}>
                          {resources.match.mode}
                        </span>
                        {resources.match.median_latency_ms != null && (
                          <div style={{ color: t.textFaint, fontSize: "10px" }}>
                            {Math.round(resources.match.median_latency_ms)} ms/job
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Profile card - what the pipeline thinks it knows about you */}
              {resumeState.has_resume && (
                <div style={{ border: `1px solid ${t.border}`, borderRadius: "4px", padding: "20px 22px", marginBottom: "32px", background: t.bgAlt }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "12px" }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>
                      Candidate Profile
                      {resumeProfile?._fallback && (
                        <span style={{ marginLeft: "8px", padding: "1px 6px", background: t.warnBg, color: t.warn, borderRadius: "3px", fontSize: "9px" }}>FALLBACK</span>
                      )}
                    </div>
                    <button onClick={reparseResume} disabled={!live || reparseBusy} style={{
                      fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px", textTransform: "uppercase",
                      background: "none", border: `1px solid ${t.border}`, color: t.textDim, borderRadius: "3px",
                      padding: "4px 10px", cursor: !live || reparseBusy ? "default" : "pointer", opacity: !live || reparseBusy ? 0.5 : 1,
                    }}>
                      {reparseBusy ? "Parsing..." : "Re-parse"}
                    </button>
                  </div>
                  {resumeProfile ? (
                    <>
                      {resumeProfile.summary && (
                        <p style={{ fontFamily: "'Instrument Serif', serif", fontSize: "17px", lineHeight: 1.45, margin: "0 0 14px", color: t.text }}>
                          {resumeProfile.summary}
                        </p>
                      )}
                      {/* Seniority + Years fall back to the Settings
                          preferences (current_level / years_experience)
                          when the resume-parse LLM leaves them blank,
                          which it often does even for resumes that
                          clearly state both in the narrative. Shows a
                          small "set" label when the value comes from
                          Settings rather than the parse, so the user can
                          tell which path populated it. */}
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "16px", marginBottom: "12px" }}>
                        {(() => {
                          const parseSeniority = resumeProfile.seniority || "";
                          const parseYears = Number(resumeProfile.years_experience) || 0;
                          const displaySeniority = parseSeniority || currentLevel || "";
                          const displayYears = parseYears || Number(yearsExperience) || 0;
                          const seniorityFromSettings = !parseSeniority && !!currentLevel;
                          const yearsFromSettings = !parseYears && Number(yearsExperience) > 0;
                          return (
                            <>
                              <div>
                                <div style={{ fontSize: "10px", color: t.textDim, textTransform: "uppercase", letterSpacing: "1px", marginBottom: "2px" }}>
                                  Seniority
                                  {seniorityFromSettings && <span style={{ marginLeft: "6px", fontSize: "9px", color: t.textDim, opacity: 0.7 }}>from settings</span>}
                                </div>
                                <div style={{ fontSize: "14px", fontWeight: 500 }}>{displaySeniority ? displaySeniority.charAt(0).toUpperCase() + displaySeniority.slice(1) : "—"}</div>
                              </div>
                              <div>
                                <div style={{ fontSize: "10px", color: t.textDim, textTransform: "uppercase", letterSpacing: "1px", marginBottom: "2px" }}>
                                  Years
                                  {yearsFromSettings && <span style={{ marginLeft: "6px", fontSize: "9px", color: t.textDim, opacity: 0.7 }}>from settings</span>}
                                </div>
                                <div style={{ fontSize: "14px", fontWeight: 500 }}>{displayYears || "—"}</div>
                              </div>
                              <div>
                                <div style={{ fontSize: "10px", color: t.textDim, textTransform: "uppercase", letterSpacing: "1px", marginBottom: "2px" }}>Domains</div>
                                <div style={{ fontSize: "14px", fontWeight: 500 }}>{(resumeProfile.domains || []).slice(0, 3).join(", ") || "—"}</div>
                              </div>
                            </>
                          );
                        })()}
                      </div>
                      {(resumeProfile.technologies || []).length > 0 && (
                        <div style={{ marginTop: "10px" }}>
                          <div style={{ fontSize: "10px", color: t.textDim, textTransform: "uppercase", letterSpacing: "1px", marginBottom: "6px" }}>Top Technologies</div>
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                            {(resumeProfile.technologies || []).slice(0, 12).map((tech, i) => (
                              <span key={i} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", padding: "3px 8px", background: t.bg, border: `1px solid ${t.borderLight}`, borderRadius: "3px", color: t.textMid }}>{tech}</span>
                            ))}
                          </div>
                        </div>
                      )}
                      {(resumeProfile.target_roles || []).length > 0 && (
                        <div style={{ marginTop: "14px", fontSize: "12px", color: t.textDim }}>
                          <span style={{ fontWeight: 600 }}>Target roles: </span>{resumeProfile.target_roles.join(" · ")}
                        </div>
                      )}
                    </>
                  ) : (
                    <p style={{ fontSize: "13px", color: t.textDim, margin: 0 }}>
                      Parsing resume into a structured profile... Press Re-parse if this stays empty.
                    </p>
                  )}
                </div>
              )}

              <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: "0 0 16px" }}>Recent Matches</h2>
              <div style={{ borderTop: `2px solid ${t.text}` }}>
                {matches.length === 0 && (
                  <div style={{ padding: "24px 0", borderBottom: `1px solid ${t.borderLight}`, fontSize: "13px", color: t.textDim, lineHeight: 1.6 }}>
                    {live
                      ? "No matches yet. Press Run Pipeline above to start a cycle, or loosen filters in Settings."
                      : "Pipeline offline. Start SENTINEL to see live matches."}
                  </div>
                )}
                {matches.slice(0, 5).map((m, i) => (
                  <div key={i} onClick={() => { setSelectedJob(m); setView("matches"); }} style={{
                    display: "grid", gridTemplateColumns: "2.5fr 1fr 0.6fr", padding: "12px 0", borderBottom: `1px solid ${t.borderLight}`, cursor: "pointer",
                    transition: "background 0.1s",
                  }} onMouseEnter={e => e.currentTarget.style.background = t.bgAlt} onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                    <span style={{ fontSize: "14px", fontWeight: 500 }}>{m.title}</span>
                    <span style={{ fontSize: "13px", color: t.accent, fontWeight: 500 }}>{m.company}</span>
                    <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "13px", fontWeight: 600, color: displayScoreOf(m) >= 0.8 ? t.good : t.text, textAlign: "right" }}>
                      {(displayScoreOf(m) * 100).toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>

              {digests.length > 0 && (
                <div style={{ marginTop: "40px" }}>
                  <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: "0 0 16px" }}>Latest Digest</h2>
                  <div style={{ borderTop: `2px solid ${t.text}`, paddingTop: "16px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", lineHeight: 1.9, color: t.textMid, whiteSpace: "pre-wrap" }}>
                    {digests[0].text}
                  </div>
                </div>
              )}
            </>)}

            {/* ── MATCHES ── */}
            {view === "matches" && (<>
              <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: "0 0 4px" }}>
                All Matches
              </h2>
              <p style={{ fontSize: "13px", color: t.textDim, margin: "0 0 4px" }}>
                Click any role for details. <span style={{ color: t.accent }}>❤ Save</span> a role (saved rows stick to the top), <span style={{ color: t.accent, fontWeight: 600 }}>✕ Dismiss</span> to hide, Apply to track it in your tracker.
              </p>
              <p style={{ fontSize: "12px", color: t.textDim, margin: "0 0 12px", fontStyle: "italic" }}>
                Saying no is the core PM skill. You can always go back on jobs you dismissed here as well.
              </p>

              {/* Filter bar. */}
              {true && (
                <div style={{
                  display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center",
                  padding: "10px 0", marginBottom: "12px",
                  borderTop: `1px solid ${t.borderLight}`, borderBottom: `1px solid ${t.borderLight}`,
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px",
                }}>
                  <span style={{ color: t.textDim, letterSpacing: "1px", textTransform: "uppercase" }}>Filter:</span>
                  {/* Filter chips — swapped to <Chip> primitive. Order
                      (Unseen → Saved → Dismissed) matches user request;
                      icons (● / star / ✕) render as real SVG now. */}
                  {[
                    { k: "unseenOnly",    label: `Unseen (${registryCounts.unseen})`,    icon: null,         dot: true },
                    { k: "starredOnly",   label: `Saved (${registryCounts.starred})`,    icon: "star",       dot: false },
                    { k: "showDismissed", label: `Show dismissed (${registryCounts.dismissed})`, icon: "x",  dot: false },
                  ].map(({ k, label, icon, dot }) => (
                    <Chip key={k} t={t} tone="accent" active={!!matchFilters[k]}
                      onClick={() => setMatchFilters((prev) => ({ ...prev, [k]: !prev[k] }))}>
                      {dot && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "currentColor", display: "inline-block" }} />}
                      {icon && <Icon name={icon} size={12} />}
                      {label}
                    </Chip>
                  ))}
                  <select value={matchFilters.windowDays}
                    onChange={(e) => setMatchFilters((prev) => ({ ...prev, windowDays: Number(e.target.value) }))}
                    title="Hide jobs older than the selected window"
                    style={{
                      background: "transparent", border: `1px solid ${t.border}`, color: t.textDim,
                      borderRadius: "3px", padding: "4px 8px", fontFamily: "inherit", fontSize: "inherit",
                    }}>
                    <option value={0}>All time</option>
                    <option value={2}>Last 48 hours</option>
                    <option value={3}>Last 3 days</option>
                    <option value={7}>Last 7 days</option>
                    <option value={14}>Last 14 days</option>
                    <option value={30}>Last 30 days</option>
                  </select>
                  <span style={{ color: t.textDim, marginLeft: "auto" }}>
                    {visibleMatches.length}/{registryCounts.total} shown
                  </span>
                </div>
              )}

              {/* Archetype filter chips. Rendered as a second filter row
                  so the main filters stay visually focused on state. We
                  hide the row entirely when NO match has an archetype
                  (clean first-run experience, no empty row). Chips with
                  zero matches are hidden to keep the bar short. */}
              {view === "matches" && Object.keys(archetypeCounts).length > 0 && (
                <div style={{
                  display: "flex", gap: "8px", padding: "10px 4px", flexWrap: "wrap", alignItems: "center",
                  borderBottom: `1px solid ${t.borderLight}`,
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px",
                }}>
                  <span style={{ color: t.textDim, letterSpacing: "1px", textTransform: "uppercase" }}>Archetype:</span>
                  {/* "All" resets the filter. Always on top of the list. */}
                  <button
                    onClick={() => setMatchFilters((prev) => ({ ...prev, archetype: null }))}
                    style={{
                      background: !matchFilters.archetype ? t.accentBg : "transparent",
                      border: `1px solid ${!matchFilters.archetype ? t.accent : t.border}`,
                      color: !matchFilters.archetype ? t.accent : t.textDim,
                      borderRadius: "3px", padding: "4px 10px", cursor: "pointer",
                      fontFamily: "inherit", fontSize: "inherit",
                    }}
                  >All ({registryCounts.total})</button>
                  {/* Iterate over the ARCHETYPE_LABELS map so chips render
                      in a stable, intuitive order (PM first, adjacents
                      last). See prettyArchetype for label mapping. */}
                  {[
                    ["pm", "Core PM"],
                    ["tpm", "TPM"],
                    ["platform_pm", "Platform PM"],
                    ["ai_pm", "AI PM"],
                    ["ops_pm", "Product Ops"],
                    ["growth_pm", "Growth PM"],
                    ["director", "Director+"],
                    ["other", "Adjacent"],
                    ["unclassified", "Unclassified"],
                  ].map(([slug, label]) => {
                    const count = archetypeCounts[slug] || 0;
                    if (!count) return null;
                    const active = matchFilters.archetype === slug;
                    return (
                      <button
                        key={slug}
                        onClick={() => setMatchFilters((prev) => ({
                          ...prev,
                          archetype: prev.archetype === slug ? null : slug,
                        }))}
                        style={{
                          background: active ? t.accentBg : "transparent",
                          border: `1px solid ${active ? t.accent : t.border}`,
                          color: active ? t.accent : t.textDim,
                          borderRadius: "3px", padding: "4px 10px", cursor: "pointer",
                          fontFamily: "inherit", fontSize: "inherit",
                        }}
                      >{label} ({count})</button>
                    );
                  })}
                </div>
              )}

              <div style={{ borderTop: `2px solid ${t.text}` }}>
                <div data-responsive="match-header" style={{ display: "grid", gridTemplateColumns: "2.4fr 0.9fr 1.3fr 0.85fr 0.7fr 0.6fr 0.6fr 0.65fr", padding: "10px 12px", borderBottom: `1px solid ${t.border}`, alignItems: "center", gap: "12px" }}>
                  {[
                    { k: "role", label: "Role" },
                    { k: "company", label: "Company" },
                    { k: "location", label: "Location" },
                    { k: "pay", label: "Pay" },
                    { k: "posted", label: "Posted" },
                    { k: "ghost", label: "Ghost" },
                    { k: "score", label: "Score" },
                    { k: null, label: "Actions" },
                  ].map(({ k, label }) => {
                    const isActive = k && matchSort.key === k;
                    const arrow = isActive ? (matchSort.dir === "asc" ? " ↑" : " ↓") : "";
                    const base = { fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", fontWeight: 600 };
                    if (!k) {
                      return <span key={label} style={{ ...base, color: t.textDim }}>{label}</span>;
                    }
                    return (
                      <button key={label}
                        onClick={() => setMatchSort(prev => ({ key: k, dir: prev.key === k && prev.dir === "desc" ? "asc" : "desc" }))}
                        title={`Sort by ${label}`}
                        style={{ ...base, background: "none", border: "none", padding: 0, textAlign: "left", cursor: "pointer", color: isActive ? t.accent : t.textDim }}>
                        {label}{arrow}
                      </button>
                    );
                  })}
                </div>
                {visibleMatches.map((m, i) => {
                  const isSelected = selectedJob?.title === m.title && selectedJob?.company === m.company;
                  const dimmed = m._dismissed;
                  // "Posted" column: prefer the actual posted_date straight
                  // from the ATS payload. Fall back to _first_seen_at (when
                  // SENTINEL first ingested the packet) only if posted_date
                  // is missing. Final fallback is a relative age label
                  // ("15d") for anything that still has neither.
                  // Humanise to "21 Apr '26" so the year is always on the
                  // row. Prior version dropped the year for this-year
                  // dates which broke when cycles ran across year rollover.
                  const rawIso = m.posted_date || m._first_seen_at || "";
                  const postedTs = rawIso ? Date.parse(rawIso) : NaN;
                  const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
                  let postedLabel = "";
                  if (Number.isFinite(postedTs)) {
                    const d = new Date(postedTs);
                    postedLabel = `${d.getDate()} ${MONTHS[d.getMonth()]} '${String(d.getFullYear()).slice(2)}`;
                  } else {
                    postedLabel = ageFor(m) || "—";
                  }
                  const postedTip = m.posted_date
                    ? `Posted ${m.posted_date}`
                    : (m._first_seen_at ? `First seen ${m._first_seen_at.slice(0, 10)}` : "No date available");
                  return (
                    <div key={m._registry_key || i} data-responsive="match-row"
                      onClick={() => { markSeenIfNeeded(m); setSelectedJob(isSelected ? null : m); }}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); markSeenIfNeeded(m); setSelectedJob(isSelected ? null : m); } }}
                      style={{
                        display: "grid", gridTemplateColumns: "2.4fr 0.9fr 1.3fr 0.85fr 0.7fr 0.6fr 0.6fr 0.65fr", padding: "12px",
                        borderBottom: `1px solid ${t.borderLight}`, alignItems: "center", gap: "12px",
                        background: isSelected ? t.accentBg : "transparent",
                        opacity: dimmed ? 0.5 : 1,
                        cursor: "pointer",
                        transition: "background 0.1s, opacity 0.15s",
                      }}>
                      <span
                        style={{ fontSize: "14px", fontWeight: 500, display: "flex", alignItems: "center", gap: "8px" }}>
                        {!m._seen && <span title="New - not yet viewed"
                          style={{ width: "6px", height: "6px", borderRadius: "50%", background: t.accent, flexShrink: 0 }}></span>}
                        {m._starred && <span title="Saved" style={{ color: t.accent, display: "inline-flex" }}><Icon name="starFilled" size={13} /></span>}
                        {m._applied && <span title="Tracked as applied"
                          style={{ fontSize: "9px", letterSpacing: "1px", color: t.good, border: `1px solid ${t.good}`, borderRadius: "3px", padding: "1px 4px" }}>APPLIED</span>}
                        <span>{m.title}</span>
                      </span>
                      <span style={{ fontSize: "13px", color: t.accent, fontWeight: 500 }}>{m.company}</span>
                      <span style={{ fontSize: "12px", color: t.textDim }}>
                        {m.location || "—"}
                        {m._country && <span title={`detected country: ${m._country}`} style={{ marginLeft: "6px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textDim, fontWeight: 500, letterSpacing: "0.5px" }}>{countryName(m._country)}</span>}
                      </span>
                      {/* Pay column: numeric range only, parsed out of the
                          ATS's boilerplate sentence. Full raw string is the
                          tooltip. "—" for listings with no posted comp. */}
                      {(() => {
                        const pay = prettySalary(m.salary);
                        const shown = pay.base || (m.salary ? "see detail" : "—");
                        return (
                          <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: m.salary ? t.textMid : t.textFaint }} title={m.salary || "No posted comp"}>
                            {shown}
                          </span>
                        );
                      })()}
                      <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.textDim }} title={postedTip}>
                        {postedLabel}
                      </span>
                      {(() => {
                        const f = m._fake;
                        if (!f || typeof f.score !== "number") {
                          return <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.textFaint }} title="No ghost-job scoring available">—</span>;
                        }
                        const pct = Math.round(f.score * 100);
                        const flagged = !!f.is_suspect;
                        const reasons = Array.isArray(f.reasons) ? f.reasons : [];
                        const tip = flagged
                          ? `Flagged as likely ghost (${pct}%). ${reasons.length ? "Reasons: " + reasons.join("; ") : ""}`
                          : `Ghost suspicion ${pct}%. Below flag threshold.`;
                        const colour = flagged ? (t.warn || t.accent) : pct >= 30 ? t.textMid : t.textFaint;
                        return (
                          <span title={tip} style={{
                            fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600,
                            color: flagged ? "#fff" : colour,
                            background: flagged ? (t.warn || t.accent) : "none",
                            border: flagged ? "none" : `1px solid ${t.borderLight || t.border}`,
                            borderRadius: "3px", padding: "2px 6px", textAlign: "center", justifySelf: "start",
                          }}>
                            {flagged ? `GHOST ${pct}` : `${pct}%`}
                          </span>
                        );
                      })()}
                      {(() => {
                        // Score + weakest-dimension hint (#100). We show
                        // the dimension pulling the score down so the
                        // user can tell at a glance why a role is 52%
                        // rather than 80% without opening the detail
                        // pane. Skip when _dimensions is absent or every
                        // sub-score is missing/strong.
                        const score = displayScoreOf(m);
                        const dims = m._dimensions || null;
                        const labels = { seniority_fit: "sen", tech_fit: "tech", domain_fit: "dom", years_fit: "yrs" };
                        let weakest = null;
                        if (dims) {
                          for (const k of Object.keys(labels)) {
                            const v = dims[k];
                            if (v === null || v === undefined) continue;
                            if (weakest === null || v < weakest.v) weakest = { k, v };
                          }
                        }
                        const showHint = weakest && weakest.v < 0.6;
                        return (
                          <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
                            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "13px", fontWeight: 600, color: score >= 0.8 ? t.good : t.text }}>
                              {(score * 100).toFixed(0)}%
                            </span>
                            {showHint && (
                              <span title={`Weakest dimension: ${labels[weakest.k]} at ${Math.round(weakest.v * 100)}%`}
                                style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "9px", color: t.textFaint, marginTop: "1px" }}>
                                {labels[weakest.k]} {Math.round(weakest.v * 100)}
                              </span>
                            )}
                          </span>
                        );
                      })()}
                      {/* Actions: ❤ saves, ✕ dismisses. Heart doubles as
                          the "save for later / positive signal" and ✕ as
                          "not relevant" - the training signals we'd
                          otherwise collect via thumbs up/down, minus the
                          redundant second pair of buttons. Data field
                          stays _starred for back-compat with the registry. */}
                      {/* Save/dismiss — IconButton gives consistent 28×28
                          boxes and swaps the old 🤍 emoji for a proper SVG
                          star that matches the ✕ stroke weight. `active`
                          flips colour so state is unambiguous. */}
                      <span style={{ display: "flex", gap: "4px", flexWrap: "wrap" }} onClick={(e) => e.stopPropagation()}>
                        <IconButton t={t}
                          icon={m._starred ? "starFilled" : "star"}
                          tone="accent"
                          active={!!m._starred}
                          title={m._starred ? "Unsave" : "Save - keep this role; saved rows stick to the top"}
                          onClick={() => { markSeenIfNeeded(m); setMatchState(m, "starred", !m._starred); }}
                        />
                        <IconButton t={t}
                          icon="x"
                          tone="danger"
                          active={!!m._dismissed}
                          title={m._dismissed ? "Restore" : "Dismiss - negative signal, hides from view"}
                          onClick={() => { markSeenIfNeeded(m); setMatchState(m, "dismissed", !m._dismissed); }}
                        />
                      </span>
                    </div>
                  );
                })}
                {!visibleMatches.length && registryCounts.total > 0 && (
                  <p style={{ color: t.textDim, fontStyle: "italic", marginTop: "20px" }}>
                    No rows match your filters. Try clearing them or switching to "All time".
                  </p>
                )}
              </div>
            </>)}

            {/* ── MARKET ── */}
            {view === "market" && (<>
              <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: "0 0 24px" }}>Market Intelligence</h2>

              {/* Tier 1 KPI row */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "1px", background: t.border, marginBottom: "32px" }}>
                <div style={{ background: t.bg, padding: "20px" }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>Hiring velocity</div>
                  <div style={{ fontFamily: "'Instrument Serif', serif", fontSize: "32px", marginTop: "6px" }}>
                    {tier1?.hiring_velocity_wow?.this_week ?? 0}
                    <span style={{ fontSize: "14px", color: (tier1?.hiring_velocity_wow?.delta_pct ?? 0) >= 0 ? t.good : t.accent, marginLeft: "8px" }}>
                      {(tier1?.hiring_velocity_wow?.delta_pct ?? 0) >= 0 ? "+" : ""}{tier1?.hiring_velocity_wow?.delta_pct ?? 0}% WoW
                    </span>
                  </div>
                  <div style={{ fontSize: "11px", color: t.textDim, marginTop: "2px" }}>Postings this week vs last</div>
                </div>
                <div style={{ background: t.bg, padding: "20px" }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>Matched jobs</div>
                  <div style={{ fontFamily: "'Instrument Serif', serif", fontSize: "32px", marginTop: "6px" }}>{tier1?.matched_job_metrics?.total_matched_jobs ?? 0}</div>
                  <div style={{ fontSize: "11px", color: t.textDim, marginTop: "2px" }}>
                    {(() => {
                      const wm = tier1?.matched_job_metrics?.work_model || {};
                      const total = Object.values(wm).reduce((a, b) => a + b, 0) || 1;
                      return `Remote ${Math.round(100 * (wm.remote || 0) / total)}% / Hybrid ${Math.round(100 * (wm.hybrid || 0) / total)}% / Onsite ${Math.round(100 * (wm.onsite || 0) / total)}%`;
                    })()}
                  </div>
                </div>
                <div style={{ background: t.bg, padding: "20px" }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>Top skill gap</div>
                  <div style={{ fontFamily: "'Instrument Serif', serif", fontSize: "22px", marginTop: "6px", textTransform: "capitalize" }}>
                    {tier1?.skill_gap_frequency?.[0]?.skill || "—"}
                  </div>
                  <div style={{ fontSize: "11px", color: t.textDim, marginTop: "2px" }}>
                    {tier1?.skill_gap_frequency?.[0] ? `${tier1.skill_gap_frequency[0].count} reports (${tier1.skill_gap_frequency[0].pct_of_reports}%)` : "no fit-gap reports yet"}
                  </div>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: "40px", marginBottom: "40px" }}>
                <div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "12px", fontWeight: 600 }}>PM Roles by Company</div>
                  <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={companyData} margin={{ left: -20 }}>
                      <XAxis dataKey="company" tick={{ fill: t.textDim, fontSize: 11 }} axisLine={{ stroke: t.border }} tickLine={false} />
                      <YAxis tick={{ fill: t.textDim, fontSize: 11 }} axisLine={false} tickLine={false} />
                      <Tooltip content={<Tip />} />
                      <Bar dataKey="count" fill={t.accent} radius={[2, 2, 0, 0]} name="Roles" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "12px", fontWeight: 600 }}>Work Model (all ingested)</div>
                  <div style={{ display: "flex", alignItems: "center", gap: "20px" }}>
                    <ResponsiveContainer width="55%" height={200}>
                      <PieChart><Pie data={remoteData.map(d => ({ name: d.n, value: d.v }))} cx="50%" cy="50%" innerRadius={40} outerRadius={70} dataKey="value" stroke={t.bg} strokeWidth={3}>
                        {remoteData.map((_, i) => <Cell key={i} fill={pieC[i]} />)}
                      </Pie><Tooltip content={<Tip />} /></PieChart>
                    </ResponsiveContainer>
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                      {remoteData.map((d, i) => (
                        <div key={d.n} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <div style={{ width: "10px", height: "10px", borderRadius: "2px", background: pieC[i] }} />
                          <span style={{ fontSize: "13px" }}>{d.n}</span>
                          <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", color: t.textDim }}>{d.v}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* Skill gap distribution */}
              <div style={{ marginBottom: "40px" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "12px", fontWeight: 600 }}>Top Skill Gaps (across all fit-gap reports)</div>
                <div style={{ borderTop: `1px solid ${t.border}` }}>
                  {(tier1?.skill_gap_frequency || []).slice(0, 8).map((g, i) => (
                    <div key={i} style={{ display: "grid", gridTemplateColumns: "2fr 0.5fr 2fr", padding: "10px 0", borderBottom: `1px solid ${t.borderLight}`, alignItems: "center", gap: "12px" }}>
                      <span style={{ fontSize: "13px", textTransform: "capitalize" }}>{g.skill}</span>
                      <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", color: t.textDim }}>{g.count} ({g.pct_of_reports}%)</span>
                      <div style={{ height: "6px", background: t.bgAlt, borderRadius: "3px", overflow: "hidden" }}>
                        <div style={{ height: "100%", width: `${Math.min(100, g.pct_of_reports)}%`, background: t.accent }} />
                      </div>
                    </div>
                  ))}
                  {!tier1?.skill_gap_frequency?.length && <p style={{ color: t.textDim, fontStyle: "italic", padding: "12px 0" }}>No fit-gap reports yet.</p>}
                </div>
              </div>

              {/* Skill frequency in matched jobs */}
              <div style={{ marginBottom: "40px" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "12px", fontWeight: 600 }}>Top Skills in Matched Jobs</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                  {(tier1?.matched_job_metrics?.skill_frequency || []).map((s, i) => (
                    <span key={i} style={{ fontSize: "12px", color: t.textMid, background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "4px 10px" }}>
                      {s.skill} <span style={{ color: t.textFaint, marginLeft: "4px" }}>({s.count})</span>
                    </span>
                  ))}
                  {!tier1?.matched_job_metrics?.skill_frequency?.length && <span style={{ color: t.textDim, fontStyle: "italic", fontSize: "13px" }}>No matches yet.</span>}
                </div>
              </div>

              {/* ── Tier 2 metrics ── */}
              <div style={{ borderTop: `2px solid ${t.text}`, paddingTop: "28px", marginTop: "12px" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "6px", fontWeight: 600 }}>Tier 2</div>
                <h3 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "22px", fontWeight: 400, margin: "0 0 20px" }}>Supply-side signals</h3>

                {/* Row 1: new companies + posting age */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "32px", marginBottom: "32px" }}>
                  <div>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "10px", fontWeight: 600 }}>New Companies (latest cycle)</div>
                    {tier2?.new_companies?.new?.length ? (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                        {tier2.new_companies.new.slice(0, 12).map((c, i) => (
                          <span key={i} style={{ fontSize: "12px", color: t.text, background: t.goodBg, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "4px 10px" }}>
                            {c.company} <span style={{ color: t.textDim, marginLeft: "4px" }}>({c.count})</span>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p style={{ color: t.textDim, fontStyle: "italic", fontSize: "13px", margin: 0 }}>
                        {tier2?.new_companies?.compared_against_cycles
                          ? "No new companies this cycle."
                          : "Needs 2+ cycles to detect novelty."}
                      </p>
                    )}
                  </div>
                  <div>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "10px", fontWeight: 600 }}>Posting Age (matched jobs)</div>
                    {tier2?.posting_age_distribution?.total ? (
                      <div style={{ borderTop: `1px solid ${t.border}` }}>
                        {Object.entries(tier2.posting_age_distribution.bins).map(([bin, count]) => {
                          const pct = tier2.posting_age_distribution.percent[bin] || 0;
                          const stale = bin === "31-60d" || bin === "61+d";
                          return (
                            <div key={bin} style={{ display: "grid", gridTemplateColumns: "1fr 0.5fr 2fr", padding: "8px 0", borderBottom: `1px solid ${t.borderLight}`, alignItems: "center", gap: "10px" }}>
                              <span style={{ fontSize: "13px", fontFamily: "'IBM Plex Mono', monospace" }}>{bin}</span>
                              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", color: t.textDim }}>{count} ({pct}%)</span>
                              <div style={{ height: "6px", background: t.bgAlt, borderRadius: "3px", overflow: "hidden" }}>
                                <div style={{ height: "100%", width: `${Math.min(100, pct)}%`, background: stale ? t.accent : t.good }} />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p style={{ color: t.textDim, fontStyle: "italic", fontSize: "13px", margin: 0 }}>No matched jobs yet.</p>
                    )}
                  </div>
                </div>

                {/* Row 2: source effectiveness */}
                <div style={{ marginBottom: "32px" }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "10px", fontWeight: 600 }}>Source Effectiveness (match rate per ATS)</div>
                  {(tier2?.source_effectiveness || []).length ? (
                    <div style={{ borderTop: `1px solid ${t.border}` }}>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 2fr", padding: "8px 0", fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textDim, textTransform: "uppercase", letterSpacing: "1px", borderBottom: `1px solid ${t.borderLight}` }}>
                        <span>Source</span><span>Ingested</span><span>Matched</span><span>Rate</span>
                      </div>
                      {tier2.source_effectiveness.map((s, i) => (
                        <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 2fr", padding: "8px 0", borderBottom: `1px solid ${t.borderLight}`, alignItems: "center", fontSize: "13px" }}>
                          <span style={{ textTransform: "capitalize" }}>{s.source}</span>
                          <span style={{ fontFamily: "'IBM Plex Mono', monospace", color: t.textDim }}>{s.ingested}</span>
                          <span style={{ fontFamily: "'IBM Plex Mono', monospace", color: t.textDim }}>{s.matched}</span>
                          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                            <div style={{ flex: 1, height: "6px", background: t.bgAlt, borderRadius: "3px", overflow: "hidden" }}>
                              <div style={{ height: "100%", width: `${Math.min(100, s.match_rate_pct)}%`, background: t.accent }} />
                            </div>
                            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", color: t.textDim, minWidth: "48px", textAlign: "right" }}>{s.match_rate_pct}%</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p style={{ color: t.textDim, fontStyle: "italic", fontSize: "13px", margin: 0 }}>No cycle history yet.</p>
                  )}
                </div>

                {/* Row 3: ghost-job rate by company */}
                <div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "10px", fontWeight: 600 }}>Ghost-Job Rate by Company <span style={{ textTransform: "none", letterSpacing: 0, color: t.textFaint, fontWeight: 400 }}>(min 3 matches)</span></div>
                  {(tier2?.ghost_job_rate_by_company || []).length ? (
                    <div style={{ borderTop: `1px solid ${t.border}` }}>
                      {tier2.ghost_job_rate_by_company.slice(0, 10).map((c, i) => (
                        <div key={i} style={{ display: "grid", gridTemplateColumns: "2fr 1fr 2fr", padding: "8px 0", borderBottom: `1px solid ${t.borderLight}`, alignItems: "center", gap: "12px" }}>
                          <span style={{ fontSize: "13px", fontWeight: 500 }}>{c.company}</span>
                          <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", color: t.textDim }}>{c.suspects}/{c.matches}</span>
                          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                            <div style={{ flex: 1, height: "6px", background: t.bgAlt, borderRadius: "3px", overflow: "hidden" }}>
                              <div style={{ height: "100%", width: `${Math.min(100, c.suspect_rate_pct)}%`, background: c.suspect_rate_pct >= 40 ? t.accent : c.suspect_rate_pct >= 15 ? t.warn : t.good }} />
                            </div>
                            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", color: t.textDim, minWidth: "48px", textAlign: "right" }}>{c.suspect_rate_pct}%</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p style={{ color: t.textDim, fontStyle: "italic", fontSize: "13px", margin: 0 }}>Needs matches with ghost-detector scores.</p>
                  )}
                </div>
              </div>
            </>)}

            {/* Fit-Gap merged into Matches detail pane; Chat moved to bottom drawer */}

            {/* ── STORIES ── */}
            {/* Read-only markdown view of data/story_bank.md. The file
                grows every time a match is analyzed (fit-gap). Users
                can also open the file directly in any editor; the path
                is surfaced so they can find it. */}
            {view === "stories" && (<>
              <div style={{ display: "flex", alignItems: "baseline", gap: "16px", marginBottom: "16px", flexWrap: "wrap" }}>
                <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: 0 }}>Story Bank</h2>
                <span style={{ color: t.textDim, fontSize: "13px" }}>
                  STAR+R bullets appended automatically on every fit-gap analysis.
                </span>
                <button
                  onClick={refreshStoryBank}
                  disabled={storyBank.loading}
                  style={{
                    marginLeft: "auto",
                    fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", letterSpacing: "1px",
                    background: "transparent", color: t.accent,
                    border: `1px solid ${t.accent}`, borderRadius: "3px",
                    padding: "6px 14px", cursor: storyBank.loading ? "wait" : "pointer",
                    opacity: storyBank.loading ? 0.6 : 1,
                  }}
                >{storyBank.loading ? "LOADING..." : "REFRESH"}</button>
              </div>

              {/* File path is shown so the non-technical user can open
                  it in any markdown editor (VSCode, Obsidian, etc). */}
              {storyBank.path && (
                <div style={{ fontSize: "11px", color: t.textFaint, marginBottom: "20px", wordBreak: "break-all", fontFamily: "'IBM Plex Mono', monospace" }}>
                  File on disk: <span style={{ color: t.textDim }}>{storyBank.path}</span>
                </div>
              )}

              {/* The file renders as monospace pre -- this is plain markdown,
                  not HTML. Readable without a markdown renderer and keeps
                  the UI code trivially simple. If we want rich rendering
                  later, swap `<pre>` for a `react-markdown` render. */}
              {!storyBank.exists && !storyBank.loading && (
                <div style={{
                  border: `1px dashed ${t.border}`, borderRadius: "4px",
                  padding: "32px", textAlign: "center", color: t.textDim, fontSize: "13px",
                }}>
                  No stories yet. The bank fills up each time a match
                  runs through fit-gap analysis — click a match and
                  open the detail view to trigger one.
                </div>
              )}
              {storyBank.exists && (
                <pre style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px",
                  lineHeight: 1.6, color: t.text,
                  background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px",
                  padding: "20px", margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
                  maxHeight: "70vh", overflow: "auto",
                }}>
                  {storyBank.text}
                </pre>
              )}
            </>)}

            {/* ── DECISIONS ── */}
            {view === "log" && (<>
              <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: "0 0 24px" }}>Decision Log</h2>

              {reactionsList.length > 0 && (
                <div style={{ marginBottom: "40px" }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "10px", fontWeight: 600 }}>
                    Your reactions
                    <span style={{ fontWeight: 400, letterSpacing: "0.5px", textTransform: "none", color: t.textFaint, marginLeft: "8px" }}>
                      — click Like / Pass / Clear to change your mind
                    </span>
                  </div>
                  <div style={{ borderTop: `1px solid ${t.border}` }}>
                    {reactionsList.map((r, i) => {
                      const synthetic = { title: r.title, company: r.company, url: r.url || "", _match_score: r.score || 0 };
                      const pillBase = {
                        fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px",
                        padding: "3px 8px", border: `1px solid ${t.border}`, borderRadius: "3px",
                        cursor: "pointer", letterSpacing: "1px", fontWeight: 600,
                        background: t.bg, color: t.textDim,
                      };
                      const activeUp = { ...pillBase, background: t.good, color: "#fff", borderColor: t.good };
                      const activeDown = { ...pillBase, background: t.accent, color: "#fff", borderColor: t.accent };
                      return (
                        <div key={i} style={{ display: "grid", gridTemplateColumns: "1.4fr 1.4fr 0.8fr 0.5fr 0.9fr", padding: "10px 0", borderBottom: `1px solid ${t.borderLight}`, fontSize: "13px", alignItems: "center", gap: "8px" }}>
                          <div style={{ display: "flex", gap: "4px" }}>
                            <button style={r.action === "up" ? activeUp : pillBase} onClick={() => setReaction(synthetic, "up")}>▲ LIKE</button>
                            <button style={r.action === "down" ? activeDown : pillBase} onClick={() => setReaction(synthetic, "down")}>▼ PASS</button>
                            <button style={pillBase} onClick={() => setReaction(synthetic, r.action)} title="Clear this reaction">CLEAR</button>
                          </div>
                          <span style={{ fontWeight: 500 }}>{r.title}</span>
                          <span style={{ color: t.accent }}>{r.company}</span>
                          <span style={{ fontFamily: "'IBM Plex Mono', monospace", color: t.textDim }}>{((r.score || 0) * 100).toFixed(0)}%</span>
                          <span style={{ fontSize: "11px", color: t.textFaint }}>{(r.ts || "").replace("T", " ").slice(0, 16)}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "10px", fontWeight: 600 }}>Pipeline pass-reasons</div>
              <div style={{ borderTop: `2px solid ${t.text}` }}>
                <div style={{ display: "grid", gridTemplateColumns: "1.8fr 0.8fr 2.5fr 0.5fr", padding: "8px 0", borderBottom: `1px solid ${t.border}` }}>
                  {["Role", "Company", "Reason", "Score"].map(h => (
                    <span key={h} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>{h}</span>
                  ))}
                </div>
                {decisionList.map((d, i) => (
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "1.8fr 0.8fr 2.5fr 0.5fr", padding: "12px 0", borderBottom: `1px solid ${t.borderLight}`, fontSize: "13px", alignItems: "baseline" }}>
                    <span style={{ fontWeight: 500 }}>{d.title}</span>
                    <span style={{ color: t.accent, fontWeight: 500 }}>{d.company}</span>
                    <span style={{ color: t.textMid }}>{d.reason}</span>
                    <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontWeight: 600, color: (d.score || 0) >= 3 ? t.textDim : t.accent }}>{d.score}</span>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: "32px", borderLeft: `3px solid ${t.accent}`, paddingLeft: "16px", maxWidth: "560px" }}>
                <p style={{ fontSize: "14px", color: t.textMid, lineHeight: 1.7, fontStyle: "italic", margin: 0 }}>
                  Saying no is the core PM skill. This log is the artefact of that discipline.
                </p>
              </div>
            </>)}

            {/* ── BLITZ (internal id remains "triage" for registry compat) ── */}
            {view === "triage" && (<>
              {/* Combo surprise overlay. Fires when a rolling-60s window
                  combo crosses a tier threshold (5/10/20/30). Rendered as
                  a fixed-position layer so it sits above the scoreboard
                  and card without shifting layout. The scale/opacity
                  keyframes are injected once via a <style> tag below. */}
              {blitzSurprise && (
                <div style={{
                  position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
                  pointerEvents: "none", zIndex: 9999,
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                  <div style={{
                    animation: "blitzSurprisePop 1.6s cubic-bezier(0.2, 0.8, 0.2, 1) forwards",
                    background: t.bg,
                    border: `3px solid ${t.accent}`,
                    borderRadius: "8px",
                    padding: "32px 56px",
                    boxShadow: `0 0 0 6px ${t.accent}30, 0 24px 60px ${t.accent}40`,
                    textAlign: "center",
                    minWidth: "360px",
                  }}>
                    <div style={{
                      fontFamily: "'Instrument Serif', serif",
                      fontSize: "56px", fontWeight: 400,
                      color: t.accent, lineHeight: 1,
                      marginBottom: "8px",
                      letterSpacing: "1px",
                    }}>
                      {blitzSurprise.label}
                    </div>
                    <div style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: "13px", letterSpacing: "1.5px", textTransform: "uppercase",
                      color: t.textMid, fontWeight: 600,
                    }}>
                      {blitzSurprise.sub}
                    </div>
                  </div>
                </div>
              )}
              <style>{`
                @keyframes blitzSurprisePop {
                  0%   { transform: scale(0.6) rotate(-4deg); opacity: 0; }
                  15%  { transform: scale(1.12) rotate(2deg); opacity: 1; }
                  30%  { transform: scale(1.0)  rotate(0deg); opacity: 1; }
                  80%  { transform: scale(1.0)  rotate(0deg); opacity: 1; }
                  100% { transform: scale(0.96) rotate(0deg); opacity: 0; }
                }
              `}</style>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "8px", flexWrap: "wrap", gap: "8px" }}>
                <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: 0 }}>Blitz</h2>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.textDim }}>
                  {triageQueue.length > 0 ? `${Math.min(triageIndex + 1, triageQueue.length)} of ${triageQueue.length}` : "queue empty"}
                </div>
              </div>
              <p style={{ fontSize: "13px", color: t.textDim, margin: "0 0 16px" }}>
                Keyboard-first rapid sort on unreacted matches. Every keep and skip trains future scoring.{" "}
                <kbd style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", padding: "1px 6px", background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "3px" }}>←</kbd> skip ·{" "}
                <kbd style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", padding: "1px 6px", background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "3px" }}>→</kbd> keep ·{" "}
                <kbd style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", padding: "1px 6px", background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "3px" }}>↓</kbd> maybe ·{" "}
                <kbd style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", padding: "1px 6px", background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "3px" }}>↑</kbd> undo cursor
              </p>

              {/* Scoreboard: 5 tiles. Session-scoped - resets on page reload
                  so the streak feels fresh each time you open Blitz. */}
              {(() => {
                const avgSecs = blitzStats.decisionCount > 0
                  ? (blitzStats.totalDecisionMs / blitzStats.decisionCount / 1000).toFixed(1)
                  : "—";
                const avgNum = blitzStats.decisionCount > 0 ? (blitzStats.totalDecisionMs / blitzStats.decisionCount / 1000) : null;
                const streakHot = blitzStats.streak >= 5;
                // Encouraging subtitles - fire only when there's actual
                // signal to celebrate, otherwise stay quiet so we don't
                // praise empty tiles.
                const keepsSub = blitzStats.keeps >= 20 ? "crushing it" : blitzStats.keeps >= 10 ? "on fire" : blitzStats.keeps >= 5 ? "nice" : null;
                const skipsSub = blitzStats.skips >= 20 ? "ruthless" : blitzStats.skips >= 10 ? "decisive" : null;
                const maybesSub = blitzStats.maybes >= 10 ? "pick a lane" : null;
                const streakSub = blitzStats.streak >= 20 ? "unreal" : blitzStats.streak >= 10 ? "locked in" : streakHot ? "on fire!" : `best ${blitzStats.bestStreak}`;
                const avgSub = avgNum == null ? "sec/decision" : avgNum < 2 ? "lightning" : avgNum < 3 ? "quick!" : avgNum < 5 ? "steady" : "sec/decision";
                const tiles = [
                  { label: "KEEPS",  value: blitzStats.keeps,  color: t.good,   sub: keepsSub },
                  { label: "SKIPS",  value: blitzStats.skips,  color: t.accent, sub: skipsSub },
                  { label: "MAYBES", value: blitzStats.maybes, color: t.text,   sub: maybesSub },
                  { label: "STREAK", value: streakHot ? `${blitzStats.streak} ` + String.fromCodePoint(0x1F525) : String(blitzStats.streak), color: streakHot ? t.accent : t.text, sub: streakSub },
                  { label: "AVG",    value: avgSecs,           color: t.text,   sub: avgSub },
                ];
                return (
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "8px", marginBottom: "20px" }}>
                    {tiles.map((tile) => (
                      <div key={tile.label} style={{
                        border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px",
                        background: t.bgAlt, textAlign: "center",
                      }}>
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "9px", letterSpacing: "1.5px", color: t.textDim, fontWeight: 600, marginBottom: "4px" }}>{tile.label}</div>
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "22px", fontWeight: 600, color: tile.color, lineHeight: 1 }}>{tile.value}</div>
                        {tile.sub && (
                          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "9px", color: t.textFaint, marginTop: "4px" }}>{tile.sub}</div>
                        )}
                      </div>
                    ))}
                  </div>
                );
              })()}

              {triageQueue.length === 0 ? (
                <div style={{ fontSize: "14px", color: t.textMid, padding: "40px 0", textAlign: "center", border: `1px dashed ${t.border}`, borderRadius: "4px" }}>
                  {blitzStats.keeps + blitzStats.skips + blitzStats.maybes > 0
                    ? `Nice run. ${blitzStats.keeps} kept, ${blitzStats.skips} skipped, best streak ${blitzStats.bestStreak}. Run a cycle for more matches, or clear a reaction to bring roles back.`
                    : "Nothing to triage. Run a cycle for more matches, or clear a reaction to send a role back into the queue."}
                </div>
              ) : (() => {
                const current = triageQueue[Math.min(triageIndex, triageQueue.length - 1)];
                if (!current) return null;
                // Slot-machine slide animation. When blitzFlash is set, the
                // card translates in the direction of the action + a ring
                // colour flashes. Cleared after 200ms; the underlying data
                // has already advanced (queue shortens on keep/skip, index
                // bumps on maybe), so once the transform resets the card
                // is already showing the next role.
                const flashAction = blitzFlash?.action;
                const flashTransform =
                  flashAction === "keep" ? "translateX(60px) rotate(3deg)" :
                  flashAction === "skip" ? "translateX(-60px) rotate(-3deg)" :
                  flashAction === "maybe" ? "translateY(20px)" :
                  "translateX(0) rotate(0)";
                const flashBorder =
                  flashAction === "keep" ? t.good :
                  flashAction === "skip" ? t.accent :
                  flashAction === "maybe" ? t.warn :
                  t.text;
                const flashGlow = flashAction
                  ? `0 0 0 3px ${flashBorder}40, 0 8px 24px ${flashBorder}30`
                  : "0 1px 2px rgba(0,0,0,0.04)";
                // Defensive coercions: match payloads occasionally carry
                // objects where we expect strings (happens when the LLM
                // parse returns a nested {value: "..."} instead of a bare
                // string). Rendering an object as a React child throws
                // and whites out the entire tab, so we flatten everything
                // to a safe string here.
                const asStr = (v) => {
                  if (v == null) return "";
                  if (typeof v === "string") return v;
                  if (typeof v === "number" || typeof v === "boolean") return String(v);
                  try { return JSON.stringify(v); } catch { return ""; }
                };
                const title = asStr(current.title) || "(untitled role)";
                const company = asStr(current.company) || "(unknown company)";
                const location = asStr(current.location);
                const remote = asStr(current.remote);
                const seniority = asStr(current.seniority);
                const description = asStr(current.description);
                const techs = Array.isArray(current.technologies) ? current.technologies : [];
                const score = displayScoreOf(current);
                const url = asStr(current.url);
                return (
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 280px", gap: "24px", alignItems: "start" }}>
                    {/* Focused match card (slot-machine animated) */}
                    <div style={{
                      border: `2px solid ${flashBorder}`,
                      borderRadius: "6px",
                      padding: "24px",
                      background: t.bgAlt,
                      transform: flashTransform,
                      boxShadow: flashGlow,
                      transition: "transform 150ms cubic-bezier(0.2, 0.8, 0.2, 1), border-color 150ms ease, box-shadow 150ms ease",
                      willChange: "transform",
                    }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "16px", marginBottom: "8px" }}>
                        <h3 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "26px", fontWeight: 400, margin: 0, lineHeight: 1.2 }}>{title}</h3>
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "22px", fontWeight: 600, color: score >= 0.8 ? t.good : t.text }}>
                          {(score * 100).toFixed(0)}%
                        </div>
                      </div>
                      <div style={{ fontSize: "15px", color: t.accent, fontWeight: 500, marginBottom: "16px" }}>{company}</div>

                      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "16px" }}>
                        {location && <span style={{ fontSize: "11px", background: t.bg, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "3px 8px" }}>{location}</span>}
                        {remote && remote !== "unknown" && <span style={{ fontSize: "11px", background: t.bg, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "3px 8px" }}>{remote}</span>}
                        {seniority && <span style={{ fontSize: "11px", background: t.bg, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "3px 8px" }}>{prettySeniority(seniority)}</span>}
                        {/* Archetype chip (PM / TPM / AI PM / ...). Classified
                            post-match by sentinel/agents/archetype.py. Tinted
                            with the accent color so it reads as "role type"
                            rather than a neutral metadata fact. */}
                        {current.archetype && prettyArchetype(current.archetype) && (
                          <span
                            title={current.archetype_rationale || ""}
                            style={{ fontSize: "11px", background: t.bg, border: `1px solid ${t.accent}`, color: t.accent, fontWeight: 600, borderRadius: "3px", padding: "3px 8px", letterSpacing: "0.3px" }}
                          >{prettyArchetype(current.archetype)}</span>
                        )}
                        {current.salary && (() => {
                          const pay = prettySalary(current.salary);
                          const shown = pay.base || current.salary;
                          return (
                            <>
                              <span title={current.salary} style={{ fontSize: "11px", background: t.goodBg || t.accentBg, border: `1px solid ${t.good || t.accent}`, borderRadius: "3px", padding: "3px 8px", color: t.good || t.accent, fontWeight: 600 }}>💵 {shown}</span>
                              {pay.extras && <span title={current.salary} style={{ fontSize: "11px", background: t.bg, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "3px 8px", color: t.textDim }}>{pay.extras}</span>}
                            </>
                          );
                        })()}
                      </div>

                      {description && (
                        <div style={{ fontSize: "13px", color: t.textMid, lineHeight: 1.55, maxHeight: "200px", overflowY: "auto", marginBottom: "16px", padding: "12px", background: t.bg, border: `1px solid ${t.borderLight}`, borderRadius: "4px" }}>
                          {description.slice(0, 900)}{description.length > 900 ? "…" : ""}
                        </div>
                      )}

                      {techs.length > 0 && (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", marginBottom: "16px" }}>
                          {techs.slice(0, 10).map((tech, i) => (
                            <span key={i} style={{ fontSize: "11px", background: t.bg, border: `1px solid ${t.borderLight}`, borderRadius: "3px", padding: "2px 8px", color: t.textMid }}>{asStr(tech)}</span>
                          ))}
                        </div>
                      )}

                      {/* Action buttons - keyboard-first, but the buttons
                          stay clickable for mouse users and touch. Arrow
                          glyphs in the labels reinforce the shortcuts. */}
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "8px" }}>
                        <button onClick={() => triageAct("skip")} disabled={!live} style={{
                          fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", fontWeight: 600, letterSpacing: "1px",
                          background: t.bgAlt, color: t.accent, border: `1px solid ${t.accent}`, borderRadius: "4px",
                          padding: "12px 16px", cursor: live ? "pointer" : "default", opacity: live ? 1 : 0.5,
                        }}>← SKIP</button>
                        <button onClick={() => triageAct("maybe")} disabled={!live} style={{
                          fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", fontWeight: 600, letterSpacing: "1px",
                          background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px",
                          padding: "12px 16px", cursor: live ? "pointer" : "default", opacity: live ? 1 : 0.5,
                        }}>↓ MAYBE</button>
                        <button onClick={() => triageAct("keep")} disabled={!live} style={{
                          fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", fontWeight: 600, letterSpacing: "1px",
                          background: t.good, color: "#fff", border: `1px solid ${t.good}`, borderRadius: "4px",
                          padding: "12px 16px", cursor: live ? "pointer" : "default", opacity: live ? 1 : 0.5,
                        }}>KEEP →</button>
                      </div>
                      {current.url && (
                        <a href={current.url} target="_blank" rel="noopener noreferrer" style={{ display: "inline-block", marginTop: "12px", fontSize: "11px", color: t.textFaint, fontFamily: "'IBM Plex Mono', monospace" }}>
                          open posting ↗
                        </a>
                      )}
                    </div>

                    {/* Sidebar: Pip + lifetime stats + learned keywords */}
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                    {(() => {
                      // Pip mood derivation. Fresh-feed window gives the
                      // ecstatic bounce state; otherwise hunger grows with
                      // time-since-last-fed.
                      const msSinceFed = pip.lastFedAt ? (pipNow - pip.lastFedAt) : Infinity;
                      const recentFeed = msSinceFed < 60000;  // last 60s
                      const hours = msSinceFed / 3600000;
                      let mood, face, line;
                      if (pip.totalFeeds === 0) { mood = "NEW";       face = "(o_o)";   line = "Feed me?";            }
                      else if (recentFeed)      { mood = "ECSTATIC";  face = "(^O^)";   line = "Nom nom nom!";        }
                      else if (hours < 12)      { mood = "HAPPY";     face = "(^_^)";   line = "Full and vibing.";    }
                      else if (hours < 24)      { mood = "CONTENT";   face = "(._.)";   line = "Could go for a snack."; }
                      else if (hours < 48)      { mood = "HUNGRY";    face = "(-_-)";   line = "Getting peckish...";  }
                      else                      { mood = "STARVING";  face = "(x_x)";   line = "Please. I'm wasting away."; }
                      const lastFedLabel = pip.lastFedAt === 0
                        ? "never fed"
                        : hours < 0.02 ? "just now"
                        : hours < 1 ? `${Math.round(msSinceFed / 60000)} min ago`
                        : hours < 48 ? `${Math.round(hours)}h ago`
                        : `${Math.floor(hours / 24)}d ago`;
                      const faceColor = mood === "STARVING" ? t.accent
                        : mood === "HUNGRY" ? t.warn
                        : mood === "ECSTATIC" ? t.good
                        : t.text;
                      // Pick a contextual saying from the backend catalogue.
                      // Falls back to the mood's own tagline if the cache
                      // hasn't warmed yet (<1s window on first load).
                      const bucket = moodToSayingBucket(mood);
                      const bucketSayings = helperSayings[bucket] || [];
                      const rotatedLine = bucketSayings.length > 0
                        ? bucketSayings[helperSayingIdx % bucketSayings.length]
                        : line;
                      const helperName = helper?.name || "Joby";
                      // Active animation state. Priority:
                      //   1. Milestone override (wins everything, gold card)
                      //   2. Pet tier from arrow-key meter (wave/bounce/celebrate)
                      //   3. Burst (nod/shake/eat/think/blink/look) - contextual
                      //   4. Mood-derived: ECSTATIC → celebrate,
                      //      HUNGRY/STARVING → sleep, else idle.
                      // Bursts expire by wall-clock so we check against now;
                      // stale bursts skip gracefully down to the mood layer.
                      // Asset URL falls back to idle if the chosen state isn't
                      // in the manifest (e.g. a stale sprite).
                      const nowMs = Date.now();
                      const liveBurst = helperBurst && helperBurst.until > nowMs
                        ? helperBurst : null;
                      const derivedState = helperOverrideState
                        ?? petTier?.state
                        ?? liveBurst?.state
                        ?? (mood === "ECSTATIC" ? "celebrate"
                          : (mood === "HUNGRY" || mood === "STARVING") ? "sleep"
                          : "idle");
                      const stateAssetUrl = helper?.assets?.[derivedState]
                        ?? helper?.asset_url;
                      // Shadow tint for the sprite card matches the mood so
                      // a STARVING helper looks visibly tinged in accent red.
                      // Milestone override paints the card gold for 5s.
                      const cardTint = helperOverrideState === "celebrate" ? t.goodBg
                        : mood === "STARVING" ? t.accentBg
                        : mood === "ECSTATIC" ? t.goodBg
                        : t.bgAlt;
                      return (
                        <div style={{
                          position: "relative",
                          border: `1px solid ${helperMilestone ? t.good : t.border}`,
                          borderRadius: "4px", padding: "14px 16px",
                          background: cardTint, textAlign: "center",
                          transition: "background 220ms ease, border-color 220ms ease",
                          boxShadow: helperMilestone ? `0 0 0 2px ${t.good}44` : "none",
                        }}>
                          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "10px" }}>
                            {helperName} · Your Helper
                          </div>
                          <div style={{
                            padding: "4px 0",
                            transform: recentFeed ? "translateY(-6px) scale(1.08)" : "translateY(0) scale(1)",
                            transition: "transform 220ms cubic-bezier(0.2, 1.4, 0.4, 1)",
                            // Desaturate the sprite slightly when starving so
                            // the mood reads even without reading the text.
                            filter: mood === "STARVING" && !helperOverrideState ? "saturate(0.5)" : "none",
                          }}>
                            {helper && stateAssetUrl ? (
                              <HelperSprite
                                // key forces the <img> to remount when the state
                                // changes, so the GIF restarts from frame 0 instead
                                // of resuming mid-animation from a cached frame.
                                key={derivedState}
                                assetUrl={stateAssetUrl}
                                label={helper.label || helper.name}
                                size={96}
                                fallbackBg={t.accent}
                              />
                            ) : (
                              // Tiny fallback while the sprite payload is
                              // still in flight on first paint.
                              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "34px", color: faceColor, lineHeight: 1, padding: "28px 0" }}>{face}</div>
                            )}
                          </div>
                          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", color: helperMilestone ? t.good : petTier ? t.good : faceColor, fontWeight: 600, marginTop: "8px" }}>
                            {helperMilestone ? "MILESTONE"
                              : petTier?.state === "celebrate" ? "ECSTATIC"
                              : petTier?.state === "bounce" ? "DELIGHTED"
                              : petTier?.state === "wave" ? "PLEASED"
                              : liveBurst?.state === "think" ? "THINKING"
                              : liveBurst?.state === "nod" ? "FILING"
                              : liveBurst?.state === "shake" ? "NEXT"
                              : liveBurst?.state === "eat" ? "MUNCHING"
                              : mood}
                          </div>
                          <div
                            // Remount on each new pet / burst line so the
                            // bubble re-animates instead of looking stuck
                            // when the same saying is picked twice in a row.
                            key={
                              liveBurst && liveBurst.line
                                ? `burst-${liveBurst.id}`
                                : petTier && petLine
                                  ? `pet-${petLineSeqRef.current}`
                                  : `mood-${mood}`
                            }
                            style={{ fontSize: "12px", color: t.textMid, fontStyle: "italic", margin: "6px 0 8px", minHeight: "32px", lineHeight: 1.4, transition: "opacity 240ms ease", animation: (liveBurst && liveBurst.line) || (petTier && petLine) ? "helper-pop 220ms cubic-bezier(0.2,1.4,0.4,1)" : "none" }}
                          >
                            "{helperMilestone ? helperMilestone.detail
                              : (liveBurst && liveBurst.line) ? liveBurst.line
                              : (petTier && petLine) ? petLine
                              : rotatedLine}"
                          </div>
                          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textFaint, lineHeight: 1.5 }}>
                            {pip.totalFeeds} feeds lifetime · {lastFedLabel}
                          </div>
                          {/* Milestone banner: a gold ribbon across the top.
                              Only visible during the 5.2s celebration window.
                              Absolutely positioned so it doesn't reflow the
                              card when it appears/disappears. */}
                          {helperMilestone && (
                            <div style={{
                              position: "absolute", top: "-10px", left: "50%",
                              transform: "translateX(-50%)",
                              background: t.good, color: "#1a1612",
                              padding: "2px 10px", borderRadius: "10px",
                              fontFamily: "'IBM Plex Mono', monospace",
                              fontSize: "10px", fontWeight: 700,
                              letterSpacing: "1px", whiteSpace: "nowrap",
                              boxShadow: "0 2px 6px rgba(0,0,0,0.25)",
                              animation: "helper-pop 220ms cubic-bezier(0.2,1.4,0.4,1)",
                            }}>
                              ★ {helperMilestone.title}
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    {/* Lifetime stats: the "you've been at it" number that
                        rewards repeat use. Days active shown as a simple
                        count of distinct ISO-date entries. */}
                    {(blitzLifetime.keeps + blitzLifetime.skips + blitzLifetime.maybes > 0) && (
                      <div style={{ border: `1px solid ${t.border}`, borderRadius: "4px", padding: "14px 16px", background: t.bgAlt }}>
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "10px" }}>
                          Your Record
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 12px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px" }}>
                          <div><span style={{ color: t.textFaint }}>Kept </span><span style={{ color: t.good, fontWeight: 600 }}>{blitzLifetime.keeps}</span></div>
                          <div><span style={{ color: t.textFaint }}>Skipped </span><span style={{ color: t.accent, fontWeight: 600 }}>{blitzLifetime.skips}</span></div>
                          <div><span style={{ color: t.textFaint }}>Maybes </span><span style={{ color: t.text, fontWeight: 600 }}>{blitzLifetime.maybes}</span></div>
                          <div><span style={{ color: t.textFaint }}>Days </span><span style={{ color: t.text, fontWeight: 600 }}>{(blitzLifetime.days || []).length}</span></div>
                          <div><span style={{ color: t.textFaint }}>Best streak </span><span style={{ color: t.accent, fontWeight: 600 }}>{blitzLifetime.bestStreak}</span></div>
                          <div><span style={{ color: t.textFaint }}>Best combo </span><span style={{ color: t.accent, fontWeight: 600 }}>{blitzLifetime.bestCombo}</span></div>
                        </div>
                      </div>
                    )}

                    <div style={{ border: `1px solid ${t.border}`, borderRadius: "4px", padding: "14px 16px", background: t.bgAlt }}>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "10px" }}>
                        Learned from your reactions
                      </div>
                      <div style={{ fontSize: "11px", color: t.textFaint, marginBottom: "8px" }}>
                        Keeps {triageLearned.samples?.keeps ?? 0} · Skips {triageLearned.samples?.skips ?? 0}
                      </div>
                      {triageLearned.needs_more ? (
                        <div style={{ fontSize: "12px", color: t.textMid, lineHeight: 1.45 }}>
                          {triageLearned.needs_more}
                        </div>
                      ) : (triageLearned.suggestions || []).length === 0 ? (
                        <div style={{ fontSize: "12px", color: t.textMid }}>
                          No strong signal yet. Keep triaging.
                        </div>
                      ) : (
                        <>
                          <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "8px", lineHeight: 1.45 }}>
                            Tokens that appear in your Skips far more than your Keeps. Copy any into the Settings blocklist to filter future cycles.
                          </div>
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                            {(triageLearned.suggestions || []).map((s) => (
                              <span key={s.token} title={`${s.skips} skipped · ${s.keeps} kept · score ${s.score}`} style={{
                                fontSize: "11px", fontFamily: "'IBM Plex Mono', monospace",
                                background: t.bg, border: `1px solid ${t.border}`, borderRadius: "3px",
                                padding: "2px 8px", color: t.textMid,
                              }}>
                                {s.token} <span style={{ color: t.accent }}>{s.skips}↓</span>
                              </span>
                            ))}
                          </div>
                        </>
                      )}
                    </div>
                    </div>
                  </div>
                );
              })()}
            </>)}

            {/* ── HISTORY ── */}
            {view === "history" && (<>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "8px", flexWrap: "wrap", gap: "8px" }}>
                <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: 0 }}>History</h2>
                <button onClick={refreshHistory} disabled={!live || logBusy} style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px",
                  background: "transparent", color: t.accent, border: `1px solid ${t.accent}`, borderRadius: "3px",
                  padding: "6px 12px", cursor: (!live || logBusy) ? "default" : "pointer", opacity: (!live || logBusy) ? 0.5 : 1,
                }}>{logBusy ? "REFRESHING…" : "REFRESH"}</button>
              </div>
              <p style={{ fontSize: "13px", color: t.textDim, margin: "0 0 20px" }}>
                Per-cycle breakdown and live log tail. Polls every 5s while this tab is open.
                {!live && " Connect the pipeline to see history."}
              </p>

              {/* Cycle timeline */}
              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "10px", fontWeight: 600 }}>
                Cycles ({cycleHistory.length})
              </div>
              {cycleHistory.length === 0 && (
                <div style={{ fontSize: "13px", color: t.textFaint, marginBottom: "32px" }}>
                  No cycles recorded yet. Trigger one from the top bar.
                </div>
              )}
              {cycleHistory.length > 0 && (
                <div style={{ borderTop: `2px solid ${t.text}`, marginBottom: "32px" }}>
                  <div style={{ display: "grid", gridTemplateColumns: "0.4fr 1fr 0.6fr 0.6fr 0.6fr 0.6fr 0.6fr", padding: "8px 0", borderBottom: `1px solid ${t.border}` }}>
                    {["#", "When", "Duration", "Ingested", "New", "Matches", "Fit-gaps"].map((h) => (
                      <span key={h} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>{h}</span>
                    ))}
                  </div>
                  {cycleHistory.map((c, i) => (
                    <div key={`${c.cycle}-${c.ts}-${i}`} style={{ display: "grid", gridTemplateColumns: "0.4fr 1fr 0.6fr 0.6fr 0.6fr 0.6fr 0.6fr", padding: "10px 0", borderBottom: `1px solid ${t.borderLight}`, fontSize: "13px", fontFamily: "'IBM Plex Mono', monospace", alignItems: "baseline" }}>
                      <span style={{ color: t.textMid }}>{c.cycle ?? "—"}</span>
                      <span style={{ color: t.textMid }}>{(c.ts || "").replace("T", " ").slice(0, 19)}</span>
                      <span style={{ color: t.textMid }}>{c.seconds != null ? `${c.seconds}s` : "—"}</span>
                      <span>{c.ingested ?? "—"}</span>
                      <span>{c.new_jobs ?? "—"}</span>
                      <span style={{ color: (c.matches || 0) > 0 ? t.good : t.textMid, fontWeight: 600 }}>{c.matches ?? "—"}</span>
                      <span>{c.fit_gaps ?? "—"}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Log tail */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px", flexWrap: "wrap", gap: "8px" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>
                  Logs {logs?.available ? `(${logs.lines?.length || 0} shown)` : "(log file not available)"}
                </div>
                <div style={{ display: "flex", gap: "4px" }}>
                  {["DEBUG", "INFO", "WARNING", "ERROR"].map((lvl) => (
                    <button key={lvl} onClick={() => setLogLevel(lvl)} style={{
                      fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                      background: logLevel === lvl ? t.accent : "transparent",
                      color: logLevel === lvl ? "#fff" : t.textMid,
                      border: `1px solid ${logLevel === lvl ? t.accent : t.border}`,
                      borderRadius: "3px", padding: "4px 8px", cursor: "pointer",
                    }}>{lvl}</button>
                  ))}
                </div>
              </div>
              <div style={{
                background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px",
                padding: "10px 12px", maxHeight: "480px", overflowY: "auto",
                fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", lineHeight: 1.5,
              }}>
                {(!logs || !logs.lines || logs.lines.length === 0) && (
                  <div style={{ color: t.textFaint }}>No log lines at this level yet.</div>
                )}
                {(logs?.lines || []).map((ln, i) => {
                  const colour = ln.level === "ERROR" || ln.level === "CRITICAL" ? t.accent
                    : ln.level === "WARNING" ? t.warn
                    : ln.level === "DEBUG" ? t.textFaint
                    : t.textMid;
                  return (
                    <div key={i} style={{ marginBottom: "2px", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      <span style={{ color: t.textFaint }}>{(ln.ts || "").slice(11, 19)}</span>
                      {" "}
                      <span style={{ color: colour, fontWeight: 600 }}>{(ln.level || "").padEnd(7)}</span>
                      {" "}
                      <span style={{ color: t.textFaint }}>{ln.logger}</span>
                      {" "}
                      <span style={{ color: t.text }}>{ln.message}</span>
                    </div>
                  );
                })}
              </div>
            </>)}

            {/* ── PIPELINE SETTINGS ── */}
            {view === "pipeline" && (<>
              <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: "0 0 8px" }}>Settings</h2>
              <p style={{ fontSize: "13px", color: t.textDim, margin: "0 0 24px" }}>Everything you can tune: keywords, threshold, cadence, scrapers, ghost-job filter, models, preferences. Changes apply on the next cycle.{!live && " Connect the pipeline to save settings."}</p>

              {/* Config drift warnings. Cheap heuristics that catch the
                  handful of footguns we've tripped over ourselves:
                    - threshold cranked above 0.9 will starve the Matches tab
                    - threshold below 0.45 produces noisy Maybe-tier floods
                    - empty keyword list silently matches nothing
                    - allowlist narrows work-mode filter to the point of zero
                  Each warning is one line + a suggested fix. No modal, no
                  nagging. We surface, the user decides. */}
              {(() => {
                const warns = [];
                if (threshold >= 0.9) warns.push({ tone: "warn", msg: `Threshold at ${Math.round(threshold*100)}% is very strict — expect few or zero matches per cycle.` });
                if (threshold <= 0.45) warns.push({ tone: "warn", msg: `Threshold at ${Math.round(threshold*100)}% is loose — low-fit and borderline-ghost jobs will bleed into Matches after the ghost penalty is applied.` });
                const kwList = (keywords || "").split(",").map(s => s.trim()).filter(Boolean);
                if (kwList.length === 0) warns.push({ tone: "err", msg: "No role keywords set — the ingest stage will skip every posting." });
                if (workModes.length === 0) warns.push({ tone: "err", msg: "No work modes ticked — every job will be filtered out." });
                const allowList = (allowedLocations || "").split(",").map(s => s.trim()).filter(Boolean);
                if (allowList.length > 0 && !workModes.includes("onsite") && !workModes.includes("hybrid")) warns.push({ tone: "warn", msg: "Location allowlist set but only Remote is ticked — the allowlist can't do anything." });
                if (salaryFloor > 0 && salaryWeight === 0) warns.push({ tone: "info", msg: "Salary floor set but salary weight is 0 — the floor won't influence scoring." });
                if (yearsExperience > 0 && yearsWeight === 0) warns.push({ tone: "info", msg: "Years of experience set but years weight is 0 — it won't influence scoring." });
                if (warns.length === 0) return null;
                return (
                  <div style={{ marginBottom: "24px", display: "flex", flexDirection: "column", gap: "6px", maxWidth: "640px" }}>
                    {warns.map((w, i) => {
                      const c = w.tone === "err" ? (t.bad || "#b94040") : w.tone === "warn" ? (t.warn || "#b88a2e") : t.textDim;
                      return (
                        <div key={i} style={{ fontSize: "12px", color: c, padding: "8px 12px", background: t.bgAlt, border: `1px solid ${c}`, borderRadius: "4px", lineHeight: 1.5 }}>
                          <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 700, letterSpacing: "1px", marginRight: "8px" }}>{w.tone === "err" ? "BLOCKER" : w.tone === "warn" ? "HEADS-UP" : "NOTE"}</span>
                          {w.msg}
                        </div>
                      );
                    })}
                  </div>
                );
              })()}

              {/* Active-filters chip bar was removed - users read it as an
                  interactive toolbar and try to click chips. The actual
                  controls below are already labelled, so the summary isn't
                  earning its keep. Keep this comment so we don't re-add it. */}

              <div style={{ maxWidth: "640px", display: "flex", flexDirection: "column", gap: "28px" }}>
                <div>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>Role Keywords</label>
                  <textarea value={keywords} onChange={e => setKeywords(e.target.value)} rows={3} style={{
                    width: "100%", fontFamily: "'Outfit', sans-serif", fontSize: "14px", padding: "12px",
                    background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px", resize: "vertical", lineHeight: 1.6,
                  }} />
                  <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "4px" }}>Comma-separated. Matches job titles containing any of these.</div>
                </div>

                <div>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>
                    How strict is a "match"? <span style={{ color: t.accent }}>{(threshold * 100).toFixed(0)}%</span>
                  </label>
                  <input type="range" min="0.4" max="0.95" step="0.05" value={threshold} onChange={e => setThreshold(parseFloat(e.target.value))} style={{ width: "100%", accentColor: t.accent }} />
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: t.textFaint, marginBottom: "6px" }}>
                    <span>40% - show me more options</span><span>90% - only the strongest matches</span>
                  </div>
                  <div style={{ fontSize: "12px", color: t.textDim, lineHeight: 1.6 }}>
                    Every job is scored against your resume. Anything at or above this score is called a match. Lower the bar to see more jobs each cycle; raise it to keep your list shorter and sharper. 55% to 65% is a comfortable starting point for most people.
                  </div>
                  <ThresholdExplainer
                    threshold={threshold}
                    salaryFloor={salaryFloor}
                    yearsExp={yearsExperience}
                    salaryWeight={salaryWeight}
                    yearsWeight={yearsWeight}
                    matchModel={matchModel}
                    embedModel={status?.models?.match?.includes("embeddings") ? "all-MiniLM-L6-v2" : null}
                    theme={t}
                  />
                </div>

                {/* Ghost-job filter: how aggressively to flag postings as
                    suspected ghost jobs. Runs a rules-based scorer against
                    nine signals (stale posting, vague location, buzzword
                    density, missing apply URL, etc.). Higher aggressiveness
                    = lower threshold = more postings flagged. */}
                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "24px" }}>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>
                    Ghost-job filter
                  </label>
                  <div style={{ fontSize: "12px", color: t.textDim, marginBottom: "12px", lineHeight: 1.6 }}>
                    Every match is scored for ghost-job suspicion using nine heuristics (age, vague location, buzzword density, missing apply link, etc.). Postings above the threshold get flagged in the Matches table. Scoring is deterministic and runs locally — no LLM calls.
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "8px" }}>
                    {[
                      { key: "low",      label: "Low",      sub: "threshold 0.60",  help: "Only flag the most obvious ghosts. Fewer false positives, more noise gets through." },
                      { key: "balanced", label: "Balanced", sub: "threshold 0.45",  help: "Default. Flags postings with two or three strong signals." },
                      { key: "strict",   label: "Strict",   sub: "threshold 0.30",  help: "Flag aggressively. Expect some real jobs to get caught — review the reasons before discarding." },
                    ].map(p => {
                      const active = fakeAggressiveness === p.key;
                      return (
                        <button key={p.key} type="button" onClick={() => setFakeAggressiveness(p.key)}
                          title={p.help}
                          style={{
                            fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600,
                            background: active ? t.accentBg : "none",
                            color: active ? t.accent : t.textMid,
                            border: `1px solid ${active ? t.accent : t.border}`,
                            borderRadius: "4px", padding: "10px 8px", cursor: "pointer",
                            display: "flex", flexDirection: "column", alignItems: "center", gap: "3px",
                          }}>
                          <span style={{ letterSpacing: "1px", textTransform: "uppercase" }}>{p.label}</span>
                          <span style={{ fontSize: "9px", color: t.textFaint, fontWeight: 500 }}>{p.sub}</span>
                        </button>
                      );
                    })}
                  </div>
                  <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "8px", lineHeight: 1.5 }}>
                    Ghosts are never hidden — just flagged with a badge and a score in the Matches table so you can skip them. Change applies to the next cycle.
                  </div>
                </div>

                {/* Ghost-score fold controls. The detector produces a 0..1
                    probability; this section decides how much that
                    probability costs a job's match score and where the UI
                    draws the "suspect" / "aging" / "clear" badges. Three
                    knobs — kept narrow on purpose:
                      1. Penalty weight: how much ghost score discounts match
                      2. Flag threshold: score at/above which the badge reads "Suspect"
                      3. Warn threshold: score at/above which the badge reads "Aging"
                    The example below re-computes live so the user sees
                    exactly what their weight does before saving. */}
                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "24px" }}>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>
                    Ghost penalty <span style={{ color: t.accent }}>{ghostWeight > 0 ? `${Math.round(ghostWeight * 100)}%` : "off"}</span>
                  </label>
                  <div style={{ fontSize: "12px", color: t.textDim, marginBottom: "12px", lineHeight: 1.6 }}>
                    How much a ghost score costs a match score. Formula: <code style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent }}>final = fit × (1 − weight × ghost)</code>. At 0% the ghost score is advisory only; at 100% a full-ghost posting loses 100% of its fit score. Default 35%.
                  </div>
                  <input type="range" min="0" max="0.8" step="0.05" value={ghostWeight}
                    onChange={e => setGhostWeight(parseFloat(e.target.value))}
                    style={{ width: "100%", accentColor: t.accent }} />
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: t.textFaint, marginTop: "2px" }}>
                    <span>0% - off (advisory only)</span><span>80% - crushes suspects</span>
                  </div>
                  {/* Live preview: "A posting with 85% fit and 60% ghost
                      becomes X% after the fold." Recomputes on every render
                      so sliding the weight updates the number immediately. */}
                  <div style={{ marginTop: "12px", background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "10px 12px", fontSize: "12px", color: t.textMid, lineHeight: 1.6 }}>
                    <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 700, letterSpacing: "1px", color: t.textDim, marginRight: "8px" }}>PREVIEW</span>
                    Fit 85%, ghost 60% → final <span style={{ color: t.accent, fontWeight: 600 }}>{Math.round(85 * (1 - ghostWeight * 0.60))}%</span>.
                    Fit 70%, ghost 20% → final <span style={{ color: t.accent, fontWeight: 600 }}>{Math.round(70 * (1 - ghostWeight * 0.20))}%</span>.
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginTop: "20px" }}>
                    <div>
                      <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "6px" }}>
                        Flag <span style={{ color: t.accent }}>{Math.round(ghostFlagThreshold * 100)}%</span>
                      </label>
                      <input type="range" min="0.2" max="0.9" step="0.05" value={ghostFlagThreshold}
                        onChange={e => {
                          const v = parseFloat(e.target.value);
                          setGhostFlagThreshold(v);
                          // Keep warn strictly below flag so the middle band
                          // is always non-empty.
                          if (ghostWarnThreshold >= v) setGhostWarnThreshold(Math.max(0, v - 0.10));
                        }}
                        style={{ width: "100%", accentColor: t.accent }} />
                      <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "2px", lineHeight: 1.4 }}>
                        Ghost score at/above this is badged <span style={{ color: t.accent, fontWeight: 600 }}>Suspect</span>.
                      </div>
                    </div>
                    <div>
                      <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "6px" }}>
                        Warn <span style={{ color: t.warn || t.accent }}>{Math.round(ghostWarnThreshold * 100)}%</span>
                      </label>
                      <input type="range" min="0.1" max="0.8" step="0.05" value={ghostWarnThreshold}
                        onChange={e => {
                          const v = parseFloat(e.target.value);
                          // Soft-clamp warn below flag.
                          setGhostWarnThreshold(Math.min(v, Math.max(0, ghostFlagThreshold - 0.05)));
                        }}
                        style={{ width: "100%", accentColor: t.warn || t.accent }} />
                      <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "2px", lineHeight: 1.4 }}>
                        Between warn and flag = <span style={{ color: t.warn || t.accent, fontWeight: 600 }}>Aging</span>. Below warn = clear.
                      </div>
                    </div>
                  </div>
                  {ghostWeight === 0 && (
                    <div style={{ marginTop: "12px", fontSize: "11px", color: t.textFaint, fontStyle: "italic", lineHeight: 1.5 }}>
                      Penalty is off — scores shown will be raw fit only. Suspect/Aging badges still appear but won't affect ranking.
                    </div>
                  )}
                </div>

                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "24px" }}>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>Model Configuration</label>
                  <div style={{ background: t.accentBg, border: `1px solid ${t.accent}30`, borderRadius: "4px", padding: "10px 14px", fontSize: "12px", color: t.accent, marginBottom: "12px" }}>
                    Changing models from the recommended defaults may reduce quality or speed. Defaults are optimised per task.
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
                    {[
                      { label: "Parse (extraction)", value: parseModel, set: setParseModel, rec: "qwen2.5:14b" },
                      { label: "Match (scoring)", value: matchModel, set: setMatchModel, rec: "qwen3:14b" },
                    ].map(m => (
                      <div key={m.label}>
                        <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "6px" }}>{m.label}</div>
                        <select value={m.value} onChange={e => m.set(e.target.value)} style={{
                          width: "100%", fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", padding: "8px",
                          background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px",
                        }}>
                          <option value="gemma3:4b">gemma3:4b (fastest, shallow)</option>
                          <option value="gemma3:12b">gemma3:12b (balanced)</option>
                          <option value="qwen2.5:14b">qwen2.5:14b (parse default)</option>
                          <option value="qwen3:14b">qwen3:14b (match default)</option>
                          <option value="deepseek-r1:14b">deepseek-r1:14b (deep, CoT)</option>
                        </select>
                        <div style={{ fontSize: "10px", color: t.textFaint, marginTop: "3px" }}>Recommended: {m.rec}</div>
                      </div>
                    ))}
                  </div>
                  <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "10px", lineHeight: 1.5 }}>
                    Peak VRAM ~12 GB with both 14B models loaded. Drop to gemma3:12b for parse if you're on 8 GB. Embeddings use BAAI/bge-m3 (CPU/GPU auto).
                  </div>
                </div>
                {/* Country hard-filter. Drops jobs whose detected country
                    is not in the selected set. Unknown-country behaviour
                    controlled by the strict toggle. */}
                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "24px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "10px" }}>
                    <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>Country (hard filter)</label>
                    <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textFaint }}>
                      {allowedCountries.length === 0 ? "off — all countries" : `${allowedCountries.length} selected`}
                    </span>
                  </div>
                  {/* Country chips spell out full names per user request.
                      Code stays as the id we persist to config; label is
                      the only thing the user sees. Swapped to <Chip>. */}
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginBottom: "10px" }}>
                    {[
                      { code: "US", label: "United States" },
                      { code: "IE", label: "Ireland" },
                      { code: "GB", label: "United Kingdom" },
                      { code: "CA", label: "Canada" },
                      { code: "DE", label: "Germany" },
                      { code: "FR", label: "France" },
                      { code: "NL", label: "Netherlands" },
                      { code: "AU", label: "Australia" },
                      { code: "SG", label: "Singapore" },
                    ].map(c => {
                      const active = allowedCountries.includes(c.code);
                      return (
                        <Chip key={c.code} t={t} tone="accent" active={active} size="md"
                          icon={active ? "check" : undefined}
                          title={c.code}
                          onClick={() => {
                            setAllowedCountries(prev => active
                              ? prev.filter(x => x !== c.code)
                              : [...prev, c.code]);
                          }}>
                          {c.label}
                        </Chip>
                      );
                    })}
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "8px" }}>
                    <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer", fontSize: "12px", color: t.textMid }}>
                      <input type="checkbox" checked={strictUnknownCountry}
                        onChange={e => setStrictUnknownCountry(e.target.checked)}
                        style={{ accentColor: t.accent }} />
                      Drop jobs we can't classify
                    </label>
                    <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer", fontSize: "12px", color: t.textMid }}>
                      <input type="checkbox" checked={allowRemoteAnyCountry}
                        onChange={e => setAllowRemoteAnyCountry(e.target.checked)}
                        style={{ accentColor: t.accent }} />
                      Let remote jobs through regardless of country
                    </label>
                  </div>
                  <div style={{ fontSize: "11px", color: t.textFaint, lineHeight: 1.5 }}>
                    Select one or more countries you'd work in. Jobs outside this set are dropped before scoring — no Bangalore, Mexico, or Brazil. Pick US + IE to cover both places you'd move to. With <em>drop unclassified</em> on, listings with vague location text also get cut; turn it off if you're seeing too few results. Empty selection = filter off.
                  </div>
                </div>

                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "24px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "10px" }}>
                    <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>Work mode & location</label>
                    <button type="button"
                      onClick={() => { setWorkModes(["remote", "hybrid", "onsite"]); setAllowedLocations(""); setBlockedLocations(""); }}
                      style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1px", background: "none", border: `1px solid ${t.border}`, color: t.accent, padding: "4px 8px", borderRadius: "3px", cursor: "pointer", textTransform: "uppercase" }}>
                      Any location
                    </button>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "6px", marginBottom: "8px" }}>
                    {[
                      { id: "remote", label: "Remote",  desc: "Work from anywhere" },
                      { id: "hybrid", label: "Hybrid",  desc: "Part-office, part-remote" },
                      { id: "onsite", label: "On-site", desc: "Fully in-office" },
                    ].map(opt => {
                      const active = workModes.includes(opt.id);
                      return (
                        <button key={opt.id} type="button"
                          onClick={() => {
                            setWorkModes(prev => active
                              ? prev.filter(m => m !== opt.id)
                              : [...prev, opt.id]);
                          }}
                          style={{
                            textAlign: "left", cursor: "pointer",
                            background: active ? t.accentBg : t.bgAlt,
                            border: `1px solid ${active ? t.accent : t.border}`,
                            borderRadius: "4px", padding: "10px 12px",
                            color: active ? t.accent : t.textMid,
                          }}>
                          <div style={{ fontSize: "13px", fontWeight: 600 }}>
                            {active ? "\u2713 " : ""}{opt.label}
                          </div>
                          <div style={{ fontSize: "11px", color: active ? t.accent : t.textFaint, marginTop: "2px" }}>{opt.desc}</div>
                        </button>
                      );
                    })}
                  </div>
                  <div style={{ fontSize: "11px", color: t.textFaint, marginBottom: "14px", lineHeight: 1.5 }}>
                    Tick every mode you'd consider. All three ticked = no work-mode filter (a job's work mode is ignored). Untick to drop jobs that only match a mode you don't want.
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                    <div>
                      <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Only these locations (optional)</div>
                      <input value={allowedLocations} onChange={e => setAllowedLocations(e.target.value)} placeholder="London, Manchester, UK"
                        style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }} />
                    </div>
                    <div>
                      <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Never these locations (optional)</div>
                      <input value={blockedLocations} onChange={e => setBlockedLocations(e.target.value)} placeholder="Bay Area, NYC"
                        style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }} />
                    </div>
                  </div>
                  <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "6px", lineHeight: 1.5 }}>
                    Comma-separated city or country names. Block list wins over allow list. Both are substring matches. Remote jobs always bypass the allow list because they're location-agnostic. Location is a soft penalty on your score, not a hard cull.
                  </div>
                </div>

                {/* Salary preference */}
                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "24px" }}>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>Salary preference</label>
                  <div style={{ fontSize: "12px", color: t.textDim, marginBottom: "12px", lineHeight: 1.6 }}>
                    Salary is a soft signal, never a hard filter. Jobs paying above your minimum get a gentle push up the list; jobs paying less get a gentle push down. Listings without a posted salary are never hidden, only lightly penalised. Drag either slider to <strong>0 to turn off</strong>.
                  </div>
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                      <div style={{ fontSize: "12px", color: t.textMid }}>Minimum salary (USD)</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>
                        {Number(salaryFloor) > 0 ? `$${(Number(salaryFloor) / 1000).toFixed(0)}k` : "off"}
                      </div>
                    </div>
                    <input type="range" min="0" max="400000" step="5000" value={Number(salaryFloor) || 0}
                      onChange={e => setSalaryFloor(Number(e.target.value))}
                      style={{ width: "100%", accentColor: t.accent }} />
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint }}>
                      <span>0 - off</span><span>$400k</span>
                    </div>
                  </div>
                  <div style={{ marginTop: "16px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                      <div style={{ fontSize: "12px", color: t.textMid }}>How much salary affects ranking</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>
                        {salaryWeight > 0 ? `${(salaryWeight * 100).toFixed(0)}%` : "off"}
                      </div>
                    </div>
                    <input type="range" min="0" max="0.4" step="0.05" value={salaryWeight} onChange={e => setSalaryWeight(parseFloat(e.target.value))}
                      style={{ width: "100%", accentColor: t.accent }} />
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint }}>
                      <span>0% - off</span><span>40% - strong pull</span>
                    </div>
                  </div>
                </div>

                {/* Experience & seniority */}
                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "24px" }}>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>Experience & seniority</label>
                  <div style={{ fontSize: "12px", color: t.textDim, marginBottom: "12px", lineHeight: 1.6 }}>
                    Stops roles that are way above (or below) your level cluttering your list. Roles 3+ bands above you, or wanting 8+ more years than you have, are dropped outright. Roles wanting 3 to 7 more years are still shown but ranked lower.
                  </div>
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                      <div style={{ fontSize: "12px", color: t.textMid }}>Years of experience in your field</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>
                        {Number(yearsExperience) > 0 ? `${yearsExperience} yr${yearsExperience === 1 ? "" : "s"}` : "off"}
                      </div>
                    </div>
                    <input type="range" min="0" max="30" step="1" value={Number(yearsExperience) || 0}
                      onChange={e => setYearsExperience(parseInt(e.target.value, 10) || 0)}
                      style={{ width: "100%", accentColor: t.accent }} />
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint }}>
                      <span>0 - off / let parser decide</span><span>30 yrs</span>
                    </div>
                  </div>
                  <div style={{ marginTop: "16px" }}>
                    <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Your current level</div>
                    <select value={currentLevel} onChange={e => setCurrentLevel(e.target.value)}
                      style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }}>
                      <option value="">— not sure / skip —</option>
                      <option value="intern">Intern</option>
                      <option value="entry">Entry level / Junior</option>
                      <option value="mid">Mid-level</option>
                      <option value="senior">Senior</option>
                      <option value="staff">Staff</option>
                      <option value="principal">Principal</option>
                      <option value="director">Director</option>
                      <option value="vp">VP</option>
                      <option value="cxo">C-level</option>
                    </select>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginTop: "14px" }}>
                    <div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                        <div style={{ fontSize: "12px", color: t.textMid }}>Years-gap penalty weight</div>
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>{yearsWeight > 0 ? `${Math.round(yearsWeight * 100)}%` : "off"}</div>
                      </div>
                      <input type="range" min="0" max="0.10" step="0.01" value={yearsWeight}
                        onChange={e => setYearsWeight(parseFloat(e.target.value))}
                        style={{ width: "100%", accentColor: t.accent }} />
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint, marginTop: "2px" }}>
                        <span>0% - off</span><span>10% - penalise hard</span>
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "6px" }}>Director / VP trap-door</div>
                      <label style={{ fontSize: "13px", color: t.textMid, display: "flex", alignItems: "center", gap: "8px" }}>
                        <input type="checkbox" checked={trapdoorEnabled} onChange={e => setTrapdoorEnabled(e.target.checked)} />
                        Hide Director / VP roles when I have less than 10 years
                      </label>
                      <div style={{ fontSize: "10px", color: t.textFaint, marginTop: "4px" }}>
                        Director and above gate on org-level scope, not raw skill overlap. Turn this off only if you're senior and intentionally applying up.
                      </div>
                    </div>
                  </div>
                  <div style={{ fontSize: "12px", color: t.textDim, marginTop: "10px", lineHeight: 1.6 }}>
                    Leave both fields blank (0 and "not sure") and this filter stays off. You'll still see everything your location and salary rules allow.
                  </div>
                </div>

                {/* ── COMPANIES / SCRAPE TARGETS ─────────────────────────
                    Where jobs come from. Three ATS flavours (Greenhouse /
                    Lever / Ashby) + five big-tech toggles. Each ATS has
                    its own add-slug row and a per-row test button so the
                    user can confirm the endpoint works before saving.
                    Designed to answer two questions at a glance:
                      1. "Which companies am I actually scanning?"
                      2. "Is this slug I just added live?"                   */}
                <div style={{ borderTop: `1px solid ${t.borderLight}`, paddingTop: "28px" }}>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "4px" }}>Companies</label>
                  <div style={{ fontSize: "12px", color: t.textFaint, marginBottom: "20px", lineHeight: 1.6 }}>
                    Pick which company careers pages the pipeline scans. ATS tenants (Greenhouse / Lever / Ashby) are fast JSON and run on every cycle. Big-tech SPAs need Playwright and only run on <em>Run Scraper</em>.
                  </div>

                  {/* ── ATS TENANT LISTS ────────────────────────────── */}
                  {[
                    { kind: "greenhouse", label: "Greenhouse", list: greenhouseCompanies, remove: removeGreenhouseSlug, add: addGreenhouseSlug, newVal: newGreenhouseSlug, setNew: setNewGreenhouseSlug,
                      hint: "slug from boards-api.greenhouse.io/v1/boards/<slug>/jobs — e.g. \"stripe\", \"airbnb\"" },
                    { kind: "lever", label: "Lever", list: leverCompanies, remove: removeLeverSlug, add: addLeverSlug, newVal: newLeverSlug, setNew: setNewLeverSlug,
                      hint: "slug from api.lever.co/v0/postings/<slug> — e.g. \"netflix\", \"palantir\"" },
                  ].map(section => (
                    <div key={section.kind} style={{ marginBottom: "22px" }}>
                      <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginBottom: "6px" }}>
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, color: t.text, letterSpacing: "0.5px" }}>{section.label}</div>
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textFaint }}>{section.list.length} tenant{section.list.length === 1 ? "" : "s"}</div>
                      </div>
                      <div style={{ fontSize: "11px", color: t.textFaint, marginBottom: "8px" }}>{section.hint}</div>
                      {/* Tenant chips */}
                      {section.list.length > 0 && (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginBottom: "10px" }}>
                          {section.list.map(slug => {
                            const key = `${section.kind}:${slug}`;
                            const testRes = tenantTests[key];
                            const busy = !!tenantTestBusy[key];
                            const tone = testRes?.ok ? t.good : testRes ? t.warn : t.textDim;
                            return (
                              <div key={slug} style={{
                                display: "inline-flex", alignItems: "center", gap: "6px",
                                background: t.bgAlt, border: `1px solid ${testRes?.ok ? t.good : t.border}`,
                                borderRadius: "3px", padding: "4px 6px 4px 10px",
                              }}>
                                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.text }}>{slug}</span>
                                {testRes && (
                                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: tone }}>
                                    {testRes.ok ? `${testRes.jobs_found} jobs` : `${testRes.status_code || "err"}`}
                                  </span>
                                )}
                                <button
                                  onClick={() => testTenant(section.kind, slug)}
                                  disabled={busy || !live}
                                  title="Test this slug against the ATS endpoint"
                                  style={{
                                    background: "none", border: "none", color: t.textDim,
                                    fontSize: "10px", fontFamily: "'IBM Plex Mono', monospace",
                                    cursor: busy || !live ? "default" : "pointer", padding: "0 4px",
                                  }}
                                >{busy ? "…" : "TEST"}</button>
                                <button
                                  onClick={() => section.remove(slug)}
                                  title="Remove this tenant"
                                  style={{
                                    background: "none", border: "none", color: t.textFaint,
                                    fontSize: "14px", cursor: "pointer", padding: "0 4px", lineHeight: 1,
                                  }}
                                >×</button>
                              </div>
                            );
                          })}
                        </div>
                      )}
                      {/* Add-new row */}
                      <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                        <input
                          type="text"
                          value={section.newVal}
                          onChange={e => section.setNew(e.target.value)}
                          onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); section.add(); } }}
                          placeholder={`add ${section.kind} slug…`}
                          style={{
                            flex: 1, fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px",
                            background: t.bg, color: t.text, border: `1px solid ${t.border}`,
                            borderRadius: "3px", padding: "6px 10px",
                          }}
                        />
                        <button
                          onClick={section.add}
                          disabled={!section.newVal.trim()}
                          style={{
                            fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "0.5px",
                            background: section.newVal.trim() ? t.accent : t.bgAlt,
                            color: section.newVal.trim() ? "#fff" : t.textDim,
                            border: "none", borderRadius: "3px", padding: "7px 14px",
                            cursor: section.newVal.trim() ? "pointer" : "default",
                          }}
                        >ADD</button>
                      </div>
                    </div>
                  ))}

                  {/* ── ASHBY (two fields: display name + slug) ───────── */}
                  <div style={{ marginBottom: "22px" }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginBottom: "6px" }}>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, color: t.text, letterSpacing: "0.5px" }}>Ashby</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textFaint }}>{ashbyCompanies.length} tenant{ashbyCompanies.length === 1 ? "" : "s"}</div>
                    </div>
                    <div style={{ fontSize: "11px", color: t.textFaint, marginBottom: "8px" }}>display name + slug from api.ashbyhq.com/posting-api/job-board/&lt;slug&gt; — e.g. "Ramp" / "ramp"</div>
                    {ashbyCompanies.length > 0 && (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginBottom: "10px" }}>
                        {ashbyCompanies.map(([display, slug]) => {
                          const key = `ashby:${slug}`;
                          const testRes = tenantTests[key];
                          const busy = !!tenantTestBusy[key];
                          const tone = testRes?.ok ? t.good : testRes ? t.warn : t.textDim;
                          return (
                            <div key={slug} style={{
                              display: "inline-flex", alignItems: "center", gap: "6px",
                              background: t.bgAlt, border: `1px solid ${testRes?.ok ? t.good : t.border}`,
                              borderRadius: "3px", padding: "4px 6px 4px 10px",
                            }}>
                              <span style={{ fontSize: "11px", color: t.text }}>{display}</span>
                              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textFaint }}>({slug})</span>
                              {testRes && (
                                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: tone }}>
                                  {testRes.ok ? `${testRes.jobs_found} jobs` : `${testRes.status_code || "err"}`}
                                </span>
                              )}
                              <button
                                onClick={() => testTenant("ashby", slug, display)}
                                disabled={busy || !live}
                                title="Test this slug"
                                style={{ background: "none", border: "none", color: t.textDim, fontSize: "10px", fontFamily: "'IBM Plex Mono', monospace", cursor: busy || !live ? "default" : "pointer", padding: "0 4px" }}
                              >{busy ? "…" : "TEST"}</button>
                              <button onClick={() => removeAshbyTenant(slug)} title="Remove" style={{ background: "none", border: "none", color: t.textFaint, fontSize: "14px", cursor: "pointer", padding: "0 4px", lineHeight: 1 }}>×</button>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                      <input
                        type="text"
                        value={newAshbyDisplay}
                        onChange={e => setNewAshbyDisplay(e.target.value)}
                        placeholder="Display name"
                        style={{ flex: "1 1 140px", fontSize: "12px", background: t.bg, color: t.text, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "6px 10px" }}
                      />
                      <input
                        type="text"
                        value={newAshbySlug}
                        onChange={e => setNewAshbySlug(e.target.value)}
                        onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addAshbyTenant(); } }}
                        placeholder="slug"
                        style={{ flex: "1 1 120px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", background: t.bg, color: t.text, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "6px 10px" }}
                      />
                      <button
                        onClick={addAshbyTenant}
                        disabled={!newAshbySlug.trim()}
                        style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "0.5px",
                          background: newAshbySlug.trim() ? t.accent : t.bgAlt,
                          color: newAshbySlug.trim() ? "#fff" : t.textDim,
                          border: "none", borderRadius: "3px", padding: "7px 14px",
                          cursor: newAshbySlug.trim() ? "pointer" : "default" }}
                      >ADD</button>
                    </div>
                  </div>

                  {/* ── BIG TECH TOGGLES ────────────────────────────── */}
                  <div style={{ marginTop: "26px", borderTop: `1px dashed ${t.borderLight}`, paddingTop: "20px" }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, color: t.text, letterSpacing: "0.5px", marginBottom: "4px" }}>Big Tech</div>
                    <div style={{ fontSize: "11px", color: t.textFaint, marginBottom: "12px" }}>
                      <strong>Slow tier</strong> (Playwright, only on Run Scraper): Apple, Meta, Microsoft, Netflix, LinkedIn.
                      <strong> Fast tier</strong> (plain HTTP, every cycle): Amazon, Google, Nvidia, Tesla, Adobe, Salesforce, Oracle, IBM, Cisco, Intel — all PMs $150k+ regularly.
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px" }}>
                      {[
                        { key: "amazon", label: "Amazon", tier: "fast", val: enableAmazon, set: setEnableAmazon, note: "JSON API, stable" },
                        { key: "google", label: "Google", tier: "fast", val: enableGoogle, set: setEnableGoogle, note: "HTML cards, fragile" },
                        { key: "nvidia", label: "Nvidia", tier: "fast", val: enableNvidia, set: setEnableNvidia, note: "Workday, stable" },
                        { key: "tesla", label: "Tesla", tier: "fast", val: enableTesla, set: setEnableTesla, note: "Workday, stable" },
                        { key: "adobe", label: "Adobe", tier: "fast", val: enableAdobe, set: setEnableAdobe, note: "Workday, stable" },
                        { key: "salesforce", label: "Salesforce", tier: "fast", val: enableSalesforce, set: setEnableSalesforce, note: "Workday, stable" },
                        { key: "oracle", label: "Oracle", tier: "fast", val: enableOracle, set: setEnableOracle, note: "Oracle Recruiting Cloud" },
                        { key: "ibm", label: "IBM", tier: "fast", val: enableIbm, set: setEnableIbm, note: "Workday, stable" },
                        { key: "cisco", label: "Cisco", tier: "fast", val: enableCisco, set: setEnableCisco, note: "Workday, stable" },
                        { key: "intel", label: "Intel", tier: "fast", val: enableIntel, set: setEnableIntel, note: "Workday, stable" },
                        { key: "apple", label: "Apple", tier: "slow", val: enableApple, set: setEnableApple, note: "Playwright · SPA" },
                        { key: "meta", label: "Meta", tier: "slow", val: enableMeta, set: setEnableMeta, note: "Playwright · GraphQL SPA" },
                        { key: "microsoft", label: "Microsoft", tier: "slow", val: enableMicrosoft, set: setEnableMicrosoft, note: "Playwright · reCAPTCHA risk" },
                        { key: "netflix", label: "Netflix", tier: "slow", val: enableNetflix, set: setEnableNetflix, note: "Playwright · SPA" },
                        { key: "linkedin", label: "LinkedIn", tier: "slow", val: enableLinkedin, set: setEnableLinkedin, note: "Playwright · login wall" },
                      ].map(row => (
                        <label key={row.key} style={{
                          display: "flex", alignItems: "flex-start", gap: "10px",
                          background: row.val ? t.bgAlt : t.bg,
                          border: `1px solid ${row.val ? t.border : t.borderLight}`,
                          borderRadius: "4px", padding: "10px 12px", cursor: "pointer",
                          opacity: row.val ? 1 : 0.65, transition: "all 0.15s",
                        }}>
                          <input
                            type="checkbox"
                            checked={row.val}
                            onChange={e => row.set(e.target.checked)}
                            style={{ marginTop: "2px", accentColor: t.accent }}
                          />
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                              <span style={{ fontSize: "12px", fontWeight: 600, color: t.text }}>{row.label}</span>
                              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "9px", letterSpacing: "0.5px",
                                color: row.tier === "slow" ? t.warn : t.textDim,
                                background: row.tier === "slow" ? (t.warnBg || t.bg) : "transparent",
                                border: `1px solid ${row.tier === "slow" ? t.warn : t.borderLight}`,
                                padding: "1px 5px", borderRadius: "2px", textTransform: "uppercase" }}>
                                {row.tier === "slow" ? "SLOW" : "FAST"}
                              </span>
                            </div>
                            <div style={{ fontSize: "10px", color: t.textFaint, marginTop: "2px" }}>{row.note}</div>
                          </div>
                        </label>
                      ))}
                    </div>
                  </div>

                  {/* ── EMPTY-STATE HINT ──────────────────────────────── */}
                  {greenhouseCompanies.length + leverCompanies.length + ashbyCompanies.length === 0 && (
                    <div style={{
                      marginTop: "16px", padding: "12px 14px",
                      background: t.bgAlt, border: `1px dashed ${t.border}`, borderRadius: "4px",
                      fontSize: "12px", color: t.textDim, lineHeight: 1.6,
                    }}>
                      <strong>No ATS tenants configured.</strong> Add a Greenhouse slug like <code style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px" }}>stripe</code> or <code style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px" }}>airbnb</code> to start pulling jobs. Test first to confirm it's live.
                    </div>
                  )}
                </div>

                {/* Data export: portable backup of the whole local state
                    (config + data tree). User can feed this to another LLM
                    for training or just keep it as a snapshot. */}
                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "24px" }}>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>
                    Data export
                  </label>
                  <div style={{ fontSize: "12px", color: t.textDim, marginBottom: "12px", lineHeight: 1.6 }}>
                    Download a zip of your SENTINEL state: settings, resume, match history, parsed job postings, digests, fit gap notes, tracker. Use it as a backup or feedstock to train another local model. Nothing leaves your machine during the export — the zip is built locally and handed straight to your browser.
                  </div>
                  <button onClick={exportBundle} disabled={!live || exportBusy} style={{
                    fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px",
                    background: live && !exportBusy ? t.accentBg : t.bgAlt,
                    color: live && !exportBusy ? t.accent : t.textDim,
                    border: `1px solid ${live && !exportBusy ? t.accent : t.border}`,
                    borderRadius: "4px", padding: "8px 16px",
                    cursor: !live || exportBusy ? "default" : "pointer",
                    opacity: !live || exportBusy ? 0.5 : 1,
                  }}>
                    {exportBusy ? "BUILDING ZIP..." : "EXPORT DATA"}
                  </button>
                  {exportMsg && (
                    <div style={{ marginTop: "10px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.textMid, background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "8px 12px" }}>
                      {exportMsg}
                    </div>
                  )}
                </div>

                <button onClick={saveSettings} disabled={!live} style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", fontWeight: 600, letterSpacing: "1px",
                  background: settingsSaved ? t.good : live ? t.accent : t.bgAlt,
                  color: "#fff", border: "none", borderRadius: "4px", padding: "12px 24px", cursor: live ? "pointer" : "default",
                  opacity: live ? 1 : 0.5, alignSelf: "flex-start", transition: "all 0.2s",
                }}>
                  {settingsSaved ? "SAVED" : "SAVE SETTINGS"}
                </button>
              </div>

              {/* ── DANGER ZONE ── Two-click reset of per-cycle data.
                  Wipes matches, digests, parsed jobs, caches, and stats
                  (everything the orchestrator regenerates on next cycle).
                  DOES NOT touch the resume, preferences, tracker, or
                  like/pass reactions. The allow-list lives in
                  core/reset_history.py so this surface stays one file. */}
              <div style={{
                marginTop: "32px",
                border: `1px solid ${t.accent}40`,
                borderRadius: "4px",
                padding: "16px 18px",
                background: isDark ? `${t.accent}0d` : `${t.accent}08`,
              }}>
                <div style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600,
                  letterSpacing: "1.5px", textTransform: "uppercase", color: t.accent, marginBottom: "6px",
                }}>Danger zone</div>
                <div style={{ fontSize: "13px", color: t.textMid, marginBottom: "12px", lineHeight: 1.5 }}>
                  Reset run history — clears matches, digests, parsed jobs, stats, and caches so the next cycle starts fresh. Your resume, preferences, tracker, and like/pass reactions are kept.
                </div>
                <button
                  onClick={resetHistory}
                  disabled={resetStatus?.busy || !live}
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600,
                    letterSpacing: "1px", textTransform: "uppercase",
                    background: resetArmed ? t.accent : "transparent",
                    color: resetArmed ? "#fff" : t.accent,
                    border: `1px solid ${t.accent}`, borderRadius: "4px",
                    padding: "10px 18px", cursor: (resetStatus?.busy || !live) ? "default" : "pointer",
                    opacity: (resetStatus?.busy || !live) ? 0.5 : 1, transition: "all 0.2s",
                  }}
                >
                  {resetStatus?.busy ? "Wiping..."
                    : resetArmed ? "Click again to confirm (5s)"
                    : "Reset Run History"}
                </button>
                {resetStatus && !resetStatus.busy && (
                  <div style={{ marginTop: "10px", fontSize: "12px", color: t.textMid, lineHeight: 1.5 }}>
                    {resetStatus.ok ? (
                      <>
                        <div style={{ color: t.good, fontWeight: 600, marginBottom: "4px" }}>
                          Done. {resetStatus.cleared?.length || 0} paths cleared.
                        </div>
                        {resetStatus.cleared?.length > 0 && (
                          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textDim }}>
                            {resetStatus.cleared.join(" · ")}
                          </div>
                        )}
                      </>
                    ) : (
                      <div style={{ color: t.accent }}>
                        Error: {resetStatus.error || (resetStatus.errors && resetStatus.errors[0]?.error) || "reset failed"}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>)}

            {/* ── PROFILE ── */}
            {view === "profile" && (<>
              <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "28px", fontWeight: 400, margin: "0 0 8px" }}>Profile</h2>
              <p style={{ fontSize: "13px", color: t.textDim, margin: "0 0 24px" }}>Who you are and what you're looking for: resume, location, salary, seniority. Preferences apply immediately; resume changes trigger a re-parse.{!live && " Connect the pipeline to save profile."}</p>

              <div style={{ maxWidth: "640px", display: "flex", flexDirection: "column", gap: "28px" }}>
                {/* Resume reference */}
                <div>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>Your Resume</label>
                  <div style={{ fontSize: "12px", color: t.textFaint, marginBottom: "12px", lineHeight: 1.6 }}>
                    Upload a PDF or DOCX to feed the match pipeline. Stored locally under data/resume. Nothing leaves your machine.
                  </div>

                  {resumeState.has_resume ? (
                    <div style={{ background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px", marginBottom: "12px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "12px", flexWrap: "wrap" }}>
                        <div>
                          <div style={{ fontSize: "13px", fontWeight: 600 }}>{resumeState.metadata?.filename || "resume"}</div>
                          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.textDim, marginTop: "2px" }}>
                            {(resumeState.metadata?.char_count || 0).toLocaleString()} chars
                            {resumeState.metadata?.size_bytes ? ` / ${(resumeState.metadata.size_bytes / 1024).toFixed(1)} kB` : ""}
                            {resumeState.metadata?.uploaded_at ? ` / ${resumeState.metadata.uploaded_at.replace("T", " ").slice(0, 16)}` : ""}
                          </div>
                        </div>
                        <div style={{ display: "flex", gap: "8px" }}>
                          <button onClick={() => fileInputRef.current?.click()} disabled={!live || resumeBusy} style={{
                            fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px",
                            background: "none", color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "6px 12px",
                            cursor: !live || resumeBusy ? "default" : "pointer", opacity: !live || resumeBusy ? 0.5 : 1,
                          }}>REPLACE</button>
                          <button onClick={reparseResume} disabled={!live || reparseBusy} style={{
                            fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px",
                            background: "none", color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "6px 12px",
                            cursor: !live || reparseBusy ? "default" : "pointer", opacity: !live || reparseBusy ? 0.5 : 1,
                          }}>{reparseBusy ? "PARSING..." : "RE-PARSE"}</button>
                          <button onClick={clearResume} disabled={!live || resumeBusy} style={{
                            fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px",
                            background: "none", color: t.accent, border: `1px solid ${t.accent}`, borderRadius: "4px", padding: "6px 12px",
                            cursor: !live || resumeBusy ? "default" : "pointer", opacity: !live || resumeBusy ? 0.5 : 1,
                          }}>CLEAR</button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <button onClick={() => fileInputRef.current?.click()} disabled={!live || resumeBusy} style={{
                      fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", fontWeight: 600, letterSpacing: "1px",
                      background: live ? t.accent : t.bgAlt, color: live ? "#fff" : t.textDim,
                      border: "none", borderRadius: "4px", padding: "10px 18px",
                      cursor: !live || resumeBusy ? "default" : "pointer", opacity: !live || resumeBusy ? 0.5 : 1,
                      alignSelf: "flex-start", marginBottom: "12px",
                    }}>
                      {resumeBusy ? "UPLOADING..." : "UPLOAD RESUME"}
                    </button>
                  )}

                  <input ref={fileInputRef} type="file" accept=".pdf,.docx,.txt,.md" onChange={(e) => uploadResume(e.target.files?.[0])} style={{ display: "none" }} />

                  <div style={{ marginTop: "16px" }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "6px" }}>Additional Notes</div>
                    <textarea value={additionalNotes} onChange={(e) => setAdditionalNotes(e.target.value)} rows={5} placeholder="Anything your resume doesn't capture: target comp, location constraints, dealbreakers, specific domains you want to aim at or avoid." style={{
                      width: "100%", fontFamily: "'Outfit', sans-serif", fontSize: "14px", padding: "12px",
                      background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px", resize: "vertical", lineHeight: 1.6,
                    }} />
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "8px", gap: "12px", flexWrap: "wrap" }}>
                      <div style={{ fontSize: "11px", color: t.textFaint }}>
                        Appended to the resume when matching. {additionalNotes.length} chars.
                      </div>
                      <button onClick={saveNotes} disabled={!live || resumeBusy} style={{
                        fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px",
                        background: "none", color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "6px 14px",
                        cursor: !live || resumeBusy ? "default" : "pointer", opacity: !live || resumeBusy ? 0.5 : 1,
                      }}>SAVE NOTES</button>
                    </div>
                  </div>

                  {resumeMsg && (
                    <div style={{ marginTop: "12px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.textMid, background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "8px 12px" }}>
                      {resumeMsg}
                    </div>
                  )}
                </div>

                {/* ── PARSED-FIELDS OVERVIEW ───
                    Read-only snapshot of what the parser thinks your
                    resume says. The editable preferences below override
                    these values where they overlap. */}
                {resumeProfile && (
                  <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "20px" }}>
                    <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>Parsed from resume</label>
                    <div style={{ fontSize: "12px", color: t.textFaint, marginBottom: "12px", lineHeight: 1.6 }}>
                      What the parser extracted. Click <strong>RE-PARSE</strong> above to refresh, or override below.
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 16px", fontSize: "13px" }}>
                      <div><span style={{ color: t.textDim }}>Seniority:</span> <span style={{ fontWeight: 500 }}>{resumeProfile.seniority || "—"}</span></div>
                      <div><span style={{ color: t.textDim }}>Years:</span> <span style={{ fontWeight: 500 }}>{resumeProfile.years_experience ?? "—"}</span></div>
                      <div style={{ gridColumn: "1 / -1" }}><span style={{ color: t.textDim }}>Target roles:</span> <span style={{ fontWeight: 500 }}>{(resumeProfile.target_roles || []).join(" · ") || "—"}</span></div>
                      <div style={{ gridColumn: "1 / -1" }}><span style={{ color: t.textDim }}>Domains:</span> <span style={{ fontWeight: 500 }}>{(resumeProfile.domains || []).join(", ") || "—"}</span></div>
                      <div style={{ gridColumn: "1 / -1" }}><span style={{ color: t.textDim }}>Tech:</span> <span style={{ fontWeight: 500 }}>{(resumeProfile.technologies || []).slice(0, 14).join(", ") || "—"}</span></div>
                    </div>
                  </div>
                )}

                {/* ── WHO YOU ARE: country / work-mode / salary / years ───
                    The pipeline-behaviour knobs (keywords, threshold,
                    scrapers, ghost filter, models) live in the Settings
                    tab. These four are about the user, not the pipeline. */}
                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "20px" }}>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>Where you'd work</label>
                  <div style={{ fontSize: "12px", color: t.textFaint, marginBottom: "12px", lineHeight: 1.6 }}>
                    Which countries are open to you, and how you'd work in them. Block list wins over allow list.
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                    <div>
                      <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Only these locations (optional)</div>
                      <input value={allowedLocations} onChange={e => setAllowedLocations(e.target.value)} placeholder="London, Manchester, UK"
                        style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }} />
                    </div>
                    <div>
                      <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Never these locations (optional)</div>
                      <input value={blockedLocations} onChange={e => setBlockedLocations(e.target.value)} placeholder="Bay Area, NYC"
                        style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }} />
                    </div>
                  </div>
                  {/* Geographic pin filter. Click the map to drop pins;
                      add as many as you want. The slider sets a single
                      radius shared by every pin (job passes if within
                      ANY pin's circle). HARD FILTER — applied during
                      the match stage server-side, so jobs outside the
                      union are dropped from scoring entirely, not just
                      hidden in the table. Locations the geocoder
                      doesn't recognise (Remote, Anywhere) always pass.
                      Click a pin to remove it. */}
                  <div style={{ marginTop: "16px" }}>
                    <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "6px" }}>
                      Or pin specific areas on the map (optional)
                    </div>
                    <JobMap
                      matches={matches}
                      theme={t}
                      height={300}
                      filterMode
                      pins={locationPins}
                      radiusKm={locationRadiusKm}
                      onPinAdd={(p) => setLocationPins(prev => [...prev, p])}
                      onPinRemove={(idx) => setLocationPins(prev => prev.filter((_, i) => i !== idx))}
                      onPinsClear={() => setLocationPins([])}
                    />
                    {locationPins.length > 0 && (
                      <div style={{ marginTop: "10px" }}>
                        <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>
                          Radius (shared by all pins): <span style={{ color: t.accent, fontWeight: 600 }}>{locationRadiusKm} km</span>
                        </div>
                        <input
                          type="range"
                          min="10"
                          max="500"
                          step="5"
                          value={locationRadiusKm}
                          onChange={(e) => setLocationRadiusKm(Number(e.target.value))}
                          style={{ width: "100%", accentColor: t.accent }}
                        />
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: t.textFaint }}>
                          <span>10 km</span><span>500 km</span>
                        </div>
                      </div>
                    )}
                    <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "6px", lineHeight: 1.5 }}>
                      Click anywhere on the map to add a pin. Click a pin to remove it. <strong>Pins and the "Only these locations" text field stack as OR</strong> — a job passes if it's in any pin radius <em>or</em> matches any text entry. So you can pin a metro on the map and also type "Toronto" if you want a city the geocoder doesn't know about. Remote jobs always bypass; cities the geocoder doesn't recognise pass the pin half by default.
                    </div>
                  </div>

                  <div style={{ marginTop: "14px" }}>
                    <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "6px" }}>Work modes you'd consider</div>
                    <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                      {[
                        { k: "remote", label: "Remote" },
                        { k: "hybrid", label: "Hybrid" },
                        { k: "onsite", label: "Onsite" },
                      ].map(({ k, label }) => {
                        const active = workModes.includes(k);
                        return (
                          <button key={k} type="button" onClick={() => {
                            setWorkModes(prev => active ? prev.filter(x => x !== k) : [...prev, k]);
                          }} style={{
                            fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "0.5px",
                            background: active ? t.accentBg : "none",
                            color: active ? t.accent : t.textMid,
                            border: `1px solid ${active ? t.accent : t.border}`,
                            borderRadius: "4px", padding: "8px 14px", cursor: "pointer",
                          }}>{label}</button>
                        );
                      })}
                    </div>
                  </div>
                </div>

                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "20px" }}>
                  <label style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, display: "block", marginBottom: "8px" }}>Compensation & seniority</label>
                  <div style={{ fontSize: "12px", color: t.textFaint, marginBottom: "12px", lineHeight: 1.6 }}>
                    Override the parser if it got these wrong. Salary is a soft penalty, not a hard cull. Drag either slider to <strong>0 to turn off</strong>.
                  </div>
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                      <div style={{ fontSize: "12px", color: t.textMid }}>Minimum salary (USD)</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>
                        {Number(salaryFloor) > 0 ? `$${(Number(salaryFloor) / 1000).toFixed(0)}k` : "off"}
                      </div>
                    </div>
                    <input type="range" min="0" max="400000" step="5000" value={Number(salaryFloor) || 0}
                      onChange={e => setSalaryFloor(Number(e.target.value))}
                      style={{ width: "100%", accentColor: t.accent }} />
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint }}>
                      <span>0 - off</span><span>$400k</span>
                    </div>
                  </div>
                  <div style={{ marginTop: "16px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                      <div style={{ fontSize: "12px", color: t.textMid }}>Years of experience</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>
                        {Number(yearsExperience) > 0 ? `${yearsExperience} yr${yearsExperience === 1 ? "" : "s"}` : "off"}
                      </div>
                    </div>
                    <input type="range" min="0" max="30" step="1" value={Number(yearsExperience) || 0}
                      onChange={e => setYearsExperience(parseInt(e.target.value, 10) || 0)}
                      style={{ width: "100%", accentColor: t.accent }} />
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint }}>
                      <span>0 - off</span><span>30 yrs</span>
                    </div>
                  </div>
                </div>

                <button onClick={saveSettings} disabled={!live} style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", fontWeight: 600, letterSpacing: "1px",
                  background: settingsSaved ? t.good : live ? t.accent : t.bgAlt,
                  color: "#fff", border: "none", borderRadius: "4px", padding: "12px 24px", cursor: live ? "pointer" : "default",
                  opacity: live ? 1 : 0.5, alignSelf: "flex-start", transition: "all 0.2s",
                }}>{settingsSaved ? "SAVED" : "SAVE PROFILE"}</button>

              </div>
            </>)}
          </div>

          {/* ── RIGHT PANEL: Job Detail ── */}
          {selectedJob && (view === "matches" || view === "liked") && (
            <div style={{
              width: "360px", flexShrink: 0, borderLeft: `1px solid ${t.border}`, paddingLeft: "32px",
              position: "sticky", top: "80px", maxHeight: "calc(100vh - 120px)", overflowY: "auto",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "16px" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>Job Detail</div>
                <button onClick={() => setSelectedJob(null)} style={{ background: "none", border: "none", color: t.textDim, cursor: "pointer", fontSize: "18px", padding: 0, lineHeight: 1 }}>x</button>
              </div>

              <h3 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "22px", fontWeight: 400, margin: "0 0 4px" }}>{selectedJob.title}</h3>
              <div style={{ fontSize: "15px", color: t.accent, fontWeight: 500, marginBottom: "16px" }}>{selectedJob.company}</div>

              {/* Tag row — all chips route through <Chip> so colours
                  are uniform (kills the "location-is-orange, tech-is-
                  gray, archetype-is-orange-but-different" rainbow).
                  Archetype keeps the accent tone because it's the
                  semantic anchor of the match. Everything else stays
                  neutral. YoE is extracted from the JD body. */}
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "20px" }}>
                {selectedJob.location && <Chip t={t} tone="neutral">{selectedJob.location}</Chip>}
                {selectedJob._country && <Chip t={t} tone="neutral" title={`detected country: ${selectedJob._country}`}>{countryName(selectedJob._country)}</Chip>}
                {selectedJob.remote && selectedJob.remote !== "unknown" && <Chip t={t} tone="neutral">{selectedJob.remote}</Chip>}
                {selectedJob.seniority && <Chip t={t} tone="neutral">{prettySeniority(selectedJob.seniority)}</Chip>}
                {(() => {
                  const yoe = extractYoE(selectedJob.description || "");
                  return yoe ? <Chip t={t} tone="neutral" title="Years of experience requested in JD">{yoe}</Chip> : null;
                })()}
                {selectedJob.archetype && prettyArchetype(selectedJob.archetype) && (
                  <Chip t={t} tone="accent" title={selectedJob.archetype_rationale || ""}>
                    {prettyArchetype(selectedJob.archetype)}
                  </Chip>
                )}
                {/* Compensation chip: numeric range only in the green pill,
                    "+ equity" / "+ bonus" type extras in a second neutral
                    pill beside it. Tooltip carries the full raw string. */}
                {selectedJob.salary && (() => {
                  const pay = prettySalary(selectedJob.salary);
                  const shown = pay.base || selectedJob.salary;
                  return (
                    <>
                      <span title={selectedJob.salary} style={{ fontSize: "11px", background: t.goodBg || t.accentBg, border: `1px solid ${t.good || t.accent}`, borderRadius: "3px", padding: "3px 8px", color: t.good || t.accent, fontWeight: 600, letterSpacing: "0.3px" }}>💵 {shown}</span>
                      {pay.extras && <span title={selectedJob.salary} style={{ fontSize: "11px", background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "3px 8px", color: t.textDim, fontWeight: 500 }}>{pay.extras}</span>}
                    </>
                  );
                })()}
              </div>

              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "28px", fontWeight: 600, color: displayScoreOf(selectedJob) >= 0.8 ? t.good : t.text, marginBottom: "4px" }}>
                {(displayScoreOf(selectedJob) * 100).toFixed(0)}%
              </div>
              <div style={{ fontSize: "11px", color: t.textDim, marginBottom: "8px" }}>Match Score</div>

              {/* Registry state badges + controls. Keeps the actions
                  reachable from the detail pane without a round-trip to
                  the list. */}
              <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "12px" }}>
                <button onClick={() => setMatchState(selectedJob, "starred", !selectedJob._starred)}
                  title={selectedJob._starred ? "Unsave" : "Save - keep this role; saved rows stick to the top"}
                  style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                    background: selectedJob._starred ? t.accentBg : "transparent",
                    color: selectedJob._starred ? t.accent : t.textDim,
                    border: `1px solid ${selectedJob._starred ? t.accent : t.border}`,
                    borderRadius: "3px", padding: "4px 10px", cursor: "pointer" }}>
                  {selectedJob._starred ? "❤ SAVED" : "🤍 SAVE"}
                </button>
                <button onClick={() => setMatchState(selectedJob, "dismissed", !selectedJob._dismissed)}
                  title={selectedJob._dismissed ? "Restore" : "Dismiss - negative signal, hides from view"}
                  style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                    background: selectedJob._dismissed ? t.accentBg : "transparent",
                    color: t.textDim, border: `1px solid ${t.border}`,
                    borderRadius: "3px", padding: "4px 10px", cursor: "pointer" }}>
                  ✕ {selectedJob._dismissed ? "DISMISSED" : "DISMISS"}
                </button>
                {/* Remove: purge expired/broken postings from the list.
                    Distinct from Dismiss (not a training signal) — this
                    says "the job no longer exists, hide it forever". */}
                <button onClick={() => { setMatchState(selectedJob, "removed", !selectedJob._removed); setSelectedJob(null); }}
                  title={selectedJob._removed ? "Restore" : "Remove (posting expired / gone)"}
                  style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                    background: "transparent",
                    color: t.textDim, border: `1px solid ${t.border}`,
                    borderRadius: "3px", padding: "4px 10px", cursor: "pointer" }}>
                  🗑 REMOVE
                </button>
                {selectedJob._applied && (
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                    color: t.good, border: `1px solid ${t.good}`, borderRadius: "3px", padding: "4px 10px" }}>
                    APPLIED
                  </span>
                )}
              </div>

              {(selectedJob._first_seen_at || selectedJob._cycle_count) && (
                <div style={{ fontSize: "10px", color: t.textFaint, marginBottom: "12px", fontFamily: "'IBM Plex Mono', monospace" }}>
                  {selectedJob._first_seen_at && `First seen ${selectedJob._first_seen_at.slice(0, 10)}`}
                  {selectedJob._cycle_count > 1 && ` · ${selectedJob._cycle_count} cycles`}
                </div>
              )}

              {/* Dimensional breakdown - only when the structured profile produced sub-scores */}
              {selectedJob._dimensions && (
                <div style={{ marginBottom: "16px", border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px", background: t.bgAlt }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "8px" }}>
                    Breakdown
                  </div>
                  {[
                    { key: "seniority_fit", label: "Seniority" },
                    { key: "tech_fit", label: "Technologies" },
                    { key: "domain_fit", label: "Domains" },
                    { key: "years_fit", label: "Years" },
                  ].map((row) => {
                    const v = selectedJob._dimensions[row.key];
                    const display = v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`;
                    const pct = v === null || v === undefined ? 0 : Math.round(v * 100);
                    return (
                      <div key={row.key} style={{ display: "grid", gridTemplateColumns: "90px 1fr 44px", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
                        <span style={{ fontSize: "11px", color: t.textMid }}>{row.label}</span>
                        <div style={{ height: "4px", background: t.border, borderRadius: "2px", overflow: "hidden" }}>
                          {v !== null && v !== undefined && (
                            <div style={{ height: "100%", width: `${pct}%`, background: v >= 0.75 ? t.good : v >= 0.4 ? t.text : t.accent }} />
                          )}
                        </div>
                        <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, textAlign: "right", color: t.textMid }}>{display}</span>
                      </div>
                    );
                  })}
                  {selectedJob._dimensions.profile_seniority && selectedJob._dimensions.job_seniority && (
                    <div style={{ fontSize: "10px", color: t.textDim, marginTop: "4px" }}>
                      You: {selectedJob._dimensions.profile_seniority} · Role: {selectedJob._dimensions.job_seniority}
                    </div>
                  )}
                </div>
              )}

              {/* ── "Why this match?" rationale ── */}
              {(() => {
                const rk = rationaleKeyFor(selectedJob);
                const rat = rationales[rk];
                const verdictColour = {
                  strong: t.good,
                  solid: t.good,
                  "worth-a-look": t.text,
                  stretch: t.accent,
                }[rat?.verdict] || t.text;
                return (
                  <div style={{ marginBottom: "16px", border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px", background: t.bgAlt }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "8px", gap: "8px" }}>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>
                        Why this match?
                      </div>
                      <button
                        onClick={() => fetchRationale(selectedJob, !!rat)}
                        disabled={!live || rationaleBusy}
                        style={{
                          fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                          background: "transparent", color: t.accent,
                          border: `1px solid ${rationaleBusy ? t.border : t.accent}`,
                          borderRadius: "3px", padding: "4px 8px",
                          cursor: (!live || rationaleBusy) ? "default" : "pointer",
                          opacity: (!live || rationaleBusy) ? 0.5 : 1,
                        }}
                      >
                        {rationaleBusy ? "THINKING…" : rat ? "REGENERATE" : "EXPLAIN"}
                      </button>
                    </div>
                    {rationaleError && (
                      <div style={{ fontSize: "11px", color: t.accent, marginBottom: "6px" }}>
                        {rationaleError}
                      </div>
                    )}
                    {!rat && !rationaleBusy && !rationaleError && (
                      <div style={{ fontSize: "12px", color: t.textFaint, lineHeight: 1.5 }}>
                        Runs deepseek-r1:14b against your profile + this JD to explain the score and flag gaps. ~10-25s.
                      </div>
                    )}
                    {rat && (
                      <>
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px", textTransform: "uppercase", color: verdictColour, marginBottom: "6px" }}>
                          {rat.verdict}
                        </div>
                        {rat.summary && (
                          <div style={{ fontSize: "13px", lineHeight: 1.55, color: t.textMid, marginBottom: rat.strengths?.length || rat.gaps?.length ? "10px" : 0 }}>
                            {rat.summary}
                          </div>
                        )}
                        {rat.strengths?.length > 0 && (
                          <div style={{ marginBottom: rat.gaps?.length ? "8px" : 0 }}>
                            <div style={{ fontSize: "10px", letterSpacing: "1px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "4px" }}>
                              Strengths
                            </div>
                            {rat.strengths.map((s, i) => (
                              <div key={i} style={{ fontSize: "12px", color: t.textMid, marginBottom: "3px", paddingLeft: "10px", position: "relative" }}>
                                <span style={{ position: "absolute", left: 0, color: t.good }}>+</span>
                                {s}
                              </div>
                            ))}
                          </div>
                        )}
                        {rat.gaps?.length > 0 && (
                          <div>
                            <div style={{ fontSize: "10px", letterSpacing: "1px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "4px" }}>
                              Gaps
                            </div>
                            {rat.gaps.map((g, i) => (
                              <div key={i} style={{ fontSize: "12px", color: t.textMid, marginBottom: "3px", paddingLeft: "10px", position: "relative" }}>
                                <span style={{ position: "absolute", left: 0, color: t.accent }}>-</span>
                                {g}
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                );
              })()}

              {/* Reaction row */}
              <div style={{ display: "flex", gap: "8px", marginBottom: "20px" }}>
                <button onClick={() => setReaction(selectedJob, "up")} disabled={!live}
                  style={{ flex: 1, fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px",
                    background: reactionFor(selectedJob) === "up" ? t.good : t.bgAlt,
                    color: reactionFor(selectedJob) === "up" ? "#fff" : t.text, border: `1px solid ${reactionFor(selectedJob) === "up" ? t.good : t.border}`,
                    borderRadius: "4px", padding: "8px 12px", cursor: live ? "pointer" : "default", opacity: live ? 1 : 0.5 }}>
                  ▲ LIKE
                </button>
                <button onClick={() => setReaction(selectedJob, "down")} disabled={!live}
                  style={{ flex: 1, fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px",
                    background: reactionFor(selectedJob) === "down" ? t.accent : t.bgAlt,
                    color: reactionFor(selectedJob) === "down" ? "#fff" : t.text, border: `1px solid ${reactionFor(selectedJob) === "down" ? t.accent : t.border}`,
                    borderRadius: "4px", padding: "8px 12px", cursor: live ? "pointer" : "default", opacity: live ? 1 : 0.5 }}>
                  ▼ PASS
                </button>
              </div>

              {selectedJob.description && (
                <div style={{ marginBottom: "20px" }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "6px" }}>Description</div>
                  <FormattedJobText text={selectedJob.description} theme={t} />
                </div>
              )}

              {(selectedJob.technologies || []).length > 0 && (
                <div style={{ marginBottom: "20px" }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600, marginBottom: "6px" }}>Technologies</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                    {selectedJob.technologies.map(t2 => <span key={t2} style={{ fontSize: "11px", background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "3px", padding: "2px 8px" }}>{t2}</span>)}
                  </div>
                </div>
              )}

              {selectedJob._fake && Object.keys(selectedJob._fake.signals || {}).length > 0 && (
                <div style={{
                  background: selectedJob._fake.is_suspect ? t.warnBg : t.bgAlt,
                  border: `1px solid ${selectedJob._fake.is_suspect ? (t.warn + "60") : t.border}`,
                  borderRadius: "4px", padding: "10px 12px", marginBottom: "20px"
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "6px" }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: selectedJob._fake.is_suspect ? t.warn : t.textDim, fontWeight: 600 }}>
                      {selectedJob._fake.is_suspect ? "Ghost-job suspect" : "Ghost-job signals"}
                    </div>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: selectedJob._fake.is_suspect ? t.warn : t.textDim, fontWeight: 600 }}>
                      {(selectedJob._fake.score * 100).toFixed(0)}%
                    </div>
                  </div>
                  {Object.entries(selectedJob._fake.signals).map(([name, sig]) => {
                    if (!sig || sig.score === 0) return null;
                    const label = name.replace(/_/g, " ");
                    return (
                      <div key={name} style={{ fontSize: "12px", color: t.textMid, marginBottom: "3px" }}>
                        <span style={{ fontWeight: 500, color: sig.score >= 0.6 ? t.warn : t.textMid }}>
                          {label}
                        </span>
                        <span style={{ color: t.textFaint }}> — {sig.reason}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {(() => { const fg = fitGapForJob(selectedJob.title, selectedJob.company); return fg ? (
                <div style={{ marginBottom: "24px", borderTop: `1px solid ${t.border}`, paddingTop: "16px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "10px" }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>Fit Analysis</div>
                    {typeof fg.match_percentage === "number" && (
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "14px", fontWeight: 600, color: (fg.match_percentage || 0) >= 80 ? t.good : t.text }}>{fg.match_percentage}%</div>
                    )}
                  </div>

                  {(fg.matched_skills || []).length > 0 && (
                    <>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "9px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.good, marginBottom: "6px", fontWeight: 600 }}>Matched skills</div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", marginBottom: "14px" }}>
                        {(fg.matched_skills || []).map(s => <span key={s} style={{ fontSize: "11px", color: isDark ? t.good : "#3d5e40", background: t.goodBg, borderRadius: "3px", padding: "2px 7px" }}>{s}</span>)}
                      </div>
                    </>
                  )}

                  {(fg.gaps || []).length > 0 && (
                    <>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "9px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.accent, marginBottom: "6px", fontWeight: 600 }}>Gaps</div>
                      <div style={{ marginBottom: "14px" }}>
                        {(fg.gaps || []).map((g, i) => (
                          <div key={i} style={{ marginBottom: "8px" }}>
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                              <span style={{ fontSize: "13px", fontWeight: 500, color: t.text }}>{g.skill}</span>
                              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, color: g.severity === "critical" ? t.accent : g.severity === "moderate" ? t.warn : t.good }}>{g.severity}</span>
                            </div>
                            {g.mitigation && <div style={{ fontSize: "12px", color: t.textDim, marginTop: "2px", lineHeight: 1.5 }}>{g.mitigation}</div>}
                          </div>
                        ))}
                      </div>
                    </>
                  )}

                  {(fg.talking_points || []).length > 0 && (
                    <>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "9px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "6px", fontWeight: 600 }}>Talking points</div>
                      {(fg.talking_points || []).map((tp, i) => (
                        <div key={i} style={{ fontSize: "12px", color: t.textMid, lineHeight: 1.55, marginBottom: "6px", paddingLeft: "10px", borderLeft: `2px solid ${t.accent}` }}>{tp}</div>
                      ))}
                    </>
                  )}
                </div>
              ) : null; })()}

              {/* ── Cover letter generator ── */}
              {(() => {
                const k = rationaleKeyFor(selectedJob);
                const letter = coverLetters[k];
                return (
                  <div style={{ marginBottom: "16px", border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px", background: t.bgAlt }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "8px", gap: "8px" }}>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>
                        Cover letter
                      </div>
                      <button
                        onClick={() => generateCoverLetter(selectedJob)}
                        disabled={!live || coverLetterBusy}
                        style={{
                          fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                          background: "transparent", color: t.accent,
                          border: `1px solid ${coverLetterBusy ? t.border : t.accent}`,
                          borderRadius: "3px", padding: "4px 8px",
                          cursor: (!live || coverLetterBusy) ? "default" : "pointer",
                          opacity: (!live || coverLetterBusy) ? 0.5 : 1,
                        }}
                      >
                        {coverLetterBusy ? "DRAFTING…" : letter ? "REGENERATE" : "GENERATE"}
                      </button>
                    </div>

                    {/* Tone picker */}
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "4px", marginBottom: "8px" }}>
                      {[
                        { key: "professional", label: "Professional" },
                        { key: "warm",         label: "Warm" },
                        { key: "punchy",       label: "Punchy" },
                      ].map(opt => {
                        const active = coverLetterTone === opt.key;
                        return (
                          <button key={opt.key} type="button" onClick={() => setCoverLetterTone(opt.key)}
                            disabled={coverLetterBusy}
                            style={{
                              fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                              background: active ? t.accentBg : "transparent",
                              color: active ? t.accent : t.textMid,
                              border: `1px solid ${active ? t.accent : t.border}`,
                              borderRadius: "3px", padding: "4px 6px",
                              cursor: coverLetterBusy ? "default" : "pointer",
                              opacity: coverLetterBusy ? 0.5 : 1,
                            }}>
                            {opt.label}
                          </button>
                        );
                      })}
                    </div>

                    {/* Optional extra instructions */}
                    <textarea value={coverLetterNote} onChange={(e) => setCoverLetterNote(e.target.value)}
                      placeholder="Optional extra instructions (e.g. mention visa sponsorship, call out a specific project)"
                      rows={2}
                      style={{
                        width: "100%", fontFamily: "'Outfit', sans-serif", fontSize: "12px", padding: "6px 8px",
                        background: t.bg, color: t.text, border: `1px solid ${t.border}`, borderRadius: "3px",
                        resize: "vertical", lineHeight: 1.4, marginBottom: "8px", boxSizing: "border-box",
                      }} />

                    {coverLetterError && (
                      <div style={{ fontSize: "11px", color: t.accent, marginBottom: "6px" }}>
                        {coverLetterError}
                      </div>
                    )}
                    {!letter && !coverLetterBusy && !coverLetterError && (
                      <div style={{ fontSize: "12px", color: t.textFaint, lineHeight: 1.5 }}>
                        Drafts a 250-350 word letter from your parsed resume and this JD. Local Ollama call, ~10-60s. Saved to data/cover_letters/.
                      </div>
                    )}

                    {letter?.text && (
                      <>
                        <textarea
                          value={letter.text}
                          onChange={(e) => setCoverLetters((prev) => ({
                            ...prev,
                            [k]: { ...(prev[k] || {}), text: e.target.value },
                          }))}
                          rows={12}
                          style={{
                            width: "100%", fontFamily: "'Outfit', sans-serif", fontSize: "13px", padding: "10px",
                            background: t.bg, color: t.text, border: `1px solid ${t.border}`, borderRadius: "3px",
                            resize: "vertical", lineHeight: 1.55, marginTop: "4px", boxSizing: "border-box",
                          }} />
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "6px", gap: "8px" }}>
                          <div style={{ fontSize: "10px", color: t.textFaint, fontFamily: "'IBM Plex Mono', monospace" }}>
                            {letter.text.split(/\s+/).filter(Boolean).length} words
                            {letter.model ? ` · ${letter.model}` : ""}
                          </div>
                          <button onClick={() => copyCoverLetter(letter.text)}
                            style={{
                              fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                              background: coverLetterCopied ? t.good : "transparent",
                              color: coverLetterCopied ? "#fff" : t.text,
                              border: `1px solid ${coverLetterCopied ? t.good : t.border}`,
                              borderRadius: "3px", padding: "4px 10px", cursor: "pointer",
                            }}>
                            {coverLetterCopied ? "COPIED" : "COPY"}
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                );
              })()}

              {/* Tailor Resume button.  Secondary (outlined) so APPLY stays
                  the primary CTA.  Status text below shows the file path the
                  server wrote so the user can open it from disk. */}
              {(() => {
                const key = selectedJob.url || `${selectedJob.company}:${selectedJob.title}`;
                const tstate = tailorState[key] || {};
                return (
                  <div style={{ marginBottom: "10px" }}>
                    <button
                      onClick={() => tailorResume(selectedJob)}
                      disabled={tstate.busy}
                      style={{
                        width: "100%",
                        fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px",
                        background: "transparent", color: t.accent,
                        border: `1px solid ${t.accent}`, borderRadius: "4px",
                        padding: "10px 24px", cursor: tstate.busy ? "wait" : "pointer",
                        opacity: tstate.busy ? 0.6 : 1,
                      }}
                    >
                      {tstate.busy ? "TAILORING..." : "TAILOR RESUME FOR THIS ROLE"}
                    </button>
                    {(tstate.pdfFile || tstate.htmlFile) && (
                      <div style={{ marginTop: "8px", fontSize: "11px", color: t.textDim }}>
                        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                          {tstate.pdfFile && (
                            <a
                              href={`${API}/api/resumes/download?file=${encodeURIComponent(tstate.pdfFile)}`}
                              target="_blank" rel="noopener noreferrer"
                              style={{
                                fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                                background: t.accent, color: "#fff", border: `1px solid ${t.accent}`,
                                borderRadius: "3px", padding: "5px 10px", textDecoration: "none",
                              }}
                            >
                              OPEN PDF
                            </a>
                          )}
                          {tstate.htmlFile && (
                            <a
                              href={`${API}/api/resumes/download?file=${encodeURIComponent(tstate.htmlFile)}`}
                              target="_blank" rel="noopener noreferrer"
                              style={{
                                fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                                background: "transparent", color: t.text, border: `1px solid ${t.border}`,
                                borderRadius: "3px", padding: "5px 10px", textDecoration: "none",
                              }}
                            >
                              OPEN HTML
                            </a>
                          )}
                        </div>
                        {tstate.pdfMethod && (
                          <div style={{ fontSize: "10px", color: t.textFaint, marginTop: "6px" }}>
                            PDF via {tstate.pdfMethod}
                            {!tstate.pdfFile && " (unavailable — HTML only)"}
                          </div>
                        )}
                        {tstate.summary && (
                          <div style={{ fontSize: "11px", color: t.textMid, marginTop: "6px", lineHeight: 1.45, fontStyle: "italic" }}>
                            "{tstate.summary}"
                          </div>
                        )}
                      </div>
                    )}
                    {tstate.error && (
                      <div style={{ fontSize: "11px", color: "#d04", marginTop: "6px" }}>
                        Error: {tstate.error}
                      </div>
                    )}
                  </div>
                );
              })()}

              <a href={selectedJob.url || "#"} target="_blank" rel="noopener noreferrer" style={{
                display: "block", textAlign: "center", fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", fontWeight: 600, letterSpacing: "1px",
                background: t.accent, color: "#fff", border: "none", borderRadius: "4px", padding: "12px 24px", textDecoration: "none",
                transition: "opacity 0.2s",
              }} onMouseEnter={e => e.target.style.opacity = "0.85"} onMouseLeave={e => e.target.style.opacity = "1"}>
                APPLY ON {(selectedJob.company || "SITE").toUpperCase()}
              </a>

              <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "8px", textAlign: "center" }}>
                Source: {selectedJob._source || "—"}
              </div>
              {Array.isArray(selectedJob._provenance) && selectedJob._provenance.length > 1 && (
                <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "4px", textAlign: "center" }}>
                  Also on: {selectedJob._provenance.filter((s) => s && s !== selectedJob._source).join(", ")}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── FOOTER ── */}
        <footer style={{ borderTop: `3px solid ${t.text}`, padding: "20px 0 40px", display: "flex", justifyContent: "space-between", fontSize: "11px", color: t.textDim }}>
          <span>Built by Eddie Baumel / 2026</span>
          <span style={{ fontFamily: "'IBM Plex Mono', monospace" }}>
            sentinel · {status?.match?.mode || "—"}{status?.match?.embeddings_active ? "" : " (llm)"}
          </span>
        </footer>
      </div>

      {/* ── WIZARD MODAL ── */}
      {wizardOpen && (
        <WizardModal
          t={t}
          onClose={() => { setWizardOpen(false); setWizardDismissed(true); }}
          onUploadResume={uploadResume}
          resumeState={resumeState}
          resumeProfile={resumeProfile}
          reparseBusy={reparseBusy}
          preflight={preflight}
          prewarm={prewarm}
          setupState={setupState}
          onRecheck={async () => {
            // Wizard 'Re-check' button: force-refresh preflight and
            // kick prewarm again (idempotent on the backend).
            try {
              const pf = await fetch(`${API}/api/preflight`).then((r) => r.json());
              if (pf) setPreflight(pf);
              await fetch(`${API}/api/prewarm`, { method: "POST" });
            } catch {}
          }}
          onFinish={async (finalPrefs) => {
            try {
              // Persist the full config patch (keywords, threshold,
              // filters, per-task models, analyse top-N).
              await fetch(`${API}/api/config`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  role_keywords: finalPrefs.keywords,
                  threshold: finalPrefs.threshold,
                  cycle_interval_minutes: finalPrefs.cycleInterval,
                  preferences: {
                    work_modes: finalPrefs.workModes,
                    allowed_locations: finalPrefs.allowedLocations,
                    blocked_locations: [],
                    salary_floor_usd: finalPrefs.salaryFloor,
                    salary_weight: finalPrefs.salaryWeight,
                    years_experience: finalPrefs.yearsExperience,
                    current_level: finalPrefs.currentLevel,
                    years_weight: finalPrefs.yearsWeight,
                    trapdoor_enabled: finalPrefs.trapdoorEnabled,
                  },
                  models: finalPrefs.models,
                  analyze_top_n: finalPrefs.analyzeTopN,
                }),
              });
              // Flip the user_store setup flag + persist identity so the
              // wizard won't re-open on the next launch.
              await fetch(`${API}/api/setup`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  identity: {
                    name: finalPrefs.name || "",
                    current_role: finalPrefs.currentRole || "",
                    target_level: finalPrefs.targetLevel || "",
                  },
                  finish: true,
                }),
              });
            } catch {}
            setWizardOpen(false); setWizardDismissed(true);
            poll();
          }}
        />
      )}

      {/* ── BOTTOM CHAT DRAWER ─────────────────────────────────────
          Persistent, context-aware chat docked to the viewport bottom.
          Collapsed: 44px bar labelled "ASK SENTINEL" with message count.
          Expanded: ~440px panel with history, input and send button.
          History persists via localStorage (capped at 100 turns). Every
          request ships { view, selectedJob, filters } so the local model
          knows what the user is looking at without the user re-stating. */}
      <div style={{
        position: "fixed",
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 90,
        background: t.bg,
        borderTop: `2px solid ${t.text}`,
        boxShadow: chatOpen ? `0 -8px 24px rgba(0,0,0,${isDark ? 0.5 : 0.12})` : "none",
        transition: "height 160ms ease",
        height: chatOpen ? "min(480px, 60vh)" : "44px",
        display: "flex",
        flexDirection: "column",
        fontFamily: "'Outfit', sans-serif",
      }}>
        {/* Header bar - always visible, click to toggle */}
        <div
          onClick={() => setChatOpen(o => !o)}
          style={{
            flex: "0 0 44px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 20px",
            cursor: "pointer",
            borderBottom: chatOpen ? `1px solid ${t.border}` : "none",
            userSelect: "none",
          }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "2px", color: t.accent }}>
              {chatOpen ? "▼" : "▲"} ASK SENTINEL
            </span>
            {chatMessages.length > 0 && (
              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textDim }}>
                {chatMessages.length} message{chatMessages.length === 1 ? "" : "s"}
              </span>
            )}
            {chatBusy && (
              <span style={{ fontSize: "11px", color: t.textDim, fontStyle: "italic" }}>Thinking…</span>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
            {chatOpen && chatMessages.length > 0 && (
              <button
                onClick={(e) => { e.stopPropagation(); clearChat(); }}
                style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px",
                  background: "transparent", color: t.textDim, border: "none",
                  cursor: "pointer", padding: 0,
                }}>
                CLEAR
              </button>
            )}
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textFaint, letterSpacing: "1px" }}>
              {view.toUpperCase()}{selectedJob ? ` · ${selectedJob.company}` : ""}
            </span>
          </div>
        </div>

        {/* Expanded body */}
        {chatOpen && (
          <>
            <div ref={chatScrollRef} style={{
              flex: 1,
              overflowY: "auto",
              padding: "16px 20px",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
            }}>
              {chatMessages.length === 0 && (
                <div style={{ color: t.textDim, fontSize: "13px", lineHeight: 1.7 }}>
                  Context-aware chat over your matches, decisions and market data. Runs on your local Ollama.
                  <br /><br />
                  Try: <em>"Which of my saved matches should I apply to first?"</em> · <em>"Summarise the top skill gaps I should close before interviewing here."</em> · <em>"What's my remote-vs-hybrid split looking like?"</em>
                </div>
              )}
              {chatMessages.map((m, i) => (
                <div key={i} style={{
                  alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                  maxWidth: "85%",
                  background: m.role === "user" ? t.accentBg : t.bgAlt,
                  border: `1px solid ${m.role === "user" ? t.accent + "40" : t.border}`,
                  borderRadius: "8px",
                  padding: "10px 14px",
                }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "9px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, marginBottom: "4px", fontWeight: 600 }}>{m.role}</div>
                  <div style={{ fontSize: "14px", lineHeight: 1.6, whiteSpace: "pre-wrap", color: t.text }}>{m.content}</div>
                </div>
              ))}
              {chatBusy && (
                <div style={{ alignSelf: "flex-start", color: t.textDim, fontSize: "13px", fontStyle: "italic" }}>Thinking…</div>
              )}
            </div>

            <div style={{ flex: "0 0 auto", display: "flex", gap: "10px", padding: "12px 20px", borderTop: `1px solid ${t.border}`, background: t.bgAlt }}>
              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } }}
                placeholder={live ? "Ask about your pipeline…" : "Connect the pipeline to start chatting."}
                disabled={!live || chatBusy}
                rows={2}
                style={{
                  flex: 1, fontFamily: "'Outfit', sans-serif", fontSize: "14px",
                  padding: "8px 12px", background: t.bg, color: t.text,
                  border: `1px solid ${t.border}`, borderRadius: "4px",
                  resize: "none", lineHeight: 1.5,
                }} />
              <button
                onClick={sendChat}
                disabled={!live || chatBusy || !chatInput.trim()}
                style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", fontWeight: 600, letterSpacing: "1px",
                  background: (!live || chatBusy || !chatInput.trim()) ? t.bgAlt : t.accent,
                  color: (!live || chatBusy || !chatInput.trim()) ? t.textDim : "#fff",
                  border: `1px solid ${(!live || chatBusy || !chatInput.trim()) ? t.border : t.accent}`,
                  borderRadius: "4px", padding: "0 20px",
                  cursor: (!live || chatBusy || !chatInput.trim()) ? "default" : "pointer",
                }}>
                SEND
              </button>
            </div>
          </>
        )}
      </div>

      <style>{`
        * { box-sizing: border-box; margin: 0; }
        ::selection { background: ${t.accent}; color: #fff; }
        body { margin: 0; background: ${t.bg}; }
        input[type=range] { height: 4px; }
        @keyframes sentinelPulse { 0%,100% { opacity: 1 } 50% { opacity: 0.25 } }
        @keyframes helper-pop {
          0%   { transform: translateX(-50%) translateY(4px) scale(0.6); opacity: 0; }
          60%  { transform: translateX(-50%) translateY(-2px) scale(1.08); opacity: 1; }
          100% { transform: translateX(-50%) translateY(0) scale(1); opacity: 1; }
        }
        /* Responsive: below 960px, collapse wide multi-column grids into
           stacks so the content doesn't overflow or squish. Matches the
           Brief/Intel/Settings grids which all use gridTemplateColumns of
           the form "1.4fr 1fr", "1fr 1fr 1fr", "2.5fr 1fr 0.6fr" etc. */
        @media (max-width: 960px) {
          .sentinel-shell [style*="grid-template-columns: 1.4fr 1fr"],
          .sentinel-shell [style*="gridTemplateColumns: 1.4fr 1fr"] { grid-template-columns: 1fr !important; }
        }
        @media (max-width: 760px) {
          /* Narrow viewport: all multi-col grids fall to single column so
             tables, intel cards, and wizard step grids don't overflow. */
          .sentinel-shell .reflow-stack { grid-template-columns: 1fr !important; gap: 12px !important; }
          .sentinel-shell h1 { font-size: 32px !important; }
          .sentinel-header { padding: 28px 0 20px !important; }
          /* Match-list rows switch to a single-column stack; the header
             row hides because a stacked header would just repeat the
             labels for every row. */
          .sentinel-shell [data-responsive="match-row"] {
            grid-template-columns: 1fr !important;
            gap: 4px !important;
            padding: 14px 0 !important;
          }
          .sentinel-shell [data-responsive="match-row"] > * { text-align: left !important; }
          .sentinel-shell [data-responsive="match-header"] { display: none !important; }
          /* Brief metric strip: 4 across is too tight; collapse to 2x2. */
          .sentinel-shell [data-responsive="metric-strip"] {
            grid-template-columns: 1fr 1fr !important;
          }
        }
        /* Large viewport: let the root breathe a bit wider than the old
           1080 cap. */
        @media (min-width: 1400px) {
          .sentinel-shell { padding-left: 48px !important; padding-right: 48px !important; }
        }
      `}</style>
    </div>
  );
}

// ─── WIZARD ────────────────────────────────────────────────────
// ─── MODEL TIER CATALOGUE ─────────────────────────────────────
// Drives the Models step. Each tier has a concrete Ollama model name,
// an approximate VRAM cost, and a one-line rationale the user sees in
// the wizard. The wizard hides tiers the user's hardware can't support,
// so rationale copy stays hardware-agnostic.
const MODEL_TIERS = {
  light:    { id: "light",    name: "gemma3:4b",       label: "Light",    vram: "~3 GB",  speed: "fastest", minVramGb: 0  },
  balanced: { id: "balanced", name: "gemma3:12b",      label: "Balanced", vram: "~8 GB",  speed: "medium",  minVramGb: 8  },
  deep:     { id: "deep",     name: "qwen3:14b",       label: "Deep",     vram: "~10 GB", speed: "slow",    minVramGb: 10 },
  reasoning:{ id: "reasoning",name: "deepseek-r1:14b", label: "Reasoning",vram: "~12 GB", speed: "slowest", minVramGb: 12 },
};

// Given a detected VRAM figure, return the set of tiers that are viable.
// CPU-only or unknown hardware keeps Light available; anything larger is
// still listed with a warning the wizard renders alongside the button.
const tiersForVram = (vramGb) => {
  const set = ["light"];
  if (vramGb == null || vramGb >= 8) set.push("balanced");
  if (vramGb != null && vramGb >= 10) set.push("deep");
  if (vramGb != null && vramGb >= 12) set.push("reasoning");
  return set;
};

// Pick a sane default tier for a task based on available VRAM. The
// wizard uses this both for its initial picks and to snap a user's old
// choice back into range if we detect their card can't support it.
const defaultTierFor = (taskId, vramGb) => {
  const band = vramGb == null ? "cpu"
    : vramGb < 8 ? "low"
    : vramGb < 10 ? "mid"
    : vramGb < 12 ? "high"
    : "top";
  // Per-task matrix: map (task, band) -> tier. Parse stays balanced even
  // on top-tier cards because extraction is mechanical; the 14B models
  // get pulled in for match and analyze where reasoning actually helps.
  const table = {
    parse:   { cpu: "light", low: "light",    mid: "balanced", high: "balanced", top: "balanced" },
    match:   { cpu: "light", low: "balanced", mid: "balanced", high: "deep",     top: "deep"      },
    analyze: { cpu: "light", low: "balanced", mid: "balanced", high: "deep",     top: "reasoning" },
    chat:    { cpu: "light", low: "balanced", mid: "balanced", high: "deep",     top: "deep"      },
  };
  return (table[taskId] || {})[band] || "light";
};

// Per-task model menus. Rationale copy is written for a general user,
// not a specific GPU. Deep stays in the list for analyze on top-tier
// cards; the wizard hides it automatically below 16 GB VRAM.
// Every task now exposes all three tiers. Eddie's call: don't block -
// warn instead. The user can pick Deep on a 6 GB card if they want to;
// we just flag that cycles will be slow.
const MODEL_TASKS = [
  {
    id: "parse",
    title: "Read job pages",
    description: "Turns raw career-page HTML into structured job data. A small, fast model is ideal here.",
    tiers: ["light", "balanced", "deep"],
    rationale: "Extraction is mechanical. Balanced (gemma3:12b) is enough for clean JSON output. Deep only helps on pages with messy layouts.",
  },
  {
    id: "match",
    title: "Score jobs against your resume",
    description: "Ranks each job by how well it fits you. Uses local embeddings when installed; only falls back to this model otherwise.",
    tiers: ["light", "balanced", "deep", "reasoning"],
    rationale: "With embeddings available (fast path), this model only runs on borderline cases. Deep (qwen3:14b) handles nuance well; Reasoning (deepseek-r1:14b) is overkill unless you want chain-of-thought rationales logged.",
  },
  {
    id: "analyze",
    title: "Explain the fit and gaps",
    description: "Writes the 'why it matches' summary, strengths and learning gaps for your best-ranked jobs.",
    tiers: ["light", "balanced", "deep", "reasoning"],
    rationale: "Reasoning (deepseek-r1:14b) produces noticeably sharper gap analyses because it thinks step-by-step. Deep is fine if you want speed. Light struggles with nuance on senior roles.",
  },
  {
    id: "chat",
    title: "Chat assistant",
    description: "Answers questions about your matches, gaps and the market inside the Chat tab.",
    tiers: ["light", "balanced", "deep", "reasoning"],
    rationale: "Interactive use wants quick replies. Deep (qwen3:14b) feels conversational at ~2-4s per reply. Reasoning adds visible think-time but gives more structured answers.",
  },
];

// ─── SMALL HELPERS ────────────────────────────────────────────
function StateBadge({ t, state, label }) {
  const colour = state === "ok" || state === "ready" ? t.good
    : state === "warn" || state === "partial" ? t.warn
    : state === "fail" || state === "failed" ? t.accent
    : state === "running" ? t.warn
    : t.textDim;
  const mark = state === "ok" || state === "ready" ? "✓"
    : state === "warn" || state === "partial" ? "!"
    : state === "fail" || state === "failed" ? "x"
    : state === "running" ? "…"
    : state === "skipped" ? "-"
    : "?";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: colour }}>
      <span style={{ display: "inline-block", width: "16px", textAlign: "center", fontWeight: 700 }}>{mark}</span>
      <span style={{ color: t.textMid }}>{label}</span>
    </span>
  );
}

function WizardModal({ t, onClose, onUploadResume, resumeState, resumeProfile, reparseBusy, preflight, prewarm, setupState, onRecheck, onFinish }) {
  const [step, setStep] = useState(0);
  // Identity
  const initialUser = (setupState && setupState.user) || {};
  const [name, setName] = useState(initialUser.name || "");
  const [currentRole, setCurrentRole] = useState(initialUser.current_role || "");
  const [targetLevel, setTargetLevel] = useState(initialUser.target_level || "senior");
  // Pipeline config
  const [keywords, setKeywords] = useState("product manager, senior product manager, technical program manager");
  const [threshold, setThreshold] = useState(0.55);
  // Work modes: any of remote / hybrid / onsite. All three on = "any".
  const [workModes, setWorkModes] = useState(["remote", "hybrid", "onsite"]);
  const [allowedLocations, setAllowedLocations] = useState("");
  const [salaryFloor, setSalaryFloor] = useState(0);
  const [salaryWeight, setSalaryWeight] = useState(0.15);
  // Cycle interval (minutes). Lower = more frequent scans.
  const [cycleInterval, setCycleInterval] = useState(30);
  // Experience: years of work and current seniority band. Years can be
  // pre-filled from the parsed resume profile; level is always user-picked
  // since parse confidence is low. Defaults are a safe "unset" state (0/"")
  // which keeps the ExperienceFilter inactive on the backend until the
  // user touches it, matching how new-grad users expect the flow to work.
  const [yearsExperience, setYearsExperience] = useState(0);
  const [currentLevel, setCurrentLevel] = useState("");
  const [yearsWeight, setYearsWeight] = useState(0.04);
  const [trapdoorEnabled, setTrapdoorEnabled] = useState(true);
  // Pre-fill years from the parsed resume once it becomes available.
  // We only do this if the user hasn't entered their own value yet (0),
  // so re-parsing doesn't clobber a manual edit.
  useEffect(() => {
    if (resumeProfile && resumeProfile.years_experience && yearsExperience === 0) {
      setYearsExperience(resumeProfile.years_experience);
    }
    if (resumeProfile && resumeProfile.seniority && !currentLevel) {
      setCurrentLevel(resumeProfile.seniority);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeProfile]);
  // User-provided VRAM override for the case where hardware detection
  // couldn't identify a GPU. null until they pick a size.
  const [vramOverride, setVramOverride] = useState(null);

  // Detected hardware from /api/preflight. Single source of truth.
  const detected = preflight && preflight.hardware && preflight.hardware.detected ? preflight.hardware : null;
  const detectedVram = detected ? detected.vram_gb : null;
  const effectiveVram = detectedVram != null ? detectedVram : vramOverride;

  // Per-task model picks. Initialise each to the tier that fits the
  // detected (or user-picked) VRAM band; re-snap below in a useEffect
  // when effectiveVram changes so we never leave a tier selected that
  // the card can't support.
  const [modelPicks, setModelPicks] = useState(() => {
    const o = {};
    for (const task of MODEL_TASKS) o[task.id] = defaultTierFor(task.id, null);
    return o;
  });
  // Soft recommendation, not a hard cap. Returns the comfortable top-N
  // for this hardware - the UI shows it as a hint and warns if the user
  // exceeds it, but does not block. Eddie explicitly asked to let the
  // slider go to 100; efficiency warning is shown inline instead.
  const topNCapFor = (vramGb) => {
    if (vramGb == null) return 5;
    if (vramGb <= 4) return 3;
    if (vramGb <= 8) return 8;
    if (vramGb < 16) return 15;
    return 25;
  };
  const TOP_N_MAX = 100;
  const [analyzeTopN, setAnalyzeTopN] = useState(10);
  const fileRef = useRef(null);
  // Track which tasks the user explicitly changed. Once touched, VRAM
  // changes should not overwrite the user's choice. Eddie's rule: allow
  // Deep anywhere; warn but never force-downgrade.
  const userTouchedTiers = useRef(new Set());

  // When VRAM first becomes known (or the user overrides), apply the
  // hardware-appropriate defaults only to tasks the user has not
  // explicitly touched. Deep is no longer hidden - the UI warns inline.
  useEffect(() => {
    const vram = effectiveVram;
    setModelPicks((prev) => {
      const next = { ...prev };
      for (const task of MODEL_TASKS) {
        if (userTouchedTiers.current.has(task.id)) continue;
        next[task.id] = defaultTierFor(task.id, vram);
      }
      return next;
    });
    // Only clamp to the absolute ceiling (TOP_N_MAX). The per-VRAM
    // recommendation (topNCapFor) is advisory now - we warn inline if
    // the user exceeds it but do not silently downgrade their choice.
    setAnalyzeTopN((n) => Math.min(Math.max(1, n), TOP_N_MAX));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveVram]);

  const steps = ["Welcome", "Ready", "Resume", "Roles", "Experience", "Filters", "Models", "Review"];

  const finish = () => {
    // Materialise the per-task model picks into concrete Ollama names
    // so the backend doesn't have to know about our tier vocabulary.
    const models = {};
    for (const task of MODEL_TASKS) {
      const tier = modelPicks[task.id] || task.recommended;
      models[task.id] = MODEL_TIERS[tier].name;
    }
    onFinish({
      name, currentRole, targetLevel,
      keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
      threshold: Number(threshold),
      workModes: workModes.filter(m => ["remote", "hybrid", "onsite"].includes(m)),
      allowedLocations: allowedLocations.split(",").map((s) => s.trim()).filter(Boolean),
      salaryFloor: Number(salaryFloor) || 0,
      salaryWeight: Number(salaryWeight) || 0,
      yearsExperience: Number(yearsExperience) || 0,
      currentLevel: currentLevel || "",
      yearsWeight: Number(yearsWeight) || 0.04,
      trapdoorEnabled: Boolean(trapdoorEnabled),
      cycleInterval: Math.max(5, Math.min(240, Number(cycleInterval) || 30)),
      models,
      analyzeTopN: Number(analyzeTopN) || 10,
    });
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 10, display: "flex", alignItems: "center", justifyContent: "center", padding: "20px" }}>
      <div style={{ width: "min(640px, 100%)", maxHeight: "90vh", overflowY: "auto", background: t.bg, color: t.text, border: `1px solid ${t.border}`, borderRadius: "8px", boxShadow: "0 20px 60px rgba(0,0,0,0.4)" }}>
        <div style={{ borderBottom: `1px solid ${t.border}`, padding: "20px 24px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", color: t.textDim, fontWeight: 600 }}>SETUP · STEP {step + 1} of {steps.length}</div>
            <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: "26px", margin: "4px 0 0", fontWeight: 400 }}>{steps[step]}</h2>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: t.textDim, fontSize: "18px", cursor: "pointer" }}>x</button>
        </div>

        <div style={{ padding: "24px", minHeight: "320px" }}>
          {/* 0. WELCOME + IDENTITY */}
          {step === 0 && (
            <div style={{ fontSize: "14px", lineHeight: 1.7, color: t.textMid }}>
              <p>SENTINEL scans 40+ company career APIs, scores each role against your profile and surfaces the best matches locally. Nothing leaves your machine unless you send a digest.</p>
              <p style={{ marginTop: "12px" }}>A few details so your matches feel like you, not a template:</p>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginTop: "14px" }}>
                <div style={{ gridColumn: "span 2" }}>
                  <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Name (optional)</div>
                  <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Eddie"
                    style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }} />
                </div>
                <div>
                  <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Current role</div>
                  <input value={currentRole} onChange={(e) => setCurrentRole(e.target.value)} placeholder="Platform PM"
                    style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }} />
                </div>
                <div>
                  <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Target level</div>
                  <select value={targetLevel} onChange={(e) => setTargetLevel(e.target.value)}
                    style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }}>
                    <option value="intern">Intern</option>
                    <option value="entry">Entry level / Junior</option>
                    <option value="mid">Mid-level</option>
                    <option value="senior">Senior</option>
                    <option value="staff">Staff</option>
                    <option value="principal">Principal</option>
                    <option value="director">Director</option>
                    <option value="vp">VP</option>
                    <option value="cxo">C-level</option>
                  </select>
                </div>
              </div>
              <p style={{ fontSize: "11px", color: t.textFaint, marginTop: "12px" }}>
                All fields are optional. They tune the fit-gap narrative and seniority scoring.
              </p>
            </div>
          )}

          {/* 1. READY CHECK - preflight + prewarm live */}
          {step === 1 && (
            <div>
              <p style={{ fontSize: "14px", color: t.textMid, lineHeight: 1.6, marginBottom: "12px" }}>
                We ran a preflight in the background. Fix anything marked x before clicking Finish, or continue and the pipeline will skip degraded parts.
              </p>
              <div style={{ background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px", marginBottom: "12px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1px", color: t.textDim, fontWeight: 700 }}>PREFLIGHT</div>
                  <button onClick={onRecheck} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, background: "none", color: t.textMid, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "4px 8px", cursor: "pointer" }}>
                    RE-CHECK
                  </button>
                </div>
                {!preflight && <div style={{ fontSize: "12px", color: t.textDim }}>Probing...</div>}
                {preflight && preflight.checks && (
                  <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: "6px" }}>
                    {Object.entries(preflight.checks).map(([k, v]) => (
                      <div key={k} style={{ display: "flex", alignItems: "flex-start", gap: "8px" }}>
                        <StateBadge t={t} state={v.state} label={k.replace(/_/g, " ")} />
                        <div style={{ fontSize: "11px", color: t.textDim, flex: 1 }}>
                          <div>{v.detail}</div>
                          {v.fix && v.state !== "ok" && v.state !== "skipped" && (
                            <div style={{ color: t.textFaint, marginTop: "2px", fontStyle: "italic" }}>Fix: {v.fix}</div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div style={{ background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1px", color: t.textDim, fontWeight: 700, marginBottom: "8px" }}>PRE-WARM</div>
                {!prewarm && <div style={{ fontSize: "12px", color: t.textDim }}>Starting...</div>}
                {prewarm && (
                  <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: "6px" }}>
                    <StateBadge t={t} state={prewarm.embeddings?.state} label={`Embeddings: ${prewarm.embeddings?.detail || prewarm.embeddings?.state || "-"}`} />
                    <StateBadge t={t} state={prewarm.ollama?.state} label={`Ollama models: ${prewarm.ollama?.detail || prewarm.ollama?.state || "-"}`} />
                  </div>
                )}
                <p style={{ fontSize: "11px", color: t.textFaint, marginTop: "8px" }}>
                  Model weights pre-load while you finish the wizard, so your first cycle feels near-instant instead of paying cold-start.
                </p>
              </div>
            </div>
          )}

          {/* 2. RESUME */}
          {step === 2 && (
            <div>
              <p style={{ fontSize: "14px", color: t.textMid, lineHeight: 1.6, marginBottom: "16px" }}>
                Upload a PDF, DOCX or plain-text resume. It stays on your computer. Nothing is sent to the cloud.
              </p>
              {resumeState.has_resume ? (
                <div style={{ background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "12px", flexWrap: "wrap" }}>
                    <div>
                      <div style={{ fontSize: "13px", fontWeight: 600 }}>
                        <span style={{ color: t.good, marginRight: "6px", fontFamily: "'IBM Plex Mono', monospace" }}>✓ Saved</span>
                        {resumeState.metadata?.filename || "resume"}
                      </div>
                      <div style={{ fontSize: "11px", color: t.textDim, marginTop: "4px" }}>
                        {(resumeState.metadata?.char_count || 0).toLocaleString()} characters read from the file. You can replace it later in Settings.
                      </div>
                      {reparseBusy && (
                        <div style={{ fontSize: "11px", color: t.warn, marginTop: "6px" }}>
                          Reading it into a structured profile... this usually takes 20 to 40 seconds the first time.
                        </div>
                      )}
                      {!reparseBusy && resumeProfile && (
                        <div style={{ fontSize: "11px", color: t.good, marginTop: "6px" }}>
                          Profile ready: {resumeProfile.seniority || "-"} · {resumeProfile.years_experience || "-"} yrs · {(resumeProfile.technologies || []).slice(0, 4).join(", ") || "no tech terms picked up"}
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => { if (fileRef.current) fileRef.current.value = ""; fileRef.current?.click(); }}
                      style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, letterSpacing: "1px", background: "none", color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "6px 12px", cursor: "pointer" }}>
                      REPLACE
                    </button>
                  </div>
                </div>
              ) : (
                <button onClick={() => { if (fileRef.current) fileRef.current.value = ""; fileRef.current?.click(); }} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "12px", fontWeight: 600, letterSpacing: "1px", background: t.accent, color: "#fff", border: "none", borderRadius: "4px", padding: "10px 18px", cursor: "pointer" }}>
                  UPLOAD RESUME
                </button>
              )}
              <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md" onChange={(e) => { onUploadResume(e.target.files?.[0]); if (e.target) e.target.value = ""; }} style={{ display: "none" }} />
              <p style={{ fontSize: "11px", color: t.textFaint, marginTop: "10px" }}>
                If you skip this, you can paste your profile into Settings instead. Without either, there's nothing to score jobs against.
              </p>
            </div>
          )}

          {/* 3. ROLE KEYWORDS */}
          {step === 3 && (
            <div>
              <p style={{ fontSize: "14px", color: t.textMid, lineHeight: 1.6, marginBottom: "12px" }}>
                Which role titles count as a match? Comma-separated. Be broader than feels right; the scorer does the final filtering.
              </p>
              <textarea value={keywords} onChange={(e) => setKeywords(e.target.value)} rows={4}
                style={{ width: "100%", fontFamily: "'Outfit', sans-serif", fontSize: "14px", padding: "12px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }} />
              <p style={{ fontSize: "11px", color: t.textFaint, marginTop: "6px" }}>
                Matching is case-insensitive substring, so "product manager" catches "Senior Product Manager" too.
              </p>
            </div>
          )}

          {/* 4. EXPERIENCE - years of work and current seniority band */}
          {step === 4 && (
            <div>
              <p style={{ fontSize: "14px", color: t.textMid, lineHeight: 1.6, marginBottom: "14px" }}>
                These two numbers prevent roles that are way too senior (or way too junior) from cluttering your list. A mid-level PM shouldn't have to scroll past Director of Product roles, and a new grad shouldn't see "15 years required" listings.
              </p>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px" }}>
                <div>
                  <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Years of experience in your field</div>
                  <input type="number" min="0" max="50" step="1" value={yearsExperience}
                    onChange={(e) => setYearsExperience(parseInt(e.target.value, 10) || 0)}
                    style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }} />
                  <div style={{ fontSize: "10px", color: t.textFaint, marginTop: "4px" }}>
                    Total years doing this kind of work. Count internships or adjacent roles loosely; we only use this to gauge gap vs a role's stated requirement.
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Your current level</div>
                  <select value={currentLevel} onChange={(e) => setCurrentLevel(e.target.value)}
                    style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }}>
                    <option value="">— not sure / skip —</option>
                    <option value="intern">Intern</option>
                    <option value="entry">Entry level / Junior</option>
                    <option value="mid">Mid-level</option>
                    <option value="senior">Senior</option>
                    <option value="staff">Staff</option>
                    <option value="principal">Principal</option>
                    <option value="director">Director</option>
                    <option value="vp">VP</option>
                    <option value="cxo">C-level</option>
                  </select>
                  <div style={{ fontSize: "10px", color: t.textFaint, marginTop: "4px" }}>
                    Where you are today, not where you're aiming. We'll still show roles one level up as stretch targets.
                  </div>
                </div>
              </div>

              <div style={{ background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px", marginTop: "16px" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1px", color: t.textDim, fontWeight: 700, marginBottom: "6px" }}>
                  WHAT THIS DOES
                </div>
                <div style={{ fontSize: "12px", color: t.textMid, lineHeight: 1.6 }}>
                  <div style={{ marginBottom: "4px" }}>
                    <strong>Hard drop</strong> when a role is 3+ levels above yours, or wants 8+ more years than you have. Those aren't stretch applications; they're noise.
                  </div>
                  <div style={{ marginBottom: "4px" }}>
                    <strong>Gentle penalty</strong> when a role wants 3 to 7 more years than you have. Still shown, just ranked lower.
                  </div>
                  <div>
                    <strong>No penalty</strong> when you're within 2 years of the requirement, or when the role is at or below your level.
                  </div>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px", marginTop: "14px" }}>
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                    <div style={{ fontSize: "12px", color: t.textMid }}>How much the years gap affects ranking</div>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>{Math.round(yearsWeight * 100)}%</div>
                  </div>
                  <input type="range" min="0" max="0.10" step="0.01" value={yearsWeight}
                    onChange={(e) => setYearsWeight(parseFloat(e.target.value))}
                    style={{ width: "100%", accentColor: t.accent }} />
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint, marginTop: "2px" }}>
                    <span>0% - ignore</span><span>10% - penalise hard</span>
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "6px" }}>Director / VP trap-door</div>
                  <label style={{ fontSize: "13px", color: t.textMid, display: "flex", alignItems: "center", gap: "8px" }}>
                    <input type="checkbox" checked={trapdoorEnabled} onChange={(e) => setTrapdoorEnabled(e.target.checked)} />
                    Hide Director / VP roles when I have less than 10 years
                  </label>
                  <div style={{ fontSize: "10px", color: t.textFaint, marginTop: "4px" }}>
                    Belt-and-braces. These roles gate on org-level scope, not raw skill overlap. Uncheck only if you're senior and intentionally applying up.
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* 5. FILTERS - work mode + location + salary + cadence. Work
              mode is a multi-select now, not a single dropdown, so the
              user can say "remote and hybrid but not onsite" instead of
              being forced into one of three mutually-exclusive presets. */}
          {step === 5 && (
            <div>
              <p style={{ fontSize: "14px", color: t.textMid, lineHeight: 1.6, marginBottom: "14px" }}>
                Pick how you'd work, where, how often we scan, and what salary matters. Location rules hide jobs; salary only ranks them.
              </p>

              {/* Work mode: three checkboxes. All three on = no filter. */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "8px" }}>
                <div style={{ fontSize: "12px", color: t.textMid, fontWeight: 600 }}>Work mode (tick every mode you'd take)</div>
                <button type="button"
                  onClick={() => { setWorkModes(["remote", "hybrid", "onsite"]); setAllowedLocations(""); }}
                  style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1px", background: "none", border: `1px solid ${t.border}`, color: t.accent, padding: "3px 8px", borderRadius: "3px", cursor: "pointer", textTransform: "uppercase" }}>
                  Any location
                </button>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "6px", marginBottom: "6px" }}>
                {[
                  { id: "remote", label: "Remote",  desc: "Work from anywhere" },
                  { id: "hybrid", label: "Hybrid",  desc: "Mix of office + remote" },
                  { id: "onsite", label: "On-site", desc: "Fully in-office" },
                ].map(opt => {
                  const active = workModes.includes(opt.id);
                  return (
                    <button key={opt.id} type="button"
                      onClick={() => {
                        setWorkModes(prev => active ? prev.filter(m => m !== opt.id) : [...prev, opt.id]);
                      }}
                      style={{
                        textAlign: "left", cursor: "pointer",
                        background: active ? t.accentBg : t.bgAlt,
                        border: `1px solid ${active ? t.accent : t.border}`,
                        borderRadius: "4px", padding: "8px 10px",
                        color: active ? t.accent : t.textMid,
                      }}>
                      <div style={{ fontSize: "13px", fontWeight: 600 }}>
                        {active ? "\u2713 " : ""}{opt.label}
                      </div>
                      <div style={{ fontSize: "10px", color: active ? t.accent : t.textFaint, marginTop: "2px" }}>{opt.desc}</div>
                    </button>
                  );
                })}
              </div>
              <p style={{ fontSize: "11px", color: t.textFaint, margin: "0 0 14px", lineHeight: 1.5 }}>
                All three ticked = any work mode accepted (the broadest setting). Untick to drop jobs that don't match.
              </p>

              {/* Location allowlist. Only gates hybrid / onsite jobs;
                  remote jobs always pass because they have no location. */}
              <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Specific places (optional, comma-separated)</div>
              <input value={allowedLocations} onChange={(e) => setAllowedLocations(e.target.value)} placeholder="London, UK, Berlin"
                style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px", marginBottom: "4px" }} />
              <p style={{ fontSize: "11px", color: t.textFaint, margin: "0 0 18px", lineHeight: 1.5 }}>
                Leave blank for anywhere. Only applied to hybrid / on-site jobs; remote jobs always pass.
              </p>

              {/* Cycle frequency: soft lever on match volume. */}
              <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: "14px", marginBottom: "18px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                  <div style={{ fontSize: "12px", color: t.textMid, fontWeight: 600 }}>How often to scan for new jobs</div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>every {cycleInterval} min</div>
                </div>
                <input type="range" min="5" max="240" step="5" value={cycleInterval}
                  onChange={(e) => setCycleInterval(Number(e.target.value) || 30)}
                  style={{ width: "100%", accentColor: t.accent }} />
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint, marginTop: "2px" }}>
                  <span>5 min - aggressive</span><span>4 hours - quiet</span>
                </div>
                <p style={{ fontSize: "11px", color: t.textFaint, margin: "6px 0 0", lineHeight: 1.5 }}>
                  30 minutes is a good default. Below 15 rarely finds extra jobs (companies don't post that fast) and uses more LLM time.
                </p>
              </div>

              {/* Salary: soft ranker, never hides. */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Minimum salary (USD; 0 to ignore)</div>
                  <input type="number" min="0" step="5000" value={salaryFloor} onChange={(e) => setSalaryFloor(e.target.value)}
                    style={{ width: "100%", fontSize: "13px", padding: "8px 10px", background: t.bgAlt, color: t.text, border: `1px solid ${t.border}`, borderRadius: "4px" }} />
                </div>
                <div>
                  <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "4px" }}>Salary impact on ranking: <strong style={{ color: t.accent }}>{Math.round(salaryWeight * 100)}%</strong></div>
                  <input type="range" min="0" max="0.4" step="0.05" value={salaryWeight} onChange={(e) => setSalaryWeight(parseFloat(e.target.value))} style={{ width: "100%", accentColor: t.accent }} />
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint, marginTop: "2px" }}>
                    <span>0% - ignore</span><span>40% - strong pull</span>
                  </div>
                </div>
              </div>
              <p style={{ fontSize: "11px", color: t.textFaint, marginTop: "8px", lineHeight: 1.5 }}>
                Jobs at or above your minimum get a small rank boost. Below get a small penalty. Missing salary = tiny penalty, never dropped.
              </p>
            </div>
          )}

          {/* 6. MODELS - per-task effort picker with rationale */}
          {step === 6 && (
            <div>
              <p style={{ fontSize: "14px", color: t.textMid, lineHeight: 1.6, marginBottom: "10px" }}>
                Pick how much thinking each task gets. Bigger models produce better answers but need more memory and time. We've matched the defaults to your hardware.
              </p>

              {/* Hardware context: detected card + VRAM, or a VRAM picker
                  when we couldn't detect anything. Drives the tier
                  filtering below. */}
              <div style={{ background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "10px 14px", marginBottom: "14px" }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1px", color: t.textDim, fontWeight: 700, marginBottom: "6px" }}>
                  YOUR HARDWARE
                </div>
                {detected ? (
                  <div>
                    <div style={{ fontSize: "13px", color: t.text, fontWeight: 500 }}>
                      {detected.vendor} {detected.name} / {detected.vram_gb} GB VRAM
                    </div>
                    <div style={{ fontSize: "11px", color: t.textFaint, marginTop: "2px" }}>
                      Detected automatically via {detected.source}. Defaults below are sized to fit.
                    </div>
                  </div>
                ) : (
                  <div>
                    <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "8px" }}>
                      We couldn't detect a GPU. Pick the closest match so we can size models right:
                    </div>
                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                      {[
                        { label: "CPU only", gb: null },
                        { label: "4 GB",  gb: 4 },
                        { label: "8 GB",  gb: 8 },
                        { label: "12 GB", gb: 12 },
                        { label: "16 GB", gb: 16 },
                        { label: "24 GB+", gb: 24 },
                      ].map((opt) => {
                        const active = vramOverride === opt.gb;
                        return (
                          <button key={opt.label}
                            onClick={() => setVramOverride(opt.gb)}
                            style={{
                              fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600,
                              background: active ? t.accent : "none",
                              color: active ? "#fff" : t.textMid,
                              border: `1px solid ${active ? t.accent : t.border}`,
                              borderRadius: "4px", padding: "6px 10px", cursor: "pointer",
                            }}>
                            {opt.label}
                          </button>
                        );
                      })}
                    </div>
                    <div style={{ fontSize: "10px", color: t.textFaint, marginTop: "6px" }}>
                      {effectiveVram == null
                        ? "No pick yet; using safest defaults that work on any machine."
                        : `Using ${effectiveVram} GB as your GPU memory budget.`}
                    </div>
                  </div>
                )}
              </div>
              {effectiveVram == null && (
                <p style={{ fontSize: "11px", color: t.warn, marginBottom: "10px", lineHeight: 1.5 }}>
                  On CPU-only machines the pipeline works, but cycles are noticeably slower and the Deep tier is hidden. We also auto-downgrade the local embedding step to a lighter fallback.
                </p>
              )}
              <p style={{ fontSize: "12px", color: t.textFaint, marginBottom: "14px" }}>
                You can change any of these later in Settings.
              </p>
              {MODEL_TASKS.map((task) => {
                const allowed = tiersForVram(effectiveVram).filter((tier) => task.tiers.includes(tier));
                const currentDefault = defaultTierFor(task.id, effectiveVram);
                const selected = modelPicks[task.id];
                const selectedTierInfo = MODEL_TIERS[selected];
                const overVram = selectedTierInfo
                  && effectiveVram != null
                  && selectedTierInfo.minVramGb > effectiveVram;
                return (
                  <div key={task.id} style={{ border: `1px solid ${t.border}`, borderRadius: "4px", padding: "12px 14px", marginBottom: "10px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "4px" }}>
                      <div style={{ fontSize: "13px", fontWeight: 600, color: t.text }}>{task.title}</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", color: t.textDim }}>
                        default: {MODEL_TIERS[currentDefault].label}
                      </div>
                    </div>
                    <div style={{ fontSize: "12px", color: t.textMid, marginBottom: "8px" }}>{task.description}</div>
                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "6px" }}>
                      {task.tiers.map((tier) => {
                        const active = selected === tier;
                        const unsupported = !allowed.includes(tier);
                        const tierInfo = MODEL_TIERS[tier];
                        const title = unsupported
                          ? `Needs ${tierInfo.minVramGb} GB VRAM - above your detected budget, but you can still pick it. Cycles will be slow.`
                          : undefined;
                        return (
                          <button
                            key={tier}
                            title={title}
                            onClick={() => {
                              userTouchedTiers.current.add(task.id);
                              setModelPicks((p) => ({ ...p, [task.id]: tier }));
                            }}
                            style={{
                              fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600,
                              background: active ? t.accent : "none",
                              color: active ? "#fff" : (unsupported ? t.textFaint : t.textMid),
                              border: `1px ${unsupported && !active ? "dashed" : "solid"} ${active ? t.accent : t.border}`,
                              borderRadius: "4px", padding: "6px 10px", cursor: "pointer",
                            }}
                          >
                            {tierInfo.label} <span style={{ opacity: 0.75, fontWeight: 400 }}>({tierInfo.vram}, {tierInfo.speed})</span>
                            {unsupported && <span style={{ opacity: 0.75, fontWeight: 400 }}> · needs {tierInfo.minVramGb} GB</span>}
                          </button>
                        );
                      })}
                    </div>
                    {overVram && (
                      <div style={{ fontSize: "11px", color: t.warn, marginBottom: "6px", lineHeight: 1.4 }}>
                        Heads up: {selectedTierInfo.label} wants ~{selectedTierInfo.minVramGb} GB VRAM. Your machine shows {effectiveVram} GB. It will still run, just slowly - you may see swapping or CPU fallback.
                      </div>
                    )}
                    <div style={{ fontSize: "11px", color: t.textFaint, lineHeight: 1.5 }}>
                      <span style={{ color: t.textMid, fontWeight: 600 }}>Why this default: </span>{task.rationale}
                    </div>
                  </div>
                );
              })}
              <div style={{ borderTop: `1px dashed ${t.border}`, paddingTop: "14px", marginTop: "12px" }}>
                {/* MATCH THRESHOLD - plain-English version */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                  <div style={{ fontSize: "13px", fontWeight: 600 }}>How strict is a "match"?</div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>{Math.round(threshold * 100)}%</div>
                </div>
                <input type="range" min="0.4" max="0.9" step="0.05" value={threshold} onChange={(e) => setThreshold(parseFloat(e.target.value))} style={{ width: "100%", accentColor: t.accent }} />
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: t.textFaint, marginTop: "2px" }}>
                  <span>40% - show me more options</span><span>90% - only the strongest matches</span>
                </div>
                <p style={{ fontSize: "11px", color: t.textFaint, marginTop: "6px", lineHeight: 1.5 }}>
                  Think of this as a similarity score between each job and your profile. Around 55% is a friendly default: enough signal to filter out clear non-fits, loose enough that you still get a browsable list. Raise it once you have plenty of matches and want a tighter shortlist.
                </p>
                <ThresholdExplainer
                  threshold={threshold}
                  salaryFloor={salaryFloor}
                  yearsExp={yearsExperience}
                  salaryWeight={salaryWeight}
                  yearsWeight={yearsWeight}
                  matchModel={(preflight?.model_map && preflight.model_map.match) || "qwen3:14b"}
                  embedModel={preflight?.sentence_transformers_ready ? "all-MiniLM-L6-v2" : null}
                  theme={t}
                />

                {/* TOP-N DEEP ANALYSIS - plain English */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "16px", marginBottom: "4px" }}>
                  <div style={{ fontSize: "13px", fontWeight: 600 }}>How many top matches get the deep explanation?</div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", color: t.accent, fontWeight: 600 }}>{analyzeTopN}</div>
                </div>
                <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                  <input
                    type="range"
                    min="1"
                    max={TOP_N_MAX}
                    step="1"
                    value={analyzeTopN}
                    onChange={(e) => setAnalyzeTopN(Math.min(TOP_N_MAX, Math.max(1, parseInt(e.target.value, 10) || 1)))}
                    style={{ flex: 1, accentColor: t.accent }}
                  />
                  <input
                    type="number"
                    min="1"
                    max={TOP_N_MAX}
                    step="1"
                    value={analyzeTopN}
                    onChange={(e) => {
                      const v = parseInt(e.target.value, 10);
                      if (!isNaN(v)) setAnalyzeTopN(Math.min(TOP_N_MAX, Math.max(1, v)));
                    }}
                    style={{
                      width: "64px",
                      padding: "4px 6px",
                      fontSize: "12px",
                      fontFamily: "'IBM Plex Mono', monospace",
                      background: t.bg,
                      color: t.textMid,
                      border: `1px solid ${t.border}`,
                      borderRadius: "3px",
                    }}
                  />
                </div>
                <p style={{ fontSize: "11px", color: t.textFaint, marginTop: "6px", lineHeight: 1.5 }}>
                  Writing the "why it matches" summary takes real compute. We cap how many of your highest-scoring jobs get that treatment each cycle so the pipeline stays fast. The rest still get scored; they just skip the detailed write-up until you pin them.
                  {effectiveVram != null && (
                    <>{' '}Your hardware can comfortably do up to <strong>{topNCapFor(effectiveVram)}</strong> per cycle.</>
                  )}
                </p>
                {effectiveVram != null && analyzeTopN > topNCapFor(effectiveVram) && (
                  <div style={{
                    fontSize: "11px",
                    color: t.warn,
                    marginTop: "4px",
                    padding: "6px 8px",
                    background: t.bg,
                    border: `1px solid ${t.warn}`,
                    borderRadius: "3px",
                    lineHeight: 1.4,
                  }}>
                    Heads up: {analyzeTopN} is above the recommended {topNCapFor(effectiveVram)} for your hardware. Cycles will take longer and your GPU will stay busier. Not blocked - just flagging it.
                  </div>
                )}
              </div>
            </div>
          )}

          {/* 7. REVIEW - read-only summary with Edit buttons back to each step */}
          {step === 7 && (
            <div style={{ fontSize: "14px", lineHeight: 1.7, color: t.textMid }}>
              <p style={{ marginBottom: "6px" }}>Quick check before we kick off. Anything off? Click Edit to jump back.</p>
              <p style={{ fontSize: "11px", color: t.textFaint, marginBottom: "14px" }}>
                Empty fields below mean "leave it to the defaults" — Run Pipeline will still work.
              </p>

              {/* Render one row per logical section with an inline Edit button. */}
              {[
                {
                  key: "identity",
                  step: 0,
                  label: "You",
                  value: (
                    <>
                      {name || <span style={{ color: t.textFaint }}>no name</span>}
                      {" · "}
                      {currentRole || <span style={{ color: t.textFaint }}>no current role</span>}
                      {" · targeting "}
                      <strong>{targetLevel || "any"}</strong>
                    </>
                  ),
                },
                {
                  key: "resume",
                  step: 2,
                  label: "Resume",
                  value: resumeState?.has_resume
                    ? (<span style={{ color: t.good, fontWeight: 600 }}>✓ {resumeState.metadata?.filename || "uploaded"}</span>)
                    : (<span style={{ color: t.warn, fontWeight: 600 }}>Not yet uploaded — pipeline will have nothing to score against</span>),
                },
                {
                  key: "roles",
                  step: 3,
                  label: "Role keywords",
                  value: (
                    <>
                      {keywords.split(",").map((k) => k.trim()).filter(Boolean).slice(0, 5).join(", ") || <span style={{ color: t.textFaint }}>none</span>}
                    </>
                  ),
                },
                {
                  key: "experience",
                  step: 4,
                  label: "Experience",
                  value: (
                    <>
                      <strong>{yearsExperience || 0}</strong> years ·{" "}
                      <strong>{currentLevel || "level unset"}</strong>
                      {" · years-gap weight "}<strong>{Math.round(yearsWeight * 100)}%</strong>
                      {trapdoorEnabled ? " · Director/VP trap-door on" : " · trap-door off"}
                    </>
                  ),
                },
                {
                  key: "filters",
                  step: 5,
                  label: "Location & salary",
                  value: (
                    <>
                      Modes{" "}
                      <strong>
                        {workModes.length === 3 ? "any" : workModes.length === 0 ? "none" : workModes.join(", ")}
                      </strong>
                      {allowedLocations ? <>; only in <strong>{allowedLocations}</strong></> : ""}
                      {" · salary floor "}
                      <strong>{salaryFloor ? `$${Number(salaryFloor).toLocaleString()}` : "none"}</strong>
                      {" (weight "}<strong>{Math.round(salaryWeight * 100)}%</strong>{")"}
                      {" · cycle every "}<strong>{cycleInterval} min</strong>
                    </>
                  ),
                },
                {
                  key: "models",
                  step: 6,
                  label: "Models & ranking",
                  value: (
                    <>
                      Parse <strong>{MODEL_TIERS[modelPicks.parse]?.label}</strong>,
                      score <strong>{MODEL_TIERS[modelPicks.match]?.label}</strong>,
                      explain <strong>{MODEL_TIERS[modelPicks.analyze]?.label}</strong>,
                      chat <strong>{MODEL_TIERS[modelPicks.chat]?.label}</strong>
                      {" · strictness "}<strong>{Math.round(threshold * 100)}%</strong>
                      {" · deep top "}<strong>{analyzeTopN}</strong>
                    </>
                  ),
                },
                {
                  key: "hardware",
                  step: 6,
                  label: "Hardware",
                  value: detected
                    ? `${detected.vendor} ${detected.name} (${detected.vram_gb} GB VRAM)`
                    : effectiveVram != null
                      ? `Self-reported ${effectiveVram} GB`
                      : "CPU only (safe defaults chosen)",
                },
              ].map((row) => (
                <div key={row.key}
                  style={{
                    display: "flex", alignItems: "flex-start", gap: "10px",
                    padding: "8px 10px", borderBottom: `1px dashed ${t.border}`,
                  }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1px", color: t.textDim, fontWeight: 700, minWidth: "130px", paddingTop: "2px" }}>
                    {row.label.toUpperCase()}
                  </div>
                  <div style={{ fontSize: "12px", color: t.textMid, flex: 1, lineHeight: 1.5 }}>
                    {row.value}
                  </div>
                  <button onClick={() => setStep(row.step)}
                    style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", fontWeight: 600, letterSpacing: "1px", background: "none", color: t.textMid, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "4px 8px", cursor: "pointer" }}>
                    EDIT
                  </button>
                </div>
              ))}

              {prewarm && (prewarm.running || prewarm.ollama?.state === "running" || prewarm.embeddings?.state === "running") && (
                <div style={{ background: t.bgAlt, border: `1px dashed ${t.border}`, borderRadius: "4px", padding: "10px 12px", fontSize: "12px", color: t.textMid, marginTop: "14px" }}>
                  Models still warming up in the background. Click Finish now; the first cycle will wait for them.
                </div>
              )}
              {resumeState?.has_resume && reparseBusy && (
                <div style={{ background: t.bgAlt, border: `1px dashed ${t.border}`, borderRadius: "4px", padding: "10px 12px", fontSize: "12px", color: t.textMid, marginTop: "10px" }}>
                  Reading your resume into a structured profile. Click Finish now; this finishes in the background.
                </div>
              )}
              <p style={{ marginTop: "14px", fontSize: "12px" }}>
                Click Finish to unlock Run Pipeline. You can change any of these in Settings later; the wizard won't pop up again.
              </p>
            </div>
          )}
        </div>

        <div style={{ borderTop: `1px solid ${t.border}`, padding: "14px 24px", display: "flex", justifyContent: "space-between" }}>
          <button onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0}
            style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, background: "none", color: t.textDim, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "8px 14px", cursor: step === 0 ? "default" : "pointer", opacity: step === 0 ? 0.4 : 1 }}>
            BACK
          </button>
          {step < steps.length - 1 ? (
            <button onClick={() => setStep((s) => Math.min(steps.length - 1, s + 1))}
              style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, background: t.accent, color: "#fff", border: "none", borderRadius: "4px", padding: "8px 18px", cursor: "pointer" }}>
              NEXT
            </button>
          ) : (
            <button onClick={finish}
              style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "11px", fontWeight: 600, background: t.good, color: "#fff", border: "none", borderRadius: "4px", padding: "8px 18px", cursor: "pointer" }}>
              FINISH
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
