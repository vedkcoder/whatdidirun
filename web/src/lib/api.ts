export interface Result {
  text_id: number;
  cmd: string;
  git_repo: string | null;
  description: string | null;
  runs: number;
  failures: number;
  last_ts: number | null;
  last_cwd: string | null;
  last_exit: number | null;
  last_branch: string | null;
  score: number;
  sources: string[];
}

export interface SearchResponse {
  query: string;
  text: string;
  filters: Record<string, unknown>;
  notes: string[];
  mode: "hybrid" | "keyword" | "recent";
  results: Result[];
  warnings: string[];
}

export interface Run {
  id: number;
  ts: number | null;
  duration_ms: number | null;
  exit_code: number | null;
  cwd: string | null;
  hostname: string | null;
  git_repo: string | null;
  git_branch: string | null;
  source: string;
}

export interface Stats {
  total_runs: number;
  unique_commands: number;
  failures: number;
  pending_embeddings: number;
  pending_descriptions: number;
  top_programs: { name: string; count: number }[];
  top_repos: { name: string; count: number }[];
  daily: { date: string; runs: number; failures: number }[];
  top_failing: { cmd: string; count: number }[];
}

export interface Health {
  lmstudio: boolean;
  base_url: string;
  embed_model: string;
  llm_enabled: boolean;
}

export interface SearchParams {
  q: string;
  smart?: boolean;
  failed?: boolean;
  repo?: string;
  since?: number;
  limit?: number;
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export function buildSearchQuery(p: SearchParams): string {
  const qs = new URLSearchParams({ q: p.q, limit: String(p.limit ?? 50) });
  if (p.smart) qs.set("smart", "true");
  if (p.failed !== undefined) qs.set("failed", String(p.failed));
  if (p.repo) qs.set("repo", p.repo);
  if (p.since !== undefined) qs.set("since", String(p.since));
  return qs.toString();
}

export const api = {
  search: (p: SearchParams, signal?: AbortSignal) => get<SearchResponse>(`/api/search?${buildSearchQuery(p)}`, signal),
  command: (id: number) => get<{ command: Result; runs: Run[] }>(`/api/command/${id}`),
  stats: () => get<Stats>("/api/stats"),
  facets: () => get<{ repos: string[] }>("/api/facets"),
  health: () => get<Health>("/api/health"),
  explain: async (cmd: string): Promise<string> => {
    const res = await fetch("/api/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cmd }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail ?? res.statusText);
    return body.explanation;
  },
};
