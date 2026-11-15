#!/bin/bash
# PreToolUse guard for `agy -p` Bash calls (worker and advisor). Registered in .claude/settings.json,
# so it is active from the start of every session, not only after a skill is invoked.
# Fails open: anything that is not clearly an `agy -p` call exits 0 silently.
input=$(cat)
flat=$(printf '%s' "$input" | sed 's/"command"[[:space:]]*:[[:space:]]*"/"command":"/')
rest=${flat#*'"command":"'}
[ "$rest" = "$flat" ] && exit 0
cmd=$rest
# A command only counts when it STARTS with agy -p (after cd segments and an env prefix), so a command
# that merely writes or prints text mentioning agy -p is left alone.
while :; do
  case "$cmd" in
    "cd "*"&& "*) cmd=${cmd#*&& } ;;
    *) break ;;
  esac
done
case "$cmd" in "MSYS_NO_PATHCONV=1 "*) cmd=${cmd#MSYS_NO_PATHCONV=1 } ;; esac
case "$cmd" in
  "agy -p "*) ;;
  *) exit 0 ;;
esac

case "$cmd" in
  *"/advisor-"*) who="advisor: agy -p"; secs="300s" ;;
  *) who="agy -p"; secs="900s" ;;
esac
case "$cmd" in
  *"--print-timeout"*) ;;
  *) echo "$who needs --print-timeout (its default, 0, waits forever). Add e.g. --print-timeout $secs and retry." >&2; exit 2 ;;
esac
case "$cmd" in
  *"--output-format"*) ;;
  *) echo "$who needs --output-format json so the result is machine-readable. Add it and retry." >&2; exit 2 ;;
esac
case "$cmd" in
  *"/advisor-"*)
    case "$cmd" in
      *"command(*)"*"write_file(*)"*|*"write_file(*)"*"command(*)"*) ;;
      *) echo "advisor: the advisor must be read-only. Add --settings with deny rules for command(*) and write_file(*) (see the antigravity-advisor skill) and retry." >&2; exit 2 ;;
    esac ;;
esac
exit 0
