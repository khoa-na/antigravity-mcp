#!/usr/bin/env python3
"""Claude Code hook handlers for the `antigravity` skill (stdlib only).

Installed as the `antigravity-agent-hook` console script and referenced from the
skill's frontmatter, so the skill folder needs no copied Python or relative paths.

    antigravity-agent-hook pre    PreToolUse  (Bash): block calls without --timeout/--json
    antigravity-agent-hook post   PostToolUse / PostToolUseFailure (Bash): inject a working-tree
                                  summary. The CLI exits 1 for every non-"succeeded" status, and
                                  Claude Code reports that as PostToolUseFailure, so both events
                                  must be wired to this handler.

Hooks are a safety net, not the contract: SKILL.md still tells the host agent to
inspect the diff and run tests itself, because other harnesses will not run these.
"""
import json
import re
import subprocess
import sys

_CALL = re.compile(r"(^|[\s;&|(])antigravity-agent(\.exe)?(\s|$)")
_MAX_CONTEXT = 2000


def _event():
    try:
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _command(event):
    tool_input = event.get("tool_input")
    return tool_input.get("command", "") if isinstance(tool_input, dict) else ""


def _has_flag(command, flag):
    return re.search(rf"(^|\s){re.escape(flag)}(\s|=|$)", command) is not None


def pre(event):
    command = _command(event)
    if not _CALL.search(command) or _has_flag(command, "--help") or _has_flag(command, "-h"):
        return 0
    missing = [f for f in ("--timeout", "--json") if not _has_flag(command, f)]
    if not missing:
        return 0
    print("antigravity-agent must be called with " + " and ".join(missing) + ". "
          "Add `--timeout <seconds>` (below your shell tool's limit) so the run cannot outlive "
          "the host, and `--json` so the status is machine-readable. Then retry.", file=sys.stderr)
    return 2


def _git(args, cwd):
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    # rstrip only: the first `git status --short` column is significant (" M" vs "M ").
    return done.stdout.rstrip() if done.returncode == 0 else None


def post(event):
    command = _command(event)
    if not _CALL.search(command) or _has_flag(command, "--help") or _has_flag(command, "-h"):
        return 0
    failed = event.get("hook_event_name") == "PostToolUseFailure"
    tool_input = event.get("tool_input") or {}
    if tool_input.get("run_in_background") and not failed:
        note = ("Antigravity was started in the background; it has NOT finished. Wait for the "
                "completion notification, then read its JSON result and inspect the diff.")
    else:
        cwd = event.get("cwd") or None
        status = _git(["status", "--short"], cwd)
        stat = _git(["diff", "--stat"], cwd)
        if status is None:
            tree = "Working tree: not a git repository (or git unavailable)."
        else:
            tree = ("Working tree now:\n" + (status or "(clean)") +
                    ("\n\n" + stat if stat else ""))
        if failed:
            lead = ("Antigravity did NOT succeed (failed, partial, timed_out or busy: read the JSON "
                    "`status`/`error`). The agent may have edited files before stopping, so inspect "
                    "the working tree below before retrying or reporting.")
        else:
            lead = ("Antigravity finished. Its status is NOT verification of correctness. "
                    "Read the diff and run the project's tests before reporting done.")
        note = lead + "\n" + tree
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUseFailure" if failed else "PostToolUse",
        "additionalContext": note[:_MAX_CONTEXT]}}))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    handlers = {"pre": pre, "post": post}
    if len(argv) != 1 or argv[0] not in handlers:
        print("usage: antigravity-agent-hook pre|post  (event JSON on stdin)", file=sys.stderr)
        return 2
    return handlers[argv[0]](_event())


if __name__ == "__main__":
    sys.exit(main())
