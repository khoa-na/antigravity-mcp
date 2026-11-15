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
import sys, os, time, tempfile, shutil, subprocess, argparse, threading, math, json, uuid, errno
from functools import wraps
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
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA") and os.path.exists(win):
        return win
    return shutil.which("agy") or (win if sys.platform == "win32" else "agy")


AGY_EXE = os.environ.get("AGY_EXE") or _default_agy_exe()
# Only use CLOUD_CODE_URL if explicitly provided by user (e.g. for geo-unlock proxy)
PROXY = os.environ.get("CLOUD_CODE_URL")
PROFILE = os.environ.get("AGY_AGENT_PROFILE")
TIMEOUT = int(os.environ.get("AGY_AGENT_TIMEOUT", "900"))
MAX_SWAPS = int(os.environ.get("AGY_MAX_SWAPS", "3"))
MAX_AUTH_REFRESHES = int(os.environ.get("AGY_MAX_AUTH_REFRESHES", "1"))
DEFAULT_MODEL = os.environ.get("AGY_MODEL", "gemini-3.8-flash-high")
MAX_INLINE_LINES = int(os.environ.get("AGY_MAX_INLINE_LINES", "350"))
MAX_INLINE_CHARS = int(os.environ.get("AGY_MAX_INLINE_CHARS", "16000"))
WS_OPT_OUT = {"none", "off", "no", "false", "-", "temp"}


class AgentError(RuntimeError):
    def __init__(self, message, status="failed", exit_code=None, output=""):
        super().__init__(message)
        self.status = status
        self.exit_code = exit_code
        self.output = output


# ponytail: serialize CLI tasks because credentials/profile are shared. Two layers:
#   1. _AGENT_LOCK  - threads inside one process (MCP server).
#   2. a file lock  - separate processes (MCP server + `antigravity-agent` CLI/skill).
# Use separate authenticated profiles/worktrees if parallel execution is needed.
_AGENT_LOCK = threading.RLock()
_PROC_LOCK_DEPTH = 0       # only mutated while holding _AGENT_LOCK
_PROC_LOCK_HANDLE = None


def _lock_path():
    return os.environ.get("AGY_LOCK_FILE") or os.path.join(tempfile.gettempdir(), "antigravity-mcp-agent.lock")


def _lock_file(fh):
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fh):
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _acquire_process_lock():
    """Take the non-blocking cross-process lock. Returns a handle, or None when file
    locking is unavailable (degrades to in-process locking only). Raises busy if held."""
    path = _lock_path()
    try:
        fh = open(path, "a+b")
    except OSError as e:
        log(f"cross-process lock unavailable ({path}): {e}")
        return None
    try:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        _lock_file(fh)
    except OSError as e:
        fh.close()
        if e.errno in (errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", -1)):
            raise AgentError(f"Another agent task is running in another process (lock: {path}); "
                             "retry after it finishes", status="busy")
        log(f"cross-process lock unavailable ({path}): {e}")
        return None
    except ImportError:
        fh.close()
        return None
    return fh


def _release_process_lock(fh):
    try:
        fh.seek(0)
        _unlock_file(fh)
    except OSError:
        pass
    finally:
        fh.close()


def _exclusive_agent(func):
    @wraps(func)
    def guarded(*args, **kwargs):
        global _PROC_LOCK_DEPTH, _PROC_LOCK_HANDLE
        if not _AGENT_LOCK.acquire(blocking=False):
            raise AgentError("Another agent task is running in this server; retry after it finishes", status="busy")
        try:
            if _PROC_LOCK_DEPTH == 0:
                _PROC_LOCK_HANDLE = _acquire_process_lock()
            _PROC_LOCK_DEPTH += 1
            try:
                return func(*args, **kwargs)
            finally:
                _PROC_LOCK_DEPTH -= 1
                if _PROC_LOCK_DEPTH == 0 and _PROC_LOCK_HANDLE is not None:
                    _release_process_lock(_PROC_LOCK_HANDLE)
                    _PROC_LOCK_HANDLE = None
        finally:
            _AGENT_LOCK.release()
    return guarded

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
    if target_profile:
        os.makedirs(target_profile, exist_ok=True)
        e["USERPROFILE"] = target_profile
        e["HOME"] = target_profile
    e["PYTHONIOENCODING"] = "utf-8"
    return e


_WRAP = "Read {infile}, execute the task, and write your final response to {outfile}."


def _cli_print_timeout(timeout):
    """Go-duration string within the outer budget, reserving up to 10s for exit/readback.

    agy print mode otherwise has an independent 5-minute default. Recompute this
    from each attempt's remaining budget, not the original request timeout.
    """
    reserve = min(10.0, timeout * 0.05)
    nanoseconds = max(1, int((timeout - reserve) * 1_000_000_000))
    seconds, fraction = divmod(nanoseconds, 1_000_000_000)
    duration = f"{seconds}.{fraction:09d}".rstrip("0").rstrip(".")
    return duration + "s"


@_exclusive_agent
def run_agent(prompt, model=None, workspace=None, resume=False, conversation_id=None, effort=None, worker_id=None, timeout=TIMEOUT):
    """Run agy CLI once. Returns (result_text, stdout, stderr, rc)."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if resume and conversation_id:
        raise ValueError("Use resume OR conversation_id, not both")
    tmp = tempfile.mkdtemp(prefix="agymcp_")
    infile = os.path.join(tmp, "in.txt")
    outfile = os.path.join(tmp, "out.txt")
    try:
        Path(infile).write_text(prompt, encoding="utf-8")
        wrapped = _WRAP.format(infile=infile, outfile=outfile)
        args = [AGY_EXE, "-p", wrapped, "--dangerously-skip-permissions",
                "--print-timeout", _cli_print_timeout(timeout)]
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
            return "", "", f"agy CLI not launchable ({AGY_EXE}): {e}", -1

        result = ""
        try:
            if os.path.exists(outfile):
                result = Path(outfile).read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            result = ""

        # stdout can contain only CLI logs. The requested out-file is the response contract.
        return result, out, err, rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@_exclusive_agent
def ask(prompt, model=None, system=None, workspace=None, resume=False, conversation_id=None, effort=None, worker_id=None, timeout=TIMEOUT):
    """Run the agent with proactive + reactive account rotation."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if resume and conversation_id:
        raise ValueError("Use resume OR conversation_id, not both")
    deadline = time.monotonic() + timeout
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
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentError("agy agent task deadline exceeded", status="timed_out")
        result, out, err, rc = run_agent(
            prompt, model=model, workspace=workspace,
            resume=resume, conversation_id=conversation_id, effort=effort,
            worker_id=worker_id, timeout=remaining
        )
        if result and rc == 0:
            return result
        if result:
            raise AgentError(f"agy agent returned partial output (rc={rc}): {err[-300:]}", status="partial", exit_code=rc, output=result)

        blob = ((err or "") + "\n" + (out or "")).lower()
        last = err or out or f"rc={rc}"

        # The CLI may reach its own print deadline before Python's hard deadline.
        if "timeout waiting for response" in blob:
            raise AgentError(f"agy CLI print deadline reached: {last[-400:]}",
                             status="timed_out", exit_code=rc)

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
            raise AgentError(f"agy agent: auth required (login/token). {last[-300:]}", exit_code=rc)

        if err == "timeout":
            raise AgentError(f"agy agent timed out after {timeout}s", status="timed_out", exit_code=rc)
        if any(m in blob for m in _LAUNCH_MARKERS):
            raise AgentError(f"agy agent could not launch: {last[-300:]}", exit_code=rc)

        raise AgentError(f"agy agent produced no out-file (rc={rc}): {last[-400:] or 'empty stderr'}", exit_code=rc)


# ---- shared helpers (used by the MCP server and the CLI/skill) --------------
def resolve_workspace(w=None, default=None):
    """Existing directory to grant via --add-dir, or None when opted out."""
    t = str(w or "").strip() or str(default or "").strip()
    if not t or t.lower() in WS_OPT_OUT:
        return None
    path = Path(t).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Workspace is not an existing directory: {path}")
    return str(path)


def handle_large_output(text, workspace, max_lines=MAX_INLINE_LINES, max_chars=MAX_INLINE_CHARS,
                        force_artifact=False):
    """Save excessively large outputs to an artifact file to prevent context window bloat."""
    lines = text.splitlines()
    if not force_artifact and max_lines > 0 and len(lines) <= max_lines and len(text) <= max_chars:
        return text

    ws_path = Path(workspace) if workspace else Path(os.getcwd())
    artifact_dir = ws_path / ".antigravity" / "artifacts"
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_file = artifact_dir / f"response_{ts}_{uuid.uuid4().hex[:4]}.md"
        out_file.write_text(text, encoding="utf-8")

        preview_text = "\n".join(lines[:40])[:min(max_chars, 4000) if max_chars > 0 else 4000]
        return (
            f"[Response exceeds threshold ({len(lines)} lines / {len(text)} chars). "
            f"Full content saved to artifact]\n"
            f"Artifact File: {out_file.as_posix()}\n\n"
            f"--- Preview (First 40 lines) ---\n"
            f"{preview_text}\n"
            "\n... [See artifact file for full output] ..."
        )
    except Exception as e:
        log("Failed to save artifact:", e)
        return text


def build_result(*, status, task_id, conversation_id, resume, workspace, model,
                 exit_code, elapsed_seconds, response, error):
    """The single JSON result contract shared by the MCP tool and the CLI."""
    return {
        "status": status,
        "task_id": task_id,
        "conversation_id": conversation_id,
        "resume_latest": resume,
        "workspace": workspace,
        "model": model or DEFAULT_MODEL,
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "verification": "not_run_by_wrapper",
        "response": response,
        "error": error,
    }


def _read_text(path):
    return Path(path).read_text(encoding="utf-8", errors="replace").lstrip("﻿")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="antigravity-agent",
        description="Run the Antigravity coding agent once (skill/CLI entry point; no MCP needed). "
                    "Exit code 0 only when status is 'succeeded'. Success is NOT verification: "
                    "inspect the diff and run tests yourself.")
    ap.add_argument("prompt", nargs="?", default=None, help="Task prompt (or use --prompt-file / stdin).")
    ap.add_argument("--prompt", dest="prompt_opt", default=None)
    ap.add_argument("--prompt-file", default=None, help="Read the prompt from this file.")
    ap.add_argument("--model", default=None)
    ap.add_argument("--system", default=None)
    ap.add_argument("--workspace", default=None,
                    help="Directory granted via --add-dir (default: current directory; 'none' omits it). "
                         "Not a security sandbox.")
    ap.add_argument("--conversation", default=None, help="Existing conversation ID to resume.")
    ap.add_argument("--resume", "--continue", dest="resume", action="store_true",
                    help="Continue the most recent CLI conversation.")
    ap.add_argument("--effort", choices=["low", "medium", "high"], default=None)
    ap.add_argument("--timeout", type=float, default=TIMEOUT,
                    help=f"Total time budget in seconds, 0 < t <= 86400 (default {TIMEOUT}).")
    ap.add_argument("--json", action="store_true", help="Print one JSON result record instead of plain text.")
    ap.add_argument("--save-artifact", action="store_true",
                    help="Always save the response under .antigravity/artifacts/ and print a preview.")
    a = ap.parse_args(argv)

    if a.prompt is not None and a.prompt_opt is not None:
        ap.error("pass the prompt either positionally or via --prompt, not both")
    if a.resume and a.conversation:
        ap.error("use --resume OR --conversation, not both")
    if not 0 < a.timeout <= 86400:
        ap.error("--timeout must be between 0 and 86400 seconds")

    try:
        if a.prompt_file:
            prompt = _read_text(a.prompt_file)
        else:
            prompt = a.prompt_opt if a.prompt_opt is not None else a.prompt
            if prompt is None:
                prompt = sys.stdin.read()
        workspace = resolve_workspace(a.workspace, os.environ.get("AGY_DEFAULT_WORKSPACE", os.getcwd()))
    except (OSError, ValueError) as e:
        ap.error(str(e))
    if not prompt or not prompt.strip():
        ap.error("empty prompt")

    task_id = uuid.uuid4().hex[:6]
    started = time.monotonic()
    error = None
    raw = ""
    try:
        raw = ask(prompt, model=a.model, system=a.system, workspace=workspace,
                  resume=a.resume, conversation_id=a.conversation, effort=a.effort,
                  worker_id=task_id, timeout=a.timeout)
    except AgentError as e:
        error, raw = e, e.output
    except Exception as e:  # keep the contract: always a status record in --json mode
        error = AgentError(str(e))
    response = handle_large_output(raw, workspace, force_artifact=a.save_artifact) if raw else ""

    if a.json:
        print(json.dumps(build_result(
            status=error.status if error else "succeeded", task_id=task_id,
            conversation_id=a.conversation, resume=a.resume, workspace=workspace,
            model=a.model, exit_code=error.exit_code if error else 0,
            elapsed_seconds=time.monotonic() - started, response=response,
            error=str(error) if error else None), ensure_ascii=False))
    else:
        if error:
            log(f"{error.status}: {error}")
        if response:
            print(response)
    sys.stdout.flush()
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
