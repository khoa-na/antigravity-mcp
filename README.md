# antigravity skill

A Claude Code **skill** that lets Claude delegate a bounded coding task to the **Google
Antigravity agent** (Gemini via the `agy` CLI) by running `agy -p` as a **background worker**.
There is no server, no MCP configuration, no Python package and no Claude sub-agent in between:
the project is one `SKILL.md` plus prompt templates. The fork-like features come from the `agy`
CLI itself:

| You want | How `agy` gives it |
|---|---|
| Runs while you keep talking to Claude | Bash `run_in_background`; Claude is notified when it exits |
| A clean main context | The brief makes `agy` end with a short (<= 15 lines) report; only that is read |
| Several workers at once | One run per directory or worktree (different directories work in parallel) |
| Isolated changes | `git worktree`: the worker edits a throwaway checkout until you accept it |
| Follow-up with the worker's memory | `--conversation <id>` (the ID is in the JSON result) |
| An independent check | A second, read-only `agy` run on the diff (deny rules enforced by `agy`) |

```text
you ── "ask Antigravity to ..." / /antigravity <task> ──► Claude Code (reads SKILL.md)
                                                            │ writes the brief, then (background):
                    cd <repo or worktree> && agy -p "..." --output-format json --print-timeout ...
                                                            │ short JSON result + completion notice
                                                            ▼
          Claude checks git diff + tests itself, optionally a read-only agy review, then reports
```

## Install

1. Install the Antigravity CLI `agy` and log in once (run `agy` interactively). `agy` must be on
   `PATH`; check with `agy models`.
2. Make the skill visible to Claude Code. It is picked up automatically inside this repo
   (`.claude/skills/antigravity/`). To use it in every project, copy that folder to
   `~/.claude/skills/antigravity/`.
3. Say "ask Antigravity to ..." or type `/antigravity <task>`.

Nothing else to install. Other harnesses that read `SKILL.md` can reuse the instructions, but the
`hooks:` frontmatter is a Claude Code feature; elsewhere the skill's "always verify yourself" steps
are the only safeguard.

## What it does

[`SKILL.md`](.claude/skills/antigravity/SKILL.md) tells Claude how to:

- brief the agent (task, file scope, constraints, acceptance checks, and the short reply format;
  templates in [`references/prompts.md`](.claude/skills/antigravity/references/prompts.md));
- run `agy -p` **in the target repo or worktree**, so it is the agent's working directory, with
  `--output-format json` and an explicit `--print-timeout`, in the background;
- run risky or parallel work in an **isolated `git worktree`**, and bring it back only after review;
- read the JSON result and exit code, and continue with `--conversation <id>` (also after a print
  timeout);
- get a **second opinion**: a separate read-only `agy` run on the diff, enforced by deny rules that
  block every file write and shell command even under `--dangerously-skip-permissions`;
- show the remaining quota with `agy -p "/quota"` (answered by the CLI, no model call);
- verify the result itself (`git status`, `git diff`, run the tests) instead of trusting "SUCCESS".

Three hooks (Claude Code only) fire for `agy -p ...` Bash calls. They are plain bash in the skill's
frontmatter (`shell: bash`, reading the event JSON on stdin), so there is nothing to install:

- **PreToolUse** blocks a call without `--print-timeout` or `--output-format`. (In a headless test
  it caught the model forgetting `--print-timeout` on its first attempt.)
- **PostToolUse** reminds Claude to inspect the tree; for a call started with `run_in_background`
  it says the run has *not finished* yet. It stays silent for `/quota` and `/usage`.
- **PostToolUseFailure** fires after a non-zero exit (error, print timeout), because such a run may
  have left half-edited files.

Measured (headless, real `agy`, main model Sonnet 5.5): an isolated-worktree task plus a read-only
`agy` review took 19 turns and about $0.30 on the Claude side; the worker needed about 40 s, the main
checkout stayed untouched, and the result matched an independent `git diff` and test run. Here the
main model does the orchestration, so the Claude-side cost depends on which model you run.

A second end-to-end test used the **Gemini 4 Hydrogen** model (`gemini-4-hydrogen-medium`) through the
skill: it built a small module (`textstats.py` with a CLI and 8 unit tests, then a `--min-length`
option and 3 more tests, 30-40 s per run). Every result matched an independent `git diff`, test run
and CLI check, a read-only Hydrogen review approved it, two workers ran in parallel in separate
worktrees, a `--conversation` follow-up remembered its earlier work, and the patch-back and cleanup
recipes worked. The test also caught a real bug in an earlier version of the skill: with
`--add-dir` the worker wrote its files outside the worktree while reporting success; the
independent verification step caught it.

## How `agy -p` behaves (verified)

| | |
|---|---|
| Output | `--output-format json` prints `{conversation_id, status, response, error, finished_reason, usage, ...}` on stdout. |
| Exit codes | `0` success, `1` error (bad model, auth, ...), `2` usage error (no JSON), `3` print timeout (partial response). |
| Timeout | `--print-timeout` defaults to `0`, which waits forever: always pass it. |
| Working directory | The directory you start `agy` in, **unless you pass `--add-dir`**: that re-roots the agent's cwd to the added directory (tested). So the skill never uses `--add-dir`; under `--dangerously-skip-permissions` the agent reads briefs and diffs stored outside the repo anyway. |
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

A forked variant was also tried and dropped: `context: fork` with a custom Haiku subagent that only
drives `agy` and reports back. It works (about $0.006 on a small task, report matched the real
diff), but it needs a second file, the subagent cannot see the conversation or ask questions, and
hooks declared in a *skill's* frontmatter do not fire inside a fork (they must live in the
subagent's own frontmatter, where they do). The `agy`-native worker pattern above gives the same
isolation and background behavior without a Claude model in the middle.

## License

MIT
