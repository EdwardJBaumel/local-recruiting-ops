import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Cpu, AlertTriangle, RefreshCw, Loader2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { type Control, Controller } from "react-hook-form";
import type { SettingsFormShape } from "@/views/Settings";
import { useOllamaModels } from "@/hooks/useOllamaModels";
import { useSystemInfo, type SystemInfoResponse } from "@/hooks/useSystemInfo";

/**
 * Models section — pick which local Ollama model handles each task.
 *
 * Why this lives in Settings instead of being a fixed config:
 *   - Hardware varies. A 16 GB GPU runs qwen3:30b-a3b for cover letters
 *     comfortably; an 8 GB GPU should drop to qwen3:8b. Hand-coding
 *     defaults that work everywhere isn't possible.
 *   - Model availability varies. Users `ollama pull` what they want;
 *     the app should adapt to what's actually installed, not assume.
 *   - Switching models for an A/B is a one-click thing that shouldn't
 *     require editing config.json by hand.
 *
 * The dropdown values come from /api/ollama-models which queries Ollama's
 * /api/tags endpoint. If Ollama is down we render a destructive banner
 * with the fix instead of a blank dropdown.
 */
interface Props {
  control: Control<SettingsFormShape>;
}

/**
 * Per-task config. Each `suggested[0]` is the recommended pick for a
 * 16 GB GPU; subsequent entries are graceful fallbacks for smaller
 * hardware. We always populate the dropdown with the suggested set
 * even when those models aren't installed locally — otherwise a user
 * with a bare Ollama install sees an empty dropdown and has no way
 * to make a pick. Items not currently installed get a "(not
 * installed)" tag so the user knows they need to `ollama pull <X>`
 * before the next pipeline cycle.
 */
const TASKS: {
  field: keyof SettingsFormShape;
  label: string;
  hint: string;
  suggested: string[];
}[] = [
  {
    field: "model_parse",
    label: "Parse",
    hint: "HTML → JSON extraction. Runs on every job card. 8B is plenty for mechanical extraction; the 14B variant doesn't move accuracy.",
    suggested: ["qwen3:8b", "qwen3:14b", "qwen3:4b", "llama3.2:3b"],
  },
  {
    field: "model_match",
    label: "Match (LLM fallback)",
    hint: "Only used when sentence-transformers isn't installed. The default embedding path is faster + deterministic.",
    suggested: ["qwen3:14b", "qwen3:8b"],
  },
  {
    field: "model_analyze",
    label: "Analyze",
    hint: "Fit/gap rationale on the top-N matches. qwen3:14b is the recommended default — strong reasoning at the 14B size class and shares VRAM resident with digest + cover letter (no swap). gemma3:12b is a fine prose-flavored alternative.",
    suggested: ["qwen3:14b", "gemma3:12b", "qwen3:8b", "phi4-reasoning:14b"],
  },
  {
    field: "model_digest",
    label: "Digest",
    hint: "Short prose summarising a cycle's market intel. Reuse the analyze model so Ollama keeps it warm.",
    suggested: ["qwen3:14b", "gemma3:12b", "qwen3:8b", "llama3.2:3b"],
  },
  {
    field: "model_cover_letter",
    label: "Cover letter",
    hint: "Tailored cover letters. qwen3:14b fits entirely on a 16 GB GPU and produces high-quality 3-4 paragraph letters. qwen3:30b-a3b is the MoE quality ceiling but spills to CPU on consumer cards (~2x slower).",
    suggested: ["qwen3:14b", "qwen3:30b-a3b", "gemma3:12b", "qwen3:8b"],
  },
];

/** Merge installed + suggested into a deduped, ordered list for the dropdown. */
function buildDropdownOptions(suggested: string[], installed: string[], current: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  // Current saved value first (so it's selected and visible even if
  // it's neither installed nor suggested — e.g. a hand-edited config).
  if (current && !seen.has(current)) {
    out.push(current);
    seen.add(current);
  }
  // Then suggested picks for this task.
  for (const m of suggested) {
    if (!seen.has(m)) {
      out.push(m);
      seen.add(m);
    }
  }
  // Then anything else the user has installed.
  for (const m of installed) {
    if (!seen.has(m)) {
      out.push(m);
      seen.add(m);
    }
  }
  return out;
}

export function ModelsSection({ control }: Props) {
  const models = useOllamaModels();
  const system = useSystemInfo();
  const qc = useQueryClient();
  const installed = models.data?.models ?? [];
  const ollamaDown = models.data && !models.data.ok;
  // Three distinct states for the dropdowns:
  //   1. models.isPending → still fetching; treat as "loading" not "missing"
  //   2. ollamaDown       → reachable but errored; show banner
  //   3. data present     → normal render
  // Without this distinction the dropdowns label every preset "(not
  // installed)" during the ~10-20s API boot window, which is what
  // surfaced as the "everything says not installed" bug.
  const loading = models.isPending;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cpu className="h-5 w-5" />
          Models
        </CardTitle>
        <CardDescription>
          Which local Ollama model runs each pipeline stage. Recommended picks below are sized for your
          hardware — see the system summary. Change any to a smaller variant if you want to free VRAM, or
          install more with <code className="font-mono text-xs">ollama pull &lt;name&gt;</code> and refresh.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* System summary — your GPU / VRAM / torch state. Banner reads
            different colors depending on whether the box is GPU-ready. */}
        <SystemBanner system={system.data} loading={system.isPending} />

        {ollamaDown && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <div className="font-medium">Ollama not reachable on {models.data?.host}</div>
              <div className="text-xs opacity-90">
                Start it with <code className="font-mono">ollama serve</code> (or install from{" "}
                <a className="underline" href="https://ollama.com/download" target="_blank" rel="noreferrer">
                  ollama.com/download
                </a>
                ), then refresh.
              </div>
              {models.data?.error && (
                <div className="text-[10px] font-mono opacity-70 truncate">{models.data.error}</div>
              )}
            </div>
            <button
              type="button"
              onClick={() => qc.invalidateQueries({ queryKey: ["ollama-models"] })}
              className="shrink-0 inline-flex items-center gap-1 text-xs underline"
              title="Re-check Ollama"
            >
              <RefreshCw className="h-3 w-3" />
              retry
            </button>
          </div>
        )}

        {loading && !ollamaDown && (
          <div className="text-xs text-muted-foreground flex items-center gap-2">
            <Loader2 className="h-3 w-3 animate-spin" />
            Checking which models are installed…
          </div>
        )}

        {!loading && !ollamaDown && installed.length > 0 && (
          <div className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground/80">{installed.length}</span> models installed
            locally on{" "}
            <code className="font-mono text-[10px]">{models.data?.host}</code>.
          </div>
        )}

        {TASKS.map((task) => (
          <Controller
            key={task.field}
            control={control}
            name={task.field}
            render={({ field }) => (
              <ModelRow
                label={task.label}
                hint={task.hint}
                value={(field.value as string) ?? ""}
                onChange={field.onChange}
                installed={installed}
                suggested={task.suggested}
                ollamaDown={ollamaDown ?? false}
                loading={loading}
              />
            )}
          />
        ))}

        {/* Quick-install hints. Lists the union of all suggested models
            that aren't currently installed, with copy-friendly ollama
            pull commands. Hidden while the installed list is still
            loading (would falsely claim every preset is missing). */}
        {!loading && <UninstalledHint installed={installed} />}
      </CardContent>
    </Card>
  );
}

/**
 * Small banner at the top of the Models card summarising the user's
 * hardware so the recommendations beneath have context. Three modes:
 *
 *   - loading:     spinner while /api/system-info resolves
 *   - GPU + CUDA:  green badge with GPU name + VRAM + torch version.
 *                  Calls out which class of model their VRAM
 *                  comfortably fits (a 16 GB card can hold a 14B model
 *                  resident with headroom; an 8 GB card should stick
 *                  to 8B variants).
 *   - GPU without CUDA torch: yellow warning + the install command
 *   - no GPU:      gray informational note that CPU mode is functional
 *                  but slower. Sets expectations.
 */
function SystemBanner({ system, loading }: { system: SystemInfoResponse | undefined; loading: boolean }) {
  if (loading || !system) {
    return (
      <div className="rounded-md border border-border/60 bg-secondary/20 px-3 py-2 text-xs flex items-center gap-2">
        <Loader2 className="h-3 w-3 animate-spin" />
        Detecting hardware…
      </div>
    );
  }
  const { gpu, torch } = system;
  const cudaOK = !!torch.available;
  const hasGpuOnSystem = !!gpu && !gpu.error;

  if (hasGpuOnSystem && cudaOK) {
    const vram = gpu!.vram_gb;
    let fitsHint = "";
    if (vram != null) {
      if (vram >= 14) fitsHint = "14B-class models fit comfortably (qwen3:14b, gemma3:12b).";
      else if (vram >= 10) fitsHint = "12B-class models fit. 14B works but may swap.";
      else if (vram >= 7) fitsHint = "8B-class is the sweet spot for your VRAM.";
      else fitsHint = "Limited VRAM — use 3-4B variants (qwen3:4b, llama3.2:3b).";
    }
    return (
      <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs space-y-1">
        <div className="font-medium text-foreground/90">
          GPU: {gpu!.name}
          {vram != null && <span className="text-muted-foreground"> · {vram} GB VRAM</span>}
        </div>
        <div className="text-muted-foreground">
          torch {torch.version} (CUDA {torch.cuda}) — embeddings + Ollama run on GPU.
        </div>
        {fitsHint && <div className="text-muted-foreground italic">{fitsHint}</div>}
      </div>
    );
  }
  if (hasGpuOnSystem && !cudaOK) {
    return (
      <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs space-y-1">
        <div className="font-medium text-amber-200/90 flex items-center gap-1">
          <AlertTriangle className="h-3.5 w-3.5" />
          GPU detected but PyTorch is CPU-only — match phase will be 50-100× slower
        </div>
        <div className="text-muted-foreground">
          GPU: {gpu!.name}
          {gpu!.vram_gb != null && <span> · {gpu!.vram_gb} GB</span>}
          {" · "}
          torch {torch.version ?? "?"} (no CUDA build)
        </div>
        <div className="text-foreground/80">
          To fix on Windows:{" "}
          <code className="font-mono text-[11px]">
            pip uninstall -y torch && pip install torch --index-url
            https://download.pytorch.org/whl/cu128
          </code>
        </div>
        <div className="text-muted-foreground">Then restart the launcher.</div>
      </div>
    );
  }
  // No GPU detected — CPU mode.
  return (
    <div className="rounded-md border border-border/60 bg-secondary/20 px-3 py-2 text-xs space-y-1">
      <div className="font-medium text-foreground/80">CPU mode (no NVIDIA GPU detected)</div>
      <div className="text-muted-foreground">
        Cycles run end-to-end on CPU. Functional but slower — match phase ~30-90 min instead of ~1 min on
        GPU. Pick 8B-class models below to keep things reasonable.
      </div>
      <div className="text-muted-foreground italic">
        torch {torch.version ?? "(not installed)"}{torch.cuda ? `, CUDA ${torch.cuda}` : ""}
      </div>
    </div>
  );
}

/**
 * Lists `ollama pull` commands for any suggested model the user hasn't
 * installed yet. Hidden when everything's already installed.
 */
function UninstalledHint({ installed }: { installed: string[] }) {
  const installedSet = new Set(installed);
  const allSuggested = Array.from(
    new Set(TASKS.flatMap((t) => t.suggested)),
  );
  const missing = allSuggested.filter((m) => !installedSet.has(m));
  if (missing.length === 0) return null;
  return (
    <div className="rounded-md border border-border/60 bg-secondary/20 px-3 py-2.5 text-xs space-y-1.5">
      <div className="font-medium text-foreground/80">
        Suggested models not yet installed
      </div>
      <div className="text-muted-foreground">
        Pulling these unlocks the dropdowns above and lets each task use
        the right-sized model. Each pull is a one-time download (~5–17 GB).
      </div>
      <pre className="font-mono text-[11px] text-foreground/90 whitespace-pre-wrap leading-relaxed">
        {missing.map((m) => `ollama pull ${m}`).join("\n")}
      </pre>
    </div>
  );
}

/**
 * One model picker row. Dropdown shows the union of: the saved value
 * (so an out-of-band config edit isn't silently overridden), the task's
 * suggested picks (so a user with a bare Ollama install still has
 * something to choose), then anything else they have installed.
 * Each option is tagged "(not installed)" when it isn't on this
 * machine yet — picking it is fine, the next pipeline cycle will fail
 * politely until `ollama pull <name>` runs.
 */
function ModelRow({
  label,
  hint,
  value,
  onChange,
  installed,
  suggested,
  ollamaDown,
  loading,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  installed: string[];
  suggested: string[];
  ollamaDown: boolean;
  loading: boolean;
}) {
  const installedSet = new Set(installed);
  const options = buildDropdownOptions(suggested, installed, value);
  // While the installed-list query is still resolving, every preset
  // would falsely look "(not installed)" because `installed = []`.
  // Suppress the tag during loading. Same goes for the inline "(not
  // installed)" suffix inside the dropdown options.
  const currentInstalled = loading || !value || installedSet.has(value);
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-sm font-medium">
          {label}
          {!currentInstalled && value && (
            <span className="ml-2 text-[10px] text-amber-500 font-normal" title="Pull this model with: ollama pull ...">
              (not installed)
            </span>
          )}
        </div>
        <div className="text-xs font-mono text-muted-foreground tabular-nums truncate max-w-[60%]" title={value}>
          {value || "(unset)"}
        </div>
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">{hint}</p>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full h-9 rounded-md border border-input bg-background px-2 text-sm font-mono"
      >
        {!value && <option value="">— pick a model —</option>}
        {options.map((m) => (
          <option key={m} value={m}>
            {m}
            {!loading && !installedSet.has(m) ? "  (not installed)" : ""}
          </option>
        ))}
        {ollamaDown && installed.length === 0 && (
          <option value="" disabled>
            ──── start Ollama to see what's installed ────
          </option>
        )}
      </select>
    </div>
  );
}
