import { forwardRef } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
  smart: boolean;
  onSmartChange: (v: boolean) => void;
  loading: boolean;
  llmEnabled: boolean;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
}

export const SearchBar = forwardRef<HTMLInputElement, Props>(function SearchBar(
  { value, onChange, smart, onSmartChange, loading, llmEnabled, onKeyDown },
  ref,
) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-zinc-300 bg-white px-4 shadow-sm focus-within:border-emerald-500 focus-within:ring-2 focus-within:ring-emerald-500/20 dark:border-zinc-700 dark:bg-zinc-900">
      <span className="font-mono text-emerald-600 select-none dark:text-emerald-400">&gt;_</span>
      <input
        ref={ref}
        autoFocus
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Describe the command… e.g. “docker build that failed last week”"
        className="h-12 flex-1 bg-transparent text-base outline-none placeholder:text-zinc-400"
        aria-label="Search commands"
      />
      {loading && <span className="size-4 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" aria-label="Searching" />}
      <button
        type="button"
        disabled={!llmEnabled}
        onClick={() => onSmartChange(!smart)}
        title={llmEnabled ? "Use the chat model to expand the query and rerank results (slower)" : "Turn on AI features in Settings to use smart search"}
        className={`rounded-lg px-2.5 py-1 text-xs font-medium transition disabled:opacity-40 ${
          smart
            ? "bg-violet-600 text-white"
            : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
        }`}
      >
        ✦ Smart
      </button>
    </div>
  );
});
