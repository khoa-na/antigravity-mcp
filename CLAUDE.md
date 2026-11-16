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
2. **Model and reasoning level** are chosen by the user each time a new `agy` conversation starts
   (`ask: each-use`): ask unless the request names them. The `Configuration` block at the top of
   `.claude/skills/antigravity/SKILL.md` and `.claude/skills/antigravity-advisor/SKILL.md` holds only
   the suggested defaults (and the `ask` switch). Do not hard-code a model name anywhere else.
   Flags are stored per role (worker, advisor) in `agy-session.json` and reused on follow-ups.
3. **Never trust the agent's report.** Check `git status`, `git diff` and run the tests yourself before
   saying a delegated task is done. Never use `--add-dir`. Never run two workers in one directory.
4. **Privacy.** An advisor briefing is sent to Google: no secrets, no personal data, no raw transcript.
5. **Hooks are session-wide.** `.claude/settings.json` registers the guards in `.claude/hooks/*.sh`
   (they check `agy -p` calls). Do not copy them back into a skill's frontmatter: they would run twice.
   Hook scripts keep LF line endings (`.gitattributes`).
6. **Quota.** Check `agy -p "/quota"` before long or repeated use, as the skills describe.

## One agy conversation per directory (session continuity)
Within one Claude Code session, one `agy` conversation per directory is shared by every role (prime,
worker, advisor, dual-advisor), so Gemini keeps the folder context. Flow the user wants: at session
start the `antigravity` skill primes the conversation (reads the folder, read-only); later the same
conversation does the coding and acts as the advisor, each call with its own model and rules.
- **Verified:** a conversation is bound to the directory it started in (resumed from another directory
  it kept its memory but wrote into the ORIGINAL directory). So the unit is one conversation per
  absolute directory. A worktree is a different directory: a new conversation WITHOUT the primed
  context, so prime it again or work in the main checkout.
- **Verified:** `--model`, `--effort` and `--settings` are per call, also when resuming. The model really
  switches (the target model's own quota bucket drops), and deny rules apply (a write was blocked). So
  the same conversation runs the worker on one model, the advisor on a stronger one, read-only.
- **State file:** `agy-session.json` in this session's scratchpad directory (the system prompt lists it;
  private to the session, survives compaction). Shape:
  `{"<abs dir>":{"id":"<conversation_id>","flags":{"worker":"<model-flags>","advisor":"<model-flags>"}}}`.
  No scratchpad listed: keep it in context only and never use a cross-session file. Prime uses the
  worker's flags (read-only), so the user is asked at most twice per session.
- **Before** any `agy` task or advisor call (not `/quota`): read the file. Entry for the directory you
  start `agy` in: pass `--conversation <id>` with this call's own flags. No entry: start a new one.
- **After** the result is read (background run: after its notification): write the returned
  `conversation_id`, and the flags used under that role. Empty ID (failed to start): write nothing.
- **One `agy` process per conversation at a time.** Do not consult the advisor while a background
  worker is resuming the same conversation: wait for it to finish.
- **Start fresh** (drop the entry) only when the user asks for a new agy session, the worktree is
  removed, or the conversation has grown too long or its quota is low.
- Anchoring caveat: Gemini's earlier history (briefs that held Claude's plans, its own work) can colour
  its later "independent" opinion. State it in the `dual-advisor` report, do not engineer around it.
- The briefing privacy rule covers the whole conversation. Input grows each turn (9.5k to 47k tokens
  over four trivial turns), so keep follow-ups short.

## Automatic advisor (opt-in)
`automatic-advisor: off`

Change `off` to `on` to make this standing behaviour: for a non-trivial task, load `antigravity-advisor`
and consult it before substantive work and again before declaring done (and when stuck), at most
once per moment. Every consult sends a briefing to Google and spends quota, so it stays off until
the user turns it on. With `off`, consult only when the user asks.
