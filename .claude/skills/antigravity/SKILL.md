---
name: antigravity
description: Delegate a bounded coding task to the Google Antigravity agent (Gemini, via the `agy` CLI) through the `antigravity-agent` command. Use only when the user explicitly asks to use Antigravity, Gemini or agy as a second coding agent. Do not use it for tasks you can just do yourself.
when_to_use: User says things like "ask Antigravity/Gemini to ...", "have agy implement ...", "get a second agent to ...".
argument-hint: "[task description]"
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          if: "Bash(antigravity-agent *)"
          command: antigravity-agent-hook pre
  PostToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          if: "Bash(antigravity-agent *)"
          command: antigravity-agent-hook post
---

# Antigravity coding agent

`antigravity-agent` runs the real Antigravity CLI agent once, with Read/Write/Bash tools,
inside a workspace directory. It edits files directly. Treat it as a junior contractor:
brief it precisely, then check its work yourself.

Task from the user: $ARGUMENTS

## Safety first
- The agent runs with `--dangerously-skip-permissions`. Workspace selection is **not** a
  sandbox. Only delegate tasks the user asked for, in a repo whose changes are recoverable
  (clean `git status` first, or tell the user it is not).
- Never put secrets or tokens in the prompt.
- One run at a time. A second concurrent call fails with `status: "busy"`.

## Check it works (once)
```bash
antigravity-doctor
```
Needs `pip install -e <this repo>` (provides `antigravity-agent`, `antigravity-doctor`) and a
logged-in `agy` CLI. If a command is not found, say so and stop; do not guess a path.

## Brief the agent
Write the prompt to a file (long prompts break shell quoting) and include:
1. The task, in one paragraph.
2. Exact files/dirs it may touch, and files it must not touch.
3. Constraints (style, no new dependencies, no commits, no network).
4. Acceptance checks it should run itself (e.g. the test command).

## Run it
Always pass `--timeout` and `--json` (a hook blocks the call otherwise).

```bash
antigravity-agent --prompt-file /path/to/prompt.md --workspace . --timeout 900 --json
```

**Timeouts are what break this.** Real runs take 100-150 s and can take many minutes. Your
shell tool has its own limit (Claude Code Bash: 2 min default, 10 min max). Either:
- run the Bash call with `run_in_background: true` and `--timeout 900`, then read the
  result when the completion notification arrives (preferred); or
- run in the foreground with the Bash `timeout` set above `--timeout` plus a margin, and
  `--timeout` no more than ~540 for a 600000 ms tool limit.

Other flags: `--model gemini-3.1-pro-high` (default `gemini-3.8-flash-high`),
`--effort low|medium|high`, `--system "..."`, `--workspace none` to omit the directory grant,
`--save-artifact` to store the full reply under `.antigravity/artifacts/`.

## Read the result
Stdout is one JSON object. Exit code is 0 only for `succeeded`.

| status | meaning | what to do |
|---|---|---|
| `succeeded` | exited 0 with a non-empty answer | **Not proof of correctness.** Verify (below). |
| `partial` | answer written, but CLI exited nonzero | Treat as untrusted; inspect the diff. Not retried. |
| `failed` | no answer / launch or auth problem | Read `error`. Auth: ask the user to log in to `agy`. |
| `timed_out` | budget exhausted | Narrow the task or raise `--timeout`; inspect the tree, it may be half-edited. |
| `busy` | another Antigravity run holds the lock | Wait, then retry once. |

`response` may be a preview if the reply was large; the full text path is in it.
`conversation_id` is `null` for new runs (the CLI's generated ID cannot be discovered).

## Follow-ups
- `--resume` continues the CLI's **most recent** conversation (global, not per repo). Use it
  only right after your own run, with nothing else using Antigravity in between.
- `--conversation <id>` resumes a specific existing ID, if the user gave you one.
- Never pass both.

## Always verify yourself
After every run, regardless of status or hooks (other harnesses do not run them):
1. `git status` and `git diff` - did it touch only the allowed files?
2. Run the project's tests/linters yourself.
3. Report to the user what the agent claimed vs. what you verified.
