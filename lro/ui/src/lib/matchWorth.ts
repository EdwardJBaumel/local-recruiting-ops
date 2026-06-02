import type { MatchPayload } from "@/types/match";

/** Minimum calibrated display score (0-1) before Like is offered. */
export const LIKE_MIN_DISPLAY = 0.5;

export function matchDisplayScore(job: MatchPayload): number {
  return job._match_score_display ?? job._match_score ?? 0;
}

/** Strong enough fit to heart / feed the feedback learner. */
export function isWorthLike(job: MatchPayload): boolean {
  return matchDisplayScore(job) >= LIKE_MIN_DISPLAY;
}
