import { describe, expect, it } from "vitest";
import type { ProviderView } from "./api";
import { changedValues, chatProviders, embedProviders, isCloud, needsReembed, type Draft } from "./settings";

const p = (id: string, over: Partial<ProviderView> = {}): ProviderView => ({
  id,
  label: id,
  chat: true,
  embeddings: false,
  needs_key: true,
  local: false,
  custom: false,
  removable: false,
  key_url: "",
  key_env: [],
  default_base_url: "",
  default_embed_model: "",
  base_url: { value: "", source: "default" },
  key: { set: false, source: null, saved: false, hint: null, error: null },
  ...over,
});

const providers = [
  p("lmstudio", { embeddings: true, local: true, needs_key: false, default_embed_model: "nomic" }),
  p("anthropic"),
  p("openai", { embeddings: true, default_embed_model: "text-embedding-3-small" }),
  p("custom-box", { custom: true, needs_key: false }),
];

const base: Draft = {
  chat_provider: "lmstudio",
  chat_model: "",
  embed_provider: "lmstudio",
  embed_model: "",
  llm_enabled: true,
  describe: true,
  reasoning_effort: "none",
};

describe("settings helpers", () => {
  it("offers only embedding-capable providers for embeddings", () => {
    expect(embedProviders(providers).map((x) => x.id)).toEqual(["lmstudio", "openai"]);
    expect(chatProviders(providers).map((x) => x.id)).toEqual(["lmstudio", "anthropic", "openai", "custom-box"]);
  });

  it("flags cloud providers only", () => {
    expect(providers.map(isCloud)).toEqual([false, true, true, false]);
  });

  it("sends only changed values and resets the model on a provider switch", () => {
    expect(changedValues(base, base)).toEqual({});
    expect(changedValues(base, { ...base, chat_provider: "anthropic" })).toEqual({ chat_provider: "anthropic", chat_model: "" });
    expect(changedValues(base, { ...base, chat_provider: "anthropic", chat_model: " claude-x " })).toEqual({
      chat_provider: "anthropic",
      chat_model: "claude-x",
    });
    expect(changedValues(base, { ...base, llm_enabled: false })).toEqual({ llm_enabled: "0" });
  });

  it("knows when saving re-embeds everything", () => {
    expect(needsReembed(base, base, providers)).toBe(false);
    expect(needsReembed(base, { ...base, embed_model: "nomic" }, providers)).toBe(false); // same as the default
    expect(needsReembed(base, { ...base, embed_provider: "openai" }, providers)).toBe(true);
    expect(needsReembed(base, { ...base, embed_model: "other" }, providers)).toBe(true);
    expect(needsReembed(base, { ...base, chat_provider: "anthropic" }, providers)).toBe(false);
  });
});
