#!/bin/bash
# PostToolUseFailure note for `agy -p` Bash calls (non-zero exit). Same detection as agy-pre.sh; fails open.
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

case "$cmd" in
  *"/advisor-"*)
    printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUseFailure","additionalContext":"The advisor call failed (the JSON on stdout, if any, says why). Do not loop retries. Continue without it and tell the user you could not get a second opinion."}}'
    exit 0 ;;
esac
printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUseFailure","additionalContext":"agy did NOT succeed (error, print timeout or killed): the JSON on stdout says which. It may have edited files before stopping, so inspect git status and git diff in the target repo before retrying or reporting."}}'
exit 0
