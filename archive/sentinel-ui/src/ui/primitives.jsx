// ─────────────────────────────────────────────────────────────────
// UI PRIMITIVES — the single source of truth for buttons, chips,
// icon-buttons, and inline SVG icons.
// ─────────────────────────────────────────────────────────────────
//
// Every control in Sentinel should route through one of these
// components. Inline-styled <button> / <span> tags scattered across
// App.jsx are the reason the app looks inconsistent; the long-term
// fix is to swap each one for <Button> / <Chip> / <IconButton>.
//
// Design goals:
//   - Consistent sizing: 24 / 32 / 40 px control heights
//   - WCAG AA contrast on every state (tokens.js enforces this)
//   - One `tone` axis: neutral | accent | good | warn | danger | info
//   - One `variant` axis: solid | outline | ghost
//   - Disabled and running states built in
//   - Icons are inline SVGs, 16px stroke 1.5, match the ✕ weight
//
// Call sites pass the active theme object (`t`) explicitly so these
// components don't need a context provider. That keeps them cheap
// to adopt incrementally — no wrapper component at the app root.
// ─────────────────────────────────────────────────────────────────

import React from "react";
import { tones, fontMono, controlSize, radius, space } from "./tokens";

// ─────────────────────────────────────────────────────────────────
// Icon — inline SVG set. Stroke width 1.5 at 16px matches the
// visual weight of the existing ✕ character, which is what prompted
// the design-system sweep in the first place. Every icon is a
// `currentColor` stroke so the parent sets the hue.
// ─────────────────────────────────────────────────────────────────
const ICON_PATHS = {
  // check mark
  check: <polyline points="3.5 8.5 6.5 11.5 12.5 4.5" fill="none" />,
  // close (X) — reference weight for everything else
  x: <g fill="none"><line x1="3.5" y1="3.5" x2="12.5" y2="12.5" /><line x1="12.5" y1="3.5" x2="3.5" y2="12.5" /></g>,
  // star outline (replaces 🤍 emoji — matches X weight)
  star: <path d="M8 1.5 l1.9 4.1 4.5 0.4 -3.4 3 1 4.4 -4 -2.4 -4 2.4 1 -4.4 -3.4 -3 4.5 -0.4 z" fill="none" />,
  // star solid (active state)
  starFilled: <path d="M8 1.5 l1.9 4.1 4.5 0.4 -3.4 3 1 4.4 -4 -2.4 -4 2.4 1 -4.4 -3.4 -3 4.5 -0.4 z" />,
  plus: <g fill="none"><line x1="8" y1="3" x2="8" y2="13" /><line x1="3" y1="8" x2="13" y2="8" /></g>,
  eye: <g fill="none"><path d="M1.5 8 C 3 4.5 5.5 3 8 3 S 13 4.5 14.5 8 C 13 11.5 10.5 13 8 13 S 3 11.5 1.5 8 Z" /><circle cx="8" cy="8" r="2.2" /></g>,
  trash: <g fill="none"><path d="M3 4 L13 4" /><path d="M5 4 L5 13 L11 13 L11 4" /><path d="M6.5 4 L6.5 2.5 L9.5 2.5 L9.5 4" /></g>,
  clock: <g fill="none"><circle cx="8" cy="8" r="6" /><polyline points="8 4.5 8 8 10.5 9.5" /></g>,
  external: <g fill="none"><polyline points="9 3 13 3 13 7" /><line x1="13" y1="3" x2="7" y2="9" /><path d="M11 9 L11 13 L3 13 L3 5 L7 5" /></g>,
  chevronDown: <polyline points="3.5 6 8 10.5 12.5 6" fill="none" />,
  filter: <g fill="none"><path d="M2 3 L14 3 L10 8 L10 13 L6 11 L6 8 Z" /></g>,
  play: <path d="M4 3 L4 13 L13 8 Z" fill="currentColor" stroke="none" />,
  warn: <g fill="none"><path d="M8 2 L14.5 13 L1.5 13 Z" /><line x1="8" y1="6" x2="8" y2="10" /><circle cx="8" cy="11.7" r="0.4" fill="currentColor" stroke="none" /></g>,
  info: <g fill="none"><circle cx="8" cy="8" r="6" /><line x1="8" y1="7" x2="8" y2="11" /><circle cx="8" cy="5" r="0.4" fill="currentColor" stroke="none" /></g>,
};

export function Icon({ name, size = 16, color = "currentColor", strokeWidth = 1.5, style }) {
  const paths = ICON_PATHS[name];
  if (!paths) return null;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      stroke={color}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
      aria-hidden="true"
    >
      {paths}
    </svg>
  );
}

// ─────────────────────────────────────────────────────────────────
// Button — the workhorse.
//
// Props:
//   t          (required) theme object from App
//   tone       "neutral" | "accent" | "good" | "warn" | "danger" | "info" | "ghost"
//   variant    "solid" | "outline"  (default "outline" for neutral, "solid" for others)
//   size       "sm" | "md" | "lg"   (default "md")
//   active     bool  — e.g. currently-running state, forces warn tone
//   disabled   bool
//   running    bool  — shows a spinner dot before children
//   iconLeft   icon name string
//   iconRight  icon name string
//   onClick
//   children
//   style      escape hatch — merged last
//
// The component is pure inline-style; no CSS file to import. This is
// deliberate — the rest of the app already does it, and there's no
// build-time CSS setup in Vite to hook into.
// ─────────────────────────────────────────────────────────────────
export function Button({
  t,
  tone = "neutral",
  variant,
  size = "md",
  active = false,
  disabled = false,
  running = false,
  iconLeft,
  iconRight,
  onClick,
  children,
  title,
  type = "button",
  style: overrideStyle,
  ...rest
}) {
  // Default variant per tone: neutral is outline, everything else solid.
  const vr = variant || (tone === "neutral" || tone === "ghost" ? "outline" : "solid");
  const sz = controlSize[size] || controlSize.md;
  const palette = tones(t);
  // When active we always paint with warn tone so "Running..." is obvious.
  const effectiveTone = active ? "warn" : tone;
  const pal = palette[effectiveTone] || palette.neutral;

  const base = {
    fontFamily: fontMono,
    fontSize: sz.fontSize,
    fontWeight: 600,
    letterSpacing: "1px",
    textTransform: "uppercase",
    height: sz.height,
    padding: `0 ${sz.padX}`,
    borderRadius: radius.md,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: space.sm,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    transition: "background 0.15s, border-color 0.15s, color 0.15s",
    whiteSpace: "nowrap",
    userSelect: "none",
    boxSizing: "border-box",
  };

  let style;
  if (disabled) {
    style = { ...base, background: t.bgAlt, color: t.textFaint, border: `1px solid ${t.border}` };
  } else if (vr === "solid") {
    style = { ...base, background: pal.bg, color: pal.fg, border: `1px solid ${pal.border}` };
  } else {
    // outline
    style = { ...base, background: "transparent", color: pal.fg, border: `1px solid ${pal.border}` };
  }

  if (overrideStyle) style = { ...style, ...overrideStyle };

  return (
    <button type={type} onClick={disabled ? undefined : onClick} title={title} style={style} {...rest}>
      {running && <RunningDot color={style.color} />}
      {iconLeft && !running && <Icon name={iconLeft} size={sz.icon} />}
      {children}
      {iconRight && <Icon name={iconRight} size={sz.icon} />}
    </button>
  );
}

function RunningDot({ color }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: color,
        animation: "sentinelPulse 1.2s infinite ease-in-out",
      }}
      aria-hidden="true"
    />
  );
}

// ─────────────────────────────────────────────────────────────────
// IconButton — 28px square, icon-only. Used for save/dismiss on
// match cards. Replaces the scattered `<button>🤍</button>` emoji
// buttons that don't align vertically with siblings.
// ─────────────────────────────────────────────────────────────────
export function IconButton({
  t,
  icon,
  tone = "neutral",
  active = false,
  disabled = false,
  onClick,
  title,
  size = 28,
  iconSize = 16,
  style: overrideStyle,
  ...rest
}) {
  const palette = tones(t);
  const pal = palette[tone] || palette.neutral;
  const style = {
    width: size,
    height: size,
    borderRadius: radius.md,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    background: active ? pal.activeBg : "transparent",
    color: active ? pal.fg : t.textMid,
    border: `1px solid ${active ? pal.border : t.border}`,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    transition: "background 0.15s, color 0.15s, border-color 0.15s",
    padding: 0,
    boxSizing: "border-box",
    ...overrideStyle,
  };
  return (
    <button type="button" onClick={disabled ? undefined : onClick} title={title} style={style} {...rest}>
      <Icon name={icon} size={iconSize} />
    </button>
  );
}

// ─────────────────────────────────────────────────────────────────
// Chip — compact label. Used for tags (location, team, posted,
// archetype, tech), filter pills, inline facts.
//
// Default tone is neutral (single muted look). `tone` escalates to
// good/warn/danger when there's signal to convey — e.g. a high-ghost
// posting gets a danger chip. Keeping the default neutral prevents
// the rainbow-tag effect the app had before.
// ─────────────────────────────────────────────────────────────────
export function Chip({
  t,
  tone = "neutral",
  active = false,
  onClick,
  icon,
  children,
  title,
  size = "sm",
  style: overrideStyle,
  ...rest
}) {
  const palette = tones(t);
  const pal = palette[tone] || palette.neutral;
  const isActiveNeutral = active && tone === "neutral";
  const bg = active ? pal.activeBg : (tone === "neutral" ? "transparent" : pal.bg);
  const fg = active || tone !== "neutral" ? pal.fg : t.textDim;
  const border = active || tone !== "neutral" ? pal.border : t.border;

  const pad = size === "sm" ? "3px 8px" : "5px 10px";
  const fs = size === "sm" ? "11px" : "12px";

  const style = {
    display: "inline-flex",
    alignItems: "center",
    gap: space.xs,
    fontFamily: fontMono,
    fontSize: fs,
    fontWeight: 500,
    letterSpacing: "0.5px",
    padding: pad,
    borderRadius: radius.sm,
    background: bg,
    color: fg,
    border: `1px solid ${border}`,
    cursor: onClick ? "pointer" : "default",
    userSelect: "none",
    whiteSpace: "nowrap",
    ...overrideStyle,
  };
  // Rendered as <span> when non-interactive so screen readers don't announce it as a button.
  const Tag = onClick ? "button" : "span";
  return (
    <Tag type={onClick ? "button" : undefined} onClick={onClick} title={title} style={style} {...rest}>
      {icon && <Icon name={icon} size={12} />}
      {children}
    </Tag>
  );
}

// ─────────────────────────────────────────────────────────────────
// Global keyframes for the running-dot pulse. Injected once at
// module load. Cheap — two class names, no library, no flicker.
// ─────────────────────────────────────────────────────────────────
if (typeof document !== "undefined" && !document.getElementById("sentinel-primitives-css")) {
  const style = document.createElement("style");
  style.id = "sentinel-primitives-css";
  style.textContent = `
    @keyframes sentinelPulse {
      0%, 100% { opacity: 0.4; transform: scale(0.8); }
      50%      { opacity: 1.0; transform: scale(1.0); }
    }
  `;
  document.head.appendChild(style);
}
