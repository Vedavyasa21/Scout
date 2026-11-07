# Scout

Scout is a secure, extensible command-line personal AI agent for Windows 11 (with Linux/WSL support on the way). You talk to it in natural language; it plans, calls real tools, inspects the results, and reports honestly what happened.

Scout is **not** a chatbot wrapper. Every capability is a registered tool with a declared input schema, permission level and timeout, executed locally through a policy gate. The language model proposes tool calls; Scout validates and authorizes them.

> **Status: working preview.** Available today: CLI and interactive session, Gemini adapter, bounded agent loop, read-only system and file tools, desktop control (windows, keyboard, mouse, screenshots, volume), app launching, voice commands, an approval policy, and a floating icon. File writes/deletes, terminal commands, web search, persistent memory and text-to-speech are **not implemented yet**. See [Roadmap](#roadmap).

## Features

- **Interactive and one-shot CLI** built with Typer and Rich.
- **Gemini provider adapter** behind a small provider interface (other providers can be added without touching the agent).
- **Bounded agent loop** — capped model/tool round trips, no recursion, no unbounded tool calls.
- **Tool registry** — every tool declares name, description, JSON-schema parameters, permission level, timeout and tags. Arguments are validated locally before execution, never trusted from the model.
- **Workspace confinement** — filesystem paths are resolved (including symlinks and `..`) and must live inside configured workspaces.
- **Desktop control** — list/focus/minimize windows, type text, press hotkeys, click, scroll, take screenshots, change volume and open URLs.
- **Application launching** — a small allowlist for routine apps, plus any installed Start Menu app behind an explicit confirmation.
- **Approval policy** — `READ`/`LOW_RISK` run automatically; `WRITE`/`EXECUTE`/`HIGH_RISK` show the exact action and wait for approval. High-risk actions require typing `allow`. Non-interactive runs deny.
- **Voice commands** — push-to-talk recording with silence detection, transcribed by Gemini and run through the same agent.
- **Floating icon** — a draggable, always-on-top orb at medium opacity: click to talk, right-click for a typed prompt.
- **Fast defaults** — `gemini-3.5-flash-lite` with minimal thinking, around 1.6s per model turn.
- **Structured logging** to a rotating log file, with secret redaction.
- **Doctor command** to check your environment.
- **Tests** covering tool registration, argument validation, path traversal, workspace restriction, allowlisting, timeout/limit behaviour and provider parsing.

## Requirements

- Python 3.11 or newer (3.13 recommended)
- Windows 11 (primary) or Linux
- A Gemini API key

## Installation

```bash
git clone https://github.com/<your-username>/scout.git
cd scout

python -m venv .venv
# Windows (PowerShell / Git Bash)
.venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1
# Linux / WSL
source .venv/bin/activate

pip install -e ".[dev]"
```

## Configuration

Copy the example file and add your key:

```bash
cp .env.example .env
```

```dotenv
SCOUT_PROVIDER=gemini
SCOUT_API_KEY=your-key-here
SCOUT_MODEL=gemini-3.5-flash-lite
SCOUT_THINKING_LEVEL=minimal
SCOUT_WORKSPACES=C:\Users\you\Projects,C:\Users\you\Documents
```

`.env` is git-ignored. Keys are never logged, printed in full, stored in the database or sent anywhere except the configured provider.

Verify the setup:

```bash
scout doctor
```

### Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCOUT_PROVIDER` | `gemini` | Provider adapter to use |
| `SCOUT_API_KEY` | — | Provider API key (required at runtime) |
| `SCOUT_MODEL` | `gemini-3.5-flash-lite` | Model name (function calling; audio input for voice) |
| `SCOUT_THINKING_LEVEL` | `minimal` | `minimal`/`low`/`medium`/`high`, or empty |
| `SCOUT_THINKING_BUDGET` | `-1` | Numeric thinking budget; overrides the level when >= 0 |
| `SCOUT_VOICE_MAX_SECONDS` | `15` | Max length of one spoken request |
| `SCOUT_VOICE_SILENCE_SECONDS` | `1.2` | Silence that ends a recording |
| `SCOUT_OVERLAY_OPACITY` | `0.65` | Floating icon opacity |
| `SCOUT_MAX_TOOL_STEPS` | `8` | Max model↔tool round trips per request |
| `SCOUT_TOOL_TIMEOUT` | `60` | Per-tool timeout (seconds) |
| `SCOUT_REQUEST_TIMEOUT` | `120` | Provider request timeout (seconds) |
| `SCOUT_MAX_RETRIES` | `2` | Retries for transient provider failures |
| `SCOUT_WORKSPACES` | user home | Comma-separated allowed directories |
| `SCOUT_ALLOWED_APPS` | safe default set | Comma-separated app names or absolute paths |
| `SCOUT_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `SCOUT_LOG_FILE` | platform log dir | Log file path |

## Usage

```bash
scout                                  # interactive session
scout ask "Check my system performance"
scout ask "Find my gem5 project"
scout ask "Open VS Code"
scout voice                            # push-to-talk loop
scout overlay                          # floating icon (click to talk)
scout tools                            # list registered tools
scout status                           # provider, model, workspaces
scout config                           # resolved config (key masked)
scout doctor                           # environment diagnostics
scout doctor --check-api               # also verify the provider connection
```

Interactive commands: `/exit`, `/clear`, `/tools`, `/help`.

### Voice and the floating icon

```bash
scout voice      # terminal loop: speak, Scout transcribes and runs it
scout overlay    # small always-on-top orb (65% opacity by default)
```

- **Click** the orb to talk; it stops after a pause and shows the answer in a bubble.
- **Drag** it anywhere; the position is remembered.
- **Right-click** for a typed prompt or to quit.
- Actions that change the machine open a confirmation dialog; high-risk actions ask you to type `allow`.
- Say "exit", "quit" or "stop listening" to end `scout voice`.
- Microphone access is required. If capture fails, Scout shows the reason instead of guessing.
- Change the icon opacity with `SCOUT_OVERLAY_OPACITY` (0.3–1.0).

Example session:

```text
$ scout ask "Find my research folder and list its contents"

 scouting  gemini-3.5-flash-lite
  step 1  search_files(query='research', include_directories=True)
  ok      1 match in 1.2s
  step 2  list_directory(path='C:\Users\you\Documents\research')
  ok      6 entries in 0.1s

The research folder is C:\Users\you\Documents\research and contains:
- papers/ (directory)
- results/ (directory)
- notes.md (12.4 KB)
...
```

## Safety model

| Level | Meaning | Current policy |
| --- | --- | --- |
| `READ` | Inspect information | Allowed; file reads stay inside workspaces |
| `LOW_RISK` | Allowlisted apps, screenshots, volume, URLs | Allowed |
| `WRITE` | Create or modify files | No write tools yet |
| `EXECUTE` | Launch any app, type, click, window changes | Confirmation required |
| `HIGH_RISK` | Screen lock and similar | Type `allow` to confirm |

Guarantees in this build:

- No `eval`/`exec` of model output; no shell strings.
- Subprocesses use argument arrays with `shell=False`.
- Filesystem access is resolved and confined to approved workspaces.
- Routine launches are restricted to an explicit allowlist; arbitrary app launches and all desktop input require approval.
- Approval panels show the tool, permission level and exact arguments; denial is final for that call.
- In a non-interactive process every approval request is denied.
- The agent only reports success when a tool returned a successful result.

## Architecture

```text
src/scout/
├── cli/           Typer commands + Rich rendering
├── agent/         Bounded loop, turn types, system prompt
├── providers/     Provider interface + Gemini adapter
├── tools/         Registry, validation, system/filesystem/apps/desktop
├── security/      Workspace checks, permission policy
├── voice/         Recording and transcription
├── desktop/       Floating icon overlay
├── config/        Pydantic settings
├── logging/       Structured logging + redaction
└── main.py        Entry point
```

The agent never talks to a vendor SDK directly: it consumes a `ModelProvider` and a `ToolRegistry`. Provider-specific tool-call formats are translated inside the adapter.

See [docs/architecture.md](docs/architecture.md) for the design details.

## Development

```bash
pytest              # tests
ruff check .        # lint
ruff format .       # format
```

## Known limitations

- Gemini is the only provider adapter.
- No persistent conversation memory yet (the overlay keeps history only while open).
- No file writes/deletes, terminal/shell, or web page reading. `open_url` only opens the browser.
- Typing is ASCII-only; non-ASCII text is rejected with an explanation.
- Voice capture was tested with synthesized audio, not on every microphone; if none is available Scout reports it.
- Tool timeouts cannot kill an already-running thread; they only stop Scout waiting.
- Desktop automation acts on the focused window; check before typing or pressing keys.
- Windows-focused; Linux support is structural and GUI/voice tools need extra setup there.

## Roadmap

1. **Milestone 1** — CLI, config, provider, bounded loop, registry, read-only tools. ✅
2. **Milestone 2** — approval policy and interactive confirmations ✅; SQLite memory, audit trail and plans still pending.
3. **Milestone 3** — filesystem writes, protected command execution, developer/Git tools.
4. **Milestone 4** — browser automation, web search, document processing.
5. **Milestone 5** — voice input and floating icon ✅; spoken replies and wake word pending.
6. **Milestone 6** — plugin SDK, packaging, public release.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
