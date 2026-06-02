import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { MapPin } from "lucide-react";
import { type Control, Controller } from "react-hook-form";
import type { SettingsFormShape } from "@/views/Settings";
import { MultiSelectLocations } from "@/components/MultiSelectLocations";

/**
 * Location filter — multi-select dropdowns for allow + block lists.
 *
 * Allow list: a posting passes the location filter if its `location`
 * substring-matches ANY entry in the allow list (OR semantics).
 * Empty allow list = no filter; every location is considered.
 *
 * Block list: takes precedence over the allow list. Any posting
 * whose location substring-matches an entry here is dropped, even
 * if it also matches the allow list.
 *
 * History
 * -------
 *   v1: Free-text inputs ("San Francisco, NYC, Seattle"). Worked
 *       but error-prone — users mistyped city names, missed common
 *       metros, didn't realise "California" as a substring would
 *       catch all CA jobs.
 *   v2: Pin-on-a-map filter with Leaflet + geocode table + radius
 *       slider. Looked cool but turned out the daily flow was just
 *       typing city names — the map was decoration.
 *   v3 (current): Multi-select dropdown with categories + free-text
 *       fallback for one-off substrings. Catches the 90% case with
 *       a click, lets power users hand-enter the rest.
 */
interface Props {
  control: Control<SettingsFormShape>;
}

export function LocationSection({ control }: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MapPin className="h-5 w-5" />
          Location filter
        </CardTitle>
        <CardDescription>
          Pick the cities, states, or regions you'd consider. Allow list is OR — a posting passes if its location
          matches <em>any</em> entry. Block list wins, so anything matching the block list is dropped even if it
          also matches the allow list. Both empty = no filter. You can also type a custom substring
          (e.g. &quot;Manchester UK&quot;) and hit Enter to add it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <Controller
          control={control}
          name="allowed_locations"
          render={({ field }) => (
            <div className="space-y-2">
              <Label className="text-sm">Allow these locations</Label>
              <MultiSelectLocations
                value={field.value ?? []}
                onChange={field.onChange}
                placeholder="Pick metros, states, or 'Remote'…"
                ariaLabel="Allowed locations"
              />
              <p className="text-xs text-muted-foreground">
                Substring-matched against the posting's location text. Pick &quot;California&quot; to catch any job
                mentioning the state, OR pick specific metros (San Francisco, San Jose, LA…) for jobs that only
                name a city. You can do both — coverage adds.
              </p>
            </div>
          )}
        />

        <Controller
          control={control}
          name="blocked_locations"
          render={({ field }) => (
            <div className="space-y-2">
              <Label className="text-sm">Block these locations</Label>
              <MultiSelectLocations
                value={field.value ?? []}
                onChange={field.onChange}
                placeholder="Block specific countries, regions, or cities…"
                ariaLabel="Blocked locations"
              />
              <p className="text-xs text-muted-foreground">
                <strong>Block list wins over allow list.</strong> Useful for countries / regions you keep seeing
                in results that you don't want — e.g. block &quot;UK&quot; (via &quot;United Kingdom&quot;) or &quot;EMEA&quot; to drop
                European postings.
              </p>
            </div>
          )}
        />
      </CardContent>
    </Card>
  );
}
