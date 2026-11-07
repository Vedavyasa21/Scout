# Security policy

Scout is an agent that can read files and launch programs on your machine, so
security issues are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through GitHub Security
Advisories ("Report a vulnerability" on the repository Security tab) or email the
maintainers listed in `pyproject.toml`.

Include a description, reproduction steps, affected version and impact. You can
expect an acknowledgement within a few days and a coordinated disclosure timeline.

## Supported versions

Only the latest released version receives security fixes while the project is in
alpha.

## Design constraints we maintain

- Model output is untrusted: arguments are validated locally and never passed to a
  shell.
- Filesystem access is resolved and confined to approved workspaces; traversal and
  symlink escapes are rejected.
- Only allowlisted applications can be launched.
- `WRITE`, `EXECUTE` and `HIGH_RISK` tools are refused until the approval workflow
  ships.
- API keys are read from the environment, redacted from logs, and never written to
  prompts or memory.
- Content read from files or web pages is treated as data, never as instructions.

## Out of scope for this alpha

- Malicious code already running with the user's privileges.
- Prompt-injection that only changes wording, without causing a denied action.
- The model provider's own handling of data sent to its API.
