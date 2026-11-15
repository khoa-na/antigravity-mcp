# antigravity skill

A Claude Code **skill** that lets Claude delegate a bounded coding task to the **Google
Antigravity agent** (Gemini via the `agy` CLI) by running `agy -p` directly. There is no
server, no MCP configuration and no Python package: the whole project is one `SKILL.md`.

```text
you ── "ask Antigravity to ..." / /antigravity <task> ──► Claude Code
                                                            │ reads SKILL.md
                                                            ▼
                      cd <repo> && agy -p "..." --output-format json --print-timeout ...
                                                            │ one JSON object on stdout
                                                            ▼
                                       Claude reads it, then verifies git diff + tests itself
```

## Install

1. Install the Antigravity CLI `agy` and log in once (run `agy` interactively). `agy` must be on
   `PATH`; check with `agy models`.
2. Make the skill visible to Claude Code. It is picked up automatically inside this repo
   (`.claude/skills/antigravity/`). To use it in every project, copy that folder to
   `~/.claude/skills/antigravity/`.
3. Say "ask Antigravity to ..." or type `/antigravity <task>`.

Nothing else to install. Other harnesses that read `SKILL.md` can use the same folder; the
`hooks:` frontmatter is a Claude Code feature, so elsewhere the skill's "always verify yourself"
steps are the only safeguard.

## What it does

[`SKILL.md`](.claude/skills/antigravity/SKILL.md) tells Claude how to:

- brief the agent (task, file scope, constraints, acceptance checks; templates in
  [`references/prompts.md`](.claude/skills/antigravity/references/prompts.md));
- run `agy -p` **in the target repo**, so the repo is the agent's working directory, with
  `--output-format json`, an explicit `--print-timeout`, and in the background when it may outlive
  the shell tool's limit;
- read the JSON result and exit code, and continue a conversation with `--conversation <id>`;
- do a **read-only review** that `agy` itself enforces (deny rules block every file write and shell
  command, even under `--dangerously-skip-permissions`);
- show the remaining quota with `agy -p "/quota"` (answered by the CLI, no model call);
- verify the result itself (`git status`, `git diff`, run the tests) instead of trusting
  "SUCCESS".

Three hooks (Claude Code only) fire for `agy -p ...` Bash calls. They are plain bash in the skill's
frontmatter (`shell: bash`, reading the event JSON on stdin), so there is nothing to install:

- **PreToolUse** blocks a call without `--print-timeout` or `--output-format`.
- **PostToolUse** reminds Claude to inspect the tree; for a call started with `run_in_background`
  it says the run has *not finished* yet. It stays silent for `/quota` and `/usage`.
- **PostToolUseFailure** fires after a non-zero exit (error, print timeout), because such a run may
  have left half-edited files.

## How `agy -p` behaves (verified)

| | |
|---|---|
| Output | `--output-format json` prints `{conversation_id, status, response, error, finished_reason, usage, ...}` on stdout. |
| Exit codes | `0` success, `1` error (bad model, auth, ...), `2` usage error (no JSON), `3` print timeout (partial response). |
| Timeout | `--print-timeout` defaults to `0`, which waits forever: always pass it. |
| Working directory | The directory you start `agy` in. Starting it in the repo makes the repo the agent's cwd. |
| Permissions | Headless mode cannot prompt. In a workspace `agy` does not trust, tool calls are auto-denied and the reply is empty, so tasks need `--dangerously-skip-permissions` (or an allow-rule / a trusted workspace in `~/.gemini/antigravity-cli/settings.json`, which is yours to configure). |
| Deny rules | `--settings '{"permissions":{"deny":["command(*)","write_file(*)"]}}'` still blocks shell commands and every file write/edit **under** `--dangerously-skip-permissions`. Only these two rules were tested. |
| Slash commands | Some run in print mode without a model call: `-p "/quota"` (alias `/usage`) prints the remaining limits. In Git Bash on Windows set `MSYS_NO_PATHCONV=1` or the leading `/` is rewritten into a path. |
| Concurrency | Two runs in different directories at once both succeed, each with its own `conversation_id`. |
| Stopping | Stopping the Claude Code background task killed `agy` and its child process (tested on Windows). On `--print-timeout`, `agy` ends the turn and its background tasks itself and exits 3. |
| Model | Without `--model`, `agy` uses the model configured in its own settings. |

## Compared with typing `agy` yourself

Same agent, same tools, same account and quota, same conversation history. Different: it is
headless and single-turn (it cannot ask you questions mid-run), it runs with permissions
auto-approved, it has a print timeout, and Claude Code, not you, writes the brief and reviews
the result.

## Limits

- **No lock.** Nothing stops two `agy` runs in the *same* repo at once; the skill tells Claude to
  run one at a time per repo. Different repos in parallel are fine.
- **No hard cleanup guarantee.** The tested stop path left nothing behind, but nothing here would
  reap an orphan if the host process itself were killed outright (the old wrapper did this with a
  Windows Job Object). The skill tells Claude to look for strays and ask you.
- **Hooks are Claude Code only.** Other harnesses have their own hook configuration; "always
  verify yourself" in the skill is the portable safeguard.
- **No account rotation** (it relied on a private script this repo never shipped); a quota error is
  reported as is.
- **Not a sandbox.** `--dangerously-skip-permissions` lets the agent run any command and edit any
  file the OS permits. Use a clean `git status` and a restricted environment for untrusted tasks.

## History

Earlier versions were an MCP server (`server.py`), then a Python package with an
`antigravity-agent` CLI that wrapped `agy` (file lock, process-tree cleanup, result record, hooks).
Both are removed: `agy -p --output-format json` now provides the result record, exit codes and
conversation ID itself, starting it in the repo fixes the working directory, `/quota` replaces the
quota command, and deny rules replace the old text-only review backend. To get the old
code back, check out the commit before this change (`git log --oneline -- server.py agy_agent.py`).

## License

MIT
