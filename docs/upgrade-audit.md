# Scout upgrade audit — Phase 0

Audited against the working tree on 2026-11-09. This report is the current-state
versus target-state baseline for the upgrade. Phase 1 changes are described at
the end; Phase 2b and Phase 3 are documented at the end, and later pillars
remain intentionally not implemented.

## A. Existing architecture

```text
Typer CLI (cli/app.py, render.py, approval.py, doctor.py)
        │
        ▼
Agent (agent/loop.py) ── provider.complete() ──► Gemini SDK
        │
        ├── ToolRegistry (tools/base.py) ── security policy ── tools/*
        │       system · filesystem (read-only) · applications · desktop
        │
        ├── ToolContext (workspaces, approver)
        └── AgentHooks (CLI progress callbacks)

Voice (voice/__init__.py) ── sounddevice ──► Gemini transcription
Wake/hotkey (voice/wake.py) ── threads ──► overlay callback
Overlay (desktop/overlay.py) ── PyQt6 + QThread ──► agent.run()
Character (desktop/character.py) ── state renderer
Config (config/) · logging (logging/) · security (security/)
```

Key facts:

- The agent loop is synchronous. `Agent.run()` calls `provider.complete()`,
  executes tools inline and returns an `AgentResult`. The overlay keeps the UI
  responsive by running that call in a `QThread`, not by making the core async.
- Tool execution is already isolated by a one-thread executor with a timeout,
  and arguments are validated before authorization and execution.
- The provider abstraction is real but thin: `complete()`, audio transcription
  and a probe. There is no capability model and no streaming.
- `AgentHooks` is an ad-hoc callback channel used by the CLI and overlay.
- The overlay is a PyQt6 HUD/character, not the Tk orb described in the older
  `docs/architecture.md`; that document had drifted.
- There is no persistence layer: history lives in memory for the session only.

## B. Existing features and implementation status

| Area | Status | Notes |
| --- | --- | --- |
| Typer CLI (`ask`, interactive, `voice`, `overlay`, `enable`) | Working | Blocking by design |
| Gemini adapter | Working | `google-genai` 3.0.0 installed |
| Bounded model/tool loop | Working | 8 steps default, 6 calls per reply |
| Tool registry + JSON-schema subset validation | Working | Unknown args rejected |
| Approval policy | Working, secure by default (Phase 2a) | `SCOUT_FULL_ACCESS` is opt-in; overlay uses a PyQt approval dialog |
| Workspace confinement, symlink/traversal checks | Working | Read-only filesystem tools |
| System diagnostics, process listing | Working | psutil |
| Window/mouse/keyboard/screenshot/volume/URL tools | Working | Focused-window actions, ASCII typing |
| App launching | Working | Allowlist + Start Menu resolution |
| Push-to-talk voice input | Working | Records with silence detection, transcribes via Gemini |
| Wake word + global hotkey | Working with privacy default | Local-only by default (custom detector); cloud recognition is opt-in; global hotkey always local |
| Floating overlay + HUD + character | Working | PyQt6, always-on-top, draggable |
| Conversation memory | Session only | Lost on exit |
| TTS / spoken replies | Missing | No speaking pipeline |
| File writes/deletes/terminal | Missing | Intentionally not exposed |
| Web reading/browser automation | Missing | `open_url` only |
| Screen understanding/OCR/UI automation | Partial (Phase 3) | Active window, region capture, local OCR and privacy masks; no accessibility tree |
| Persistent memory/project intelligence | Missing | No SQLite |
| Workflows, skills, plugin SDK | Missing | No extension loader beyond `custom_feature.py` |
| Structured audit trail | Partial | Rotating JSON logs; no approval ledger |
| Async engine, events, streaming | Missing before Phase 1 | Added in Phase 1 |

## C. Missing capabilities (mapped to the target pillars)

- Pillars 1–2: async event bus, streaming, capability-aware providers, routing.
- Pillar 3: planner/executor/verifier/recovery, queues, pause/resume.
- Pillar 4: active-window/UI/OCR/screen-region perception and privacy masks.
- Pillar 5: verified computer control; accessibility-first automation.
- Pillar 6: SQLite personal/project/task memory with inspection and export.
- Pillar 7: repository intelligence, Git, tests, terminal adapters.
- Pillar 8: workflow engine with triggers, conditions, approvals, history.
- Pillar 9: signed skill manifests, discovery, isolation, permission grants.
- Pillar 10: Playwright/browser research, untrusted-content handling.
- Pillar 11: local wake word, streaming STT, TTS, barge-in.
- Pillar 12–14: document/file intelligence, proactive context, productivity.
- Pillar 15: specialized agents with permission inheritance.
- Pillar 16: centralized security engine, audit, rollback, emergency stop.
- Pillar 17–19: settings/dashboard, cross-platform adapters, release tooling.

## D. Performance bottlenecks

1. The provider call blocks the caller (`google-genai` sync client). The overlay
   hides this in a worker thread, but the CLI cannot render partial output.
2. Retries use `time.sleep()` inside the request path, so a transient failure
   freezes that thread for up to 4 seconds per attempt.
3. No token streaming: the first visible answer arrives only after the whole
   model response completes.
4. Each tool call creates a fresh `ThreadPoolExecutor`; cheap in isolation, but
   wasteful for chatty tool sequences.
5. Timeouts stop Scout waiting, not the running handler thread.
6. Wake-word detection sends every speech burst to Google/Gemini, adding
   latency and network dependency to the wake path.
7. The overlay runs a 60 fps waveform timer plus a 30 fps character timer, even
   when idle.
8. `build_registry()` re-imports and re-registers all tools for every agent.

## E. Security weaknesses (ranked)

1. **Critical — approval bypass by default (closed in Phase 2a).**
   `Settings.full_access` used to default to `True`, and the overlay constructed
   `AllowAllApprover()`. The README promises approvals for
   `WRITE`/`EXECUTE`/`HIGH_RISK`; that is now the default behavior. Full access
   is an explicit opt-in. See the Phase 2a section at the end.
2. **High — wake-word privacy (closed in Phase 2b).** `WakeWordListener` used to
   send 3.2 s audio bursts to Google Speech Recognition and then Gemini. It is
   now local-only by default; cloud recognition requires an explicit opt-in.
3. **High — secrets at rest (closed in Phase 2b).** `scout init` now stores the
   key in Windows Credential Manager and removes the plaintext copy; `.env`
   remains a fallback for non-Windows and automation.
4. **High — screenshots (closed in Phase 2b).** Captures are refused when the
   focused window title matches a sensitive pattern, and old/excess PNGs are
   pruned automatically.
5. **Medium — unverified actions.** Clicks/typing are fire-and-forget on the
   focused window; success is assumed from the OS accepting the input.
6. **Medium — no emergency stop (closed in Phase 2a).** Every run registers a
   token with `EMERGENCY_STOP`; the CLI, overlay, tray, character and approval
   dialogs all expose Stop. A running tool thread is still not killable.
7. **Medium — no approval audit trail (closed in Phase 2b).** A redacted,
   rotated JSONL ledger records runs, approval requests/decisions, denials and
   tool results; `scout audit` inspects it.
8. **Medium — no egress control (partially closed in Phase 2b).** An optional
   host allowlist now gates `open_url`/`search_web`. Provider and future
   browser/skill traffic still use the process's own network access.
9. **Low — timeout threads keep running** after Scout reports failure.
10. **Low — prompt injection surface** is currently small (no web/file writes),
    but file contents and window titles are already untrusted model input.

## F. Proposed target architecture

```text
Desktop UI / character / voice ── consume events ──┐
                                                   ▼
                                           Event bus (asyncio)
                                                   ▲
CLI ─────────────────────────────────────► Agent orchestrator
                                             planner · executor · verifier
                                                   │
                    ┌──────────────┬───────────────┼──────────────┐
                 providers      memory          task engine     tools
              (cloud/local)   (SQLite)        (queue/workflows) (registry)
                                                   │
                                            security engine
                                      permissions · audit · rollback
```

The character and UI remain consumers only. The security engine is independent
of the model and re-validates every action.

## G. Migration plan

1. **Phase 0 (this report):** freeze the baseline, identify bugs and risks.
2. **Phase 1 (implemented):** async agent core, event bus, streaming provider
   interface, token rendering, cancellation primitive, regression tests.
3. **Phase 2 (implemented, 2a + 2b):** security defaults and permission manager,
   task planner/verifier, approval + audit persistence, Stop/cancel in CLI and
   overlay, credential storage and privacy controls.
4. **Phase 3+:** perception, memory, developer tools, voice, workflows, skills,
   browser, productivity, multi-agent, release engineering.

Each phase keeps `Agent.run()` working until its callers migrate, and adds tests
before changing defaults. No phase may silently weaken the security policy.

## H. Prioritized roadmap

| Priority | Work | Phase |
| --- | --- | --- |
| P0 | Async event pipeline + streaming + cancellation | 1 ✅ |
| P0 | Approval defaults, permission manager, audit, emergency stop | 2 ✅ |
| P0 | Planner/executor/verifier with task verification | 2 ✅ |
| P1 | Multi-provider + smart routing | 2–3 |
| P1 | Screen perception and privacy masks | 3 ✅ |
| P1 | SQLite memory and project intelligence | 4 |
| P2 | Local wake word, TTS, barge-in | 5 |
| P2 | Workflows and plugin SDK with isolation | 6 |
| P3 | Browser/research, documents, productivity | 7 |
| P3 | Multi-agent, proactive assistance, marketplace | 8–9 |

## I. Dependencies and external services

Current: Python 3.11+, Typer, Rich, Pydantic/pydantic-settings, google-genai
3.0.0, psutil, platformdirs, pyautogui, pygetwindow, sounddevice, Pillow,
pystray, PyQt6, SpeechRecognition; dev: pytest, ruff.

Phase 1 adds no dependency: `asyncio`, `threading` and the Gemini async client
ship with the existing stack.

Later phases will need (subject to approval): SQLite/SQLAlchemy, keyring or
Windows Credential Manager, openWakeWord/ONNX, faster-whisper, Piper or edge-tts,
Playwright, optional OpenAI/Anthropic/DeepSeek/Ollama clients, and packaging/
installer tooling.

## J. Risks and compatibility issues

- **Async/PyQt integration:** the overlay currently calls the sync facade inside
  a QThread. Migrating it to native async needs a signal bridge and careful
  shutdown; until then it will not render streamed tokens.
- **Streaming tool calls:** provider chunks may carry function calls; the agent
  assembles them and replays the raw parts. Thought signatures must survive a
  future provider/model change; test this with real Gemini traffic.
- **Cancellation semantics:** a Python thread running a tool cannot be killed.
  Cancellation is observed between model chunks and tool calls, not mid-handler.
- **Security defaults (done in Phase 2a):** `full_access` is now opt-in and
  the overlay prompts; existing explicit `SCOUT_FULL_ACCESS=true` values still
  take precedence and should be reviewed on upgrade.
- **Provider drift:** the SDK is pinned loosely (`>=0.3`); capability detection
  and streaming tests use fakes, not live calls.
- **Windows-first:** wake word, hotkey, screenshot and input paths are Windows
  specific; Linux/macOS adapters are unverified.
- **Docs drift:** `docs/architecture.md` described the old Tk overlay; it is
  updated alongside this report.
- **Coverage gaps:** no live-provider contract tests, no long-running task tests,
  no overlay interaction tests beyond renderer/worker regressions.

## Phase 1 — what changed (and what did not)

Implemented:

- `scout/events.py`: immutable `Event`, `EventType`, synchronous fan-out bus
  with async queue consumers and listener failure isolation.
- Provider layer: `ProviderCapabilities`, `ProviderChunk`, async `stream()` with
  a safe non-streaming fallback, and a native Gemini async stream that emits
  text deltas and preserves the raw model content for replay.
- Agent: `run_async()` with `MODEL_CHUNK`/tool/approval/success/error/cancel
  events, tool execution off the event loop, and `CancellationToken` /
  `AgentCancelled`. `run()` remains the synchronous facade.
- CLI: terminal token streaming when stdout is a TTY; piped output and tests keep
  the existing answer panel. Non-TTY output is unchanged.
- Reliability fixes found during the audit: the overlay worker crashed after
  every successful run (`succeeded_tools` count treated as a list) and the
  completion handler built history with the `Turn` union instead of
  `AssistantTurn`; both are fixed and regression-tested. Tool batches larger
  than the per-reply cap are now reported to the model instead of silently
  dropped.
- Tests: event bus, async streaming/cancellation/limit/approval events, Gemini
  stream parsing, CLI stream renderer, and overlay worker/history regressions.

Deliberately not implemented in Phase 1: task planner/verifier, security default
changes, memory, vision, TTS, workflow/skill engine, UI event bridge. Those are
Phase 2+ and must not be claimed as working.

## Phase 2a — security slice (implemented)

The highest-severity finding in section E is now closed by default:

- `SCOUT_FULL_ACCESS` defaults to `false`; `READ`/`LOW_RISK` still run
  automatically, while `WRITE`/`EXECUTE`/`HIGH_RISK` require an approval.
- The overlay no longer uses `AllowAllApprover`. `OverlayApprover` queues a PyQt
  dialog onto the GUI thread while the worker waits. Deny is the default on
  timeout, window close and exceptions; high-risk calls require typing `allow`.
- `scout/control.py` adds a process-wide `StopController`. Every active run
  registers its cancellation token, so the overlay pill/HUD, tray, character
  menu, approval dialogs and CLI Ctrl+C can all stop work immediately. Stop also
  denies a pending approval, and cancels no longer masquerade as errors.
- New tests cover the secure default, the stop controller, a genuinely running
  agent cancelled through the global controller, the queued approval bridge and
  the worker's cancelled signal.

Later Phase 2 slices are now implemented too; see Phase 2b below.

## Phase 2b — remaining Phase 2 slices (implemented)

- **Permission manager:** `PolicyEngine.evaluate()` returns AUTO/ASK/DENY. Hard
  denies from `SCOUT_DENIED_TOOLS` win over full access; auto-approved tools and
  session grants skip the prompt; all decisions are audited.
- **Audit persistence:** `security/audit.py` writes redacted, size-rotated JSONL
  records and the `scout audit` command reads them. Run start/finish/cancel,
  approvals, denials and tool results are covered.
- **Planner/verifier:** the model-visible `update_plan` tool stores a bounded
  plan; unfinished/failed plans trigger exactly one verification turn before the
  final answer. Plan changes emit `plan_updated`; results include the plan.
- **Overlay streaming:** `AgentHooks.on_model_chunk` drives a queued Qt signal so
  the HUD renders model deltas while the worker runs.
- **Screenshot privacy:** sensitive focused windows are refused; old/excess
  captures are pruned.
- **Credentials:** Windows Credential Manager storage with `.env` fallback.
- **Wake privacy:** cloud wake audio is opt-in (`SCOUT_WAKE_CLOUD_FALLBACK`),
  local custom detectors and the Windows hotkey remain available.
- **Egress:** optional `SCOUT_EGRESS_ALLOWLIST` for URLs Scout opens.

Remaining after Phase 2 are the separate Phase 3+ pillars. Phase 3 is now
implemented; see below. SQLite conversation memory, developer/Git tools, local
ASR/TTS, workflows and skills, page-reading browser research, multi-agent
orchestration and release engineering remain future work.

## Phase 3 — screen perception (implemented)

- `perception.py` adds active-window metadata (title, process, pid, rectangle,
  minimized), full-screen and region capture, and window-title privacy masks.
- Sensitive focused windows are refused; sensitive background windows are
  blacked out before the PNG is written.
- `active_window`, `screenshot_region` and `read_screen_text` tools were added.
- Local OCR uses the Windows OCR engine through PowerShell, so no new Python
  dependency is required. Small crops are upscaled before recognition.
- `shot-`, `region-` and `ocr-` captures share the existing age/count retention
  policy.

Not included in Phase 3: accessibility-tree/UI-element automation, OCR language
selection, and document OCR. UI actions still use coordinates, so they remain
unverified beyond the OS accepting the input.
