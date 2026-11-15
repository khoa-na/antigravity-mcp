# antigravity-agent

A **skill + CLI** that lets Claude Code (or any harness with a shell tool) delegate a coding
task to the **Google Antigravity agent** (Gemini 3.x via the `agy` CLI). It is a real coding
agent with Read / Write / Bash tools, not just an LLM API: it reads files, runs commands and
edits the workspace you grant it.

There is no server and no MCP configuration. The skill tells the host agent which command to
run; the command prints one JSON record.

```text
Claude Code ── /antigravity ──► SKILL.md ──► antigravity-agent (agy_agent.py)
                                                 ├─ temp in.txt / out.txt
                                                 ├─ agy -p ... --dangerously-skip-permissions
                                                 ├─ cross-process lock + process-tree cleanup
                                                 └─ one JSON result record on stdout
```

## Install

Requirements: Windows, Linux or WSL2; Python 3.10+; the Antigravity CLI `agy`, logged in
at least once.

```bash
git clone https://github.com/khoa-na/antigravity-mcp.git
cd antigravity-mcp
pip install -e .          # or: uv pip install -e .
antigravity-doctor        # offline check; never reads credentials or runs the agent
python -m unittest discover -s tests -v
```

This provides three commands on `PATH`: `antigravity-agent`, `antigravity-agent-hook`,
`antigravity-doctor`.

### Install the skill (Claude Code)

The skill lives in [`.claude/skills/antigravity/`](.claude/skills/antigravity/SKILL.md) and is
picked up automatically inside this repo. To use it in every project, copy that folder to
`~/.claude/skills/antigravity/`. Then say "ask Antigravity to ..." or type `/antigravity <task>`.
Other harnesses that read `SKILL.md` can use the same folder; the `hooks:` frontmatter is a
Claude Code feature, so elsewhere the skill's "always verify yourself" steps are the only
safeguard.

### WSL2 / Linux

Use a native Linux `agy` installation and authenticate it inside WSL. `agy` is discovered on
`PATH`; `AGY_EXE` overrides it. A Windows `agy.exe` is not a drop-in replacement (prompts and
workspace grants use Linux paths).

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -e .
.venv/bin/antigravity-doctor
```

## Usage

```bash
antigravity-agent --prompt-file task.md --workspace . --timeout 900 --json
```

| Flag | Meaning |
|---|---|
| `PROMPT` / `--prompt` / `--prompt-file` / stdin | The task. |
| `--workspace DIR` | Directory granted via `--add-dir` (default: current directory; `none` omits it). **Not a sandbox.** |
| `--model`, `--effort low\|medium\|high`, `--system` | Model, reasoning effort, system instructions. |
| `--timeout SECONDS` | Total budget, `0 < t <= 86400`, shared across retries (default `AGY_AGENT_TIMEOUT`, 900). |
| `--conversation ID` / `--resume` | Continue a specific conversation / the CLI's most recent one. Not both. |
| `--json` | Print one JSON record (below) instead of plain text. |
| `--save-artifact` | Save the full reply under `.antigravity/artifacts/` and print a preview. |
| `--models` | List the models the CLI offers (live `agy models`). |
| `--review [FOCUS]` `[--staged]` | Read-only review of `git diff HEAD` through the text-only HTTP backend (cannot edit files). |
| `--quota` | Remaining quota per model (HTTP backend). |

Exit code is `0` only when the status is `succeeded`; `2` for invalid arguments.

### Result record

`--json` prints: `status`, `task_id`, `conversation_id`, `resume_latest`, `workspace`, `model`,
`exit_code`, `elapsed_seconds`, `verification="not_run_by_wrapper"`, `response`, `error`.

- `succeeded`: the CLI exited 0 and wrote a non-empty response file. It does **not** prove the
  code is correct or that tests passed. Inspect the diff and run tests yourself.
- `partial`: an answer was written before a nonzero exit. Not retried.
- `failed`, `timed_out`, `busy`: explicit failures (exit 1). Stdout-only CLI logs are never
  accepted as an answer.
- `conversation_id` is read from the CLI (`agy --output-format json`), so it is the ID of the
  conversation that was created or continued. Pass it back with `--conversation` for an exact
  follow-up; `--resume` means "the CLI's most recent conversation" globally, not per repo.
- Large replies are saved under `<workspace>/.antigravity/artifacts/` and `response` holds a
  preview plus the path (`AGY_MAX_INLINE_LINES`, `AGY_MAX_INLINE_CHARS`).

### Reliability

- **Timeouts.** The budget starts before the quota preflight; each CLI attempt gets the
  remaining time, with `agy --print-timeout` set 5% (at most 10 s) shorter so the CLI can exit
  cleanly. Your shell tool has its own limit (Claude Code Bash: 2 min default, 10 min max):
  run long tasks in the background.
- **One run at a time.** An in-process lock plus a file lock (`AGY_LOCK_FILE`, default
  `<tmp>/antigravity-agent.lock`) serialize runs across processes; the loser gets `busy`.
  It does not lock out a human editor or an interactive `agy`.
- **No orphans.** On timeout, exception or SIGTERM the whole `agy` process tree is killed
  (Windows: a Job Object with kill-on-close, which also reaps the tree if the wrapper is killed
  outright; POSIX: the process group). After a clean exit nothing the CLI left running is touched.
- **Retries.** Quota or auth errors can replay a task when no response file exists; inspect the
  working tree after failures.

### Hooks (Claude Code skill)

They only fire for `antigravity-agent` Bash calls that start the agent:
- **PreToolUse** blocks a task call that lacks `--timeout` or `--json`.
- **PostToolUse** and **PostToolUseFailure** inject `git status` / `git diff --stat`. A
  non-`succeeded` status exits 1, which Claude Code reports as a *failure* event, so both are
  wired. After a failure the note warns that files may be half-edited; for background runs a
  success event says the task has not finished yet.

## Authentication

Two independent paths:

- The agent (`antigravity-agent "..."`) uses the native `agy` CLI login. It needs no HTTP
  backend tokens. Set `AGY_PROACTIVE=0` to skip the optional backend quota preflight when you
  only use the CLI.
- `--review` and `--quota` (and the optional preflight / account rotation) use the HTTP
  backend. On Windows it reads the Credential Manager entry `gemini:antigravity`. On
  WSL/Linux/macOS supply `AGY_ACCESS_TOKEN` (optionally `AGY_REFRESH_TOKEN`,
  `AGY_TOKEN_EXPIRY` in ISO 8601) from your environment or secret manager. Refreshed tokens
  stay in the running process. Never put token values in the repository.

Automatic account rotation is optional and needs a separate account manager's `agy_switch.py`
(`AGY_SWITCH_SCRIPT`); this repository does not ship it.

## Security

The agent runs with `agy --dangerously-skip-permissions`. Workspace selection and the locks do
**not** confine the filesystem. Use a clean `git status` (or a throwaway worktree) and a
separately restricted environment for untrusted tasks. `--review` is text-only and cannot
execute tools.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `AGY_MODEL` | `gemini-3.8-flash-high` | Default model (`antigravity-agent --models` lists what is available). |
| `AGY_EXE` | auto-detected | Explicit path to `agy`. |
| `AGY_AGENT_TIMEOUT` | `900` | Default `--timeout`. |
| `AGY_DEFAULT_WORKSPACE` | current directory | Default `--workspace`; `none` omits the grant. |
| `AGY_AGENT_PROFILE` | unset | Stable profile directory for the CLI (authenticate it first). |
| `AGY_LOCK_FILE` | `<tmp>/antigravity-agent.lock` | Cross-process lock file. |
| `AGY_MAX_INLINE_LINES` / `AGY_MAX_INLINE_CHARS` | `350` / `16000` | Threshold before a reply becomes an artifact. |
| `AGY_PROACTIVE` | on | `0` skips the backend quota preflight. |
| `AGY_ACCESS_TOKEN`, `AGY_REFRESH_TOKEN`, `AGY_TOKEN_EXPIRY` | unset | Backend auth on WSL/Linux. |
| `AGY_SWITCH_SCRIPT` | unset | Optional account rotation script. |
| `CLOUD_CODE_URL` | Google's backend | Only for a custom local unlock proxy. |

## Coming from the MCP server

Earlier versions shipped `server.py`, an MCP server with `ask-antigravity` and friends. It was
removed in v3: the skill and the CLI cover the same agent without a long-running process.
`generate-tests`, `ping` and the MCP resources/prompts are gone; their useful parts are now
prompt templates in the skill (`references/prompts.md`) and a one-line connectivity check in
`SKILL.md`. The last revision with the server is the commit before this change
(`git log --oneline -- server.py`).

## License

MIT
