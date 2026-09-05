#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agy_agent.py — AGENTIC Antigravity backend for the antigravity-mcp server.

Runs the real `agy` CLI AGENT with Read/Write/Bash tools.
Mechanism:
    prompt --> temp in.txt
    agy.exe -p "Read in.txt, execute the task, and write your final response to out.txt." --dangerously-skip-permissions
    out.txt --> returned to caller
"""
import sys, os, time, tempfile, shutil, subprocess, argparse
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

# Reuse account rotation / quota pre-check if available
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from agy_backend import Session, swap_account, ensure_quota_before, load_config
    _HAS_ROTATION = True
except Exception as _e:
    _HAS_ROTATION = False
    _IMPORT_ERR = _e


def _default_agy_exe():
    win = os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")
    if os.environ.get("LOCALAPPDATA") and os.path.exists(win):
        return win
    return shutil.which("agy") or win


AGY_EXE = os.environ.get("AGY_EXE") or _default_agy_exe()
# Only use CLOUD_CODE_URL if explicitly provided by user (e.g. for geo-unlock proxy)
PROXY = os.environ.get("CLOUD_CODE_URL")
PROFILE = os.environ.get("AGY_AGENT_PROFILE")
TIMEOUT = int(os.environ.get("AGY_AGENT_TIMEOUT", "900"))
MAX_SWAPS = int(os.environ.get("AGY_MAX_SWAPS", "3"))
MAX_AUTH_REFRESHES = int(os.environ.get("AGY_MAX_AUTH_REFRESHES", "1"))
DEFAULT_MODEL = os.environ.get("AGY_MODEL", "gemini-3.8-flash-high")

_QUOTA_MARKERS = (
    "resource_exhausted", "resource has been exhausted", "quota",
    "rate limit", "rate_limit", "too many requests", "exhausted", "429"
)
_AUTH_MARKERS = (
    "please login", "please run /login", "login required", "unauthorized",
    "invalid api key", "credential", "reauthenticate", "401", "not logged in"
)
_LAUNCH_MARKERS = (
    "not found", "no such file", "not recognized", "cannot find",
    "is not recognized as"
)


def log(*a):
    print("[agy_agent]", *a, file=sys.stderr, flush=True)


def _env(worker_id=None):
    e = dict(os.environ)
    if PROXY:
        e["CLOUD_CODE_URL"] = PROXY
    else:
        e.pop("CLOUD_CODE_URL", None)
    target_profile = PROFILE
    if target_profile and worker_id:
        target_profile = os.path.join(target_profile, f"worker_{worker_id}")
    if target_profile:
        try:
            os.makedirs(target_profile, exist_ok=True)
        except Exception:
            pass
        e["USERPROFILE"] = target_profile
        e["HOME"] = target_profile
    e["PYTHONIOENCODING"] = "utf-8"
    return e


_WRAP = "Read {infile}, execute the task, and write your final response to {outfile}."


def run_agent(prompt, model=None, workspace=None, resume=False, conversation_id=None, effort=None, worker_id=None, timeout=TIMEOUT):
    """Run agy CLI once. Returns (result_text, stdout, stderr, rc)."""
    tmp = tempfile.mkdtemp(prefix="agymcp_")
    infile = os.path.join(tmp, "in.txt")
    outfile = os.path.join(tmp, "out.txt")
    try:
        Path(infile).write_text(prompt, encoding="utf-8")
        wrapped = _WRAP.format(infile=infile, outfile=outfile)
        args = [AGY_EXE, "-p", wrapped, "--dangerously-skip-permissions"]
        if model:
            args += ["--model", model]
        if workspace:
            args += ["--add-dir", workspace]
        if resume:
            args += ["--continue"]
        elif conversation_id:
            args += ["--conversation", str(conversation_id)]
        if effort:
            args += ["--effort", str(effort)]

        try:
            p = subprocess.run(
                args, input=b"", capture_output=True,
                cwd=tmp, env=_env(worker_id), timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            out = p.stdout.decode("utf-8", "replace")
            err = p.stderr.decode("utf-8", "replace")
            rc = p.returncode
        except subprocess.TimeoutExpired:
            return "", "", "timeout", -1
        except (FileNotFoundError, NotADirectoryError, OSError) as e:
            return "", "", f"agy.exe not launchable ({AGY_EXE}): {e}", -1

        result = ""
        try:
            if os.path.exists(outfile):
                result = Path(outfile).read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            result = ""

        # Fallback to stdout if out.txt missing but process succeeded
        if not result and rc == 0 and out.strip():
            clean_out = out.strip()
            lines = [line for line in clean_out.splitlines() if not line.startswith("Task completed. The answer has been written to")]
            result = "\n".join(lines).strip()

        return result, out, err, rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ask(prompt, model=None, system=None, workspace=None, resume=False, conversation_id=None, effort=None, worker_id=None):
    """Run the agent with proactive + reactive account rotation."""
    model = model or DEFAULT_MODEL
    if system and system.strip():
        prompt = f"[SYSTEM INSTRUCTION]\n{system.strip()}\n\n[TASK]\n{prompt}"

    # Proactive quota check if backend rotation is loaded
    if _HAS_ROTATION:
        try:
            cfg = load_config()
            if cfg.get("enabled"):
                sess = Session()
                sess.ensure_fresh()
                ensure_quota_before(sess, model, cfg)
        except Exception as e:
            log("proactive quota check skipped:", e)

    swaps = 0
    auth_refreshes = 0
    last = ""
    while True:
        result, out, err, rc = run_agent(
            prompt, model=model, workspace=workspace,
            resume=resume, conversation_id=conversation_id, effort=effort,
            worker_id=worker_id
        )
        if result:
            return result

        blob = ((err or "") + "\n" + (out or "")).lower()
        last = err or out or f"rc={rc}"

        # Reactive: quota exhaustion
        if _HAS_ROTATION and swaps < MAX_SWAPS and any(m in blob for m in _QUOTA_MARKERS):
            log(f"quota hit, rotating account ({swaps + 1}/{MAX_SWAPS})")
            if swap_account():
                swaps += 1
                time.sleep(1)
                continue

        # Reactive: auth error
        if any(m in blob for m in _AUTH_MARKERS):
            if _HAS_ROTATION and auth_refreshes < MAX_AUTH_REFRESHES:
                auth_refreshes += 1
                try:
                    if Session().refresh():
                        log(f"auth flake: token force-refreshed, retrying agent ({auth_refreshes}/{MAX_AUTH_REFRESHES})")
                        time.sleep(1)
                        continue
                except Exception as e:
                    log(f"force-refresh failed: {e}")
            raise RuntimeError(f"agy agent: auth required (login/token). {last[-300:]}")

        if err == "timeout":
            raise RuntimeError(f"agy agent timed out after {TIMEOUT}s")
        if any(m in blob for m in _LAUNCH_MARKERS):
            raise RuntimeError(f"agy agent could not launch: {last[-300:]}")

        raise RuntimeError(f"agy agent produced no out-file (rc={rc}): {last[-400:] or 'empty stderr'}")


def main():
    ap = argparse.ArgumentParser(description="CLI wrapper for Antigravity Agent")
    ap.add_argument("--model", default=None)
    ap.add_argument("--system", default=None)
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--conversation", default=None)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--prompt", default=None)
    a = ap.parse_args()

    prompt = a.prompt if a.prompt is not None else sys.stdin.read()
    if not prompt or not prompt.strip():
        log("empty prompt")
        sys.exit(2)
    try:
        ans = ask(
            prompt, model=a.model, system=a.system,
            workspace=a.workspace, conversation_id=a.conversation,
            effort=a.effort
        )
    except Exception as e:
        log("ERROR:", e)
        sys.exit(1)
    sys.stdout.write(ans + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()