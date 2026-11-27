const UNITS: [string, number][] = [
  ["y", 365 * 86400],
  ["mo", 30 * 86400],
  ["w", 7 * 86400],
  ["d", 86400],
  ["h", 3600],
  ["m", 60],
];

export function ago(ts: number | null, now = Date.now() / 1000): string {
  if (!ts) return "imported";
  const s = now - ts;
  for (const [unit, size] of UNITS) if (s >= size) return `${Math.floor(s / size)}${unit} ago`;
  return "just now";
}

export function shortPath(p: string | null, home?: string): string {
  if (!p) return "";
  if (home && (p === home || p.startsWith(home + "/"))) return "~" + p.slice(home.length);
  return p;
}

export function duration(ms: number | null): string {
  if (ms == null) return "";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`;
}

export function dateTime(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const TIME_RANGES = [
  { label: "Any time", seconds: undefined },
  { label: "Today", seconds: 86400 },
  { label: "7 days", seconds: 7 * 86400 },
  { label: "30 days", seconds: 30 * 86400 },
] as const;
