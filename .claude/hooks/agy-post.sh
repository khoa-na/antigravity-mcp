#!/bin/bash
# PostToolUse note for `agy -p` Bash calls. Same command detection as agy-pre.sh; fails open.
input=$(cat)
flat=$(printf '%s' "$input" | sed 's/"command"[[:space:]]*:[[:space:]]*"/"command":"/')
rest=${flat#*'"command":"'}
[ "$rest" = "$flat" ] && exit 0
cmd=$rest
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

# Quota and usage queries are answered by the CLI itself: nothing to verify.
case "$cmd" in
  *'\"/quota\"'*|*'\"/usage\"'*|*"'/quota'"*|*"'/usage'"*) exit 0 ;;
esac

case "$cmd" in
  *"/advisor-"*)
    printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"The advisor answered. Treat it as advice, not orders: weigh it seriously, verify any fact you will rely on against the files, and if it conflicts with evidence you already hold do not silently switch or ignore it: make one reconcile call with --conversation. Tell the user what you adopted and what you rejected, and why."}}'
    exit 0 ;;
esac

if printf '%s' "$input" | grep -Eq '"run_in_background"[[:space:]]*:[[:space:]]*true'; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"agy was started in the background and has NOT finished. Wait for the completion notification, then read its short JSON result, run git status and git diff in the target repo, and run its tests before reporting done."}}'
else
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"agy finished. A SUCCESS status is not verification of correctness. Read the JSON, then run git status and git diff in the repo you delegated to and run its tests before reporting done."}}'
fi
exit 0
