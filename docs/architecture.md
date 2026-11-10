# Scout architecture

Scout is split into layers with one rule: **the model proposes, Scout disposes.**
No model output reaches the OS without passing local validation and the policy gate.

```text
┌─────────────────────────────────────────────────────────────┐
│ cli/            Typer commands · Rich rendering · REPL      │
├─────────────────────────────────────────────────────────────┤
│ events/         structured events · in-process bus          │
├─────────────────────────────────────────────────────────────┤
│ agent/          async loop · plans · verification · cancel  │
├──────────────────────────────┬──────────────────────────────┤
│ providers/                   │ tools/                       │
│  base.py  Turn/ToolCall      │  base.py  registry, schema,  │
│  gemini.py  SDK translation  │           policy, execution  │
│                              │  system.py                   │
│                              │  filesystem.py               │
│                              │  applications.py             │
│                              │  desktop.py  windows/input    │
├──────────────────────────────┴──────────────────────────────┤
│ security/       policy · audit ledger · egress · credentials│
│ perception.py   active window · region capture · local OCR  │
│ control.py      process-wide emergency stop for active runs │
│ voice/          recording · Gemini transcription            │
│ desktop/        floating icon and approval dialogs          │
│ config/         Pydantic settings · .env                    │
│ logging/        JSON file logs · secret filtering           │
└─────────────────────────────────────────────────────────────┘
```

## Request lifecycle

1. The CLI loads validated `Settings` and builds the registry.
2. `Agent.run_async(message, history, bus=..., cancel=...)` appends a `UserTurn`
   and enters the loop as one asyncio task. `Agent.run()` is a synchronous
   facade used by the overlay worker thread and old callers.
3. The agent creates a per-run `PlanTracker` and a `ToolContext` carrying the
   task id, audit ledger and plan, then publishes `agent_started`,
   `agent_thinking` and `model_started`.
4. `provider.stream(...)` yields text deltas (`model_chunk`), tool calls and the
   raw provider payload; the agent accumulates the reply, forwards deltas to the
   `on_model_chunk` hook, and emits `model_completed`.
5. If the reply has tool calls, the registry validates arguments, asks the
   central permission manager for AUTO/ASK/DENY, consults the approver when
   needed, executes in a worker thread with a timeout, and returns a
   `ToolResult`. Every request/decision/result is written to the audit ledger;
   the agent emits `tool_started` / `tool_completed` (and `approval_required`
   first when the policy will ask).
6. The result is appended as a `ToolTurn`; the model sees it on the next call.
   If the model updated its plan, `plan_updated` is emitted.
7. When the model replies without tool calls, an unfinished or failed plan
   triggers one `verification_started` turn that asks the model to reconcile it
   with tool results. On the next final reply the text becomes the answer and
   `agent_success` / `verification_completed` are emitted. Provider failures emit
   `agent_error`; cancellation emits `agent_cancelled`.

The loop is capped by `SCOUT_MAX_TOOL_STEPS`; one reply may request at most
`MAX_CALLS_PER_ROUND` (6) calls. Calls beyond the cap are answered with a failed
`ToolTurn` telling the model to continue in another step, so a large batch is
never dropped silently. Hitting the step cap returns
`completed=False, limit_reached=True` and emits `agent_limit` instead of
pretending success.

Cancellation is cooperative: a `CancellationToken` is checked between model
chunks and before each tool call. Every active run registers its token with
`control.EMERGENCY_STOP`, so a UI button or Ctrl+C can stop all runs without a
reference to the agent. A running tool thread cannot be killed, so it finishes
in the background while no further work starts.

## Provider abstraction

`ModelProvider` exposes `capabilities`, `complete()` and `stream()`. Turns are
vendor-neutral. The default `stream()` runs `complete()` off the event loop and
yields a single chunk, so every provider works without streaming. Gemini
overrides it with the native async SDK stream and declares streaming/audio
capabilities.

| Turn | Meaning |
| --- | --- |
| `UserTurn` | user message |
| `AssistantTurn` | model text / tool calls, plus the raw provider payload |
| `ToolTurn` | the result of one tool call |

The Gemini adapter translates turns to `google.genai` contents. `AssistantTurn.raw`
replays the exact model content so thought signatures and call ids survive — this
is required by thinking models and is why the raw payload is stored. The
streaming path accumulates the actual chunk parts into the raw content for the
same reason.

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

`PolicyEngine` is the central permission manager. It evaluates every call to
`AUTO`, `ASK` or a hard `DENY`:

- `SCOUT_DENIED_TOOLS` is checked first and blocks a tool even with full access;
- `SCOUT_AUTO_APPROVE_TOOLS` and session grants skip the prompt but are audited;
- `READ`/`LOW_RISK` are automatic; `WRITE`/`EXECUTE`/`HIGH_RISK` require an
  `Approver` unless full access is explicitly enabled.

The interactive approvers are:

- the CLI renders a Rich panel with the tool, permission and exact arguments;
- the overlay shows a PyQt dialog on the GUI thread while the worker waits
  (`OverlayApprover`); high-risk actions require typing `allow`, and each dialog
  has a Stop Scout button;
- without an interactive approver the call is denied, so piped/CI runs cannot
  approve themselves;
- `ALLOW_SESSION` remembers that tool for the rest of the session;
- `SCOUT_FULL_ACCESS` defaults to `false`; turning it on is an explicit opt-in.

The registry re-times execution after approval, so prompt deliberation is not
reported as tool latency. Exceptions inside an approver are treated as denial.

## Planning and verification

`update_plan` is a model-visible tool backed by `agent/plan.py`. It stores 1–8
steps with `pending`, `active`, `done`, `failed` or `skipped` status. The tool is
`READ` level because it only changes local task metadata. The agent emits the
full plan on `plan_updated` and includes it in `AgentResult.plan`.

Before accepting an answer while steps are still pending/active or failed, the
agent performs exactly one verification pass. It appends a user turn listing the
unfinished steps and asks the model to mark them done, fix them, or state the
blocker. This is a bounded consistency check, not a formal verifier; it cannot
prove an action happened beyond the tool result.

## Audit, egress and credentials

`security/audit.py` appends redacted JSON Lines to `settings.resolved_audit_file`
with size rotation. Records cover run start/finish/cancel, approval request,
decision, denial and tool result. Values longer than 400 characters are
truncated, lists/objects are bounded, and keys containing password/secret/token/
api-key/cookie/credential are replaced with `***`. `scout audit` reads the newest
records. Audit I/O failures never break a run.

`security/egress.py` is an optional host allowlist. `open_url` and `search_web`
call it before launching a browser; an empty `SCOUT_EGRESS_ALLOWLIST` preserves
the old behavior. Provider traffic is not routed through this policy.

`security/credentials.py` stores the provider key in Windows Credential Manager
(DPAPI, per-user) and reports failure on other platforms; `save_api_key` then
falls back to the per-user `.env` with best-effort permissions. `load_settings`
reads Credential Manager only when no key is present in env/.env.

## Privacy controls

- **Screenshots:** the tool checks the focused window title against
  `SCOUT_SCREENSHOT_BLOCKED_TITLES` and refuses the capture rather than trying to
  blur it. Old captures are pruned by age and count after each successful shot.
- **Wake audio:** `WakeWordListener` is local-only by default. Custom detectors
  still run; Google/Gemini recognition requires an explicit
  `SCOUT_WAKE_CLOUD_FALLBACK=true` opt-in. The Windows hotkey remains local.
- **Overlay streaming:** `AgentHooks.on_model_chunk` drives a queued Qt signal so
  the HUD shows text while the worker thread streams.

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

## Screen perception

`perception.py` is the Windows-facing screen layer:

- `active_window_info()` returns the focused window title, process name, pid,
  rectangle and minimized state using `pygetwindow`, the Win32 foreground-window
  API and `psutil`.
- `capture_screen()` captures the primary screen with `pyautogui`, then paints
  black boxes over visible windows whose titles match
  `SCOUT_SCREENSHOT_BLOCKED_TITLES`. A sensitive focused window is refused by
  the tool instead of masked, because that is the user's active work.
- `crop_region()` validates and crops an explicit rectangle. `save_screenshot()`
  writes `shot-`, `region-` or `ocr-` PNGs; `prune_screenshots()` applies age and
  count retention.
- `ocr_image()` uses the installed Windows OCR engine through PowerShell (no
  extra Python dependency), upscaling small crops for better glyph recognition.
  It runs locally, returns at most 20,000 characters at the tool boundary, and
  reports missing language/engine errors instead of guessing.

Tools: `active_window` (READ), `screenshot` / `screenshot_region` (LOW_RISK) and
`read_screen_text` (LOW_RISK, OCR). UI automation still targets screen
coordinates; an accessibility-tree implementation is future work.

## Voice and overlay

`voice/` records 16 kHz mono PCM with `sounddevice`, stops after a configurable
silence window, wraps the audio in a WAV container, and sends it to Gemini for
transcription. The transcript is treated exactly like typed input and goes through
the same agent and approval path.

`desktop/overlay.py` is a frameless PyQt6 HUD and `desktop/character.py` is the
animated companion. The agent runs in a `QThread`, so the UI never blocks; click
the character to summon the assistant, drag to move, right-click for a typed
prompt or to quit. The overlay keeps a bounded conversation history. Its approval
path uses `OverlayApprover`, which bridges the worker thread to a queued PyQt
dialog. The overlay, tray and character expose Stop, all backed by
`EMERGENCY_STOP` (see `control.py`).

Token streaming is wired into the CLI (`cli/streaming.py`) when stdout is a TTY.
The overlay receives `on_model_chunk` through `AgentHooks` and forwards deltas
over a queued Qt signal, so the HUD updates while the QThread is running.
Wake audio is local-only by default; cloud recognition requires an explicit
`SCOUT_WAKE_CLOUD_FALLBACK=true` opt-in.

## Logging and privacy

Logs are JSON lines in a rotating file (5 MB × 3). Records may carry `task_id`,
`tool`, `event`, `status`, `permission` and `duration_ms`. A filter replaces the
configured API key with `***redacted***` in messages and tracebacks. Logs never go
to stdout, so the CLI output stays clean. The separate audit ledger records
approvals and tool results even when diagnostic logging is quiet.

## Testing strategy

- Unit tests cover schema validation, permission decisions, path traversal and
  symlink escapes, allowlisting, timeouts and provider parsing.
- Agent tests use scripted fake providers (blocking and streaming) — no network.
- Event tests cover listener order, failure isolation and async queues; async
  agent tests cover streaming assembly, tool events, approval events, limits,
  cancellation and the synchronous facade. Control tests cover the global stop
  and the queued approval bridge.
- Phase 2 tests cover hard-deny precedence, auto-approved tools, audit redaction,
  plan verification, egress blocking, screenshot exclusion/retention and
  local-only wake audio. Phase 3 tests cover active-window metadata, region
  capture, privacy masks and OCR dispatch. Overlay tests cover chunk forwarding
  to the HUD.
- CLI tests use Typer's runner with the provider patched; the stream renderer is
  tested independently so non-TTY output stays stable.
- Overlay regressions cover the worker's tool-name reporting and the completion
  handler's history turns.
- Filesystem tests use `tmp_path` only; nothing touches user data.
- Symlink tests skip on systems where creating links is not permitted.
