---
name: antigravity
description: Delegate a bounded coding task to the Google Antigravity agent (Gemini, via the `agy` CLI) by running `agy -p` as a background worker. Use only when the user explicitly asks to use Antigravity, Gemini or agy as a second coding agent. Do not use it for tasks you can just do yourself.
when_to_use: User says things like "ask Antigravity/Gemini to ...", "have agy implement ...", "get a second agent to ...".
argument-hint: "[task description]"
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          if: "Bash(agy -p *)"
          shell: bash
          command: |
            input=$(cat)
            case "$input" in
              *"agy -p"*) ;;
              *) exit 0 ;;
            esac
            case "$input" in
              *"--print-timeout"*) ;;
              *) echo "agy -p needs --print-timeout (its default, 0, waits forever). Add e.g. --print-timeout 900s and retry." >&2; exit 2 ;;
            esac
            case "$input" in
              *"--output-format"*) ;;
              *) echo "agy -p needs --output-format json so the result is machine-readable. Add it and retry." >&2; exit 2 ;;
            esac
  PostToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          if: "Bash(agy -p *)"
          shell: bash
          command: |
            input=$(cat)
            case "$input" in
              *"agy -p"*) ;;
              *) exit 0 ;;
            esac
            case "$input" in
              *'\"/quota\"'*|*'\"/usage\"'*|*"'/quota'"*|*"'/usage'"*) exit 0 ;;
            esac
            if printf '%s' "$input" | grep -Eq '"run_in_background"[[:space:]]*:[[:space:]]*true'; then
              printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"agy was started in the background and has NOT finished. Wait for the completion notification, then read its short JSON result, run git status and git diff in the target repo, and run its tests before reporting done."}}'
            else
              printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"agy finished. A SUCCESS status is not verification of correctness. Read the JSON, then run git status and git diff in the repo you delegated to and run its tests before reporting done."}}'
            fi
  PostToolUseFailure:
    - matcher: "Bash"
      hooks:
        - type: command
          if: "Bash(agy -p *)"
          shell: bash
          command: |
            input=$(cat)
            case "$input" in
              *"agy -p"*) ;;
              *) exit 0 ;;
            esac
            printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUseFailure","additionalContext":"agy did NOT succeed (error, print timeout or killed): the JSON on stdout says which. It may have edited files before stopping, so inspect git status and git diff in the target repo before retrying or reporting."}}'
---

# Antigravity coding agent

`agy -p` runs the Antigravity CLI agent **once, headless**, with Read/Write/Bash tools. It is
the same agent a person gets by typing `agy` in a terminal, with these differences: one prompt,
one turn, no way to ask you questions mid-run, and no permission prompts.

Treat each run as a **background worker that you manage through the `agy` CLI**, no Claude
sub-agent in between:

| You want | How `agy` gives it |
|---|---|
| Runs while you keep talking to the user | Bash `run_in_background: true`; you are notified when it exits |
| Your context stays clean | The brief makes `agy` end with a short report; you read only that |
| Several workers at once | One run per repo or worktree (different directories work in parallel) |
| Isolated changes | `git worktree`: the worker edits a throwaway checkout (see "Isolated run") |
| Follow-up with the worker's memory | `--conversation <id>` |
| An independent check | A second, read-only `agy` run on the diff (see "Second opinion") |

Task from the user: $ARGUMENTS

## Safety first
- `--dangerously-skip-permissions` is required for tasks (headless mode cannot prompt; without it,
  shell commands in a workspace `agy` does not trust are silently denied and you get an empty
  reply). So the agent can run any command and edit any file the OS lets it. Only delegate tasks
  the user asked for, in a repo whose changes are recoverable (clean `git status` first, or use an
  isolated worktree, or say it is not).
- Explicit deny rules still apply under that flag (verified); see "Second opinion" for the syntax.
- Never put secrets or tokens in the prompt.
- Never run two workers in the *same* directory: nothing locks it. Different directories are fine
  (verified).

## Check it works (once)
```bash
agy models
```
Lists the model IDs (first column) and proves `agy` is installed and logged in. If the command is
not found or errors, say so and stop; do not guess a path. If it says the account must be verified,
that is the user's to do.

## Brief the agent
Write the task to a file **outside the repo** (a temp dir; long prompts break shell quoting) with:
1. The task, in one paragraph.
2. Exact files/dirs it may touch, and files it must not touch.
3. Constraints (style, no new dependencies, **no commits**, no network).
4. Acceptance checks it should run itself (e.g. the test command).
5. "Read AGENTS.md / GEMINI.md first if present" when the repo has project rules.
6. **The reply format**, so its answer is the short report you read instead of a transcript:
   > Your final reply must be at most 15 lines of plain text: (a) files you changed, (b) each
   > command you ran with its real result, (c) anything you could not do or are unsure about. No
   > code listings.

Templates (TDD, review, security audit, test generation): [references/prompts.md](references/prompts.md).

## Run it
Start the process **in the target repo or worktree**: that makes it the agent's working directory,
as when a person runs `agy` there.

**Do not use `--add-dir`.** It re-roots the agent's working directory to the added directory
(verified), so the agent would no longer be "in" the repo, and with a worktree it could end up in
the parent folder that holds your other worktrees. It is not needed: under
`--dangerously-skip-permissions` the agent can read the brief and diff files that live outside the
repo. On Windows write the paths inside the prompt text as `C:/Users/...` (forward slashes).

```bash
cd /abs/path/to/repo && agy -p "Read /abs/tmp/prompt.md and carry out the task described there. Follow its reply format exactly." --dangerously-skip-permissions --output-format json --print-timeout 900s
```

- **Run it with the Bash tool's `run_in_background: true`.** Real runs take 100-150 s up to many
  minutes, longer than the Bash foreground limit (2 min default, 10 min max). Tell the user it
  started, carry on, and read the result when the completion notification arrives. If your harness
  has no background mode, run in the foreground with the Bash `timeout` above `--print-timeout`.
- **Always pass `--print-timeout`** (Go duration: `900s`, `15m`) and `--output-format json`; a hook
  blocks the call otherwise. The `--print-timeout` default, 0, waits forever. When it fires, `agy`
  stops the turn and its background tasks itself and exits 3 (partial work).
- Do not pass `--model` unless the user asked: `agy` then uses the model they configured. Otherwise
  `--model <id>` (from `agy models`) and `--effort low|medium|high|xhigh|max`.
- **Git Bash on Windows rewrites a prompt that starts with `/`** into a path. Prefix such commands
  with `MSYS_NO_PATHCONV=1` (needed for the quota command below).

## Isolated run (recommended for risky, large or parallel work)
Give the worker its own checkout so your working tree is untouched until you decide:

```bash
cd /abs/path/to/repo && git worktree add -b agy/<id> /abs/tmp/agy-<id> HEAD
cd /abs/tmp/agy-<id> && agy -p "..." --dangerously-skip-permissions --output-format json --print-timeout 900s
```
- The worktree starts from `HEAD`: uncommitted changes in the main checkout are **not** in it.
- Review the result with `git -C /abs/tmp/agy-<id> status --short` and `git diff`, and run the tests
  there. Bring accepted work back only with the user's OK, e.g.
  `git -C /abs/tmp/agy-<id> diff HEAD > /abs/tmp/agy-<id>.patch && git -C /abs/path/to/repo apply /abs/tmp/agy-<id>.patch`.
- Clean up when the user has accepted or rejected it: `git worktree remove --force /abs/tmp/agy-<id>`
  and `git branch -D agy/<id>`. Several workers = several worktrees, each with its own run.

## Read the result
Stdout is one JSON object (do not scrape anything else). With the reply format above it is small:

| field | meaning |
|---|---|
| `status` | `SUCCESS` or `ERROR` |
| `response` | the agent's final reply: your short report (partial if it was cut off) |
| `error`, `finished_reason` | why it failed, e.g. `print_timeout`, `error` |
| `conversation_id` | keep it for follow-ups (empty if it failed before starting) |
| `duration_seconds` | **not** this run's time when you use `--conversation`: in a 6-step test it read 38, 88, 163, 246, 323 and 2745 s while each call really took 33-86 s (it looks like time since the conversation began). Time the call yourself. |

Exit code: `0` success, `1` error (bad model, auth, ...), `2` usage error (unknown or stray
argument: **no JSON is printed**, only a message on stderr), `3` print timeout (partial). A
non-zero code makes your shell tool report a failed command: read the JSON anyway. `SUCCESS` means
the turn completed, not that the code is right. After `ERROR`/`3` the tree may be half-edited.

## Follow-ups
- `--conversation <conversation_id>` resumes that exact conversation (preferred: it survives other
  Antigravity use in between). After exit 3, continue with "Continue and finish the task".
- `--continue` resumes the CLI's **most recent** conversation, global and not per repo. Use it only
  right after your own run.
- Pass the same safety flags again; each run is a new process.

## Quota
```bash
MSYS_NO_PATHCONV=1 agy -p "/quota" --output-format json --print-timeout 30s
```
Answered by the CLI itself: instant, no model call, no quota spent. `response` is a tab-separated
table of weekly and 5-hour limits remaining per model group. Check it before a long task, and
report an exhausted quota instead of retrying.

## Second opinion (read-only `agy` review of the diff)
A separate `agy` run can check the worker's change without being able to touch anything: deny rules
block every file write and shell command even under `--dangerously-skip-permissions` (verified:
`write_file` covers `edit_file` too).

```bash
cd /abs/path/to/repo-or-worktree && git diff HEAD > /abs/tmp/review.diff && agy -p "You are a senior reviewer. Read /abs/tmp/review.diff and review it, reading other files in the repo for context if needed. You cannot and must not edit files or run commands. Answer in at most 15 lines: Critical issues (file:line), Suggestions, Verdict." --dangerously-skip-permissions --settings '{"permissions":{"deny":["command(*)","write_file(*)"]}}' --output-format json --print-timeout 300s
```
Untracked files are not in `git diff HEAD`; add them to the diff file yourself if they matter. These
two rules are the ones that were tested; run `git status` afterwards anyway. This is a second model
run, not proof: you still verify with tests.

## If it goes wrong
- Stopping a background Claude Code task (or its timeout) killed `agy` and its child processes in
  a test on Windows (verified), so nothing is normally left running. If you still suspect a stray
  process, look (`tasklist | findstr /i agy` on Windows, `pgrep -a agy` elsewhere), tell the user,
  and stop it only with their OK.
- Quota or rate-limit errors: report them; do not loop retries.

## Always verify yourself
After every run, regardless of status or hooks (other harnesses do not run them):
1. `git status` and `git diff` in the repo or worktree: did it touch only the allowed files?
2. Run the project's tests/linters yourself.
3. Tell the user what the agent claimed (its short report) versus what you verified.
