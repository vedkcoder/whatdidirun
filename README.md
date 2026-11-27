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

WhatdidIRun?? records every command you run in bash, along with its working directory, exit code, duration, git repo and branch. It asks a local LLM to write a one-line description of each command, then indexes everything for both keyword and semantic search. You can find commands from the CLI, from an inline picker (**Ctrl-G**), or from a web UI.

The package is called `whatdidirun`; the command you type is `recall`.

Everything stays on your machine. Commands are stored in SQLite, keyword search uses SQLite FTS5 (BM25), vectors are stored in ChromaDB, and embeddings and descriptions come from your own [LM Studio](https://lmstudio.ai) server or any other OpenAI-compatible server.

## Features

- **Natural-language search** that combines BM25 and vector results with Reciprocal Rank Fusion, then boosts recent and frequently used commands.
- **Query understanding:** `today`, `yesterday`, `last week`, `last 3 days`, `2 weeks ago`, `this month`, `on tuesday`, `since/before <date>`, `failed` / `succeeded`, `in <repo or dir>`.
- **Smart mode** (`--smart`): the LLM expands your query and reranks the results.
- **Three ways to search:** a CLI, a terminal picker on **Ctrl-G** that puts the chosen command on your prompt, and a React web UI with filters, run history, explanations and stats.
- **`recall explain <cmd>`** asks the model what a command does.
- **Secret redaction:** API keys, tokens, `--password` values, URL credentials and auth headers are masked before anything is stored or sent to a model.
- **Fast capture:** the shell hook is pure bash and takes under 1 ms per prompt. Commands typed with a **leading space** are never recorded.
- **Works without the LLM:** if the model server is down, capture and keyword search keep working. Commands that missed description or embedding are filled in on the next ingest.

## How it works

```
bash (preexec/precmd hook) ──► spool.jsonl ──► recall ingest ──► SQLite (runs + FTS5/BM25)
   pure bash, <1ms                                 │  redact secrets, git repo/branch
                                                   ├─► LLM: one-line description per unique command
                                                   └─► embeddings ──► ChromaDB
search: query parser (time / failed / repo) ─► BM25 + vector ─► Reciprocal Rank Fusion ─► recency/frequency boost
        optional --smart: LLM query expansion + rerank
```

**Why descriptions?** Embedding models don't understand terse shell syntax. On its own, `df -h` ranks below `ls -lS` for "free disk space". So recall asks the chat model for a short description of each unique command once, at ingest time, and indexes that description for both BM25 and vector search. Searches never wait on the LLM unless you pass `--smart`.

Capture uses a vendored copy of [bash-preexec](https://github.com/rcaloras/bash-preexec) 0.5.0.

## Requirements

- Linux or macOS with **bash** (zsh and fish are not supported yet)
- **Python 3.12 or 3.13** and [uv](https://docs.astral.sh/uv/), or Docker
- **Node.js 20+**, only to build the web UI from source
- An OpenAI-compatible model server. [LM Studio](https://lmstudio.ai) is the tested setup:
  - an embedding model, by default `text-embedding-nomic-embed-text-v1.5`
  - any chat model for descriptions, explanations and `--smart`. Small models work well.

## Installation

### Option A: native install (recommended)

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
| `recall import [file]` | Import an existing bash history file (safe to run more than once) |
| `recall reindex` | Re-embed everything, e.g. after changing the embedding model |
| `recall stats` | Counts, failures, pending work, top programs |
| `recall doctor` | Diagnose configuration and model-server connectivity |

## Configuration

Settings come from `~/.config/recall/config.toml`, or the file named by `RECALL_CONFIG`. Environment variables override that file. Config file keys are the variable names in lower case without the `RECALL_` prefix.

| Variable | Default | |
|---|---|---|
| `RECALL_LLM_BASE_URL` | `http://localhost:1234/v1` | OpenAI-compatible server (`/v1` is added if missing) |
| `RECALL_LLM_API_KEY` | `lm-studio` | Sent as the bearer token |
| `RECALL_EMBED_MODEL` | `text-embedding-nomic-embed-text-v1.5` | Run `recall reindex` after changing it |
| `RECALL_CHAT_MODEL` | first non-embedding model loaded | |
| `RECALL_REASONING_EFFORT` | `none` | Stops thinking models from spending most of each call on reasoning. Set it to an empty value to leave the parameter out. |
| `RECALL_LLM_TIMEOUT` | `60` | Seconds |
| `RECALL_LLM_ENABLED` | `1` | `0` never calls the chat model (embeddings only) |
| `RECALL_DESCRIBE` | `1` | Write LLM descriptions at ingest |
| `RECALL_DATA_DIR` | `~/.local/share/recall` | SQLite DB, Chroma directory, spool |
| `RECALL_WEB_HOST` / `RECALL_WEB_PORT` | `127.0.0.1` / `8765` | Web UI bind address |
| `RECALL_KEY` | `\C-g` | Picker key binding (shell only) |
| `RECALL_AUTO_INGEST` / `RECALL_INGEST_EVERY` | `1` / `25` | Background ingest on shell start / every N commands (shell only) |

`ignore_patterns`, a list of regexes, can only be set in the config file. Commands that match any of them are never stored:

```toml
# ~/.config/recall/config.toml
llm_base_url = "http://localhost:1234"
ignore_patterns = ['^\s*$', '^(ls|cd|pwd|clear|exit|history)\s*$', '^vault ']
```

## Privacy

- Nothing leaves your machine except calls to the model server you configure. If that server is remote, the redacted command text is sent to it.
- Redaction is best-effort pattern matching. To keep a command out of recall entirely, start it with a space.
- To delete everything, remove `~/.local/share/recall` (or your `RECALL_DATA_DIR`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `LM Studio unreachable … keyword results only` | Start the server and load both models, then run `recall doctor`. From Docker, enable "Serve on Local Network". |
| `embed model … ✗ not loaded` | Load the model in LM Studio or set `RECALL_EMBED_MODEL` to one that is loaded |
| Each chat call takes about 50 s | Your model is a thinking model; keep `RECALL_REASONING_EFFORT=none` |
| New commands don't show up | Run `recall ingest`, or wait. The shell hook ingests every 25 commands, and the web UI ingests every minute. |
| `shell hook: no commands captured yet` | Make sure the `source …/recall.bash` line is in `~/.bashrc`, then open a new interactive shell |
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
  capture/      bash hook + vendored bash-preexec
  cli.py        Typer CLI
  ingest.py     spool → SQLite, git info, describe + embed
  search.py     hybrid search, RRF, boosts, smart mode
  query_parse.py  time / status / repo extraction from queries
  redact.py     secret masking
  vectors.py    ChromaDB index
  lmstudio.py   OpenAI-compatible client
  tui/          Textual picker
  web/          FastAPI backend (serves the built UI from web/dist)
web/            React + Vite + Tailwind frontend
tests/
```

## Releasing

Releases are automated by [`.github/workflows/release.yml`](.github/workflows/release.yml):

```bash
# 1. bump `version` in pyproject.toml, then refresh the lockfile
uv lock
git commit -am "Release 0.2.0"
# 2. tag and push. The tag must match the pyproject version.
git tag -a v0.2.0 -m "WhatdidIRun?? 0.2.0"
git push origin main v0.2.0
```

The workflow runs the tests, builds the wheel and sdist with the web UI bundled, and pushes a multi-arch image to `ghcr.io/vedkcoder/whatdidirun` tagged `0.2.0`, `0.2` and `latest`. It then creates a GitHub Release with generated notes. To release an existing tag again, open **Actions → Release → Run workflow** and enter the tag.

## Contributing

Issues and pull requests are welcome. Please run `pytest` and `npm test` before opening a PR. For larger changes, such as support for another shell, open an issue first so we can agree on the approach.

## License

[MIT](LICENSE). The vendored `bash-preexec.sh` is MIT-licensed by its authors; its notice is kept in the file.
