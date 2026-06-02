import { useMemo } from "react";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import type { MatchPayload } from "@/types/match";
import { useUIStore } from "@/stores/ui";
import { rowKey } from "@/lib/rowKey";
import { parsePostedDate, formatPostedAge } from "@/lib/postedAge";
import { GhostBadge } from "@/components/GhostBadge";
import { Badge } from "@/components/ui/badge";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

/**
 * MatchTable — TanStack Table around the matches array.
 *
 * Why TanStack Table instead of a hand-rolled <table>:
 *   - Sorting state lives in the table instance; toggle on column
 *     click is one line.
 *   - Column definitions are typed to the row shape (MatchPayload),
 *     so the cell renderers get full IntelliSense.
 *   - Column resizing is opt-in: `enableColumnResizing: true` plus a
 *     drag handle in the header gives users the "I want Role wider"
 *     escape hatch without us having to handcraft a hidden column-
 *     settings menu.
 *
 * Selection state lives in the Zustand UI store (`selectedJobUrl`)
 * so the detail panel can read it without prop drilling.
 */
interface Props {
  matches: MatchPayload[];
}

export function MatchTable({ matches }: Props) {
  const selectedUrl = useUIStore((s) => s.selectedJobUrl);
  const setSelectedUrl = useUIStore((s) => s.setSelectedJobUrl);
  const sortFromStore = useUIStore((s) => s.matchSort);
  const setSortInStore = useUIStore((s) => s.setMatchSort);

  // Mirror the Zustand sort into TanStack's internal SortingState shape.
  // Single source of truth: Zustand. TanStack just renders.
  const sorting: SortingState = useMemo(
    () => [{ id: sortFromStore.key, desc: sortFromStore.dir === "desc" }],
    [sortFromStore],
  );

  // Column order = LEFT to RIGHT in the rendered table. Width budget
  // tuned so Role gets the LION'S share — long PM titles like
  // "Senior Product Manager, Community" used to truncate to junk
  // because Company was eating ~105px while Role got the leftover.
  // Now Company is a tight 90px (fits "Salesforce", "ServiceNow", etc.
  // and gracefully truncates the rare longer name) and Role has no
  // explicit size, so it inherits all leftover width via table-fixed.
  //
  // Resize via the drag handle in each header — defaults are starting
  // points, not constraints. Sizes don't persist across reloads (yet);
  // wire that into Zustand if it becomes a friction point.
  //
  // `sortDescFirst` per-column = direction the FIRST click sorts.
  // Numeric columns (score, ghost, posted) start desc — "show me the
  // best/most-recent first." Text columns (role, company, location)
  // start asc — alphabetical default.
  // Per-column horizontal alignment. Role and Location stay LEFT
  // because they hold long, variable-length text — centred long text
  // with truncation slides off-axis as the visible portion gets
  // shorter, which is genuinely hard to scan. Role+Location left,
  // everything else (short, mostly numeric, fixed-width-ish) centered.
  const columns = useMemo<ColumnDef<MatchPayload>[]>(
    () => [
      {
        id: "role",
        header: "Role",
        accessorFn: (m) => m.title,
        cell: ({ row }) => (
          <div className="font-medium truncate" title={row.original.title}>
            {row.original.title}
          </div>
        ),
        sortDescFirst: false,
        minSize: 200,
        meta: { align: "left" } as { align: "left" | "center" },
        // No explicit size → gets the leftover width under table-fixed.
      },
      {
        id: "company",
        header: "Company",
        accessorFn: (m) => m.company,
        cell: ({ row }) => (
          <span className="text-sm truncate block" title={row.original.company}>
            {row.original.company}
          </span>
        ),
        sortDescFirst: false,
        size: 90,
        minSize: 60,
        meta: { align: "center" } as { align: "left" | "center" },
      },
      {
        id: "location",
        header: "Location",
        accessorFn: (m) => m.location ?? "",
        cell: ({ row }) => (
          <span className="text-sm text-muted-foreground truncate block" title={row.original.location ?? ""}>
            {row.original.location ?? "—"}
          </span>
        ),
        sortDescFirst: false,
        size: 160,
        minSize: 80,
        meta: { align: "left" } as { align: "left" | "center" },
      },
      {
        id: "posted",
        header: "Posted",
        // Sort uses parsed timestamp. Rows with no usable date sort to
        // the bottom (treat as oldest) so the freshness sort still
        // makes sense — undated rows aren't credible "fresh" hits.
        accessorFn: (m) => parsePostedDate(m.posted_date) ?? 0,
        cell: ({ row }) => {
          const iso = row.original.posted_date;
          const text = formatPostedAge(iso);
          // The em-dash surfaces both "no date" and "unparseable date".
          // Hover-title spells out which so the user knows whether to
          // re-run the pipeline (legacy data, no date) or report a bug
          // (new data, weird format the parser couldn't handle).
          const title = !iso
            ? "No posted_date on this row — run a pipeline cycle to refresh."
            : parsePostedDate(iso) == null
            ? `Unparseable posted_date: ${iso}`
            : iso;
          return (
            <span className="text-xs text-muted-foreground" title={title}>
              {text}
            </span>
          );
        },
        sortDescFirst: true,
        size: 70,
        minSize: 50,
      },
      {
        id: "ghost",
        header: "Ghost",
        accessorFn: (m) => m._fake?.score ?? 0,
        cell: ({ row }) =>
          row.original._fake ? (
            <GhostBadge score={row.original._fake.score} />
          ) : (
            <span className="text-xs text-muted-foreground">—</span>
          ),
        sortDescFirst: true,
        size: 95,
        minSize: 70,
      },
      {
        id: "score",
        header: "Score",
        accessorFn: (m) => m._match_score_display ?? 0,
        cell: ({ row }) => {
          const pct = Math.round((row.original._match_score_display ?? 0) * 100);
          return (
            <span className="font-mono tabular-nums font-semibold text-accent">
              {pct}
            </span>
          );
        },
        sortDescFirst: true,
        size: 65,
        minSize: 50,
      },
    ],
    [],
  );

  const table = useReactTable({
    data: matches,
    columns,
    state: { sorting },
    // `enableSortingRemoval: false` skips the third "no sort" tick of
    // TanStack's default cycle. Each header click now flips just
    // between asc and desc, which is what users expect from a leader-
    // board-style table.
    enableSortingRemoval: false,
    // Live column resizing — drag the right edge of any header to
    // adjust width. `onChange` updates as the user drags (smoother UX
    // than `onEnd` which only commits on mouseup).
    enableColumnResizing: true,
    columnResizeMode: "onChange",
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      const first = next[0] ?? { id: "score", desc: true };
      setSortInStore({
        key: first.id as never,
        dir: first.desc ? "desc" : "asc",
      });
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="rounded-md border overflow-x-auto">
      {/* table-fixed honours per-column widths; without it, the browser
       *  auto-fits and a long Role cell pushes everything sideways. */}
      <table className="w-full text-sm table-fixed">
        <thead className="bg-secondary/50">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => {
                const sortDir = h.column.getIsSorted();
                const Icon = sortDir === "asc" ? ArrowUp : sortDir === "desc" ? ArrowDown : ArrowUpDown;
                const isResizing = h.column.getIsResizing();
                const align = (h.column.columnDef.meta as { align?: "left" | "center" } | undefined)?.align ?? "center";
                const alignClass = align === "left" ? "text-left" : "text-center";
                return (
                  <th
                    key={h.id}
                    style={{ width: h.getSize() }}
                    className={`relative ${alignClass} px-3 py-2.5 text-xs font-medium uppercase tracking-wider text-muted-foreground select-none`}
                  >
                    {/* Header label — clickable area for sort. The resize
                        handle is OUTSIDE this span so a drag on the
                        right edge doesn't accidentally toggle sort. */}
                    <span
                      className="inline-flex items-center gap-1.5 cursor-pointer"
                      onClick={h.column.getToggleSortingHandler()}
                    >
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      <Icon className="h-3 w-3" />
                    </span>

                    {/* Drag-to-resize handle. Sits on the right edge of
                        every header except the last (the right edge of
                        the table itself isn't a column boundary). */}
                    {h.column.getCanResize() && (
                      <div
                        onMouseDown={h.getResizeHandler()}
                        onTouchStart={h.getResizeHandler()}
                        onClick={(e) => e.stopPropagation()}
                        className={`absolute right-0 top-0 h-full w-1.5 cursor-col-resize select-none touch-none transition-colors ${
                          isResizing ? "bg-accent" : "bg-transparent hover:bg-accent/50"
                        }`}
                        title="Drag to resize column"
                      />
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            const key = rowKey(row.original);
            const isSelected = key === selectedUrl;
            return (
              <tr
                key={row.id}
                onClick={() => setSelectedUrl(key)}
                className={`border-t cursor-pointer transition-colors ${
                  isSelected ? "bg-accent/10" : "hover:bg-secondary/30"
                }`}
              >
                {row.getVisibleCells().map((cell) => {
                  const align = (cell.column.columnDef.meta as { align?: "left" | "center" } | undefined)?.align ?? "center";
                  const alignClass = align === "left" ? "text-left" : "text-center";
                  return (
                    <td key={cell.id} className={`px-3 py-3 align-middle ${alignClass}`}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          {table.getRowModel().rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="text-center py-12 text-sm text-muted-foreground">
                No matches yet. Click <Badge variant="outline">Run Pipeline</Badge> in the header to start.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
