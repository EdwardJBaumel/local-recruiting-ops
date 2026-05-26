import { cn } from "@/lib/utils";

/** Minimal lantern icon for the header wordmark. */
export function LanternMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("shrink-0", className)}
      aria-hidden
    >
      <path
        d="M8 3.5h8a1 1 0 0 1 1 1v1.2c0 .55-.45 1-1 1H8a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"
        fill="currentColor"
        opacity="0.55"
      />
      <path
        d="M7 7.5h10a2 2 0 0 1 2 2v7.5a4.5 4.5 0 0 1-4.5 4.5h-5A4.5 4.5 0 0 1 5 17V9.5a2 2 0 0 1 2-2Z"
        fill="currentColor"
        opacity="0.35"
      />
      <path
        d="M9 10.5h6v5.5a2.5 2.5 0 0 1-5 0v-5.5Z"
        fill="currentColor"
      />
      <path
        d="M10.5 20.5h3"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        opacity="0.7"
      />
    </svg>
  );
}
