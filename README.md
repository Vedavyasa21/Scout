# Scout

Scout is a secure, extensible command-line personal AI agent for Windows 11 (with Linux/WSL support on the way). You talk to it in natural language; it plans, calls real tools, inspects the results, and reports honestly what happened.

Scout is **not** a chatbot wrapper. Every capability is a registered tool with a declared input schema, permission level and timeout, executed locally through a policy gate. The language model proposes tool calls; Scout validates and authorizes them.

> **Status: Milestone 1 (foundation).** Working today: CLI, configuration, Gemini provider adapter, bounded agent loop, tool registry with local schema validation, read-only system inspection, workspace-confined filesystem reading/search, and allowlisted application launching. Write operations, the full approval workflow, persistent memory and browser automation are **not implemented yet**. See [Roadmap](#roadmap).

## Features (Milestone 1)

- **Interactive and one-shot CLI** built with Typer and Rich.
- **Gemini provider adapter** behind a small provider interface (other providers can be added without touching the agent).
- **Bounded agent loop** — capped model/tool round trips, no recursion, no unbounded tool calls.
- **Tool registry** — every tool declares name, description, JSON-schema parameters, permission level, timeout and tags. Arguments are validated locally before execution, never trusted from the model.
- **Workspace confinement** — filesystem paths are resolved (including symlinks and `..`) and must live inside configured workspaces.
- **Application allowlist** — only explicitly allowlisted executables can be launched.
- **Structural safety gate** — in this build only `READ` and `LOW_RISK` tools can run; `WRITE`, `EXECUTE` and `HIGH_RISK` are refused until the approval workflow ships.
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
SCOUT_MODEL=gemini-3.5-flash
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
| `SCOUT_MODEL` | `gemini-3.5-flash` | Model name (must support function calling) |
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
scout tools                            # list registered tools
scout status                           # provider, model, workspaces
scout config                           # resolved config (key masked)
scout doctor                           # environment diagnostics
scout doctor --check-api               # also verify the provider connection
```

Interactive commands: `/exit`, `/clear`, `/tools`, `/help`.

Example session:

```text
$ scout ask "Find my research folder and list its contents"

 scouting  gemini-2.0-flash
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
| `READ` | Inspect information | Allowed inside workspaces |
| `LOW_RISK` | Launch allowlisted apps, open approved folders | Allowed |
| `WRITE` | Create or modify files | Refused (Milestone 2) |
| `EXECUTE` | Run programs/commands | Refused (Milestone 2) |
| `HIGH_RISK` | Delete, install, security changes | Refused (Milestone 2) |

Guarantees in this build:

- No `eval`/`exec` of model output; no shell strings.
- Subprocesses use argument arrays with `shell=False`.
- Filesystem access is resolved and confined to approved workspaces.
- Launching is restricted to an explicit allowlist.
- The agent only reports success when a tool returned a successful result.

## Architecture

```text
src/scout/
├── cli/           Typer commands + Rich rendering
├── agent/         Bounded loop, turn types, system prompt
├── providers/     Provider interface + Gemini adapter
├── tools/         Registry, validation, system/filesystem/applications
├── security/      Workspace boundary checks
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

## Known limitations (Milestone 1)

- Gemini is the only provider adapter.
- No persistent conversation memory yet (each `scout ask` is a fresh session).
- No write, delete, terminal or browser tools yet; the permission gate refuses them.
- Tool timeouts cannot kill an already-running thread; they only stop Scout waiting.
- `web search` inside Scout is not implemented yet (the model may still answer from its knowledge).
- Windows-focused; Linux support is structural but not yet tested in CI.

## Roadmap

1. **Milestone 1** — CLI, config, provider, bounded loop, registry, read-only tools. ✅
2. **Milestone 2** — permission engine, interactive approvals, SQLite memory, audit trail, plans.
3. **Milestone 3** — filesystem writes, protected command execution, developer/Git tools.
4. **Milestone 4** — browser automation, web search, document processing.
5. **Milestone 5** — voice input/output, desktop UI, wake word.
6. **Milestone 6** — plugin SDK, packaging, public release.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
