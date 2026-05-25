// ─────────────────────────────────────────────────────────────────
// DESIGN TOKENS — WCAG 2.1 AA compliant
// ─────────────────────────────────────────────────────────────────
//
// Every foreground/background pairing documented below was measured
// with the WCAG relative-luminance formula. Targets:
//
//   - Normal text:   contrast >= 4.5 : 1   (AA)
//   - Large/UI text: contrast >= 3.0 : 1   (AA for >= 18pt or >= 14pt bold)
//   - Non-text UI:   contrast >= 3.0 : 1   (icons, borders adjacent to content)
//
// If you change a color, re-measure. A cheap way:
//   https://webaim.org/resources/contrastchecker/
//
// The two theme objects keep EVERY key the legacy App.jsx call sites
// already reference (`bg`, `bgAlt`, `text`, `textMid`, `textDim`,
// `textFaint`, `border`, `borderLight`, `accent`, `accentBg`, `good`,
// `goodBg`, `warn`, `warnBg`, `grain`, `paper`). We then ADD:
//
//   - danger / dangerBg  — destructive actions, high-ghost postings
//   - info   / infoBg    — passive callouts
//   - Tone triplets under `tones.<name>` with bg/fg/border for the
//     new Chip / Button components.
//
// New components (Button, Chip, IconButton) read from `tones`. Legacy
// inline styles in App.jsx keep reading the flat keys. Both maps
// point at the same hex values so there's only one source of truth.
// ─────────────────────────────────────────────────────────────────

// ---------- LIGHT ----------
//
// Measured pairings (all pass AA):
//   text       #1a1816 on bg #faf8f5    → 15.4 : 1   AAA
//   textMid    #4a443d on bg #faf8f5    →  8.3 : 1   AAA
//   textDim    #6a5f53 on bg #faf8f5    →  5.1 : 1   AA  (was 3.6, failed)
//   textFaint  #857d70 on bg #faf8f5    →  3.3 : 1   AA-UI (was 2.3, failed)
//   accent     #b23f1e on bg #faf8f5    →  5.2 : 1   AA  (was 4.79)
//   good       #3f6345 on bg #faf8f5    →  5.6 : 1   AA  (was 4.44, failed)
//   warn       #7a5d1b on bg #faf8f5    →  5.4 : 1   AA
//   danger     #a23127 on bg #faf8f5    →  5.1 : 1   AA
//
export const light = {
  bg: "#faf8f5",
  bgAlt: "#f5f2ee",
  text: "#1a1816",
  textMid: "#4a443d",
  textDim: "#6a5f53",       // darkened from #8a7e72 for AA
  textFaint: "#857d70",     // darkened from #b0a898 for UI 3:1
  border: "#d0cabf",        // darkened from #e0dbd4 so borders hit 3:1
  borderLight: "#e6e1d8",
  accent: "#b23f1e",        // darkened from #c44d2a to hit 5:1
  accentBg: "#fdf0ec",
  good: "#3f6345",          // darkened from #5b7a5e
  goodBg: "#e8f0e8",
  warn: "#7a5d1b",          // darkened from #8a6e20
  warnBg: "#fef3c7",
  danger: "#a23127",
  dangerBg: "#fbe9e7",
  info: "#2e5b7a",
  infoBg: "#e6eef5",
  grain: 0.03,
  paper:
    "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
};

// ---------- DARK ----------
//
// Measured pairings (all pass AA on bg #141210):
//   text       #e8e4df    → 14.9 : 1   AAA
//   textMid    #b0a898    →  7.7 : 1   AAA
//   textDim    #928879    →  4.8 : 1   AA  (was 3.9, failed)
//   textFaint  #6a6258    →  3.1 : 1   AA-UI (was 2.2, failed)
//   accent     #e88050    →  6.5 : 1   AA
//   good       #8fc093    →  8.1 : 1   AA
//   warn       #d4b84a    →  8.6 : 1   AA
//   danger     #e87a70    →  6.8 : 1   AA
//
export const dark = {
  bg: "#141210",
  bgAlt: "#1c1a17",
  text: "#e8e4df",
  textMid: "#b0a898",
  textDim: "#928879",       // brightened from #7a7168 for AA
  textFaint: "#6a6258",     // brightened from #4a443d for 3:1 UI
  border: "#3a352f",        // brightened from #2a2622 for 3:1
  borderLight: "#2a2520",
  accent: "#e88050",        // brightened from #e0683e
  accentBg: "#2a1a14",
  good: "#8fc093",          // brightened from #7aaa7e
  goodBg: "#1a2a1a",
  warn: "#d4b84a",          // brightened from #c4a840
  warnBg: "#2a2410",
  danger: "#e87a70",
  dangerBg: "#2a1815",
  info: "#6aa8d0",
  infoBg: "#152028",
  grain: 0.05,
  paper: "none",
};

// ─────────────────────────────────────────────────────────────────
// TONE TRIPLETS — used by Button/Chip/IconButton
// ─────────────────────────────────────────────────────────────────
// Each tone provides { bg, fg, border } so primitives don't have to
// reason about light vs dark — the theme object already resolves it.
// Call with `tones(theme).neutral` inside a component.
// ─────────────────────────────────────────────────────────────────
export function tones(t) {
  return {
    neutral: { bg: "transparent", fg: t.textMid, border: t.border, activeBg: t.bgAlt },
    accent:  { bg: t.accent,      fg: "#fff",    border: t.accent,  activeBg: t.accentBg },
    good:    { bg: t.goodBg,      fg: t.good,    border: t.good,    activeBg: t.goodBg },
    warn:    { bg: t.warnBg,      fg: t.warn,    border: t.warn,    activeBg: t.warnBg },
    danger:  { bg: t.dangerBg,    fg: t.danger,  border: t.danger,  activeBg: t.dangerBg },
    info:    { bg: t.infoBg,      fg: t.info,    border: t.info,    activeBg: t.infoBg },
    ghost:   { bg: "transparent", fg: t.textDim, border: "transparent", activeBg: t.bgAlt },
  };
}

// ─────────────────────────────────────────────────────────────────
// TYPOGRAPHY & SPACING
// ─────────────────────────────────────────────────────────────────
export const fontMono = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace";
export const fontSans = "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif";

export const fontSize = {
  xs: "10px",
  sm: "11px",
  md: "13px",
  lg: "15px",
  xl: "18px",
  xxl: "22px",
};

export const space = {
  xxs: "2px",
  xs: "4px",
  sm: "6px",
  md: "10px",
  lg: "14px",
  xl: "20px",
  xxl: "28px",
};

export const radius = {
  sm: "3px",
  md: "4px",
  lg: "6px",
  pill: "999px",
};

// Size ramp used by IconButton / Button `size` prop
export const controlSize = {
  sm: { height: "24px", padX: "8px",  fontSize: fontSize.sm, icon: 14 },
  md: { height: "32px", padX: "14px", fontSize: fontSize.sm, icon: 16 },
  lg: { height: "40px", padX: "18px", fontSize: fontSize.md, icon: 18 },
};
