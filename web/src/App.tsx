import { useCallback, useEffect, useRef, useState } from "react";
import { DetailDrawer } from "./components/DetailDrawer";
import { Filters, type FilterState } from "./components/Filters";
import { ResultList } from "./components/ResultList";
import { SearchBar } from "./components/SearchBar";
import { Settings } from "./components/Settings";
import { Stats } from "./components/Stats";
import { api, type Health, type Result, type SearchResponse } from "./lib/api";
import { TIME_RANGES } from "./lib/format";

type Tab = "search" | "stats" | "settings";
const TABS: Tab[] = ["search", "stats", "settings"];

// The tab lives in the URL hash, so the tray app can open #settings directly.
function tabFromHash(): Tab {
  const t = window.location.hash.replace(/^#\/?/, "") as Tab;
  return TABS.includes(t) ? t : "search";
}

export default function App() {
  const [tab, setTabState] = useState<Tab>(tabFromHash);
  const setTab = useCallback((t: Tab) => {
    setTabState(t);
    const hash = t === "search" ? "" : `#${t}`;
    if (window.location.hash !== hash) history.replaceState(null, "", hash || window.location.pathname + window.location.search);
  }, []);
  const [query, setQuery] = useState("");
  const [smart, setSmart] = useState(false);
  const [filters, setFilters] = useState<FilterState>({ range: 0, status: "any", repo: "" });
  const [resp, setResp] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [open, setOpen] = useState<Result | null>(null);
  const [repos, setRepos] = useState<string[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const refreshHealth = useCallback(() => {
    api.health().then(setHealth).catch(() => {});
  }, []);

  useEffect(() => {
    api.facets().then((f) => setRepos(f.repos)).catch(() => {});
    refreshHealth();
    const onHash = () => setTabState(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [refreshHealth]);

  // Debounced search; smart mode waits longer since each call hits the LLM.
  useEffect(() => {
    if (tab !== "search") return;
    const ctrl = new AbortController();
    const range = TIME_RANGES[filters.range].seconds;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await api.search(
          {
            q: query,
            smart: smart && query.trim().length > 0,
            failed: filters.status === "any" ? undefined : filters.status === "failed",
            repo: filters.repo || undefined,
            since: range ? Date.now() / 1000 - range : undefined,
          },
          ctrl.signal,
        );
        setResp(r);
        setSelected(0);
        setError(null);
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError(String(e));
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    }, smart ? 600 : 200);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query, smart, filters, tab]);

  const copy = useCallback(async (cmd: string) => {
    try {
      await navigator.clipboard.writeText(cmd);
      setToast("Copied to clipboard");
    } catch {
      setToast("Clipboard unavailable");
    }
    setTimeout(() => setToast(null), 1500);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement !== input.current && !open) {
        e.preventDefault();
        setTab("search");
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setTab]);

  const results = resp?.results ?? [];
  const onInputKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((s) => Math.min(results.length - 1, s + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((s) => Math.max(0, s - 1));
    } else if (e.key === "Enter" && results[selected]) {
      e.preventDefault();
      if (e.metaKey || e.ctrlKey) copy(results[selected].cmd);
      else setOpen(results[selected]);
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-4 pb-16">
      <header className="flex items-center justify-between py-5">
        <div className="flex items-center gap-2">
          <span className="grid size-8 place-items-center rounded-lg bg-emerald-600 font-mono text-sm font-bold text-white">&gt;_</span>
          <span className="text-lg font-semibold tracking-tight">WhatdidIRun??</span>
        </div>
        <nav className="flex items-center gap-1 text-sm">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1.5 capitalize ${
                tab === t ? "bg-zinc-200 font-medium dark:bg-zinc-800" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
              }`}
            >
              {t}
            </button>
          ))}
          {health && (
            <button
              type="button"
              onClick={() => setTab("settings")}
              className="ml-3 flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
              title={`Embeddings: ${health.embed_provider ?? "LM Studio"} · ${health.embed_model}${health.embed_ok ?? health.lmstudio ? "" : " (unreachable)"}\nChat: ${health.chat_provider ?? "LM Studio"}${health.llm_enabled ? "" : " (AI features off)"}`}
            >
              <span className={`size-2 rounded-full ${(health.embed_ok ?? health.lmstudio) ? "bg-emerald-500" : "bg-red-500"}`} />
              {providerLabel(health)}
            </button>
          )}
        </nav>
      </header>

      {tab === "settings" ? (
        <Settings onSaved={refreshHealth} />
      ) : tab === "stats" ? (
        <Stats />
      ) : (
        <main className="space-y-3">
          <SearchBar
            ref={input}
            value={query}
            onChange={setQuery}
            smart={smart}
            onSmartChange={setSmart}
            loading={loading}
            llmEnabled={health?.llm_enabled ?? true}
            onKeyDown={onInputKey}
          />
          <Filters value={filters} onChange={setFilters} repos={repos} notes={resp?.notes ?? []} />
          {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">{error}</p>}
          {resp?.warnings.map((w) => (
            <p key={w} className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
              {w}
            </p>
          ))}
          {resp && (
            <div className="flex items-center justify-between text-xs text-zinc-500">
              <span>
                {results.length} {results.length === 1 ? "result" : "results"} ·{" "}
                {resp.mode === "recent" ? "most recent" : resp.mode === "hybrid" ? "keyword + semantic" : "keyword only"}
              </span>
              <span className="hidden sm:inline">↑↓ navigate · ↵ details · ⌘/Ctrl+↵ copy · / focus</span>
            </div>
          )}
          {results.length > 0 ? (
            <ResultList results={results} selected={selected} onSelect={setSelected} onOpen={setOpen} onCopy={copy} />
          ) : (
            resp && !loading && <p className="py-12 text-center text-sm text-zinc-500">No matching commands.</p>
          )}
        </main>
      )}

      <DetailDrawer result={open} onClose={() => setOpen(null)} onCopy={copy} llmEnabled={health?.llm_enabled ?? true} />
      {toast && (
        <div className="fixed bottom-6 left-1/2 z-30 -translate-x-1/2 rounded-lg bg-zinc-900 px-3 py-2 text-sm text-white shadow-lg dark:bg-zinc-100 dark:text-zinc-900">
          {toast}
        </div>
      )}
    </div>
  );
}

function providerLabel(h: Health): string {
  const embed = (h.embed_provider ?? "LM Studio").replace(/ \(.*\)$/, "");
  const chat = (h.chat_provider ?? "LM Studio").replace(/ \(.*\)$/, "");
  return embed === chat || !h.llm_enabled ? embed : `${chat} + ${embed}`;
}
