import { useEffect, useState } from "react";
import { api, type Stats as StatsData } from "../lib/api";

function Card({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone ?? ""}`}>{value.toLocaleString()}</div>
    </div>
  );
}

function BarList({ title, items, mono }: { title: string; items: { name: string; count: number }[]; mono?: boolean }) {
  const max = Math.max(1, ...items.map((i) => i.count));
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h3 className="mb-3 text-xs font-semibold tracking-wide text-zinc-500 uppercase">{title}</h3>
      {items.length === 0 && <p className="text-sm text-zinc-500">Nothing yet.</p>}
      <ul className="space-y-1.5">
        {items.map((i) => (
          <li key={i.name} className="relative flex items-center justify-between gap-3 rounded px-2 py-1 text-sm">
            <span className="absolute inset-y-0 left-0 rounded bg-emerald-500/15" style={{ width: `${(i.count / max) * 100}%` }} />
            <span className={`relative truncate ${mono ? "font-mono text-xs" : ""}`} title={i.name}>
              {i.name}
            </span>
            <span className="relative tabular-nums text-zinc-500">{i.count}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Stats() {
  const [data, setData] = useState<StatsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.stats().then(setData).catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!data) return <p className="text-sm text-zinc-500">Loading…</p>;
  const maxDay = Math.max(1, ...data.daily.map((d) => d.runs));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card label="Commands run" value={data.total_runs} />
        <Card label="Unique commands" value={data.unique_commands} />
        <Card label="Failed runs" value={data.failures} tone="text-red-600 dark:text-red-400" />
        <Card label="Awaiting indexing" value={Math.max(data.pending_embeddings, data.pending_descriptions)} tone="text-zinc-500" />
      </div>
      <section className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
        <h3 className="mb-3 text-xs font-semibold tracking-wide text-zinc-500 uppercase">Last 30 days</h3>
        {data.daily.length === 0 ? (
          <p className="text-sm text-zinc-500">No timestamped commands yet.</p>
        ) : (
          <div className="flex h-32 items-end gap-1">
            {data.daily.map((d) => (
              <div key={d.date} className="flex h-full flex-1 flex-col justify-end" title={`${d.date}: ${d.runs} runs, ${d.failures} failed`}>
                <div className="rounded-t bg-emerald-500/80" style={{ height: `${((d.runs - d.failures) / maxDay) * 100}%` }} />
                <div className="bg-red-500/80" style={{ height: `${(d.failures / maxDay) * 100}%` }} />
              </div>
            ))}
          </div>
        )}
      </section>
      <div className="grid gap-4 md:grid-cols-2">
        <BarList title="Top programs" items={data.top_programs} mono />
        <BarList title="Top repos" items={data.top_repos} />
      </div>
      <BarList title="Most failing commands" items={data.top_failing.map((f) => ({ name: f.cmd, count: f.count }))} mono />
    </div>
  );
}
