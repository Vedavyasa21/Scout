# Scout architecture

Scout is split into layers with one rule: **the model proposes, Scout disposes.**
No model output reaches the OS without passing local validation and the policy gate.

```text
┌─────────────────────────────────────────────────────────────┐
│ cli/            Typer commands · Rich rendering · REPL      │
├─────────────────────────────────────────────────────────────┤
│ agent/          bounded loop · system prompt · run result   │
├──────────────────────────────┬──────────────────────────────┤
│ providers/                   │ tools/                       │
│  base.py  Turn/ToolCall      │  base.py  registry, schema,  │
│  gemini.py  SDK translation  │           policy, execution  │
│                              │  system.py                   │
│                              │  filesystem.py               │
│                              │  applications.py             │
│                              │  desktop.py  windows/input    │
├──────────────────────────────┴──────────────────────────────┤
│ security/       workspace resolution · policy · redaction   │
│ voice/          recording · Gemini transcription            │
│ desktop/        floating icon and approval dialogs          │
│ config/         Pydantic settings · .env                    │
│ logging/        JSON file logs · secret filtering           │
└─────────────────────────────────────────────────────────────┘
```

## Request lifecycle

1. The CLI loads validated `Settings` and builds the registry.
2. `Agent.run(message, history)` appends a `UserTurn` and enters the loop.
3. The provider receives the system prompt, the turn history and JSON tool specs.
4. If the reply has tool calls, the registry validates arguments, consults the
   approval policy (prompting the user when required), executes with a timeout,
   and returns a `ToolResult`.
5. The result is appended as a `ToolTurn`; the model sees it on the next call.
6. When the model replies without tool calls, that text is the final answer.

The loop is capped by `SCOUT_MAX_TOOL_STEPS`; one reply may request at most
`MAX_CALLS_PER_ROUND` (6) calls. Hitting the cap returns
`completed=False, limit_reached=True` instead of pretending success.

## Provider abstraction

`ModelProvider.complete()` is the only thing the agent knows about a vendor.
Turns are vendor-neutral:

| Turn | Meaning |
| --- | --- |
| `UserTurn` | user message |
| `AssistantTurn` | model text / tool calls, plus the raw provider payload |
| `ToolTurn` | the result of one tool call |

The Gemini adapter translates turns to `google.genai` contents. `AssistantTurn.raw`
replays the exact model content so thought signatures and call ids survive — this
is required by thinking models and is why the raw payload is stored.

Adding a provider:

1. Implement `ModelProvider` in `src/scout/providers/<name>.py`.
2. Translate `Turn` objects to the vendor format and parse tool calls back.
3. Map vendor errors to `ProviderError` with secret-redacted messages.
4. Register the name in `SUPPORTED_PROVIDERS` and `create_provider`.

## Tool contract

Every tool declares name, description, JSON-schema parameters, permission level,
handler, optional timeout and tags. The registry:

- rejects duplicate or malformed names;
- validates arguments against the schema subset **before** execution;
- asks the policy gate for authorization;
- runs the handler in a worker thread with a timeout;
- converts exceptions and timeouts into failed `ToolResult`s.

Validation is intentionally strict: unknown arguments are rejected, numeric
bounds and enums are enforced, and `true` is not accepted for an integer field.

### Approval policy

`PolicyEngine` auto-approves `READ` and `LOW_RISK`. `WRITE`, `EXECUTE` and
`HIGH_RISK` require an `Approver`:

- the CLI renders a Rich panel with the tool, permission and exact arguments;
- the overlay uses a Tk dialog (typed `allow` for high-risk actions);
- without an interactive approver the call is denied, so piped/CI runs cannot
  approve themselves;
- `ALLOW_SESSION` remembers that tool for the rest of the session.

The registry re-times execution after approval, so prompt deliberation is not
reported as tool latency. Exceptions inside an approver are treated as denial.

## Filesystem safety

Model-supplied paths are never used directly:

1. Expand `~` and resolve relative paths against the working directory.
2. `Path.resolve()` follows symlinks and collapses `..`.
3. The resolved path must be relative to one of the approved workspaces.
4. Read tools additionally enforce a text-extension allowlist, a byte cap and a
   NUL-byte binary check.

Search uses `os.walk(..., followlinks=False)`, skips heavy/hidden directories and
bounds depth and result count.

## Application safety

Applications are resolved from an explicit allowlist. Resolution order: absolute
allowlisted path, `PATH` lookup, then a small Windows fallback table. Launch uses
`os.startfile` on Windows and an argument-array `Popen` without a shell elsewhere.
Scout reports that the OS accepted the launch request; it does not claim to know
the window state.

The separate `launch_app` tool resolves any installed application from Start Menu
shortcuts (`.lnk`/`.url`), refuses ambiguous names with a candidate list, and runs
at `EXECUTE` level, so an arbitrary app launch always needs confirmation.

## Desktop control

Desktop tools live in `tools/desktop.py` and use `pyautogui`/`pygetwindow`, imported
lazily so a missing GUI dependency only disables those tools. Guardrails:

- coordinates are checked against the current screen size;
- key names are validated against `pyautogui.KEYBOARD_KEYS` before any press;
- typing is ASCII-only and capped at 2000 characters;
- window lookup prefers an exact title and refuses ambiguous matches;
- `pyautogui.FAILSAFE` stays enabled: moving the pointer to a screen corner aborts;
- screenshot files go to Scout's data directory, not user workspaces.

## Voice and overlay

`voice/` records 16 kHz mono PCM with `sounddevice`, stops after a configurable
silence window, wraps the audio in a WAV container, and sends it to Gemini for
transcription. The transcript is treated exactly like typed input and goes through
the same agent and approval path.

`desktop/overlay.py` is a frameless Tk orb at 65% opacity: click to talk, drag to
move, right-click for a typed prompt or to quit. It keeps a bounded conversation
history and routes approvals through modal dialogs on the Tk thread while the
agent runs in a worker thread.

## Logging and privacy

Logs are JSON lines in a rotating file (5 MB × 3). Records may carry `task_id`,
`tool`, `event`, `status`, `permission` and `duration_ms`. A filter replaces the
configured API key with `***redacted***` in messages and tracebacks. Logs never go
to stdout, so the CLI output stays clean.

## Testing strategy

- Unit tests cover schema validation, permission decisions, path traversal and
  symlink escapes, allowlisting, timeouts and provider parsing.
- Agent tests use a scripted fake provider — no network, no real tools.
- CLI tests use Typer's runner with the provider patched.
- Filesystem tests use `tmp_path` only; nothing touches user data.
- Symlink tests skip on systems where creating links is not permitted.
