# What did I Run ??

**Search your shell history in plain English, with every model running locally.**

```console
$ recall search "docker build that failed in project1 last week"
 1 ✗ docker build -t api:dev .
      6d ago · ~/code/project1 · 3×

$ recall search "how much free disk space"
 1 ✓ df -h
      2h ago · ~ · 41×
```

What did I Run ?? records every command you run in bash or PowerShell, along with its working directory, exit code, duration, git repo and branch. It asks an LLM, local by default, to write a one-line description of each command, then indexes everything for both keyword and semantic search. You can find commands from the CLI, from an inline picker (**Ctrl-G**), or from a web UI.

The package is called `whatdidirun`; the command you type is `recall`.

By default everything stays on your machine. Commands are stored in SQLite, keyword search uses SQLite FTS5 (BM25), and vectors are stored in ChromaDB. Embeddings and descriptions come from your own [LM Studio](https://lmstudio.ai) or Ollama server, or from a cloud provider you choose (see [Providers](#providers)).

## Features

- **Natural-language search** that combines BM25 and vector results with Reciprocal Rank Fusion, then boosts recent and frequently used commands.
- **Query understanding:** `today`, `yesterday`, `last week`, `last 3 days`, `2 weeks ago`, `this month`, `on tuesday`, `since/before <date>`, `failed` / `succeeded`, `in <repo or dir>`.
- **Smart mode** (`--smart`): the LLM expands your query and reranks the results.
- **Three ways to search:** a CLI, a terminal picker on **Ctrl-G** that puts the chosen command on your prompt, and a React web UI with filters, run history, explanations and stats.
- **`recall explain <cmd>`** asks the model what a command does.
- **Secret redaction:** API keys, tokens, `--password` values, URL credentials and auth headers are masked before anything is stored or sent to a model.
- **Fast capture:** the bash hook is pure bash and takes under 1 ms per prompt; the PowerShell hook wraps your prompt. Commands typed with a **leading space** are never recorded.
- **Windows app:** a one-click installer with a tray icon, PowerShell integration and Ctrl-G.
- **Your choice of AI provider:** local LM Studio or Ollama, or OpenAI, Anthropic (Claude), Gemini, OpenRouter, Groq, Mistral, DeepSeek, xAI, Together, or any OpenAI-compatible server. Chat and embeddings can use different providers. You manage providers, models and encrypted API keys on the Settings page.
- **Works without the LLM:** if the model server is down, capture and keyword search keep working. Commands that missed description or embedding are filled in on the next ingest.

## How it works

```
bash / PowerShell hook ──────► spool.jsonl ──► recall ingest ──► SQLite (runs + FTS5/BM25)
                                                   │  redact secrets, git repo/branch
                                                   ├─► LLM: one-line description per unique command
                                                   └─► embeddings ──► ChromaDB
search: query parser (time / failed / repo) ─► BM25 + vector ─► Reciprocal Rank Fusion ─► recency/frequency boost
        optional --smart: LLM query expansion + rerank
```

**Why descriptions?** Embedding models don't understand terse shell syntax. On its own, `df -h` ranks below `ls -lS` for "free disk space". So recall asks the chat model for a short description of each unique command once, at ingest time, and indexes that description for both BM25 and vector search. Searches never wait on the LLM unless you pass `--smart`.

Capture uses a vendored copy of [bash-preexec](https://github.com/rcaloras/bash-preexec) 0.5.0.

## Requirements

- **Linux or macOS with bash**, or **Windows 10/11 with PowerShell** (5.1 or 7). zsh and fish are not supported yet.
- **Python 3.12 or 3.13** and [uv](https://docs.astral.sh/uv/), or Docker
- **Node.js 20+**, only to build the web UI from source
- An OpenAI-compatible model server. [LM Studio](https://lmstudio.ai) is the tested setup:
  - an embedding model, by default `text-embedding-nomic-embed-text-v1.5`
  - any chat model for descriptions, explanations and `--smart`. Small models work well.

## Installation

### Windows: installer

1. Download `WhatDidIRun-<version>-setup.exe` from the [latest release](https://github.com/vedkcoder/whatdidirun/releases/latest) and run it. It installs for your user only and needs no admin rights.
   The installer isn't code-signed yet, so Windows SmartScreen may warn you. Click **More info**, then **Run anyway**.
2. Keep both options ticked:
   - **Record commands in PowerShell and bind Ctrl-G**, which adds a marked block to your PowerShell profile(s)
   - **Start in the system tray when I sign in**
3. Choose where the AI comes from: right-click the tray icon, then **Settings…**.
   - **Local (default):** install [LM Studio](https://lmstudio.ai) or [Ollama](https://ollama.com), load an embedding model and a small chat model, and start its server.
   - **Cloud:** pick a provider such as OpenAI, Anthropic, Gemini, OpenRouter or Groq, paste its API key and press **Test**. Claude and Groq have no embeddings, so pair them with an embedding provider such as OpenAI, Gemini, Mistral or a local one.
4. Open a **new** PowerShell window and check the setup:

```powershell
recall doctor                       # checks both providers, the models and the shell hook
recall import                       # optional: backfill your PSReadLine history
```

The tray icon (**What did I Run ??**) runs the web UI at http://127.0.0.1:8765 and indexes new commands every minute. Right-click it for **Open**, **Settings…**, **Index now**, **Start at login** and **Quit**. In PowerShell, press **Ctrl-G** to search; the chosen command lands on your prompt.

On Windows, data lives in `%LOCALAPPDATA%\recall` and the config file is `%APPDATA%\recall\config.toml`. Uninstalling (Settings → Apps) removes the app, the PATH entry, the profile block and the login item, but keeps your data. If you use a prompt theme like oh-my-posh or starship, make sure it is set up *before* the recall block in your profile; the installer appends the block at the end.

To set up a PowerShell hook by hand, for example on Linux or macOS or without the installer, run `recall init powershell --install`, or `recall init powershell` to print the line to add to `$PROFILE`.

### Option A: native install (recommended on Linux/macOS)

A native install gives you everything, including the Ctrl-G picker.

```bash
git clone https://github.com/vedkcoder/whatdidirun.git
cd whatdidirun

# Build the web UI into src/recall/web/dist. This must happen before installing.
(cd web && npm ci && npm run build)

uv tool install .        # puts `recall` on your PATH
```

Add the hook to `~/.bashrc`:

```bash
export RECALL_LLM_BASE_URL=http://localhost:1234   # your LM Studio server
eval "$(recall init bash | tail -1)"               # or paste the `source …/recall.bash` line it prints
```

Open a new shell, then:

```bash
recall doctor                     # checks the server, models, embedding dimension and chat latency
recall import ~/.bash_history     # optional: backfill your existing history
```

### Option B: Docker Compose

The container runs the web UI plus a background indexer that drains the spool, describes new commands and embeds them every minute. **Capture still happens in your host shell**, because the hook has to run inside bash. The host shell and the container share the data directory through a bind mount.

```bash
git clone https://github.com/vedkcoder/whatdidirun.git
cd whatdidirun
cp .env.example .env              # set RECALL_UID/RECALL_GID to `id -u`/`id -g`
docker compose pull                # prebuilt image (amd64 + arm64) from ghcr.io
docker compose up -d               # or: docker compose up -d --build, to build from source
```

Then add the hook to `~/.bashrc`. With Docker, only the hook is needed on the host:

```bash
source /path/to/whatdidirun/src/recall/capture/recall.bash
```

Open http://127.0.0.1:8765. To run one-off CLI commands against the same data:

```bash
docker compose run --rm whatdidirun doctor
docker compose run --rm whatdidirun search "docker build that failed"
docker compose run --rm whatdidirun import /data/bash_history   # copy the file into the data dir first
```

Docker notes:

- **Reaching LM Studio.** The default URL is `http://host.docker.internal:1234/v1`, which points at the Docker host. In LM Studio, turn on **"Serve on Local Network"**; otherwise LM Studio only listens on the host's loopback interface, which the container can't reach. If your model server runs on another machine, set `RECALL_LLM_BASE_URL` in `.env`.
- **Git repo detection.** The compose file mounts your home directory **read-only** at the same path. That lets the indexer read `.git/HEAD` for each command's working directory. Remove that volume if you don't want it. Repo filters will then be empty for commands the container indexes.
- **No authentication.** The web UI has no login, so the port is published on `127.0.0.1` only. Put a reverse proxy with authentication in front of it before exposing it further.
- **Ctrl-G needs a native install.** The picker runs `recall tui` in your shell, so it needs a native `recall` on your PATH. You can run a native install and the container side by side on the same data directory.

## Usage

| Command | |
|---|---|
| **Ctrl-G** in bash | Picker pre-filled with the current line; Enter puts the chosen command on your prompt |
| `recall search <words> [-n 10] [--smart] [--failed] [--repo X] [--json] [-v]` | One-shot search (`-v` shows how the query was interpreted) |
| `recall tui` | Picker: ↑↓ move, Enter choose, Ctrl-S smart mode, Ctrl-T explain, Esc cancel |
| `recall web [--open] [--host H] [--port P]` | Web UI at http://127.0.0.1:8765: search, filters, run history, explain, stats |
| `recall explain <cmd>` | Explain a command with the chat model |
| `recall ingest` | Drain the spool, then describe and embed new commands. Also runs on shell start and every 25 commands. |
| `recall import [file] [--format bash\|psreadline]` | Import an existing bash or PowerShell (PSReadLine) history file (safe to run more than once) |
| `recall init bash\|powershell [--install\|--uninstall]` | Print or install the shell hook |
| `recall tray` | System-tray app: web UI + background indexer (Windows app; `pip`/`uv` users need the `tray` extra) |
| `recall reindex` | Re-embed everything, e.g. after changing the embedding model |
| `recall stats` | Counts, failures, pending work, top programs |
| `recall doctor` | Diagnose configuration and model-server connectivity |

## Providers

recall uses two kinds of model, and they can come from different providers:
- a **chat model**, which writes command descriptions, rewrites smart searches and explains commands
- an **embedding model**, which powers meaning-based search

LM Studio is the default for both, so nothing leaves your machine unless you choose a cloud provider.

| Provider | Id | Chat | Embeddings | API key variable |
|---|---|---|---|---|
| LM Studio | `lmstudio` | ✓ | ✓ | not needed |
| Ollama | `ollama` | ✓ | ✓ | not needed |
| OpenAI | `openai` | ✓ | ✓ | `OPENAI_API_KEY` |
| Anthropic (Claude) | `anthropic` | ✓ | | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini` | ✓ | ✓ | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| OpenRouter | `openrouter` | ✓ | | `OPENROUTER_API_KEY` |
| Groq | `groq` | ✓ | | `GROQ_API_KEY` |
| Mistral | `mistral` | ✓ | ✓ | `MISTRAL_API_KEY` |
| DeepSeek | `deepseek` | ✓ | | `DEEPSEEK_API_KEY` |
| xAI (Grok) | `xai` | ✓ | | `XAI_API_KEY` |
| Together AI | `together` | ✓ | ✓ | `TOGETHER_API_KEY` |
| Any OpenAI-compatible server | `custom` | ✓ | ✓ | `RECALL_CUSTOM_API_KEY` (optional) |

For example, to use Claude for chat and keep embeddings local in Ollama:

```bash
export RECALL_CHAT_PROVIDER=anthropic RECALL_CHAT_MODEL=<model id> ANTHROPIC_API_KEY=sk-ant-...
export RECALL_EMBED_PROVIDER=ollama
recall doctor      # checks both providers
recall providers   # lists providers and which keys are set
```

You can also save these choices instead of exporting variables. Open **Settings** in the web UI (the tray menu has a shortcut) to pick providers and models, test them and manage API keys and custom endpoints. These commands change the same saved settings:

```bash
recall config set chat_provider anthropic
recall config set chat_model <model id>
recall keys set anthropic        # prompts for the key, so it stays out of your shell history
recall config show               # every setting and where its value comes from
```

Saved settings beat `config.toml`, and environment variables beat both.

**How API keys are stored.** Saved keys are encrypted (AES-256-GCM) in recall's database and are only shown in full through Settings → Reveal.
- **On Windows**, the encryption key is protected with DPAPI, so only your Windows account can decrypt the keys.
- **On Linux and macOS**, the encryption key is a file in the data directory that only you can read (`secret.key`, mode 0600). Anyone who can read your files can therefore decrypt the keys.
- **In Docker**, set `RECALL_SECRET_KEY` to a passphrase so the encryption key isn't stored next to the data.

**The Settings page only accepts changes from this computer.** The server refuses settings requests from other websites and from other hosts. If it listens on the network (`RECALL_WEB_HOST=0.0.0.0`, as in Docker), settings are read-only unless you set `RECALL_SETTINGS_TOKEN`. The Settings page then asks for that token.

With a cloud provider, the text of your commands and their descriptions is sent to that provider. recall removes passwords, tokens and keys it recognises before storing a command. Changing the embedding provider or model re-embeds your whole history, which costs money with paid providers.

## Configuration

Settings come from `~/.config/recall/config.toml` (Windows: `%APPDATA%\recall\config.toml`), or the file named by `RECALL_CONFIG`. Environment variables override that file. Config file keys are the variable names in lower case without the `RECALL_` prefix.

| Variable | Default | |
|---|---|---|
| `RECALL_CHAT_PROVIDER` | `lmstudio` | Provider for descriptions, smart search and explain. See [Providers](#providers). |
| `RECALL_EMBED_PROVIDER` | `lmstudio` | Provider for embeddings. Changing it re-embeds your whole history. |
| `RECALL_CHAT_MODEL` | first non-embedding model (local providers) | Required for cloud providers |
| `RECALL_EMBED_MODEL` | the provider's default embedding model | Changing it re-embeds your whole history |
| `RECALL_<PROVIDER>_API_KEY` | | API key, e.g. `RECALL_OPENAI_API_KEY`. The provider's usual variable, e.g. `OPENAI_API_KEY`, also works. |
| `RECALL_<PROVIDER>_BASE_URL` | the provider's API | e.g. `RECALL_OLLAMA_BASE_URL`, or `RECALL_CUSTOM_BASE_URL` for your own OpenAI-compatible server |
| `RECALL_LLM_BASE_URL` / `RECALL_LLM_API_KEY` | `http://localhost:1234/v1` / `lm-studio` | LM Studio's address and key (from 0.1; the same as `RECALL_LMSTUDIO_*`) |
| `RECALL_REASONING_EFFORT` | `none` | Stops thinking models from spending most of each call on reasoning. Set it to an empty value to leave the parameter out. |
| `RECALL_LLM_TIMEOUT` | `60` | Seconds |
| `RECALL_LLM_ENABLED` | `1` | `0` never calls the chat model (embeddings only) |
| `RECALL_DESCRIBE` | `1` | Write LLM descriptions at ingest |
| `RECALL_SECRET_KEY` | | Passphrase for encrypting saved API keys, instead of a key file (useful in Docker) |
| `RECALL_SETTINGS_TOKEN` | | Lets the Settings page make changes when the server listens on the network |
| `RECALL_DATA_DIR` | `~/.local/share/recall` (Windows: `%LOCALAPPDATA%\recall`) | SQLite DB, Chroma directory, spool |
| `RECALL_WEB_HOST` / `RECALL_WEB_PORT` | `127.0.0.1` / `8765` | Web UI bind address |
| `RECALL_KEY` | `\C-g` | Picker key binding (shell only) |
| `RECALL_AUTO_INGEST` / `RECALL_INGEST_EVERY` | `1` / `25` | Background ingest on shell start / every N commands (shell only) |

`ignore_patterns`, a list of regexes, can only be set in the config file. Commands that match any of them are never stored:

```toml
# ~/.config/recall/config.toml
chat_provider = "anthropic"
chat_model = "<a model from `recall doctor` or the provider's docs>"
embed_provider = "ollama"
ignore_patterns = ['^\s*$', '^(ls|cd|pwd|clear|exit|history)\s*$', '^vault ']

[providers.ollama]
base_url = "http://gpu-box:11434"
```

API keys are best kept out of this file: use the environment variables above.

## Privacy

- Nothing leaves your machine except calls to the model server you configure. If that server is remote, the redacted command text is sent to it.
- Redaction is best-effort pattern matching. To keep a command out of recall entirely, start it with a space.
- To delete everything, remove `~/.local/share/recall` on Linux/macOS or `%LOCALAPPDATA%\recall` on Windows (or your `RECALL_DATA_DIR`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `… unreachable; keyword results only` | Start the embedding provider (for LM Studio, load both models), then run `recall doctor`. From Docker, enable "Serve on Local Network" in LM Studio. |
| `embed model ? not listed` | Load the model in LM Studio or Ollama, or set `RECALL_EMBED_MODEL` to one the provider offers |
| `No API key for …` / `… rejected the API key` | Set the provider's key variable (see [Providers](#providers)); `recall providers` shows which keys are set |
| `Choose a chat model for …` | Cloud providers need `RECALL_CHAT_MODEL`; their docs or `/models` endpoint list the ids |
| Each chat call takes about 50 s | Your model is a thinking model; keep `RECALL_REASONING_EFFORT=none` |
| New commands don't show up | Run `recall ingest`, or wait. The shell hook ingests every 25 commands, and the web UI ingests every minute. |
| `shell hook: no commands captured yet` | bash: make sure the `source …/recall.bash` line is in `~/.bashrc`. PowerShell: run `recall init powershell --install`. Then open a new shell. |
| Windows: `recall` is not recognized | Open a new terminal after installing (PATH changes only reach new windows) |
| Windows: running scripts is disabled | Allow your profile to run: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Docker: permission errors in the data dir | Set `RECALL_UID`/`RECALL_GID` in `.env` to your `id -u`/`id -g` |

## Development

```bash
uv sync
.venv/bin/pytest                  # backend tests (the model server is mocked)
cd web && npm ci
npm run dev                       # Vite dev server; proxies /api to `recall web` on :8765
npm test                          # Vitest
```

Project layout:

```
src/recall/
  capture/      bash + PowerShell hooks, vendored bash-preexec
  shells.py     hook paths, PowerShell profile install/uninstall
  tray.py       system-tray app (Windows)
  cli.py        Typer CLI
  ingest.py     spool → SQLite, git info, describe + embed
  search.py     hybrid search, RRF, boosts, smart mode
  query_parse.py  time / status / repo extraction from queries
  redact.py     secret masking
  vectors.py    ChromaDB index
  llm.py        chat + embedding client used everywhere
    providers/    adapters (OpenAI-compatible, Anthropic, Gemini) and the provider catalog
  tui/          Textual picker
  web/          FastAPI backend (serves the built UI from web/dist)
web/            React + Vite + Tailwind frontend
packaging/      Windows app: PyInstaller spec, Inno Setup script, icon generator
tests/
```

## Releasing

Releases are automated by [`.github/workflows/release.yml`](.github/workflows/release.yml):

```bash
# 1. bump `version` in pyproject.toml, then refresh the lockfile
uv lock
git commit -am "Release 0.2.0"
# 2. tag and push. The tag must match the pyproject version.
git tag -a v0.2.0 -m "v0.2.0"
git push origin main v0.2.0
```

The workflow runs the tests, builds the wheel and sdist with the web UI bundled, builds the Windows installer, and pushes a multi-arch image to `ghcr.io/vedkcoder/whatdidirun` tagged `0.2.0`, `0.2` and `latest`. It then creates a GitHub Release with generated notes and attaches the wheel, sdist and `setup.exe`. Pull requests run the same tests on Linux and Windows and upload a test installer as the `windows-installer` artifact. To release an existing tag again, open **Actions → Release → Run workflow** and enter the tag.

## Contributing

Issues and pull requests are welcome. Please run `pytest` and `npm test` before opening a PR. For larger changes, such as support for another shell, open an issue first so we can agree on the approach.

## License

[MIT](LICENSE). The vendored `bash-preexec.sh` is MIT-licensed by its authors; its notice is kept in the file.
