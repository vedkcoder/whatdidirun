import { useEffect, useRef } from "react";
import type { Result } from "../lib/api";
import { ago } from "../lib/format";

interface Props {
  results: Result[];
  selected: number;
  onSelect: (i: number) => void;
  onOpen: (r: Result) => void;
  onCopy: (cmd: string) => void;
}

const SOURCE_STYLE: Record<string, string> = {
  bm25: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  vector: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  llm: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
};

export function ResultList({ results, selected, onSelect, onOpen, onCopy }: Props) {
  const refs = useRef<(HTMLLIElement | null)[]>([]);
  useEffect(() => refs.current[selected]?.scrollIntoView({ block: "nearest" }), [selected]);

  return (
    <ul className="divide-y divide-zinc-200 overflow-hidden rounded-xl border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900">
      {results.map((r, i) => {
        const failed = r.last_exit !== null && r.last_exit !== 0;
        return (
          <li
            key={r.text_id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            onMouseEnter={() => onSelect(i)}
            onClick={() => onOpen(r)}
            className={`group cursor-pointer px-4 py-3 transition ${i === selected ? "bg-emerald-50 dark:bg-emerald-950/30" : ""}`}
          >
            <div className="flex items-start gap-3">
              <span
                className={`mt-1.5 size-2 shrink-0 rounded-full ${failed ? "bg-red-500" : "bg-emerald-500"}`}
                title={failed ? `last exit ${r.last_exit}` : "last run succeeded"}
              />
              <div className="min-w-0 flex-1">
                <code className="block font-mono text-sm break-all whitespace-pre-wrap text-zinc-900 dark:text-zinc-100">{r.cmd}</code>
                {r.description && <p className="mt-0.5 text-sm text-zinc-500 dark:text-zinc-400">{r.description}</p>}
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
                  <span>{ago(r.last_ts)}</span>
                  {r.last_cwd && <span className="truncate font-mono">{r.last_cwd}</span>}
                  {r.git_repo && (
                    <span>
                      ⎇ {r.git_repo}
                      {r.last_branch ? `@${r.last_branch}` : ""}
                    </span>
                  )}
                  {r.runs > 1 && <span>{r.runs} runs</span>}
                  {r.failures > 0 && <span className="text-red-600 dark:text-red-400">{r.failures} failed</span>}
                  {r.sources.map((s) => (
                    <span key={s} className={`rounded px-1.5 py-px text-[10px] font-medium uppercase ${SOURCE_STYLE[s] ?? ""}`}>
                      {s}
                    </span>
                  ))}
                </div>
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onCopy(r.cmd);
                }}
                className="shrink-0 rounded-md px-2 py-1 text-xs text-zinc-500 opacity-0 transition group-hover:opacity-100 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              >
                Copy
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
