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
  lmstudio: boolean; // 0.1 name: embeddings reachable
  embed_ok?: boolean;
  base_url: string;
  embed_model: string;
  embed_provider?: string;
  chat_provider?: string;
  llm_enabled: boolean;
}

export type Source = "env" | "settings" | "file" | "default";

export interface SettingValue<T = string> {
  value: T;
  source: Source;
}

export interface ProviderView {
  id: string;
  label: string;
  chat: boolean;
  embeddings: boolean;
  needs_key: boolean;
  local: boolean;
  custom: boolean;
  removable: boolean;
  key_url: string;
  key_env: string[];
  default_base_url: string;
  default_embed_model: string;
  base_url: SettingValue;
  key: { set: boolean; source: Source | null; saved: boolean; hint: string | null; error: string | null };
}

export interface SettingsData {
  writable: boolean;
  reveal: boolean;
  token_required: boolean;
  key_backend: "dpapi" | "file" | "env";
  values: {
    chat_provider: SettingValue;
    chat_model: SettingValue;
    embed_provider: SettingValue;
    embed_model: SettingValue;
    llm_enabled: SettingValue<boolean>;
    describe: SettingValue<boolean>;
    reasoning_effort: SettingValue;
    llm_timeout: SettingValue;
  };
  providers: ProviderView[];
  commands: number;
  embed_id: string;
}

export interface TestResult {
  ok: boolean;
  model?: string;
  reply?: string;
  dims?: number;
  error?: string;
  ms: number;
}

const TOKEN_KEY = "recall.settingsToken";

export function settingsToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setSettingsToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: token lasts until reload */
  }
}

/** Settings endpoints need this header (the server uses it to refuse cross-site requests). */
async function settingsCall<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Recall-Request": "1" };
  const token = settingsToken();
  if (token) headers["X-Recall-Token"] = token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* not JSON */
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const err = new Error(typeof detail === "string" ? detail : `${res.status} ${res.statusText}`);
    (err as Error & { status?: number }).status = res.status;
    throw err;
  }
  return data as T;
}

export const settingsApi = {
  get: () => settingsCall<SettingsData>("GET", "/api/settings"),
  save: (values: Record<string, string | null>) => settingsCall<SettingsData>("PUT", "/api/settings", { values }),
  setKey: (provider: string, apiKey: string) =>
    settingsCall<{ ok: boolean; hint: string }>("PUT", `/api/settings/keys/${encodeURIComponent(provider)}`, { api_key: apiKey }),
  removeKey: (provider: string) => settingsCall<{ ok: boolean }>("DELETE", `/api/settings/keys/${encodeURIComponent(provider)}`),
  revealKey: (provider: string) =>
    settingsCall<{ api_key: string }>("POST", `/api/settings/keys/${encodeURIComponent(provider)}/reveal`),
  models: (provider: string, opts: { base_url?: string; api_key?: string } = {}) =>
    settingsCall<{ models: string[] }>("POST", `/api/providers/${encodeURIComponent(provider)}/models`, opts),
  test: (body: { kind: "chat" | "embed"; provider: string; model?: string; base_url?: string; api_key?: string }) =>
    settingsCall<TestResult>("POST", "/api/settings/test", body),
  addCustom: (body: { label: string; base_url: string; embeddings: boolean; api_key?: string }) =>
    settingsCall<{ id: string }>("POST", "/api/settings/custom", body),
  updateCustom: (id: string, body: { label?: string; base_url?: string; embeddings?: boolean }) =>
    settingsCall<{ ok: boolean }>("PUT", `/api/settings/custom/${encodeURIComponent(id)}`, body),
  removeCustom: (id: string) => settingsCall<{ ok: boolean }>("DELETE", `/api/settings/custom/${encodeURIComponent(id)}`),
};

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
