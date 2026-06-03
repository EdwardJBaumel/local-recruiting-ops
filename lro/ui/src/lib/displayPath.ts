/**
 * Paths safe to show in the UI (screenshots, portfolio demos).
 * Strips Windows user home prefixes; keeps project-relative tails.
 */

export function displayPath(path: string | undefined | null): string {
  if (!path) return "";
  const normalised = path.replace(/\\/g, "/");

  const markers = [
    "/dev/projects/",
    "/local-recruiting-ops/",
    "/lantern/",
    "/lro/",
    "/AI_recruiter/",
  ];
  const lower = normalised.toLowerCase();
  for (const marker of markers) {
    const idx = lower.indexOf(marker.slice(1));
    if (idx >= 0) return `…${normalised.slice(idx)}`;
  }

  return normalised.replace(/^[A-Za-z]:\/Users\/[^/]+\//i, "~/");
}

/** Redact home-directory segments inside log tail lines. */
export function sanitiseLogLines(lines: string[]): string[] {
  return lines.map((line) =>
    line
      .replace(/[A-Za-z]:\\Users\\[^\\]+\\/g, "~\\")
      .replace(/[A-Za-z]:\/Users\/[^/]+\//g, "~/"),
  );
}
