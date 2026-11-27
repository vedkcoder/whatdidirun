import { useEffect, useState } from "react";
import { api, type Result, type Run } from "../lib/api";
import { dateTime, duration } from "../lib/format";

interface Props {
  result: Result | null;
  onClose: () => void;
  onCopy: (cmd: string) => void;
  llmEnabled: boolean;
}

export function DetailDrawer({ result, onClose, onCopy, llmEnabled }: Props) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [explanation, setExplanation] = useState<string | null>(null);
  const [explaining, setExplaining] = useState(false);

  useEffect(() => {
    setRuns(null);
    setExplanation(null);
    if (result) api.command(result.text_id).then((d) => setRuns(d.runs)).catch(() => setRuns([]));
  }, [result]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!result) return null;

  const explain = async () => {
    setExplaining(true);
    try {
      setExplanation(await api.explain(result.cmd));
    } catch (e) {
      setExplanation(`⚠ ${(e as Error).message}`);
    } finally {
      setExplaining(false);
    }
  };

  return (
    <div className="fixed inset-0 z-20 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-zinc-950/30 backdrop-blur-[1px]" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-2xl flex-col overflow-y-auto bg-white shadow-2xl dark:bg-zinc-900">
        <header className="sticky top-0 flex items-center justify-between border-b border-zinc-200 bg-white/90 px-5 py-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/90">
          <h2 className="text-sm font-semibold">Command details</h2>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800" aria-label="Close">
            ✕
          </button>
        </header>
        <div className="space-y-5 p-5">
          <div className="rounded-lg bg-zinc-950 p-4 text-zinc-100">
            <code className="block font-mono text-sm break-all whitespace-pre-wrap">{result.cmd}</code>
          </div>
          {result.description && <p className="text-sm text-zinc-600 dark:text-zinc-400">{result.description}</p>}
          <div className="flex gap-2">
            <button onClick={() => onCopy(result.cmd)} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700">
              Copy command
            </button>
            <button
              onClick={explain}
              disabled={!llmEnabled || explaining}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              {explaining ? "Explaining…" : "✦ Explain"}
            </button>
          </div>
          {explanation && (
            <div className="rounded-lg border border-violet-200 bg-violet-50 p-4 text-sm whitespace-pre-wrap dark:border-violet-900 dark:bg-violet-950/30">
              {explanation}
            </div>
          )}
          <section>
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-zinc-500 uppercase">
              Runs {runs && `(${runs.length})`}
            </h3>
            {!runs ? (
              <p className="text-sm text-zinc-500">Loading…</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
                <table className="w-full text-left text-xs">
                  <thead className="bg-zinc-50 text-zinc-500 dark:bg-zinc-800/50">
                    <tr>
                      <th className="px-3 py-2 font-medium">When</th>
                      <th className="px-3 py-2 font-medium">Directory</th>
                      <th className="px-3 py-2 font-medium">Branch</th>
                      <th className="px-3 py-2 text-right font-medium">Took</th>
                      <th className="px-3 py-2 text-right font-medium">Exit</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                    {runs.map((r) => (
                      <tr key={r.id}>
                        <td className="px-3 py-2 whitespace-nowrap">{r.source === "import" && !r.ts ? "imported" : dateTime(r.ts)}</td>
                        <td className="max-w-56 truncate px-3 py-2 font-mono" title={r.cwd ?? ""}>
                          {r.cwd ?? "—"}
                        </td>
                        <td className="px-3 py-2">{r.git_branch ?? "—"}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{duration(r.duration_ms)}</td>
                        <td className={`px-3 py-2 text-right tabular-nums ${r.exit_code ? "text-red-600 dark:text-red-400" : ""}`}>
                          {r.exit_code ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}
