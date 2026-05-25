import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useResetHistory } from "@/hooks/useReset";
import { AlertTriangle, CheckCircle2, Trash2 } from "lucide-react";

/**
 * Danger zone — destructive actions that need a deliberate second
 * click to confirm. Two-click pattern instead of a modal so it stays
 * compact in the form. First click arms (button turns red, label
 * changes); second click within ARM_WINDOW_MS fires the reset.
 *
 * 10s arm window — the original 5s was too short once the description
 * grew enough that a careful reader would re-disarm before reaching
 * the second click. UX cost of doubling: minimal; UX cost of
 * being-too-short: "I clicked reset and nothing happened" reports.
 */
const ARM_WINDOW_MS = 10_000;

export function DangerSection() {
  const reset = useResetHistory();
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), ARM_WINDOW_MS);
    return () => clearTimeout(t);
  }, [armed]);

  const onClick = () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    reset.mutate();
    setArmed(false);
  };

  return (
    <Card className="border-destructive/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-destructive">
          <AlertTriangle className="h-5 w-5" />
          Danger zone
        </CardTitle>
        <CardDescription>
          Wipes per-cycle data so the next cycle starts from scratch — match registry (including
          your stars / dismisses), parsed jobs, market intel, digests, URL dedupe. Your resume,
          settings, and learned feedback survive.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button
          onClick={onClick}
          variant={armed ? "destructive" : "outline"}
          disabled={reset.isPending}
          // Make the armed state physically obvious — a thicker ring +
          // a subtle pulse so even a distracted user can't miss "this
          // is the dangerous click I'm about to make". The base
          // destructive variant alone is just red, which can blend in
          // on a dark theme.
          className={armed ? "ring-2 ring-destructive ring-offset-2 ring-offset-background animate-pulse" : ""}
        >
          <Trash2 className="h-4 w-4 mr-2" />
          {reset.isPending
            ? "Resetting…"
            : armed
              ? "Click again to confirm wipe"
              : "Reset cycle data"}
        </Button>

        {/* Armed-state hint with countdown context. Only one of armed
            / success / error is visible at a time. */}
        {armed && !reset.isPending && (
          <div className="text-sm text-destructive flex items-start gap-1.5">
            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
            <div>
              <strong>Armed.</strong> Click the red button again to wipe, or wait 10 seconds and
              the button auto-disarms.
            </div>
          </div>
        )}
        {reset.isSuccess && !reset.isPending && (
          <div className="rounded-md border border-ghost-clear/40 bg-ghost-clear/10 p-3 text-sm">
            <div className="flex items-start gap-1.5 text-ghost-clear">
              <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                <strong>History cleared.</strong>{" "}
                {(reset.data?.cleared?.length ?? 0) > 0 && (
                  <span className="opacity-80">
                    Removed: {(reset.data?.cleared ?? []).join(", ")}.
                  </span>
                )}
                <div className="text-foreground/80 mt-1">
                  Switch to the Matches tab — it should be empty. Click <strong>Run Pipeline</strong>{" "}
                  in the header to start a fresh cycle.
                </div>
              </div>
            </div>
          </div>
        )}
        {reset.isError && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm space-y-2">
            <div className="flex items-start gap-1.5 text-destructive">
              <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                <strong>Reset rejected.</strong> {reset.error.message}
              </div>
            </div>
            {/* Force-reset escape hatch. Surface this only after the
                first rejection so the safety guard still blocks
                accidental wipes during real cycles, but a stuck
                stage-label flag (orchestrator crashed mid-cycle and
                never reset progress to "idle") doesn't lock the user
                out forever. The backend's `force: true` flag bypasses
                the cycle guard. */}
            <div className="flex items-start gap-2 pl-1">
              <Button
                onClick={() => reset.mutate({ force: true })}
                size="sm"
                variant="destructive"
                disabled={reset.isPending}
              >
                <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                Force reset (override guard)
              </Button>
              <p className="text-[11px] text-foreground/70 leading-relaxed">
                Use this if no cycle is actually running but the guard keeps blocking — usually a
                previous cycle crashed and left a stale flag. Restarting the launcher would also
                fix it.
              </p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
