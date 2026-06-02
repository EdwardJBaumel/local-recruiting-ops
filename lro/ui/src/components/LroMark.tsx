import { cn } from "@/lib/utils";

/** Pipeline + local dot mark for Local Recruiting Ops (LRO). */
export function LroMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("shrink-0", className)}
      aria-hidden
    >
      <rect x="3" y="3" width="18" height="18" rx="5" fill="currentColor" opacity="0.2" />
      <rect x="6" y="7" width="12" height="2" rx="1" fill="currentColor" />
      <rect x="6" y="11" width="12" height="2" rx="1" fill="currentColor" opacity="0.85" />
      <rect x="6" y="15" width="9" height="2" rx="1" fill="currentColor" opacity="0.7" />
      <circle cx="17.5" cy="16" r="1.75" fill="currentColor" />
    </svg>
  );
}
