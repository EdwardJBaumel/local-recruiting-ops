import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { MatchPayload } from "@/types/match";

type Reaction = "up" | "down" | "star" | "dismiss" | "clear";

/**
 * Like / Pass / Star / Dismiss a match. Optimistic-update pattern:
 *
 *   1. We immediately patch the matches cache so the UI changes the
 *      moment the user clicks — no waiting on the round-trip.
 *   2. We POST to the backend.
 *   3. If the server rejects, we roll back to the prior cache state.
 *   4. On success we DON'T invalidate — the optimistic patch is the
 *      truth. Re-pulling /api/matches would risk overwriting an
 *      in-flight cycle's progress.
 *
 * This is the standard TanStack Query mutation pattern; once you've
 * read this file you know how every mutation in Lantern is structured.
 */
export function useReactToMatch() {
  const qc = useQueryClient();

  return useMutation<
    unknown,                                                  // server response (we don't care)
    Error,                                                    // error type
    { url: string; reaction: Reaction },                      // mutation argument
    { previous: MatchPayload[] | undefined }                  // rollback context
  >({
    // Backend canonical path is POST /api/reactions. The earlier
    // path "/decisions/react" was a typo with no server route — every
    // Like / Star / Pass click looked successful (the optimistic
    // patch fired) but the server got 404 and never persisted, so
    // the next page reload lost the user's stars and dismissals.
    // Caught during the dead-code audit.
    mutationFn: ({ url, reaction }) =>
      api.post("/reactions", { url, reaction }),

    onMutate: async ({ url, reaction }) => {
      // Pause any in-flight matches refetches so they can't clobber
      // our optimistic patch mid-mutation.
      await qc.cancelQueries({ queryKey: ["matches"] });

      // Snapshot for rollback.
      const previous = qc.getQueryData<MatchPayload[]>(["matches"]);

      qc.setQueryData<MatchPayload[]>(["matches"], (old) =>
        (old ?? []).map((m) =>
          m.url === url ? { ...m, ...applyReaction(m, reaction) } : m,
        ),
      );

      return { previous };
    },

    onError: (_err, _vars, ctx) => {
      // Roll back to the snapshot.
      if (ctx?.previous) qc.setQueryData(["matches"], ctx.previous);
    },
  });
}

/** Pure local mapping of reaction → flag patch on a match payload. */
function applyReaction(m: MatchPayload, r: Reaction): Partial<MatchPayload> {
  switch (r) {
    case "up":
      return { _starred: true, _seen: true };
    case "down":
      return { _dismissed: true, _seen: true };
    case "star":
      return { _starred: !m._starred, _seen: true };
    case "dismiss":
      return { _dismissed: !m._dismissed, _seen: true };
    case "clear":
      return { _starred: false, _dismissed: false };
  }
}
