import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { useConfig, useSaveConfig } from "@/hooks/useConfig";
import { useTestTenant } from "@/hooks/useTenants";
import { Building2, Plus, Check, X, Loader2, AlertCircle } from "lucide-react";

/**
 * CompaniesSection — lets the user grow the scraper coverage by adding
 * Greenhouse / Lever / Ashby tenant slugs without editing config.json.
 *
 * Flow:
 *   1. User picks ATS provider (greenhouse / lever / ashby)
 *   2. Types the slug (the company's identifier on that ATS — e.g.
 *      "stripe", "openai", "ramp")
 *   3. Optional: types the display name (Ashby uses both)
 *   4. Clicks Test → backend hits the ATS API and reports:
 *        - count of open jobs
 *        - sample title (sanity check that it's the right company)
 *   5. If happy, clicks Add → POSTs to /api/config to update the
 *      scraper list. The mutation invalidates useConfig so the list
 *      below refreshes immediately.
 *
 * Why a separate component (not folded into LocationSection): tenant
 * management is a different mental task from "where I'd work" — and
 * keeping it on its own card means the user can scroll past it
 * 99% of the time when they're just tweaking other settings.
 */
export function CompaniesSection() {
  const config = useConfig();
  const save = useSaveConfig();
  const test = useTestTenant();

  const [kind, setKind] = useState<"greenhouse" | "lever" | "ashby">("greenhouse");
  const [slug, setSlug] = useState("");
  const [display, setDisplay] = useState("");

  const greenhouse = config.data?.ingest?.greenhouse_companies ?? [];
  const lever = config.data?.ingest?.lever_companies ?? [];
  const ashby = config.data?.ingest?.ashby_companies ?? [];

  // Add-handler: optimistically appends to whichever list, POSTs the
  // updated list, clears the form on success. The slug normalisation
  // (lowercase, trim) matches what the backend already does in
  // /api/config so we're consistent across paths.
  const onAdd = async () => {
    const cleanSlug = slug.trim().toLowerCase();
    if (!cleanSlug) return;
    const cleanDisplay = display.trim() || cleanSlug;
    let patch: Parameters<typeof save.mutate>[0] = {};
    if (kind === "greenhouse") {
      if (greenhouse.includes(cleanSlug)) return; // already there
      patch = { ingest: { greenhouse_companies: [...greenhouse, cleanSlug] } };
    } else if (kind === "lever") {
      if (lever.includes(cleanSlug)) return;
      patch = { ingest: { lever_companies: [...lever, cleanSlug] } };
    } else {
      if (ashby.some(([, s]) => s === cleanSlug)) return;
      patch = { ingest: { ashby_companies: [...ashby, [cleanDisplay, cleanSlug]] } };
    }
    save.mutate(patch, {
      onSuccess: () => {
        setSlug("");
        setDisplay("");
        test.reset();
      },
    });
  };

  const onRemove = async (removeKind: "greenhouse" | "lever" | "ashby", removeSlug: string) => {
    let patch: Parameters<typeof save.mutate>[0] = {};
    if (removeKind === "greenhouse") {
      patch = { ingest: { greenhouse_companies: greenhouse.filter((s) => s !== removeSlug) } };
    } else if (removeKind === "lever") {
      patch = { ingest: { lever_companies: lever.filter((s) => s !== removeSlug) } };
    } else {
      patch = { ingest: { ashby_companies: ashby.filter(([, s]) => s !== removeSlug) } };
    }
    save.mutate(patch);
  };

  const onTest = () => {
    const cleanSlug = slug.trim().toLowerCase();
    if (!cleanSlug) return;
    test.mutate({ kind, slug: cleanSlug, display: display.trim() || cleanSlug });
  };

  const testResult = test.data;
  const testOk = testResult?.ok && (testResult.count ?? 0) > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Building2 className="h-5 w-5" />
          Companies
        </CardTitle>
        <CardDescription>
          Add companies whose careers page lives on Greenhouse, Lever, or Ashby. Find the slug in their careers
          URL — e.g. <code className="text-xs">boards.greenhouse.io/<strong>stripe</strong></code>,{" "}
          <code className="text-xs">jobs.lever.co/<strong>netflix</strong></code>, or{" "}
          <code className="text-xs">jobs.ashbyhq.com/<strong>openai</strong></code>. Test before saving so you know
          the slug actually returns jobs.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Add form */}
        <div className="space-y-3 rounded-md border bg-secondary/20 p-4">
          <div className="grid grid-cols-1 md:grid-cols-[140px_1fr_1fr_auto] gap-2 items-end">
            <div className="space-y-1">
              <Label htmlFor="add-kind" className="text-xs">ATS</Label>
              <select
                id="add-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as typeof kind)}
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              >
                <option value="greenhouse">Greenhouse</option>
                <option value="lever">Lever</option>
                <option value="ashby">Ashby</option>
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="add-slug" className="text-xs">Slug</Label>
              <Input
                id="add-slug"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="stripe"
                onKeyDown={(e) => e.key === "Enter" && onTest()}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="add-display" className="text-xs">
                Display name {kind !== "ashby" && <span className="opacity-50">(optional)</span>}
              </Label>
              <Input
                id="add-display"
                value={display}
                onChange={(e) => setDisplay(e.target.value)}
                placeholder={kind === "ashby" ? "OpenAI" : "(defaults to slug)"}
              />
            </div>
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={onTest} disabled={!slug.trim() || test.isPending}>
                {test.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Test"}
              </Button>
              <Button
                type="button"
                variant="accent"
                onClick={onAdd}
                disabled={!slug.trim() || save.isPending || !testOk}
                title={!testOk ? "Test the slug first to confirm it returns jobs" : "Add to config"}
              >
                <Plus className="h-4 w-4 mr-1.5" />
                Add
              </Button>
            </div>
          </div>

          {/* Test result */}
          {test.isError && (
            <div className="text-sm text-destructive flex items-center gap-1.5">
              <AlertCircle className="h-4 w-4" />
              {test.error.message}
            </div>
          )}
          {testResult && testResult.ok && (testResult.count ?? 0) > 0 && (
            <div className="text-sm text-ghost-clear flex items-center gap-1.5">
              <Check className="h-4 w-4" />
              {testResult.count} open job{testResult.count === 1 ? "" : "s"}
              {testResult.sample && (
                <span className="text-muted-foreground">· e.g. "{testResult.sample}"</span>
              )}
            </div>
          )}
          {testResult && testResult.ok && (testResult.count ?? 0) === 0 && (
            <div className="text-sm text-ghost-aging flex items-center gap-1.5">
              <AlertCircle className="h-4 w-4" />
              Slug works but the API returned 0 jobs right now. Could be a real lull, or the slug is technically
              valid but inactive.
            </div>
          )}
          {testResult && !testResult.ok && (
            <div className="text-sm text-destructive flex items-center gap-1.5">
              <AlertCircle className="h-4 w-4" />
              {testResult.error ?? "Slug not found on this ATS."}
            </div>
          )}
        </div>

        {/* Configured tenants — sectioned by ATS so the user can see
            what's already there and remove dead ones. */}
        <TenantList
          title="Greenhouse"
          tenants={greenhouse.map((s) => ({ slug: s, display: s }))}
          onRemove={(s) => onRemove("greenhouse", s)}
        />
        <TenantList
          title="Lever"
          tenants={lever.map((s) => ({ slug: s, display: s }))}
          onRemove={(s) => onRemove("lever", s)}
        />
        <TenantList
          title="Ashby"
          tenants={ashby.map(([d, s]) => ({ slug: s, display: d }))}
          onRemove={(s) => onRemove("ashby", s)}
        />

        {/* Custom big-tech scrapers — each maps to a hand-written
            scraper in agents/ingest.py. Splitting the toggle list
            into FAST and SLOW tier matches the actual run model:
            FAST runs on Run Pipeline, SLOW only on Run Scraper. */}
        <CustomSourcesPanel />
      </CardContent>
    </Card>
  );
}

// Custom big-tech sources we ship with. Each maps to a hand-written
// scraper in agents/ingest.py against a public-feed endpoint that the
// company itself uses to render their careers page. We only include
// sources whose TOS / robots.txt either explicitly permits scraping
// or is silent (Amazon, Google's first-page HTML, the Workday-hosted
// tenants where Workday's whole product is "syndicate your jobs
// everywhere").
//
// Removed entries: Netflix (old API dead, moved to a JS-only Phenom
// platform), Salesforce + IBM (request schema drifted, returns HTTP
// 422), Cisco (tenant slug renamed, returns 404). Surfacing them as
// disabled toggles was just clutter — if any of them ship a working
// public feed again, add the row + the fetcher back.
const FAST_TIER_SOURCES: { key: keyof NonNullable<import("@/types/config").AppConfig["ingest"]>; label: string; note: string }[] = [
  { key: "enable_amazon", label: "Amazon", note: "JSON API" },
  { key: "enable_google", label: "Google", note: "Public careers HTML, single-page (per their robots.txt)" },
  { key: "enable_nvidia", label: "Nvidia", note: "Workday" },
  { key: "enable_adobe",  label: "Adobe",  note: "Workday" },
  { key: "enable_intel",  label: "Intel",  note: "Workday" },
];

function CustomSourcesPanel() {
  const config = useConfig();
  const save = useSaveConfig();
  const ing = config.data?.ingest ?? {};

  const toggle = (key: keyof typeof ing) => {
    const next = !ing[key];
    save.mutate({ ingest: { [key]: next } as never });
  };

  return (
    <div className="space-y-3 pt-2 border-t">
      <div className="rounded-md border bg-secondary/20 p-3 text-xs space-y-1.5">
        <div className="font-medium text-foreground">Sources we scrape are all public-feed by design.</div>
        <div className="text-muted-foreground">
          Greenhouse, Lever, Ashby, and Workday host public job APIs designed for syndication — companies opt in
          to making their listings available there. Amazon and Google's careers pages are publicly indexed (Google
          page-1 only, per their robots.txt). We deliberately don't ship scrapers for sites whose TOS or
          robots.txt prohibits automated access (Meta, Apple, Microsoft, Tesla, Oracle), and we don't keep dead
          toggles around — Netflix, Salesforce, IBM and Cisco's endpoints have all drifted; their rows are removed
          rather than left as no-op switches.
        </div>
      </div>

      <div>
        <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
          Custom sources <span className="opacity-60">(run with the rest of the pipeline)</span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {FAST_TIER_SOURCES.map((src) => (
            <SourceToggle
              key={src.key as string}
              label={src.label}
              note={src.note}
              checked={!!ing[src.key]}
              onClick={() => toggle(src.key)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function SourceToggle({
  label,
  note,
  checked,
  onClick,
}: {
  label: string;
  note: string;
  checked: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={note}
      className={`text-left rounded-md border px-3 py-2 transition-colors ${
        checked ? "border-accent bg-accent/10" : "border-border hover:bg-secondary/50"
      }`}
    >
      <div className="flex items-center gap-2">
        <input type="checkbox" checked={checked} readOnly className="accent-accent" />
        <span className="text-sm font-medium">{label}</span>
      </div>
      <div className={`text-[10px] mt-0.5 ${checked ? "text-muted-foreground" : "text-muted-foreground/70"}`}>
        {note}
      </div>
    </button>
  );
}

function TenantList({
  title,
  tenants,
  onRemove,
}: {
  title: string;
  tenants: { slug: string; display: string }[];
  onRemove: (slug: string) => void;
}) {
  if (tenants.length === 0) {
    return (
      <div>
        <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1.5">{title}</div>
        <div className="text-sm text-muted-foreground italic">none configured</div>
      </div>
    );
  }
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1.5">
        {title} <span className="font-mono">({tenants.length})</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {tenants.map((t) => (
          <Badge
            key={t.slug}
            variant="secondary"
            className="font-mono text-[11px] gap-1.5 pr-1"
            title={t.display !== t.slug ? `${t.display} → ${t.slug}` : t.slug}
          >
            {t.slug}
            <button
              type="button"
              onClick={() => onRemove(t.slug)}
              className="hover:bg-destructive/20 rounded p-0.5"
              title="Remove"
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}
      </div>
    </div>
  );
}
