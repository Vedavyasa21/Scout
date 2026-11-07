# Contributing to Scout

Thanks for helping build Scout. This project values small, reviewable changes and
an explicit safety model over feature count.

## Getting started

```bash
git clone https://github.com/<your-username>/scout.git
cd scout
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
```

Copy `.env.example` to `.env` and use a **test** API key for manual runs. Never
commit `.env`.

## Ground rules

- Every new capability is a registered tool with a JSON schema and permission level.
- Never execute model-generated shell strings, and never use `shell=True`.
- Never bypass `PermissionGate`; new permission levels need discussion first.
- Filesystem tools must confine themselves to configured workspaces.
- Do not log secrets, credentials or private file contents.
- Do not claim a feature works unless a test or a reproducible manual check proves it.
- Do not weaken or skip tests to make CI pass.

## Adding a tool

1. Create or extend a module under `src/scout/tools/`.
2. Write a pure-ish handler `(args, ToolContext) -> ToolResult` that catches expected
   errors and returns `ok=False` instead of raising.
3. Declare a precise schema: `additionalProperties: false`, bounds, enums.
4. Choose the least-privileged `PermissionLevel`.
5. Register it in the module's `register(registry)`.
6. Add tests: validation, permission level, failure paths, workspace escape.

Only `READ` and `LOW_RISK` tools can run today. Write/execute tools should be
developed behind the Milestone 2 approval workflow.

## Pull requests

- Keep the change focused; one concern per PR.
- Include tests for behavior changes.
- Describe the security implications of new tools.
- Update `README.md` or `docs/architecture.md` when behavior changes.
- CI must pass: Ruff and the test suite on Windows and Linux.

## Reporting bugs

Use the issue template and include: OS, Python version, `scout doctor` output
(redact anything sensitive), the command, and the observed vs expected result.
