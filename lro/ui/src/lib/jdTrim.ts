/**
 * Job-description trimmer.
 *
 * Most ATS feeds (Greenhouse, Lever, Ashby, Workday) ship JDs as a
 * single HTML blob with everything — company boilerplate, mission
 * statement, benefits, EEO disclaimer, perks, etc. — concatenated
 * around the actual job content.
 *
 * The bits a candidate genuinely needs to read are three sections:
 *   1. What you'll do        (responsibilities)
 *   2. What you'll bring     (requirements / qualifications)
 *   3. Nice-to-haves         (preferred qualifications)
 *
 * Everything else — "About Stripe", "Our values", "Equal opportunity
 * employer", "Total rewards", "Why join us" — is filler from the
 * candidate's perspective. We strip it.
 *
 * Strategy: parse the HTML, scan for headings (h1-h4 + bold/strong
 * paragraphs) whose text matches our keyword list, and keep ONLY the
 * heading + everything that follows it up to the next heading. If we
 * can't find any recognisable section headers (rare — happens with
 * plain-text-with-no-structure JDs), we fall back to the original
 * description so the user never sees less than what was there before.
 *
 * This runs CLIENT-side only — DOMParser is a browser API. Server-side
 * the full description is preserved on disk; trimming is a viewing
 * preference, not destructive.
 */

// Phrases that mark the start of a section we want to KEEP. Matched
// case-insensitively against trimmed heading text. Order doesn't matter.
const KEEP_HEADINGS = [
  // Responsibilities
  "what you'll do",
  "what you will do",
  "what you’ll do",
  "responsibilities",
  "the role",
  "your role",
  "the job",
  "your day to day",
  "day-to-day",
  "in this role",
  "you will",
  "your impact",
  // Requirements / qualifications
  "what you'll bring",
  "what you will bring",
  "what you’ll bring",
  "requirements",
  "qualifications",
  "minimum qualifications",
  "basic qualifications",
  "required qualifications",
  "preferred qualifications",
  "nice to have",
  "nice-to-have",
  "nice to haves",
  "nice-to-haves",
  "what we're looking for",
  "what we are looking for",
  "what we’re looking for",
  "who you are",
  "about you",
  "you have",
  "skills and qualifications",
  "skills & qualifications",
  "experience",
];

// Element tags that count as "section headers" worth scanning. We
// include p+strong and div+strong because plenty of feeds (lever
// especially) use bold paragraphs as headings rather than real h-tags.
const HEADING_TAGS = new Set(["H1", "H2", "H3", "H4", "H5", "H6"]);

function isKeepHeadingText(text: string): boolean {
  const norm = text
    .trim()
    .toLowerCase()
    .replace(/[:：]+\s*$/, "") // strip trailing colons
    .replace(/\s+/g, " ");
  if (!norm) return false;
  return KEEP_HEADINGS.some((kw) => norm === kw || norm.startsWith(kw + " ") || norm.startsWith(kw + ":"));
}

/**
 * Heuristic: a <p> or <div> whose first child is <strong>/<b> AND that
 * bold text matches our heading keywords counts as a section header.
 * This catches the very common Greenhouse/Lever pattern of
 * `<p><strong>Responsibilities</strong></p>`.
 */
function isBoldParaHeading(el: Element): boolean {
  if (!(el.tagName === "P" || el.tagName === "DIV")) return false;
  const firstChild = el.firstElementChild;
  if (!firstChild) return false;
  if (firstChild.tagName !== "STRONG" && firstChild.tagName !== "B") return false;
  // The whole paragraph should basically just be the bold heading —
  // bail if there's substantial body text inline. Threshold of 3x the
  // bold length catches `<p><strong>Reqs</strong>: stuff</p>` (which
  // we still want to KEEP, since it has body) but NOT
  // `<p><strong>Reqs</strong></p>` (a true heading).
  const wholeText = (el.textContent ?? "").trim();
  const boldText = (firstChild.textContent ?? "").trim();
  if (!boldText) return false;
  if (wholeText.length > boldText.length * 1.4) return false;
  return isKeepHeadingText(boldText);
}

function isHeading(el: Element): boolean {
  if (HEADING_TAGS.has(el.tagName)) {
    return isKeepHeadingText(el.textContent ?? "");
  }
  return isBoldParaHeading(el);
}

/**
 * Walk the parsed body's children. For each element, decide if it's a
 * keep-section header. If yes, start a new "keep" run that captures
 * every following sibling until the next h1-h4 (regardless of whether
 * THAT heading matches — any major heading ends the current section).
 *
 * Returns the trimmed HTML, or empty string if nothing matched.
 */
export function trimJobDescription(html: string): string {
  if (typeof window === "undefined" || typeof DOMParser === "undefined") {
    return html;
  }
  if (!html || !html.trim()) return html;

  const doc = new DOMParser().parseFromString(html, "text/html");
  const root = doc.body;
  if (!root) return html;

  const children = Array.from(root.children);
  if (!children.length) return html;

  const keptChunks: string[] = [];
  let inKeptRun = false;

  for (const el of children) {
    const isMajorHeading = HEADING_TAGS.has(el.tagName);
    if (isHeading(el)) {
      // Start (or continue) a kept run. Always include the heading itself.
      inKeptRun = true;
      keptChunks.push(el.outerHTML);
      continue;
    }
    if (inKeptRun && isMajorHeading) {
      // A non-keep major heading ends the current run.
      inKeptRun = false;
      continue;
    }
    if (inKeptRun) {
      keptChunks.push(el.outerHTML);
    }
  }

  let trimmed = keptChunks.join("\n").trim();
  // If our heuristic found nothing, fall back to the original — better
  // to show too much than nothing at all.
  if (!trimmed) return html;

  // Hard length cap so the detail card is genuinely scannable. The
  // earlier 1800-char cap left enough copy for a full Greenhouse JD
  // section block — readable but you still had to scroll. 800 chars
  // is the sweet spot for "first read in 30 seconds": you see the
  // section heading + the first 3-5 bullets, which is almost always
  // enough signal to decide whether to "Show full description" for
  // depth or click Apply. The full blob is one click away via the
  // toggle.
  trimmed = capByVisibleLength(trimmed, 800);
  return trimmed;
}

/**
 * Cap an HTML fragment to roughly `maxChars` of VISIBLE text. Walks the
 * parsed tree, dropping or truncating nodes once the budget is spent.
 * The "show full description" toggle is what users hit when they want
 * the unedited blob, so this aggressive trim is fine here.
 */
function capByVisibleLength(html: string, maxChars: number): string {
  const doc = new DOMParser().parseFromString(`<div id="root">${html}</div>`, "text/html");
  const root = doc.getElementById("root");
  if (!root) return html;

  // Quick bail — already short enough.
  if ((root.textContent ?? "").length <= maxChars) return html;

  let used = 0;
  let truncated = false;

  // Walk top-level children in document order. Keep blocks until
  // we'd overshoot, then drop the rest. For the partial block at the
  // boundary, keep its first few list items if it's a <ul>/<ol>.
  const out: string[] = [];
  for (const child of Array.from(root.children)) {
    if (truncated) break;
    const childText = (child.textContent ?? "").length;
    if (used + childText <= maxChars) {
      out.push(child.outerHTML);
      used += childText;
      continue;
    }
    // Block would overshoot. If it's a list, keep the first N items
    // until we hit budget; otherwise just stop and tag-on a "..." note.
    if (child.tagName === "UL" || child.tagName === "OL") {
      const partial = doc.createElement(child.tagName.toLowerCase());
      for (const li of Array.from(child.children)) {
        const liLen = (li.textContent ?? "").length;
        if (used + liLen > maxChars) break;
        partial.appendChild(li.cloneNode(true));
        used += liLen;
      }
      if (partial.children.length > 0) {
        out.push(partial.outerHTML);
      }
    } else {
      // Headings / paragraphs / divs — keep the heading itself if it
      // looks like a section header so the user knows the cut point
      // wasn't mid-sentence-of-a-prose-paragraph.
      if (/^H[1-6]$/.test(child.tagName)) {
        out.push(child.outerHTML);
      }
    }
    truncated = true;
  }

  if (truncated) {
    out.push('<p class="text-xs italic opacity-60 mt-2">… more in full description</p>');
  }
  return out.join("\n");
}

/**
 * Was the original visibly trimmed? Used to render a "Show full
 * description" toggle so the user can always see the boilerplate if
 * they specifically want to.
 */
export function wasTrimmed(original: string, trimmed: string): boolean {
  return trimmed.length > 0 && trimmed.length < original.length;
}
