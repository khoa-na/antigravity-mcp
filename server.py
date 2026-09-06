#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py — Advanced Pure Python MCP Server for Google Antigravity.
Connects Codex CLI, Claude Code, Cursor, and any MCP client directly
to Google Antigravity coding agents with full Read/Write/Bash capabilities.

Upgrades in v2.1:
1. Pure Python Architecture (FastMCP / MCPServer).
2. Stable CLI profiles and explicit conversation resumption.
3. Specialized Tools:
   - ask-antigravity / ask-gemini (general agent)
   - review-diff (automated git diff review)
   - check-quota (live quota percentages for all models)
   - generate-tests (targeted unit test generator)
   - list-models & ping
4. Periodic elapsed-time heartbeat (not execution progress).
5. Output Artifact Management (context preservation on large outputs).
"""
import sys
import os
import json
import time
import uuid
import asyncio
import argparse
import subprocess
from pathlib import Path
import anyio

# Support both mcp 2.x (MCPServer) and mcp 1.x (FastMCP)
try:
    from mcp.server.mcpserver import MCPServer, Context
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer, Context
    except ImportError:
        raise ImportError("Please install mcp: pip install mcp")

import agy_agent
import agy_backend

# Initialize MCP Server
mcp = MCPServer(
    name="antigravity",
    instructions=(
        "Google Antigravity coding agent with Read/Write/Bash tools (Gemini 3.8 Flash / Gemini 3.1 Pro). "
        "Provides agentic code execution, automated git reviews, unit test generation, and quota monitoring."
    )
)

DEFAULT_WORKSPACE = os.environ.get("AGY_DEFAULT_WORKSPACE", os.getcwd())
WS_OPT_OUT = {"none", "off", "no", "false", "-", "temp"}
DEFAULT_MAX_INLINE_LINES = int(os.environ.get("AGY_MAX_INLINE_LINES", "350"))
DEFAULT_MAX_INLINE_CHARS = int(os.environ.get("AGY_MAX_INLINE_CHARS", "16000"))


def resolve_workspace(w: str | None = None) -> str | None:
    t = str(w or "").strip() or str(DEFAULT_WORKSPACE or "").strip()
    if not t or t.lower() in WS_OPT_OUT:
        return None
    path = Path(t).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Workspace is not an existing directory: {path}")
    return str(path)


def strip_bom(s: str) -> str:
    return s.lstrip("\ufeff")


def resolve_input(
    prompt: str = "",
    prompt_file: str | None = None,
    system: str | None = None,
    system_file: str | None = None,
    cleanup: bool = False
) -> tuple[str, str | None, list[Path]]:
    cleanup_files = []
    final_prompt = prompt
    if prompt_file:
        p = Path(prompt_file)
        if p.exists():
            final_prompt = strip_bom(p.read_text(encoding="utf-8", errors="replace"))
            if cleanup:
                cleanup_files.append(p)
        else:
            raise FileNotFoundError(f"prompt_file not found: {prompt_file}")

    final_system = system
    if system_file:
        s = Path(system_file)
        if s.exists():
            final_system = strip_bom(s.read_text(encoding="utf-8", errors="replace"))
            if cleanup:
                cleanup_files.append(s)
        else:
            raise FileNotFoundError(f"system_file not found: {system_file}")

    return final_prompt, final_system, cleanup_files


def handle_large_output(
    text: str,
    workspace: str | None,
    max_lines: int = DEFAULT_MAX_INLINE_LINES,
    max_chars: int = DEFAULT_MAX_INLINE_CHARS,
    force_artifact: bool = False
) -> str:
    """Save excessively large outputs to an artifact file to prevent context window bloat."""
    lines = text.splitlines()
    if not force_artifact and max_lines > 0 and len(lines) <= max_lines and len(text) <= max_chars:
        return text

    # Destination directory
    ws_path = Path(workspace) if workspace else Path(os.getcwd())
    artifact_dir = ws_path / ".antigravity" / "artifacts"
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"response_{ts}_{uuid.uuid4().hex[:4]}.md"
        out_file = artifact_dir / filename
        out_file.write_text(text, encoding="utf-8")

        preview_lines = lines[:40]
        preview_text = "\n".join(preview_lines)[:min(max_chars, 4000) if max_chars > 0 else 4000]
        return (
            f"[Response exceeds threshold ({len(lines)} lines / {len(text)} chars). "
            f"Full content saved to artifact]\n"
            f"Artifact File: {out_file.as_posix()}\n\n"
            f"--- Preview (First 40 lines) ---\n"
            f"{preview_text}\n"
            "\n... [See artifact file for full output] ..."
        )
    except Exception as e:
        # Fallback to inline if saving artifact fails
        agy_agent.log("Failed to save artifact:", e)
        return text


async def _run_with_telemetry(func, ctx: Context | None = None, label: str = "Antigravity"):
    """Run blocking work with elapsed-time heartbeats, not measured progress."""
    stop_event = asyncio.Event()
    start_time = time.time()

    async def _telemetry_monitor():
        counter = 0
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=15.0)
                break
            except asyncio.TimeoutError:
                counter += 1
                elapsed = int(time.time() - start_time)
                if ctx:
                    try:
                        await ctx.report_progress(progress=counter, total=None)
                        # Periodic status ping
                        status_msg = f"🛰️ {label} is working... ({elapsed}s elapsed)"
                        await ctx.info(status_msg)
                    except Exception:
                        pass

    monitor_task = asyncio.create_task(_telemetry_monitor())
    try:
        return await anyio.to_thread.run_sync(func)
    finally:
        stop_event.set()
        await monitor_task


# =============================================================================
# MCP TOOLS
# =============================================================================

@mcp.tool(name="ask-antigravity")
async def ask_antigravity(
    prompt: str = "",
    resume: bool = False,
    prompt_file: str | None = None,
    model: str | None = None,
    system: str | None = None,
    system_file: str | None = None,
    workspace: str | None = None,
    conversation_id: str | None = None,
    effort: str | None = None,
    cleanup: bool = False,
    save_artifact: bool = False,
    ctx: Context = None,
    result_format: str = "text",
    timeout: float = agy_agent.TIMEOUT,
) -> str:
    """Ask the Google Antigravity AGENT (Gemini 3.8 Flash / Pro) — a coding agent WITH
    file tools (Read/Write/Bash), like a second Claude Code / Codex.

    By default it works in the current directory (process workspace), so it can read and
    run your repo like Codex. Pass an existing conversation ID for explicit resumption.

    Parameters:
    - prompt: The task/code instruction.
    - resume: If True, automatically continues the most recent conversation session (--continue) without needing an ID.
    - prompt_file: Absolute path to a file containing the prompt (wins over prompt).
    - model: Model ID (default: gemini-3.8-flash-high, or gemini-3.1-pro-high, etc.).
    - system: Optional system instructions.
    - system_file: Absolute path to a file containing system instructions.
    - workspace: Directory granted via --add-dir. 'none' omits this grant; it is NOT a security sandbox.
    - conversation_id: Resume/continue an existing session by conversation ID.
    - effort: Reasoning effort (low, medium, high).
    - cleanup: If true, delete prompt_file / system_file after execution.
    - save_artifact: If true, always saves response to an artifact file in .antigravity/artifacts/.
    - result_format: 'text' (compatible default) or 'json' for execution metadata. Success is NOT verification of the code.
    - timeout: Total agent time budget in seconds, shared across retries.
    """
    resolved_prompt, resolved_system, cleanup_files = resolve_input(
        prompt=prompt, prompt_file=prompt_file,
        system=system, system_file=system_file,
        cleanup=cleanup
    )
    if not resolved_prompt or not resolved_prompt.strip():
        raise ValueError("Prompt is required — pass `prompt` or a non-empty `prompt_file`")
    if resume and conversation_id:
        raise ValueError("Use resume OR conversation_id, not both")
    if result_format not in {"text", "json"}:
        raise ValueError("result_format must be text or json")
    if not 0 < timeout <= 86400:
        raise ValueError("timeout must be between 0 and 86400 seconds")

    ws = resolve_workspace(workspace)
    worker_id = uuid.uuid4().hex[:6]
    session_conv_id = conversation_id
    started = time.monotonic()

    try:
        def _execute():
            return agy_agent.ask(
                prompt=resolved_prompt,
                model=model,
                system=resolved_system,
                workspace=ws,
                resume=resume,
                conversation_id=session_conv_id,
                effort=effort,
                worker_id=worker_id, timeout=timeout
            )
        error = None
        try:
            raw_result = await _run_with_telemetry(_execute, ctx, label=f"Worker-{worker_id}")
        except agy_agent.AgentError as exc:
            if result_format == "text":
                raise
            error = exc
            raw_result = exc.output
        formatted = handle_large_output(raw_result, ws, force_artifact=save_artifact)
        if result_format == "json":
            return json.dumps({
                "status": error.status if error else "succeeded",
                "task_id": worker_id,
                "conversation_id": session_conv_id,
                "resume_latest": resume,
                "workspace": ws,
                "model": model or agy_agent.DEFAULT_MODEL,
                "exit_code": error.exit_code if error else 0,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "verification": "not_run_by_wrapper",
                "response": formatted,
                "error": str(error) if error else None,
            }, ensure_ascii=False)
        return f"Antigravity ({model or agy_agent.DEFAULT_MODEL}) response:\n{formatted}"
    finally:
        for cf in cleanup_files:
            try:
                cf.unlink(missing_ok=True)
            except Exception as e:
                agy_agent.log(f"cleanup failed for {cf}: {e}")


@mcp.tool(name="ask-gemini")
async def ask_gemini(
    prompt: str = "",
    resume: bool = False,
    prompt_file: str | None = None,
    model: str | None = None,
    system: str | None = None,
    system_file: str | None = None,
    workspace: str | None = None,
    conversation_id: str | None = None,
    effort: str | None = None,
    cleanup: bool = False,
    save_artifact: bool = False,
    ctx: Context = None,
    result_format: str = "text",
    timeout: float = agy_agent.TIMEOUT,
) -> str:
    """Alias for ask-antigravity."""
    return await ask_antigravity(
        prompt=prompt, resume=resume, prompt_file=prompt_file, model=model,
        system=system, system_file=system_file, workspace=workspace,
        conversation_id=conversation_id, effort=effort, cleanup=cleanup,
        save_artifact=save_artifact, ctx=ctx, result_format=result_format, timeout=timeout
    )


@mcp.tool(name="review-diff")
async def review_diff(
    workspace: str | None = None,
    staged: bool = False,
    focus: str | None = None,
    model: str | None = None,
    ctx: Context = None
) -> str:
    """Perform an automated, structured code review on current uncommitted or staged git changes.
    Sends only the diff to the text backend; does not launch a file-editing agent.

    Parameters:
    - workspace: Path to the git repository (default: current workspace).
    - staged: If true, reviews only staged changes (git diff --staged). Otherwise reviews all uncommitted changes.
    - focus: Optional focus area (e.g. 'security', 'performance', 'logic bugs', 'code style').
    - model: Target model ID (default: gemini-3.7-flash-high).
    """
    ws_dir = resolve_workspace(workspace) or os.getcwd()
    git_cmd = ["git", "diff", "--staged"] if staged else ["git", "diff", "HEAD"]

    try:
        proc = subprocess.run(
            git_cmd, cwd=ws_dir, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30
        )
        if proc.returncode != 0:
            return f"Error executing git command (rc={proc.returncode}): {proc.stderr.strip()}"
        diff_text = proc.stdout.strip()
    except Exception as e:
        return f"Error executing git command: {e}"

    if not diff_text:
        return f"No {'staged' if staged else 'uncommitted tracked'} git changes detected in the workspace."

    focus_clause = f"Focus especially on: {focus}." if focus else "Check for bugs, security risks, performance regressions, and style."
    prompt = (
        f"You are a senior code reviewer. Perform a rigorous, professional code review of the following git diff.\n"
        f"{focus_clause}\n\n"
        f"Format your review as follows:\n"
        f"### 1. Summary of Changes\n"
        f"### 2. Critical Issues & Potential Bugs\n"
        f"### 3. Suggestions & Optimizations\n"
        f"### 4. Overall Recommendation (Approve / Request Changes)\n\n"
        f"```diff\n{diff_text}\n```"
    )

    def _execute():
        # No CLI tools are exposed by the text-only backend.
        return agy_backend.ask(
            prompt=prompt,
            model=model or agy_agent.DEFAULT_MODEL,
            system="You are an expert code reviewer. Provide constructive, precise feedback."
        )

    review_res = await _run_with_telemetry(_execute, ctx, label="CodeReviewer")
    return f"Code Review Report ({model or agy_agent.DEFAULT_MODEL}):\n\n{review_res}"


@mcp.tool(name="check-quota")
def check_quota() -> str:
    """Retrieve and display live remaining quota percentages for all Google Antigravity models.
    Shows health status (Healthy / Low / Depleted) for each model.
    """
    try:
        sess = agy_backend.Session()
        sess.ensure_fresh()
        quota = agy_backend._retrieve_quota(sess)
        if not quota:
            return "Unable to retrieve quota: empty response from backend."

        lines = [
            "### Google Antigravity Live Model Quotas",
            "",
            "| Model | Remaining Quota | Health Status |",
            "|---|---|---|",
        ]
        priority_keys = [
            "gemini-3.7-flash-tiered", "gemini-3.6-flash-high", "gemini-3-flash",
            "gemini-3.1-pro-high", "gemini-3.1-pro-low",
            "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium"
        ]
        all_keys = list(priority_keys) + [k for k in quota.keys() if k not in priority_keys]
        for k in all_keys:
            if k in quota:
                frac = quota[k]
                pct = f"{frac * 100:.0f}%" if isinstance(frac, (int, float)) else str(frac)
                status = "🟢 Healthy" if frac > 0.3 else ("🟡 Low" if frac > 0.05 else "🔴 Depleted")
                lines.append(f"| `{k}` | **{pct}** | {status} |")

        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving quota from backend: {e}"


@mcp.tool(name="generate-tests")
async def generate_tests(
    target_file: str,
    test_framework: str = "pytest",
    workspace: str | None = None,
    write_to_file: bool = False,
    output_file: str | None = None,
    model: str | None = None,
    ctx: Context = None
) -> str:
    """Generate thorough unit tests for a specified source code file.

    Parameters:
    - target_file: Relative or absolute path to the file you want to test.
    - test_framework: Test framework (e.g. 'pytest', 'unittest', 'jest', 'go test'). Default: 'pytest'.
    - workspace: Workspace root directory.
    - write_to_file: If True, writes the generated tests directly into output_file.
    - output_file: Destination file path for generated tests (e.g. 'tests/test_auth.py').
    - model: Target model ID (default: gemini-3.8-flash-high).
    """
    ws_dir = resolve_workspace(workspace) or os.getcwd()
    target_path = Path(target_file)
    if not target_path.is_absolute():
        target_path = Path(ws_dir) / target_path

    if not target_path.exists():
        raise FileNotFoundError(f"Target file does not exist: {target_path}")

    source_code = target_path.read_text(encoding="utf-8", errors="replace")
    prompt = (
        f"You are a Senior QA / Test Engineer. Generate a comprehensive unit test suite using {test_framework} "
        f"for the following code.\n"
        f"Requirements:\n"
        f"- Cover happy paths, common edge cases, invalid inputs, and error handling.\n"
        f"- Use clean fixtures and mock external dependencies where appropriate.\n"
        f"- Write clean, idiomatic, ready-to-run code.\n\n"
        f"Source File ({target_path.name}):\n"
        f"```\n{source_code}\n```"
    )

    if write_to_file and output_file:
        out_path = Path(output_file)
        if not out_path.is_absolute():
            out_path = Path(ws_dir) / out_path
        prompt += f"\n\nWrite the complete test code directly into file: {out_path.as_posix()}."

    def _execute():
        return agy_agent.ask(
            prompt=prompt,
            model=model or agy_agent.DEFAULT_MODEL,
            workspace=ws_dir if write_to_file else None,
            system="You are an expert test automation engineer."
        )

    res = await _run_with_telemetry(_execute, ctx, label="TestGenerator")
    return f"Generated Unit Tests ({test_framework}):\n\n{res}"


@mcp.tool(name="ping")
async def ping(prompt: str = "Reply with exactly one word: PONG", ctx: Context = None) -> str:
    """Health check — verifies that the Antigravity CLI or backend responds."""
    def _execute():
        res, out, err, rc = agy_agent.run_agent(prompt, model=agy_agent.DEFAULT_MODEL, timeout=30)
        if rc != 0 or not res.strip():
            raise RuntimeError(f"Antigravity health check failed (rc={rc}): {err[-300:] or 'empty response'}")
        return res
    return await _run_with_telemetry(_execute, ctx, label="Ping")


@mcp.tool(name="list-models")
def list_models() -> list[str]:
    """List available models in Google Antigravity."""
    return [
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-low",
        "gemini-3.7-flash-high",
        "gemini-3.7-flash-medium",
        "gemini-3.7-flash-low",
        "gemini-3.6-flash-high",
        "gemini-3.6-flash-medium",
        "gemini-3.6-flash-low",
        "gemini-3.5-flash-high",
        "gemini-3.5-flash-medium",
        "gemini-3.5-flash-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    ]


# =============================================================================
# MCP RESOURCES
# =============================================================================

@mcp.resource("antigravity://quota", mime_type="text/markdown", description="Live remaining quota percentages for Google Antigravity models.")
def quota_resource() -> str:
    """Live remaining quota percentages for all available Antigravity models."""
    return check_quota()


@mcp.resource("antigravity://models", mime_type="application/json", description="List of available models in Google Antigravity.")
def models_resource() -> str:
    """JSON list of all available Antigravity models."""
    return json.dumps(list_models(), indent=2)


@mcp.resource("antigravity://status", mime_type="application/json", description="Active Google Antigravity runtime status and configuration.")
def status_resource() -> str:
    """Active runtime status and configuration of the Antigravity MCP Server."""
    return json.dumps({
        "status": "ready",
        "default_model": agy_agent.DEFAULT_MODEL,
        "agy_exe": agy_agent.AGY_EXE,
        "default_workspace": DEFAULT_WORKSPACE,
        "active_profile": agy_agent.PROFILE or "default",
        "has_rotation": agy_agent._HAS_ROTATION
    }, indent=2)


# =============================================================================
# MCP PROMPTS
# =============================================================================

@mcp.prompt(name="code-review", description="Prompt template for structured, senior-level code review of a diff or implementation.")
def code_review_prompt(diff_or_code: str, focus: str = "") -> str:
    """Generates a structured prompt for reviewing code or diffs."""
    focus_clause = f"Focus especially on: {focus}." if focus else "Check for bugs, security risks, edge cases, and performance regressions."
    return (
        f"You are a senior principal engineer. Perform a rigorous, professional code review on the following code/diff.\n"
        f"{focus_clause}\n\n"
        f"Format your review as follows:\n"
        f"### 1. Summary of Changes\n"
        f"### 2. Critical Issues & Potential Bugs\n"
        f"### 3. Edge Cases & Concurrency Concerns\n"
        f"### 4. Suggestions & Optimizations\n"
        f"### 5. Final Recommendation (Approve / Request Changes)\n\n"
        f"```\n{diff_or_code}\n```"
    )


@mcp.prompt(name="tdd-feature", description="Prompt template for Test-Driven Development (TDD): write failing tests first, then implement.")
def tdd_feature_prompt(feature_description: str, target_file: str = "") -> str:
    """Generates a structured prompt guiding Test-Driven Development (TDD)."""
    target_info = f" Target file: `{target_file}`." if target_file else ""
    return (
        f"You are a test-driven development (TDD) specialist.{target_info}\n"
        f"Feature specification:\n{feature_description}\n\n"
        f"Follow the strict Red-Green-Refactor cycle:\n"
        f"1. RED: First, write comprehensive unit tests covering happy paths and edge cases. Run them to confirm they fail.\n"
        f"2. GREEN: Write the minimal implementation code to make all tests pass cleanly.\n"
        f"3. REFACTOR: Clean up and optimize the implementation while maintaining 100% passing tests.\n"
        f"Execute and verify all tests before concluding."
    )


@mcp.prompt(name="security-audit", description="Security audit prompt template focusing on OWASP Top 10, sanitization, authentication, and CVE patterns.")
def security_audit_prompt(code_or_path: str, context: str = "") -> str:
    """Generates a structured prompt for an in-depth security audit."""
    ctx_info = f"\nContext/Architecture notes: {context}\n" if context else ""
    return (
        f"You are an application security expert. Perform an in-depth security audit on the following code or component based on OWASP Top 10 vulnerabilities.{ctx_info}\n\n"
        f"Inspect for:\n"
        f"- Injection vulnerabilities (SQLi, Command Injection, XSS, Path Traversal)\n"
        f"- Authentication & Authorization flaws (broken access control, token leakage)\n"
        f"- Cryptographic and secret management weaknesses\n"
        f"- Insecure deserialization or unsafe input handling\n"
        f"- Resource exhaustion / DoS risks\n\n"
        f"For each finding, provide:\n"
        f"- Severity (CRITICAL, HIGH, MEDIUM, LOW)\n"
        f"- Vulnerable code snippet\n"
        f"- Proof-of-concept explanation\n"
        f"- Concrete remediation code\n\n"
        f"Target:\n```\n{code_or_path}\n```"
    )


# =============================================================================
# DUAL-MODE CLI ENTRY POINT
# =============================================================================

def main():
    # If invoked with no command-line arguments, run as MCP server over stdio
    # (standard behavior for Claude Desktop, Cursor, Codex CLI, etc.)
    if len(sys.argv) == 1:
        mcp.run(transport="stdio")
        return

    parser = argparse.ArgumentParser(
        prog="antigravity-mcp",
        description="Antigravity v2.1 — Dual-Mode CLI & High-Performance Pure Python MCP Server."
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Prompt to execute via Antigravity agent in standalone CLI mode."
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run as standard MCP server over stdio."
    )
    parser.add_argument(
        "--quota",
        action="store_true",
        help="Display live quota percentages for all Antigravity models."
    )
    parser.add_argument(
        "--ping",
        action="store_true",
        help="Check connectivity and health of Antigravity agent."
    )
    parser.add_argument(
        "--models",
        action="store_true",
        help="List all supported models."
    )
    parser.add_argument(
        "--review",
        nargs="?",
        const="",
        default=None,
        help="Review current git diff in workspace. Optional argument: focus area."
    )
    parser.add_argument(
        "--continue", "--resume",
        dest="resume",
        action="store_true",
        help="Resume the previous conversation session (--continue)."
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help=f"Model to use (default: {agy_agent.DEFAULT_MODEL})."
    )
    parser.add_argument(
        "--system", "-s",
        default=None,
        help="System instruction for the agent."
    )
    parser.add_argument(
        "--workspace", "-w",
        default=None,
        help="Workspace directory (defaults to current directory)."
    )
    parser.add_argument(
        "--effort", "-e",
        choices=["low", "medium", "high"],
        default=None,
        help="Reasoning effort level."
    )

    args = parser.parse_args()

    if args.mcp:
        mcp.run(transport="stdio")
        return

    if args.quota:
        print(check_quota())
        return

    if args.ping:
        res = asyncio.run(ping())
        print(res)
        return

    if args.models:
        for m in list_models():
            print(f"- {m}")
        return

    if args.review is not None:
        focus = args.review if args.review.strip() else None
        print(asyncio.run(review_diff(workspace=args.workspace, focus=focus, model=args.model)))
        return

    if args.prompt or args.resume:
        user_prompt = args.prompt or "Continue from previous state."
        res = asyncio.run(ask_antigravity(
            prompt=user_prompt,
            resume=args.resume,
            model=args.model,
            system=args.system,
            workspace=args.workspace,
            effort=args.effort
        ))
        print(res)
        return

    # Fallback to help
    parser.print_help()


if __name__ == "__main__":
    main()
