# Scout

Scout is a secure, extensible command-line personal AI agent for Windows 11 (with Linux/WSL support on the way). You talk to it in natural language; it plans, calls real tools, inspects the results, and reports honestly what happened.

Scout is **not** a chatbot wrapper. Every capability is a registered tool with a declared input schema, permission level and timeout, executed locally through a policy gate. The language model proposes tool calls; Scout validates and authorizes them.

> **Status: working preview.** Available today: CLI and interactive session, Gemini adapter with token streaming, async event-driven agent loop with cancellation, bounded tool execution, model-driven plans with a verification pass, read-only system and file tools, desktop control (windows, keyboard, mouse, screenshots, volume), screen perception (active window, region capture and local OCR), app launching, voice commands, secure approvals, a durable audit ledger, a desktop character, and a floating icon. File writes/deletes, terminal commands, page-reading web search, persistent conversation memory and text-to-speech are **not implemented yet**. See [Roadmap](#roadmap).

## Features

- **Interactive and one-shot CLI** built with Typer and Rich.
- **Gemini provider adapter** behind a small provider interface (other providers can be added without touching the agent).
- **Async, event-driven core** — each request runs as an asyncio task that emits structured events (thinking, model deltas, tool started/completed, approval required, success, error, cancelled). `Agent.run()` remains a synchronous facade for existing callers.
- **Token streaming** — answers appear in the terminal as the model writes them; piped output and tests keep the single final panel.
- **Cancellation and emergency stop** — a thread-safe token stops a run between model chunks and tool calls, emitting `agent_cancelled` instead of pretending the task finished. Stop is available from the overlay pill/HUD, tray, character menu, approval dialogs, and Ctrl+C in the CLI.
- **Planner and verification** — for multi-step work the model declares a short plan with `update_plan` and keeps each step's status honest. Before accepting a final answer with unfinished or failed steps, Scout runs one verification pass that asks the model to reconcile the plan with real tool results.
- **Bounded agent loop** — capped model/tool round trips, no recursion, no unbounded tool calls.
- **Tool registry** — every tool declares name, description, JSON-schema parameters, permission level, timeout and tags. Arguments are validated locally before execution, never trusted from the model.
- **Workspace confinement** — filesystem paths are resolved (including symlinks and `..`) and must live inside configured workspaces.
- **Desktop control** — list/focus/minimize windows, type text, press hotkeys, click, scroll, take screenshots, change volume and open URLs.
- **Screen perception** — inspect the focused window's title, process, pid and bounds; capture the full screen or a rectangular region; read visible text with the local Windows OCR engine. Sensitive focused windows are refused and sensitive background windows are blacked out before an image is saved.
- **Application launching** — a small allowlist for routine apps, plus any installed Start Menu app behind an explicit confirmation.
- **Approval policy, secure by default** — `READ`/`LOW_RISK` run automatically; `WRITE`/`EXECUTE`/`HIGH_RISK` show the exact action and wait for approval. High-risk actions require typing `allow`. Non-interactive runs deny. `SCOUT_FULL_ACCESS` is opt-in, never the default. Hard denies (`SCOUT_DENIED_TOOLS`) cannot be overridden by full access, and `SCOUT_AUTO_APPROVE_TOOLS` skips prompts only for explicitly trusted tools.
- **Durable audit ledger** — every run, approval request/decision, denial and tool result is appended to a redacted JSONL ledger. Inspect it with `scout audit`.
- **Privacy controls** — screenshots are refused when the focused window looks sensitive and old captures are pruned; ambient wake-word audio stays local unless `SCOUT_WAKE_CLOUD_FALLBACK=true`; `SCOUT_EGRESS_ALLOWLIST` can restrict URLs Scout opens; API keys use Windows Credential Manager when available.
- **Voice commands** — push-to-talk recording with silence detection, transcribed by Gemini and run through the same agent.
- **Desktop companion** — Scout himself sits in the corner: fluffy fur, cyan eyes, goggles and an orange scarf. He listens, tilts his head when thinking, works, celebrates, droops on errors and naps when idle. Click him to summon the assistant; drag him anywhere.
- **Floating icon** — a draggable, always-on-top orb at medium opacity: click to talk, right-click for a typed prompt.
- **Fast defaults** — `gemini-3.5-flash-lite` with minimal thinking, around 1.6s per model turn.
- **Structured logging** to a rotating log file, with secret redaction.
- **Doctor command** to check your environment.
- **Tests** covering tool registration, argument validation, path traversal, workspace restriction, allowlisting, timeout/limit behaviour, provider parsing, permissions, audit redaction, planning/verification, screenshot privacy and overlay streaming.

## Requirements

- Python 3.11 or newer (3.13 recommended)
- Windows 11 (primary) or Linux
- A Gemini API key

## Installation

```bash
git clone https://github.com/<your-username>/scout.git
cd scout

python -m venv .venv
# Windows (cmd.exe)
.venv\Scripts\activate.bat       # use this in cmd.exe, not `source`
# PowerShell
.venv\Scripts\Activate.ps1        # PowerShell: .venv\Scripts\Activate.ps1
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

### First-time setup with your own key

Scout is open source, so each user provides their own Gemini API key. Run:

```bash
scout init
```

You will be prompted to paste your key (it is stored locally only, never sent anywhere except Google). On Windows it is saved in Credential Manager; on other systems it falls back to the per-user config file at `global_env_file()`. Never paste a key into a project `.env` that you might commit.

If you already have a key saved, running `scout init` again lets you keep or replace it. After `scout init`, you can run:

```bash
scout
```

or

```bash
scout ask "open notepad"
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
| `SCOUT_OVERLAY_LABEL` | `scout` | Custom label shown on the floating overlay |
| `SCOUT_MAX_TOOL_STEPS` | `8` | Max model↔tool round trips per request |
| `SCOUT_FULL_ACCESS` | `false` | Bypass approval prompts (trusted automation only) |
| `SCOUT_DENIED_TOOLS` | — | Comma-separated tools that can never run, even with full access |
| `SCOUT_AUTO_APPROVE_TOOLS` | — | Comma-separated tools that skip the prompt (still audited) |
| `SCOUT_EGRESS_ALLOWLIST` | — | Comma-separated hosts Scout may open; empty allows any http(s) host |
| `SCOUT_AUDIT_ENABLED` | `true` | Write the durable approval/tool ledger |
| `SCOUT_AUDIT_FILE` | data dir | Override the audit ledger path |
| `SCOUT_SCREENSHOT_BLOCKED_TITLES` | privacy set | Window-title fragments that refuse screenshots |
| `SCOUT_SCREENSHOT_RETENTION_DAYS` | `7` | Delete screenshots older than this (0 keeps them) |
| `SCOUT_SCREENSHOT_MAX_FILES` | `50` | Keep at most this many screenshots (0 disables the cap) |
| `SCOUT_WAKE_CLOUD_FALLBACK` | `false` | Allow cloud recognition of wake audio; local/hotkey is the default |
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
scout audit                            # recent approval/tool ledger
scout status                           # provider, model, workspaces
scout config                           # resolved config (key masked)
scout doctor                           # environment diagnostics
scout doctor --check-api               # also verify the provider connection
```

Interactive commands: `/exit`, `/clear`, `/tools`, `/help`.

### Voice and the floating icon

```bash
scout voice      # terminal loop: speak, Scout transcribes and runs it
scout overlay    # companion + small always-on-top orb (65% opacity by default)
scout overlay --no-companion   # orb only
```

- **Click Scout** (the character) to bring up the assistant and start listening; drag him to move him; right-click him to stop, hide or quit.
- **Click** the orb to talk; it stops after a pause and shows the answer in a bubble.
- **Drag** it anywhere; the position is remembered.
- **Right-click** for a typed prompt, Stop Scout, or to quit.
- **Stop** with the red ■ button in the pill/HUD, the tray menu, the character's right-click menu, or the button inside an approval dialog. Ctrl+C stops the CLI.
- Actions that change the machine open a confirmation dialog; high-risk actions ask you to type `allow`. The dialog offers Allow once, Allow for session, Deny and Stop Scout.
- Say "exit", "quit" or "stop listening" to end `scout voice`.
- Microphone access is required. If capture fails, Scout shows the reason instead of guessing.
- Wake-word audio is **not sent to a cloud recognizer by default**. Say "Hey Scout" only when a local custom detector is installed; `Ctrl+Alt+S` always works. Set `SCOUT_WAKE_CLOUD_FALLBACK=true` to opt into cloud recognition.
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
| `LOW_RISK` | Allowlisted apps, screenshots, screen OCR, volume, URLs | Allowed; sensitive windows are refused or masked |
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
├── agent/         Async loop, plans, turn types, system prompt
├── events/        Structured event types + in-process bus
├── providers/     Provider interface, capabilities + Gemini adapter
├── tools/         Registry, validation, system/filesystem/apps/desktop/planning
├── security/      Workspace checks, permission policy, audit, egress, credentials
├── perception.py  Active window, region capture, privacy masks, local OCR
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
- Scout's character is a procedural 2.5D mascot (no rigged GLB/Blender model yet), so his expressions are drawn rather than skeleton-animated.
- The CLI consumes the event bus and streams tokens; the overlay renders streamed deltas in the HUD through a queued Qt signal.
- The planner is model-driven: it is only as good as the model's `update_plan` calls, and verification is one extra bounded turn, not a formal proof.
- Stop is cooperative: it cancels between model chunks and before the next tool call, but a tool already running in a thread is not killed.
- Overlay approvals are modal dialogs on the GUI thread; they time out to denial after 120 seconds.
- Wake-word privacy is enforced by disabling cloud recognition by default; local wake detection currently requires a custom detector. The Windows hotkey is always local.
- Audit records redact obvious secret keys and truncate values, but they are local files: protect the data directory accordingly.
- Screen OCR uses the installed Windows OCR language and runs through PowerShell; it is Windows-only and capped at 20,000 characters. UI automation still acts on screen coordinates rather than accessibility elements.
- No file writes/deletes, terminal/shell, or web page reading. `open_url` only opens the browser.
- Typing is ASCII-only; non-ASCII text is rejected with an explanation.
- Voice capture was tested with synthesized audio, not on every microphone; if none is available Scout reports it.
- Tool timeouts cannot kill an already-running thread; they only stop Scout waiting.
- Desktop automation acts on the focused window; check before typing or pressing keys.
- Windows-focused; Linux support is structural and GUI/voice tools need extra setup there.

## Roadmap

1. **Milestone 1** — CLI, config, provider, bounded loop, registry, read-only tools. ✅
2. **Milestone 2** — approval policy and interactive confirmations ✅; secure-by-default access, overlay approval dialogs, emergency stop, audit ledger and plans/verification ✅; SQLite conversation memory still pending.
3. **Milestone 3** — filesystem writes, protected command execution, developer/Git tools.
4. **Milestone 4** — browser automation, web search, document processing.
5. **Milestone 5** — voice input and floating icon ✅; spoken replies and wake word pending.
6. **Upgrade Phase 1** — async event pipeline, streaming, cancellation, regression fixes ✅.
7. **Upgrade Phase 2** — secure defaults, permission manager, planner/verifier, audit ledger, overlay token streaming, credential storage and privacy controls ✅.
8. **Upgrade Phase 3** — active-window/screen-region perception, local OCR and privacy masks ✅.
9. **Upgrade Phase 4+** — SQLite memory, developer tools, local wake/TTS, workflows/skills, browser/research, multi-agent and release engineering. These are separate pillars and are not implemented yet.
10. **Milestone 6** — plugin SDK, packaging, public release.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
