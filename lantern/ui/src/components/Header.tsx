import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useStatus } from "@/hooks/useStatus";
import { useRunPipeline } from "@/hooks/useRunPipeline";
import { AlertTriangle, Loader2, Play } from "lucide-react";
import { LanternMark } from "@/components/LanternMark";

/**
 * App header — brand, live cycle status, Run Pipeline button, AND the
 * top-level nav tabs all in one bar. Splitting the tabs out into a
 * separate row below produced a "two disconnected bands" feel; the
 * eye expects a navbar to be a single chrome surface that owns both
 * branding and navigation.
 *
 * The TabsList here works because the parent App.tsx renders the
 * `<Tabs>` Radix root that wraps Header + main, so this list shares
 * the same context as the TabsContent panels in App.tsx.
 *
 * Brand mark: small lantern icon in an accent-tinted chip — ties the
 * wordmark to the orange palette without the old floating middot.
 */
export function Header() {
  const status = useStatus();
  const run = useRunPipeline();

  // Derive the cycle-active flag the same way the server does — true
  // if the server's flag is set OR the orchestrator's progress.stage
  // is non-idle. Belt-and-suspenders: works whether the auto-loop
  // (off by default) or the manual button kicked off the run.
  // Include isPending so the button stays "active" between mutation
  // success and the first post-click /api/status poll (was a 1-frame
  // flash back to "Run Pipeline").
  const inProgress = !!status.data?.cycle_in_progress || run.isPending;
  const stage = status.data?.progress?.stage_label;

  // Backend-unreachable banner. Fires when the heartbeat endpoint has
  // errored AND has never successfully loaded data — that's a
  // genuine "Python server isn't running" signal, not a transient
  // network blip. We don't want to flash the banner during a normal
  // restart where placeholderData still serves prior good data; the
  // !status.data guard does that.
  const backendDown = !!status.error && !status.data;

  return (
    <header className="border-b border-border/80 bg-background/85 backdrop-blur-md sticky top-0 z-50">
      {/* Backend-down banner. Sits above row 1 because it's a process-
          level problem ("Python server isn't running"), not a per-row
          one — banner-style placement signals "this is the thing
          blocking everything else." */}
      {backendDown && (
        <div className="bg-destructive/15 border-b border-destructive/30 text-destructive text-sm">
          <div className="container py-2 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>
              Backend not reachable on <span className="font-mono">localhost:8099</span>. Run
              <span className="font-mono mx-1">start.ps1</span>
              (or double-click <span className="font-mono">Start LANTERN.cmd</span>) to bring up the Python API.
            </span>
          </div>
        </div>
      )}
      <div className="container">
        {/* Row 1 — brand + live status + Run Pipeline. */}
        <div className="flex items-center justify-between py-3">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/10 ring-1 ring-accent/25 shadow-[0_0_20px_-8px_hsl(var(--accent)/0.8)]">
                <LanternMark className="h-[18px] w-[18px] text-accent" />
              </div>
              <div className="flex items-baseline gap-2">
                <h1 className="text-lg font-semibold tracking-tight">Lantern</h1>
                <span className="text-xs text-muted-foreground hidden sm:inline">
                  local-first job intelligence
                </span>
              </div>
            </div>

            {/* Live cycle status. Hidden when nothing's happening so
                the header stays calm at rest. */}
            {inProgress && (
              <Badge variant="secondary" className="ml-2 gap-2">
                <Loader2 className="h-3 w-3 animate-spin" />
                {stage ?? "Running"}
              </Badge>
            )}
          </div>

          <div className="flex items-center gap-2">
            {/* Surface mutation rejection inline. .error is set when
                the server returns ok:false (cycle already running,
                setup not complete) — explicit so the button doesn't
                look "stuck" after a click that the backend silently
                rejected. */}
            {run.isError && (
              <span className="text-sm text-destructive max-w-xs truncate" title={run.error.message}>
                {run.error.message}
              </span>
            )}

            <Button
              onClick={() => run.mutate()}
              disabled={inProgress || run.isPending}
              variant="accent"
              size="sm"
              title="Run all configured scrapers: ATS APIs (Greenhouse / Lever / Ashby / Workday) plus the bespoke public-feed sources (Amazon, Google, free job boards). ~3-5 min."
            >
              <Play className="h-4 w-4 mr-2" />
              {run.isPending ? "Starting..." : inProgress ? "Running..." : "Run Pipeline"}
            </Button>
          </div>
        </div>

        {/* Row 2 — tab nav. Underline-style triggers that visually
            attach to the header's bottom border via -mb-px. The
            override classes neutralise the default "pill on muted
            background" look from the base TabsList primitive — that
            style is fine for in-card tab groups (Settings sub-tabs,
            etc.) but wrong for top-level navigation, which should
            feel like part of the chrome. */}
        <TabsList className="h-auto bg-transparent p-0 gap-1 -mb-px rounded-none justify-start">
          <NavTab value="brief">Brief</NavTab>
          <NavTab value="matches">Matches</NavTab>
          <NavTab value="history">History</NavTab>
          <NavTab value="settings">Settings</NavTab>
        </TabsList>
      </div>
    </header>
  );
}

/**
 * Header-style tab trigger. Wraps the base TabsTrigger and overrides
 * the pill/shadow active state with an accent underline that bleeds
 * into the header's bottom border, giving the classic "the active tab
 * owns the page below" feel.
 */
function NavTab({ value, children }: { value: string; children: React.ReactNode }) {
  return (
    <TabsTrigger
      value={value}
      className="rounded-none border-b-2 border-transparent px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground data-[state=active]:bg-transparent data-[state=active]:border-accent data-[state=active]:text-foreground data-[state=active]:shadow-none transition-colors"
    >
      {children}
    </TabsTrigger>
  );
}
