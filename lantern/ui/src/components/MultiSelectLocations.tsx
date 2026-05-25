import { useEffect, useMemo, useRef, useState } from "react";
import { Check, X, ChevronDown } from "lucide-react";
import {
  LOCATION_OPTIONS,
  CATEGORY_LABELS,
  type LocationOption,
} from "@/lib/locationOptions";

/**
 * Multi-select dropdown for location strings.
 *
 * Why this exists (vs. shadcn's Command + Popover combo)
 * ------------------------------------------------------
 * We don't have Radix Popover or Command primitives in the project,
 * and pulling them in just for the location picker felt like the
 * exact kind of accidental dependency growth that turned the
 * original pin-map into a maintenance burden. This component is
 * ~200 lines, no extra runtime deps, and does what the form needs:
 *   - chips for already-selected values, removable
 *   - a popover-ish dropdown with category headers + search
 *   - click-outside dismiss
 *   - keyboard escape dismiss
 *   - free-text "Add custom" path for substrings we don't list
 *
 * Wire format
 * -----------
 * Parent owns `value` as a string[] of substrings (lowercased). The
 * dropdown lets the user toggle entries from LOCATION_OPTIONS, but
 * they can also type a substring that isn't in the list and we'll
 * accept it — important for things like "Manchester UK" or "Tier 2
 * city" where the user has a very specific token to match against.
 */

interface Props {
  /** Currently selected substrings (lowercased). */
  value: string[];
  /** Called with the new selection array on every change. */
  onChange: (next: string[]) => void;
  /** Placeholder text when nothing is selected. */
  placeholder?: string;
  /** Visible label for the field — also drives aria-label. */
  ariaLabel?: string;
  /** Optional custom option list. Defaults to LOCATION_OPTIONS. */
  options?: LocationOption[];
}

export function MultiSelectLocations({
  value,
  onChange,
  placeholder = "Select locations…",
  ariaLabel = "Locations",
  options = LOCATION_OPTIONS,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Click-outside + Escape close the dropdown. Effect only attaches
  // while open so we're not paying for global listeners on every
  // form re-render.
  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Focus the search input when the dropdown opens so the user can
  // type immediately. Tiny UX win — saves a click.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const selectedSet = useMemo(() => new Set(value), [value]);

  // Match labels case-insensitively against both label + value so
  // the user can search for "SF" and find "San Francisco" via the
  // value "san francisco". A bit more permissive than label-only.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) => o.label.toLowerCase().includes(q) || o.value.includes(q),
    );
  }, [options, query]);

  // Group filtered options by category preserving the original
  // category order (so "US Metros" always appears above "US States",
  // not in alphabetic order from Object.entries which is insertion-
  // order in modern JS — luckily that's what we want).
  const grouped = useMemo(() => {
    const g: Partial<Record<string, LocationOption[]>> = {};
    for (const o of filtered) {
      const list = g[o.category] ?? [];
      list.push(o);
      g[o.category] = list;
    }
    return g;
  }, [filtered]);

  function toggle(v: string) {
    if (selectedSet.has(v)) onChange(value.filter((x) => x !== v));
    else onChange([...value, v]);
  }

  function remove(v: string) {
    onChange(value.filter((x) => x !== v));
  }

  // When the user types something that isn't in the option list and
  // hits Enter, we add the typed string as a custom substring. Lets
  // power users add "Manchester UK" / "Tier 2 city" without us
  // having to enumerate every possible substring.
  function addCustom() {
    const q = query.trim().toLowerCase();
    if (!q) return;
    if (selectedSet.has(q)) return; // already added — no-op
    onChange([...value, q]);
    setQuery("");
  }
  const queryMatchesExisting = options.some(
    (o) => o.value === query.trim().toLowerCase(),
  );

  // Resolve a substring to its option (for the chip label). Falls
  // back to the substring itself for custom entries.
  function labelFor(v: string): string {
    return options.find((o) => o.value === v)?.label ?? v;
  }

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger: chip strip + chevron. Acts like a combobox input —
          clicking anywhere on it (except a chip's X) opens the
          dropdown. Implemented as a div (not a button) because each
          selected chip contains its own remove button, and HTML
          forbids `<button>` nested inside `<button>` (browsers will
          silently re-parent the inner one and React will throw a
          hydration warning). The div + role="combobox" pattern is
          the standard accessible alternative — keyboard handlers
          below give it Enter/Space activation parity. */}
      <div
        role="combobox"
        tabIndex={0}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          // Enter / Space toggle the dropdown. Down-arrow opens it.
          // Escape is handled by the document-level listener while
          // the dropdown is open.
          if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
            // Don't intercept when the event came from a child
            // button (chip X). e.target !== currentTarget catches
            // that — the chip's X handler does its own stopPropagation.
            if (e.target === e.currentTarget) {
              e.preventDefault();
              setOpen(true);
            }
          }
        }}
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="w-full min-h-9 rounded-md border border-input bg-background px-2 py-1.5 flex flex-wrap gap-1.5 items-center cursor-pointer hover:border-accent/60 focus-within:border-accent focus:outline-none focus:ring-2 focus:ring-ring/40 transition-colors"
      >
        {value.length === 0 ? (
          <span className="text-sm text-muted-foreground">{placeholder}</span>
        ) : (
          value.map((v) => (
            <span
              key={v}
              className="inline-flex items-center gap-1 rounded bg-secondary px-2 py-0.5 text-xs"
            >
              {labelFor(v)}
              <button
                type="button"
                onClick={(e) => {
                  // Stop the click from bubbling to the trigger div
                  // (which would re-toggle the dropdown).
                  e.stopPropagation();
                  remove(v);
                }}
                className="hover:text-destructive opacity-60 hover:opacity-100"
                aria-label={`Remove ${labelFor(v)}`}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))
        )}
        <span className="ml-auto pl-1 text-muted-foreground">
          <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </div>

      {open && (
        <div className="absolute z-50 left-0 right-0 mt-1 rounded-md border bg-popover shadow-md max-h-80 overflow-hidden flex flex-col">
          {/* Search row — also doubles as a "type to add custom" input.
              Pressing Enter adds the typed string verbatim if it isn't
              already an option (handled by addCustom below). */}
          <div className="border-b">
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && query.trim() && !queryMatchesExisting) {
                  e.preventDefault();
                  addCustom();
                }
              }}
              placeholder="Search or type a custom substring…"
              className="w-full bg-transparent px-3 py-2 text-sm outline-none"
            />
          </div>

          <div className="overflow-auto">
            {/* No matches AND non-empty query → offer "Add as custom". */}
            {filtered.length === 0 && (
              <div className="px-3 py-3 text-xs space-y-2">
                <div className="text-muted-foreground">No preset matches "{query}".</div>
                {query.trim() && (
                  <button
                    type="button"
                    onClick={addCustom}
                    className="text-accent hover:underline"
                  >
                    + Add "{query.trim().toLowerCase()}" as custom substring
                  </button>
                )}
              </div>
            )}

            {/* Iterate categories in their defined order. The original
                CATEGORY_LABELS dict is keyed by the same category strings,
                so we use its keys to preserve display order. */}
            {(Object.keys(CATEGORY_LABELS) as (keyof typeof CATEGORY_LABELS)[]).map((cat) => {
              const opts = grouped[cat];
              if (!opts || opts.length === 0) return null;
              return (
                <div key={cat}>
                  <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-muted-foreground bg-muted/40">
                    {CATEGORY_LABELS[cat]}
                  </div>
                  {opts.map((o) => {
                    const checked = selectedSet.has(o.value);
                    return (
                      <button
                        key={o.value}
                        type="button"
                        onClick={() => toggle(o.value)}
                        className={`w-full text-left px-3 py-1.5 text-sm hover:bg-secondary flex items-center gap-2 ${checked ? "bg-accent/10" : ""}`}
                      >
                        <span
                          className={`inline-flex h-4 w-4 items-center justify-center rounded border ${
                            checked ? "bg-accent border-accent" : "border-input"
                          }`}
                        >
                          {checked && <Check className="h-3 w-3 text-accent-foreground" />}
                        </span>
                        <span className="flex-1">{o.label}</span>
                        <span className="text-[10px] font-mono text-muted-foreground/60">
                          {o.value}
                        </span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
