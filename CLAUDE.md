# Antigravity skills: standing instructions

This repository ships three Claude Code skills that use the Antigravity CLI (`agy`, Gemini). This file
is re-read from disk at the start of every session and after every compaction, so these rules hold for
the whole session. The skill texts themselves can be dropped or truncated by compaction.

## The skills and when to use them
- **`antigravity`**: delegate a bounded coding task to `agy` as a background worker (own git worktree,
  you verify the result). Use when the user asks for Antigravity, Gemini or agy to do work.
- **`antigravity-advisor`**: read-only second opinion from `agy` on a plan, an approach or finished work.
  Use when the user asks for Antigravity/Gemini as advisor.
- **`dual-advisor`**: for a hard problem, Claude's built-in advisor and the Antigravity advisor answer
  independently, then Claude's advisor merges them. Use only when the user asks for both. Needs the
  built-in `advisor` tool (`/advisor <model>`).

## Rules that always apply
1. **Load the skill before using `agy`.** Invoke it with the Skill tool even if you remember the
   commands: after a compaction the full text may be gone or cut, and the Skill tool restores it.
   Never run `agy -p` from memory. (`agy models` and `agy -p "/quota"` are fine on their own.)
2. **Model and reasoning level** are declared only in the `Configuration` block at the top of
   `.claude/skills/antigravity/SKILL.md` and `.claude/skills/antigravity-advisor/SKILL.md`. Do not
   hard-code a model name anywhere else. A model named in the user's request overrides it for that run.
3. **Never trust the agent's report.** Check `git status`, `git diff` and run the tests yourself before
   saying a delegated task is done. Never use `--add-dir`. Never run two workers in one directory.
4. **Privacy.** An advisor briefing is sent to Google: no secrets, no personal data, no raw transcript.
5. **Hooks are session-wide.** `.claude/settings.json` registers the guards in `.claude/hooks/*.sh`
   (they check `agy -p` calls). Do not copy them back into a skill's frontmatter: they would run twice.
   Hook scripts keep LF line endings (`.gitattributes`).
6. **Quota.** Check `agy -p "/quota"` before long or repeated use, as the skills describe.

## Automatic advisor (opt-in)
`automatic-advisor: off`

Change `off` to `on` to make this standing behaviour: for a non-trivial task, load `antigravity-advisor`
and consult it before substantive work and again before declaring done (and when stuck), at most
once per moment. Every consult sends a briefing to Google and spends quota, so it stays off until
the user turns it on. With `off`, consult only when the user asks.
