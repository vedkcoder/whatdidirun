import { useCallback, useEffect, useMemo, useState } from "react";
import { setSettingsToken, settingsApi, type ProviderView, type SettingsData, type TestResult } from "../lib/api";
import {
  changedValues,
  chatProviders,
  draftFrom,
  embedProviders,
  isCloud,
  needsReembed,
  sourceLabel,
  type Draft,
} from "../lib/settings";

const card = "rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900";
const input =
  "w-full rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950";
const btn =
  "rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800";
const primary = "rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50";

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function Badge({ children, tone = "zinc" }: { children: React.ReactNode; tone?: "zinc" | "amber" | "emerald" | "red" }) {
  const tones = {
    zinc: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
    amber: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    emerald: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    red: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  };
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${tones[tone]}`}>{children}</span>;
}

function Section({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className={card}>
      <h3 className="text-sm font-semibold">{title}</h3>
      {hint && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}
      <div className="mt-4 space-y-3">{children}</div>
    </section>
  );
}

function Field({ label, locked, children }: { label: string; locked?: boolean; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-2 text-xs font-medium text-zinc-600 dark:text-zinc-400">
        {label}
        {locked && <Badge tone="amber">set by an environment variable</Badge>}
      </span>
      {children}
    </label>
  );
}

function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <label className="flex items-center gap-3 text-sm">
      <input type="checkbox" className="size-4 accent-emerald-600" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function TestLine({ result }: { result: TestResult | "running" | null }) {
  if (!result) return null;
  if (result === "running") return <p className="text-xs text-zinc-500">Testing…</p>;
  if (!result.ok) return <p className="text-xs break-words text-red-600 dark:text-red-400">✗ {result.error}</p>;
  const what = result.dims !== undefined ? `${result.dims}-dimension vectors` : `replied ${JSON.stringify(result.reply)}`;
  return (
    <p className="text-xs text-emerald-700 dark:text-emerald-400">
      ✓ {result.model}: {what} in {(result.ms / 1000).toFixed(1)}s
    </p>
  );
}

/** Provider + model picker with a live model list and a Test button. */
function ModelPicker({
  kind,
  providers,
  provider,
  model,
  onProvider,
  onModel,
  lockedProvider,
  lockedModel,
  disabled,
}: {
  kind: "chat" | "embed";
  providers: ProviderView[];
  provider: string;
  model: string;
  onProvider: (id: string) => void;
  onModel: (m: string) => void;
  lockedProvider: boolean;
  lockedModel: boolean;
  disabled: boolean;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [test, setTest] = useState<TestResult | "running" | null>(null);
  const info = providers.find((p) => p.id === provider);

  useEffect(() => {
    setModels([]);
    setModelsError(null);
    setTest(null);
    if (!info || (info.needs_key && !info.key.set)) return;
    let live = true;
    settingsApi
      .models(provider)
      .then((r) => live && setModels(kind === "embed" ? r.models : r.models.filter((m) => !/embed/i.test(m))))
      .catch((e) => live && setModelsError(errorText(e)));
    return () => {
      live = false;
    };
  }, [provider, kind, info?.key.set]); // eslint-disable-line react-hooks/exhaustive-deps

  const placeholder =
    kind === "embed"
      ? info?.default_embed_model
        ? `default: ${info.default_embed_model}`
        : "model id"
      : info?.local || info?.custom
        ? "auto: first model the server reports"
        : "choose a model";
  const listId = `${kind}-models`;

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label="Provider" locked={lockedProvider}>
        <select className={input} value={provider} disabled={disabled || lockedProvider} onChange={(e) => onProvider(e.target.value)}>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
              {p.needs_key && !p.key.set ? " (needs a key)" : ""}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Model" locked={lockedModel}>
        <input
          className={input}
          list={listId}
          value={model}
          placeholder={placeholder}
          disabled={disabled || lockedModel}
          onChange={(e) => onModel(e.target.value)}
          spellCheck={false}
        />
        <datalist id={listId}>
          {models.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      </Field>
      <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
        <button
          type="button"
          className={btn}
          disabled={test === "running"}
          onClick={async () => {
            setTest("running");
            try {
              setTest(await settingsApi.test({ kind, provider, model }));
            } catch (e) {
              setTest({ ok: false, error: errorText(e), ms: 0 });
            }
          }}
        >
          Test
        </button>
        {info?.needs_key && !info.key.set ? (
          <span className="text-xs text-amber-700 dark:text-amber-400">Add an API key for {info.label} below first.</span>
        ) : modelsError ? (
          <span className="text-xs break-words text-zinc-500">Couldn't list models: {modelsError}</span>
        ) : models.length > 0 ? (
          <span className="text-xs text-zinc-500">{models.length} {models.length === 1 ? "model" : "models"} available: start typing to pick one.</span>
        ) : null}
        <TestLine result={test} />
      </div>
    </div>
  );
}

function KeyRow({ p, writable, onChanged }: { p: ProviderView; writable: boolean; onChanged: () => void }) {
  const [value, setValue] = useState("");
  const [revealed, setRevealed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [baseUrl, setBaseUrl] = useState(p.base_url.source === "settings" ? p.base_url.value : "");
  const [showAdvanced, setShowAdvanced] = useState(p.base_url.source === "settings");
  const envKey = p.key.source === "env";

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-36 text-sm font-medium">{p.label}</span>
        {envKey ? (
          <Badge tone="amber">key from {p.key_env.length ? p.key_env.join(" / ") : "an environment variable"}</Badge>
        ) : p.key.saved ? (
          <Badge tone="emerald">key saved {p.key.hint}</Badge>
        ) : p.key.set ? (
          <Badge>{sourceLabel(p.key.source)}</Badge>
        ) : p.needs_key ? (
          <Badge tone="zinc">no key</Badge>
        ) : (
          <Badge>key optional</Badge>
        )}
        {p.key.error && <Badge tone="red">{p.key.error}</Badge>}
        {p.key_url && (
          <a className="text-xs text-emerald-700 hover:underline dark:text-emerald-400" href={p.key_url} target="_blank" rel="noreferrer">
            Get a key ↗
          </a>
        )}
        <button type="button" className="ml-auto text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100" onClick={() => setShowAdvanced(!showAdvanced)}>
          {showAdvanced ? "Hide address" : "Address…"}
        </button>
      </div>
      {writable && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="password"
            className={`${input} max-w-sm flex-1`}
            placeholder={p.key.saved ? "Replace the saved key" : "Paste an API key"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            aria-label={`API key for ${p.label}`}
          />
          <button type="button" className={btn} disabled={busy || !value.trim()} onClick={() => run(async () => {
            await settingsApi.setKey(p.id, value);
            setValue("");
            setRevealed(null);
          })}>
            Save key
          </button>
          {p.key.saved && (
            <>
              <button type="button" className={btn} disabled={busy} onClick={async () => {
                if (revealed !== null) return setRevealed(null);
                setErr(null);
                try {
                  setRevealed((await settingsApi.revealKey(p.id)).api_key);
                } catch (e) {
                  setErr(errorText(e));
                }
              }}>
                {revealed !== null ? "Hide" : "Reveal"}
              </button>
              <button type="button" className={btn} disabled={busy} onClick={() => {
                if (confirm(`Remove the saved API key for ${p.label}?`)) run(() => settingsApi.removeKey(p.id).then(() => setRevealed(null)));
              }}>
                Remove
              </button>
            </>
          )}
        </div>
      )}
      {revealed !== null && <code className="mt-2 block rounded bg-zinc-100 px-2 py-1 font-mono text-xs break-all select-all dark:bg-zinc-800">{revealed}</code>}
      {showAdvanced && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            className={`${input} max-w-sm flex-1`}
            placeholder={p.default_base_url || "https://…/v1"}
            value={baseUrl}
            disabled={!writable || p.base_url.source === "env"}
            onChange={(e) => setBaseUrl(e.target.value)}
            spellCheck={false}
            aria-label={`API address for ${p.label}`}
          />
          {writable && p.base_url.source !== "env" && (
            <button type="button" className={btn} disabled={busy} onClick={() => run(() => settingsApi.save({ [`provider.${p.id}.base_url`]: baseUrl.trim() || null }))}>
              {baseUrl.trim() ? "Save address" : "Use default"}
            </button>
          )}
          <span className="text-xs text-zinc-500">
            Now: <span className="font-mono">{p.base_url.value || "not set"}</span> ({sourceLabel(p.base_url.source)})
          </span>
        </div>
      )}
      {err && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{err}</p>}
    </li>
  );
}

function CustomEndpoints({ data, writable, onChanged }: { data: SettingsData; writable: boolean; onChanged: () => void }) {
  const [label, setLabel] = useState("");
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const [embeddings, setEmbeddings] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const custom = data.providers.filter((p) => p.removable);

  return (
    <Section
      title="Custom endpoints"
      hint="Any OpenAI-compatible server: vLLM, llama.cpp, LiteLLM, a company gateway, … They appear in the provider lists above."
    >
      {custom.length > 0 && (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {custom.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <span className="font-medium">{p.label}</span>
              <span className="font-mono text-xs text-zinc-500">{p.base_url.value}</span>
              {p.embeddings && <Badge>embeddings</Badge>}
              {writable && (
                <button
                  type="button"
                  className="ml-auto text-xs text-red-600 hover:underline dark:text-red-400"
                  onClick={async () => {
                    if (!confirm(`Remove ${p.label} and its saved key?`)) return;
                    try {
                      await settingsApi.removeCustom(p.id);
                      onChanged();
                    } catch (e) {
                      setErr(errorText(e));
                    }
                  }}
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {writable && (
        <form
          className="grid gap-2 sm:grid-cols-2"
          onSubmit={async (e) => {
            e.preventDefault();
            setErr(null);
            try {
              await settingsApi.addCustom({ label, base_url: url, embeddings, api_key: key || undefined });
              setLabel("");
              setUrl("");
              setKey("");
              onChanged();
            } catch (ex) {
              setErr(errorText(ex));
            }
          }}
        >
          <input className={input} placeholder="Name, e.g. GPU box" value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Endpoint name" />
          <input className={input} placeholder="http://host:8000/v1" value={url} onChange={(e) => setUrl(e.target.value)} spellCheck={false} aria-label="Endpoint address" />
          <input className={input} type="password" placeholder="API key (optional)" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" aria-label="Endpoint API key" />
          <div className="flex items-center justify-between gap-2">
            <Toggle checked={embeddings} onChange={setEmbeddings} label="Offers embeddings" />
            <button type="submit" className={btn} disabled={!label.trim() || !url.trim()}>
              Add endpoint
            </button>
          </div>
        </form>
      )}
      {err && <p className="text-xs text-red-600 dark:text-red-400">{err}</p>}
    </Section>
  );
}

function TokenPrompt({ onSaved }: { onSaved: () => void }) {
  const [token, setToken] = useState("");
  return (
    <section className={card}>
      <h3 className="text-sm font-semibold">Settings token required</h3>
      <p className="mt-1 text-sm text-zinc-500">This server listens on the network, so changing settings needs the token set in RECALL_SETTINGS_TOKEN.</p>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setSettingsToken(token.trim());
          onSaved();
        }}
      >
        <input className={`${input} max-w-sm`} type="password" value={token} onChange={(e) => setToken(e.target.value)} aria-label="Settings token" />
        <button type="submit" className={primary} disabled={!token.trim()}>
          Continue
        </button>
      </form>
    </section>
  );
}

export function Settings({ onSaved }: { onSaved?: () => void }) {
  const [data, setData] = useState<SettingsData | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; status?: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmReembed, setConfirmReembed] = useState(false);
  const [allProviders, setAllProviders] = useState(false);

  const load = useCallback(async (keepDraft = false) => {
    try {
      const d = await settingsApi.get();
      setData(d);
      setLoadError(null);
      if (!keepDraft) setDraft(draftFrom(d));
    } catch (e) {
      setLoadError({ message: errorText(e), status: (e as { status?: number }).status });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const saved = useMemo(() => (data ? draftFrom(data) : null), [data]);
  const changes = useMemo(() => (saved && draft ? changedValues(saved, draft) : {}), [saved, draft]);
  const reembed = !!(saved && draft && data && needsReembed(saved, draft, data.providers));

  if (loadError?.status === 401) return <TokenPrompt onSaved={() => load()} />;
  if (loadError) return <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">Couldn't load settings: {loadError.message}</p>;
  if (!data || !draft || !saved) return <p className="text-sm text-zinc-500">Loading…</p>;

  const v = data.values;
  const writable = data.writable;
  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });
  const chatInfo = data.providers.find((p) => p.id === draft.chat_provider);
  const embedInfo = data.providers.find((p) => p.id === draft.embed_provider);
  const cloud = [chatInfo, embedInfo].filter(isCloud);
  const keyRows = data.providers.filter((p) => p.needs_key || p.local || p.custom);
  // Show what matters first: providers in use, with a key, or customised; the rest on request.
  const relevant = (p: ProviderView) =>
    [draft.chat_provider, draft.embed_provider, saved.chat_provider, saved.embed_provider].includes(p.id) ||
    p.key.set ||
    p.key.saved ||
    p.base_url.source !== "default";
  const shownRows = allProviders ? keyRows : keyRows.filter(relevant);
  const hiddenCount = keyRows.length - shownRows.length;
  const dirty = Object.keys(changes).length > 0;
  const backendNote = {
    dpapi: "Saved keys are encrypted and can only be decrypted by your Windows account.",
    file: "Saved keys are encrypted with a key file in recall's data folder that only your user can read.",
    env: "Saved keys are encrypted with RECALL_SECRET_KEY.",
  }[data.key_backend];

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const d = await settingsApi.save(changes);
      setData(d);
      setDraft(draftFrom(d));
      setConfirmReembed(false);
      setNotice(reembed ? `Saved. Re-embedding ${data.commands.toLocaleString()} commands in the background.` : "Saved.");
      setTimeout(() => setNotice(null), 4000);
      onSaved?.();
    } catch (e) {
      setSaveError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4 pb-20">
      {!writable && (
        <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          Settings are read-only because this server listens on the network. Set RECALL_SETTINGS_TOKEN to change them here, or use environment variables.
        </p>
      )}

      <Section title="AI features" hint="recall works without AI too: keyword search needs no model at all.">
        <Toggle checked={draft.llm_enabled} disabled={!writable || v.llm_enabled.source === "env"} onChange={(x) => set({ llm_enabled: x })} label="Use a chat model (descriptions, smart search, explain)" />
        <Toggle checked={draft.describe} disabled={!writable || !draft.llm_enabled || v.describe.source === "env"} onChange={(x) => set({ describe: x })} label="Write a plain-English description of each new command (improves search)" />
      </Section>

      <Section title="Chat model" hint="Writes descriptions, rewrites smart searches and explains commands.">
        <ModelPicker
          kind="chat"
          providers={chatProviders(data.providers)}
          provider={draft.chat_provider}
          model={draft.chat_model}
          onProvider={(id) => set({ chat_provider: id, chat_model: "" })}
          onModel={(m) => set({ chat_model: m })}
          lockedProvider={v.chat_provider.source === "env"}
          lockedModel={v.chat_model.source === "env"}
          disabled={!writable}
        />
        {(chatInfo?.local || chatInfo?.custom) && (
          <Field label="Reasoning effort" locked={v.reasoning_effort.source === "env"}>
            <select className={`${input} max-w-xs`} value={draft.reasoning_effort} disabled={!writable || v.reasoning_effort.source === "env"} onChange={(e) => set({ reasoning_effort: e.target.value })}>
              <option value="none">none (fastest; recommended for thinking models)</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="omit">don't send</option>
            </select>
          </Field>
        )}
      </Section>

      <Section title="Embeddings" hint="Turns commands into vectors for meaning-based search. Anthropic, Groq, DeepSeek and some others don't offer embeddings.">
        <ModelPicker
          kind="embed"
          providers={embedProviders(data.providers)}
          provider={draft.embed_provider}
          model={draft.embed_model}
          onProvider={(id) => set({ embed_provider: id, embed_model: "" })}
          onModel={(m) => set({ embed_model: m })}
          lockedProvider={v.embed_provider.source === "env"}
          lockedModel={v.embed_model.source === "env"}
          disabled={!writable}
        />
        {reembed && (
          <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
            <p>
              Saving re-embeds all {data.commands.toLocaleString()} commands with {embedInfo?.label ?? draft.embed_provider}.
              {isCloud(embedInfo) ? " With a paid provider this costs money." : ""} Your previous index is kept, so switching back is free.
            </p>
            <Toggle checked={confirmReembed} onChange={setConfirmReembed} label="I understand" />
          </div>
        )}
      </Section>

      {cloud.length > 0 && (
        <p className="rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm text-violet-900 dark:border-violet-900 dark:bg-violet-950/30 dark:text-violet-200">
          With {cloud.map((p) => p!.label).join(" and ")}, the text of your commands and their descriptions is sent to {cloud.length > 1 ? "those providers" : "that provider"}. recall
          removes passwords, tokens and keys it recognises before storing a command.
        </p>
      )}

      <Section title="API keys and addresses" hint={backendNote}>
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {shownRows.map((p) => (
            <KeyRow key={p.id} p={p} writable={writable} onChanged={() => load(true)} />
          ))}
        </ul>
        {(hiddenCount > 0 || allProviders) && (
          <button type="button" className="text-xs font-medium text-emerald-700 hover:underline dark:text-emerald-400" onClick={() => setAllProviders(!allProviders)}>
            {allProviders ? "Show only the providers in use" : `Show all providers (${hiddenCount} more)`}
          </button>
        )}
      </Section>

      <CustomEndpoints data={data} writable={writable} onChanged={() => load(true)} />

      {writable && (dirty || notice || saveError) && (
        <div className="fixed inset-x-0 bottom-0 z-10 border-t border-zinc-200 bg-white/95 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/95">
          <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3">
            {saveError ? (
              <span className="text-sm break-words text-red-600 dark:text-red-400">{saveError}</span>
            ) : notice ? (
              <span className="text-sm text-emerald-700 dark:text-emerald-400">{notice}</span>
            ) : (
              <span className="text-sm text-zinc-500">Unsaved changes</span>
            )}
            {dirty && (
              <div className="ml-auto flex gap-2">
                <button type="button" className={btn} onClick={() => setDraft(saved)} disabled={saving}>
                  Discard
                </button>
                <button type="button" className={primary} onClick={save} disabled={saving || (reembed && !confirmReembed)}>
                  {saving ? "Saving…" : "Save"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
