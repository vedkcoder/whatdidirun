import { TIME_RANGES } from "../lib/format";

export interface FilterState {
  range: number; // index into TIME_RANGES
  status: "any" | "failed" | "ok";
  repo: string;
}

interface Props {
  value: FilterState;
  onChange: (v: FilterState) => void;
  repos: string[];
  notes: string[];
}

function Segmented<T extends string | number>({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: T }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg bg-zinc-200/70 p-0.5 dark:bg-zinc-800">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          onClick={() => onChange(o.value)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
            o.value === value
              ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-600 dark:text-white"
              : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Filters({ value, onChange, repos, notes }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Segmented
        options={TIME_RANGES.map((r, i) => ({ label: r.label, value: i }))}
        value={value.range}
        onChange={(range) => onChange({ ...value, range })}
      />
      <Segmented
        options={[
          { label: "All", value: "any" as const },
          { label: "Failed", value: "failed" as const },
          { label: "Succeeded", value: "ok" as const },
        ]}
        value={value.status}
        onChange={(status) => onChange({ ...value, status })}
      />
      {repos.length > 0 && (
        <select
          value={value.repo}
          onChange={(e) => onChange({ ...value, repo: e.target.value })}
          className="rounded-lg border border-zinc-300 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900"
          aria-label="Repository"
        >
          <option value="">All repos</option>
          {repos.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
      )}
      {notes.length > 0 && (
        <span className="text-xs text-zinc-500">
          understood: {notes.map((n) => (
            <span key={n} className="ml-1 rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
              {n}
            </span>
          ))}
        </span>
      )}
    </div>
  );
}
