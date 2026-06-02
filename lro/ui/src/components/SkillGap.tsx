import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useMatches } from "@/hooks/useMatches";
import { useMemo } from "react";

/**
 * Skill gap card — two parallel columns showing where the user's
 * profile reliably matches vs where it consistently falls short.
 *
 * Where the data comes from
 * -------------------------
 * Each match payload carries a `_fit_gap` block populated by the
 * ANALYZE stage. The shape is `{ matched: string[], gaps: string[] }`.
 *   - `matched`  — skills/topics the resume covered for that role.
 *   - `gaps`     — skills/topics the role wanted but the resume lacked.
 *
 * Aggregating those two arrays across every visible match produces
 * the most-frequent moats (consistent strengths) and the most-frequent
 * gaps (most actionable weak spots). It's the most directly usable
 * career-feedback signal in the whole app — "across 60 roles, you
 * matched 'roadmap leadership' 48 times and missed 'SQL' 41 times".
 *
 * Why no chart
 * ------------
 * A heat map for skill data ends up either too sparse (many skills,
 * few rows) or too crowded. A side-by-side ranked list is denser, more
 * scannable, and reads like a recruiter's notebook — which is the
 * audience for this card.
 *
 * Normalisation
 * -------------
 * The same skill arrives in many surface forms ("postgres", "Postgres",
 * "PostgreSQL", "postgresql") because each role's analyzer produces its
 * own casing. We lowercase + trim and dedupe inside each match before
 * counting, so a match that lists both "Postgres" and "PostgreSQL"
 * doesn't double-count toward the global tally.
 */

interface RankedSkill {
  name: string;
  count: number;
}

const TOP_N = 8;

function aggregate(matches: { _fit_gap?: { matched?: string[]; gaps?: string[] } }[], key: "matched" | "gaps"): RankedSkill[] {
  const counts = new Map<string, { display: string; n: number }>();
  for (const m of matches) {
    const raw = m._fit_gap?.[key] ?? [];
    // Per-match dedupe: same skill mentioned twice in one fit_gap
    // shouldn't get double-credited.
    const seen = new Set<string>();
    for (const skill of raw) {
      const normalized = skill.trim().toLowerCase();
      if (!normalized || seen.has(normalized)) continue;
      seen.add(normalized);
      const slot = counts.get(normalized);
      if (slot) {
        slot.n += 1;
      } else {
        // Preserve the first-seen casing so the list reads like the
        // analyzer wrote it, not all-lowercase.
        counts.set(normalized, { display: skill.trim(), n: 1 });
      }
    }
  }
  return Array.from(counts.values())
    .map((v) => ({ name: v.display, count: v.n }))
    .sort((a, b) => b.count - a.count)
    .slice(0, TOP_N);
}

export function SkillGap() {
  const matches = useMatches();

  const { moats, gaps, sample } = useMemo(() => {
    const visible = (matches.data ?? []).filter((m) => !m._removed && !m._dismissed);
    return {
      moats: aggregate(visible, "matched"),
      gaps: aggregate(visible, "gaps"),
      sample: visible.length,
    };
  }, [matches.data]);

  const empty = moats.length === 0 && gaps.length === 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Skill moats vs skill gaps</CardTitle>
        <CardDescription>
          {sample > 0
            ? `Aggregated across ${sample} match${sample === 1 ? "" : "es"} — what your resume keeps hitting and where it keeps falling short.`
            : "Aggregated across the match registry."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {empty ? (
          <p className="text-sm text-muted-foreground py-6 text-center">
            Not enough analysed matches yet — fit-gap data appears once roles reach the ANALYZE stage.
          </p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <SkillColumn title="Moats — most-matched" items={moats} tone="moat" />
            <SkillColumn title="Gaps — most-missed" items={gaps} tone="gap" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SkillColumn({ title, items, tone }: { title: string; items: RankedSkill[]; tone: "moat" | "gap" }) {
  // The dot is the only colour difference — green for moats (good
  // news), red for gaps (action item). Bar length is `count / max`
  // for the column so the longest item fills the row.
  const max = items[0]?.count ?? 1;
  const dotClass = tone === "moat" ? "bg-emerald-500" : "bg-red-500";
  const barClass = tone === "moat" ? "bg-emerald-500/30" : "bg-red-500/30";
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-muted-foreground mb-3">{title}</div>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">— None recorded yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((s) => {
            const pct = Math.max(8, Math.round((s.count / max) * 100));
            return (
              <li key={s.name} className="flex items-center gap-2">
                <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${dotClass}`} />
                <span className="text-sm flex-1 truncate" title={s.name}>{s.name}</span>
                <div className="relative w-24 h-2 rounded bg-secondary overflow-hidden shrink-0">
                  <div className={`absolute inset-y-0 left-0 ${barClass}`} style={{ width: `${pct}%` }} />
                </div>
                <span className="text-xs font-mono tabular-nums text-muted-foreground w-6 text-right shrink-0">
                  {s.count}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
