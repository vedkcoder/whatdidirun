import type { ProviderView, SettingsData, Source } from "./api";

export const chatProviders = (ps: ProviderView[]) => ps.filter((p) => p.chat);
export const embedProviders = (ps: ProviderView[]) => ps.filter((p) => p.embeddings);

/** Sends command text off this machine (shown with a privacy note). */
export const isCloud = (p: ProviderView | undefined) => !!p && !p.local && !p.custom;

export function sourceLabel(source: Source | null | undefined): string {
  switch (source) {
    case "env":
      return "set by an environment variable";
    case "file":
      return "from config.toml";
    case "settings":
      return "saved here";
    default:
      return "default";
  }
}

export interface Draft {
  chat_provider: string;
  chat_model: string;
  embed_provider: string;
  embed_model: string;
  llm_enabled: boolean;
  describe: boolean;
  reasoning_effort: string;
}

export function draftFrom(s: SettingsData): Draft {
  const v = s.values;
  return {
    chat_provider: v.chat_provider.value,
    chat_model: v.chat_model.value,
    embed_provider: v.embed_provider.value,
    embed_model: v.embed_model.value,
    llm_enabled: v.llm_enabled.value,
    describe: v.describe.value,
    reasoning_effort: v.reasoning_effort.value,
  };
}

/** The values to send: only what changed. Models are sent as "" (= provider default) when cleared. */
export function changedValues(saved: Draft, draft: Draft): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of Object.keys(draft) as (keyof Draft)[]) {
    if (draft[key] === saved[key]) continue;
    const value = draft[key];
    out[key] = typeof value === "boolean" ? (value ? "1" : "0") : value.trim();
  }
  // Switching provider without picking a model: use the new provider's default, not the old model.
  if ("chat_provider" in out && !("chat_model" in out)) out.chat_model = "";
  if ("embed_provider" in out && !("embed_model" in out)) out.embed_model = "";
  return out;
}

/** Saving these changes re-embeds the whole history (a different vector space). */
export function needsReembed(saved: Draft, draft: Draft, providers: ProviderView[]): boolean {
  const model = (d: Draft) => d.embed_model.trim() || providers.find((p) => p.id === d.embed_provider)?.default_embed_model || "";
  return saved.embed_provider !== draft.embed_provider || model(saved) !== model(draft);
}
