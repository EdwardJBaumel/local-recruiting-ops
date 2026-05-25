import { Badge } from "@/components/ui/badge";

/**
 * Ghost-state pill. Three states map to three semantic colors:
 *
 *   clear    (green, score < 0.30)  — likely a real, fresh role
 *   caution  (orange, 0.30 ≤ s < 0.45) — some ghost signals fired,
 *            worth a second look, but not damning
 *   suspect  (red, score >= 0.45) — multiple ghost signals fired,
 *            treat as likely placeholder
 *
 * Why "Caution" and not "Aging" for the middle tier: the ghost score
 * is a sum across nine heuristics (post age, vague location, missing
 * salary band, buzzword density, etc). A freshly-posted job with mildly
 * generic language can score 30+ from the non-age signals alone — calling
 * that "Aging" makes a temporal claim the score doesn't actually support.
 * "Caution" describes what the user should DO (look closer) without
 * lying about why.
 *
 * Thresholds match the backend defaults in core/preferences.py
 * (`ghost_warn_threshold` = 0.30, `ghost_flag_threshold` = 0.45). If
 * the user tunes those in Settings, the labels here re-classify
 * automatically because the source of truth is the score itself, not
 * a prebaked enum.
 */
interface Props {
  score: number;
  warnAt?: number;
  flagAt?: number;
}

export function GhostBadge({ score, warnAt = 0.30, flagAt = 0.45 }: Props) {
  // Native title attribute = browser-default tooltip on hover. Cheap,
  // reliable, no extra deps. The text below is what teaches the user
  // what each tier actually means.
  if (score >= flagAt) {
    return (
      <Badge
        variant="suspect"
        title={`Suspect · ${Math.round(score * 100)}/100 ghost score. Multiple stale-listing signals fired (e.g. posted >60 days ago, vague location, missing apply link). Treat as likely a placeholder posting, not a real opening.`}
      >
        Suspect · {Math.round(score * 100)}
      </Badge>
    );
  }
  if (score >= warnAt) {
    return (
      <Badge
        variant="aging"
        title={`Caution · ${Math.round(score * 100)}/100 ghost score. Some signals fired (vague language, missing salary, buzzword density, etc). Worth a second look but not damning — could just be a generically-written real posting.`}
      >
        Caution · {Math.round(score * 100)}
      </Badge>
    );
  }
  return (
    <Badge
      variant="clear"
      title={`Clear · ${Math.round(score * 100)}/100 ghost score. None of the nine ghost signals fired strongly. Likely a real, fresh posting.`}
    >
      Clear · {Math.round(score * 100)}
    </Badge>
  );
}
