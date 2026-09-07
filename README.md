# antigravity-mcp (Pure Python Edition)

An MCP (Model Context Protocol) server that exposes the **Google Antigravity (Gemini 3.8 Flash / Gemini 3.1 Pro)** coding **agent** to **Codex CLI**, **Claude Code**, and any MCP-compatible AI environment.

> **A real coding agent, not just an LLM API.** `ask-antigravity` / `ask-gemini` invoke the actual `agy` CLI agent, equipped with **Read / Write / Bash tools**. It can read files, inspect repositories, run commands, and modify code directly. By default, it operates in the directory from which the caller runs (`--add-dir`), seeing the codebase just like Codex.

---

## What's New in Pure Python Edition

1. **Pure Python Architecture**: Eliminated Node.js (`npm`, `node_modules`, `src/index.js`). Runs directly with Python and the official `mcp` SDK.
2. **Modern Model Support**: Defaulted to high-performance **Gemini 3.8 Flash** (`gemini-3.8-flash-high`) for blazing-fast speed and low cost, with full support for **Gemini 3.1 Pro** (`gemini-3.1-pro-high`).
3. **Session & Multi-Turn Support**: Added `conversation_id` parameter to continue and resume ongoing agent workflows (`agy --conversation <ID>`).
4. **Standardized English Prompting**: Replaced hardcoded foreign wrappers with clear, robust task prompts that prevent agent execution stalls.
5. **Keep-Alive & Telemetry**: Built-in background progress loop prevents caller timeouts during long reasoning tasks.

---

## Architecture

```text
Codex CLI / Claude Code
       │
       ▼ (MCP stdio protocol)
   server.py (Pure Python MCP Server)
       │
       ├─► ping ──────────────► Fast health-check
       ├─► list-models ───────► Available Antigravity models
       │
       └─► ask-antigravity ───► agy_agent.py
                                  │
                                  ├─ File-in (temp in.txt)
                                  ├─ agy.exe -p ... --dangerously-skip-permissions
                                  ├─ Workspace granted (--add-dir <cwd>)
                                  └─ File-out (temp out.txt)
```

---

## Quick Start

### 1. Requirements
- **Windows** (uses Windows Credential Manager for Google Antigravity OAuth tokens).
- **Python 3.10+**.
- **Google Antigravity CLI (`agy`)** installed and authenticated at least once (`cmdkey /list:gemini:antigravity`).

### 2. Installation
```powershell
git clone https://github.com/khoa-na/antigravity-mcp.git
cd antigravity-mcp
pip install -r requirements.txt
```

### 3. Verify Server
```powershell
python -m unittest discover -s tests -v
```

The test suite is offline: it mocks authentication, HTTP requests, and agent execution,
and stores test artifacts in temporary directories. `run_demo.py` is a separate **live**
demo that invokes the authenticated agent; it is not an offline test.

---

## Client Configuration

### Codex CLI Configuration

Add to your Codex CLI configuration (`~/.codex/config.toml`):

```toml
[mcp_servers.antigravity]
command = "python"
args = ["D:/antigravity-mcp/server.py"]

[mcp_servers.antigravity.env]
AGY_MODEL = "gemini-3.7-flash-high"
```

### Claude Code Configuration

Add to your `.claude.json`:

```json
{
  "mcpServers": {
    "antigravity": {
      "type": "stdio",
      "command": "python",
      "args": ["D:\\antigravity-mcp\\server.py"],
      "env": {
        "AGY_MODEL": "gemini-3.8-flash-high"
      }
    }
  }
}
```

---

## Available MCP Tools

### `ask-antigravity` / `ask-gemini`
Executes tasks using the Google Antigravity coding agent.

| Parameter | Type | Description |
|---|---|---|
| `prompt` | string | The task / question / code instruction. |
| `resume` | boolean | If true, automatically continues the most recent conversation session (`--continue`) without needing an ID. |
| `prompt_file` | string | Absolute path to a file containing the prompt (ideal for large codebases or artifacts). |
| `model` | string | Target model ID (default: `gemini-3.8-flash-high`, or `gemini-3.1-pro-high`). |
| `workspace` | string | Existing directory granted via `--add-dir`, normalized to an absolute path. Default: server working directory. `"none"` omits the grant; it is **not a security sandbox**. |
| `conversation_id` | string | Conversation ID to resume a specific historical session. |
| `effort` | string | Reasoning effort (`low`, `medium`, `high`). |
| `system` / `system_file` | string | System instructions (inline or file path). |
| `cleanup` | boolean | Automatically delete `prompt_file` / `system_file` after execution. |
| `save_artifact` | boolean | If true, saves full response to an artifact file in `.antigravity/artifacts/`. |
| `result_format` | string | `"text"` (compatible default) or `"json"` for execution status and metadata. |
| `timeout` | number | Positive agent time budget in seconds (maximum 86400), shared across CLI retries. Default: `AGY_AGENT_TIMEOUT`. |

### Reliable handoff to a supervising agent

Use `result_format="json"` when the caller needs machine-readable results. The tool
returns a JSON **string**, not a separate MCP structured-content object. It includes
`status`, `task_id`, `conversation_id`, `resume_latest`, `workspace`, `model`,
`exit_code`, `elapsed_seconds`, `response`, `error`, and
`verification="not_run_by_wrapper"`.

- `succeeded` means the CLI exited with code 0 and wrote a non-empty response file.
  It does **not** prove the requested code is correct or that tests passed.
- `partial` preserves an answer written before a nonzero exit. It is not retried.
- `failed`, `timed_out`, and `busy` are explicit failures. In text mode these raise
  tool errors; in JSON mode they are returned as status records. Invalid inputs
  still raise tool errors in either mode.
- stdout-only CLI logs are no longer accepted as a successful answer.
- The caller should send the task, file scope, constraints, and acceptance checks
  in `prompt` or `prompt_file`, then independently inspect the diff and run tests.

New calls no longer invent conversation IDs: `--conversation` resumes an **existing**
CLI conversation. The result echoes an explicitly supplied `conversation_id`; otherwise
it is `null` because this wrapper cannot yet discover the CLI-generated ID. A `task_id`
is only a correlation label, not a resumable conversation. Use either an existing
`conversation_id` or `resume=true`, not both. `resume=true` refers to the CLI's most
recent conversation, not a workspace-specific session maintained by this server.

`AGY_AGENT_PROFILE`, when set, is stable across calls; random worker subdirectories
are no longer created. Authenticate that profile before using it. Calls into the CLI
are serialized **within one server process**, including `ping`; overlapping calls fail
with `busy`. This does not lock out another server process or a human editor. Avoid
concurrent writers to the same checkout.

The timeout budget starts before quota preflight and remaining time is passed to each
CLI attempt. Each attempt explicitly sets `agy --print-timeout` from that remaining
budget, reserving 5% (at most 10 seconds) for CLI exit and result collection. For
example, a 600-second attempt uses `--print-timeout 590s`, rather than the CLI's
independent default of 5 minutes. Python retains the original remaining budget as
its hard subprocess timeout. CLI `timeout waiting for response` errors are reported
as `timed_out`, not as a generic missing-output-file failure. Partial responses are
still reported as `partial`, never as success.

Restart/reconnect the MCP server after updating these files; an already-running
Python process does not automatically reload the wrapper.

Already-running auth/quota/switcher calls retain their own timeouts;
this is not a hard wall-clock deadline for all network operations. The 15-second
heartbeat reports elapsed time only. Background jobs, cancellation of descendant
processes, and durable task logs are not implemented yet. Quota/auth retries can
replay a task when no response file exists; inspect the working tree after failures.

**Security:** coding tools still invoke `agy --dangerously-skip-permissions`.
Workspace selection and the in-process lock do not enforce filesystem confinement.
Use a separately restricted environment for untrusted tasks. `review-diff` now uses
the existing text-only HTTP backend, not the CLI, so it cannot execute file tools;
it requires backend authentication/connectivity and does not fall back to the CLI.
Staged review never includes unstaged changes. Git failures are reported explicitly,
and untracked files are not part of the diff.

---

## Dual-Mode CLI

`antigravity-mcp` is not only an MCP server; it is also a standalone CLI tool you can use directly from PowerShell, CMD, or Bash without needing an MCP client!

```powershell
# 1. Live model quota status
antigravity-mcp --quota

# 2. Fast connectivity health-check
antigravity-mcp --ping

# 3. List all supported models
antigravity-mcp --models

# 4. Instant automated git diff review
antigravity-mcp --review "security and performance"

# 5. Ask Antigravity directly from the terminal
antigravity-mcp "Refactor the authentication module using bcrypt"

# 6. One-click conversation continuation (no session ID needed!)
antigravity-mcp --resume "Now write tests for that refactored module"

# 7. Start as standard MCP stdio server
antigravity-mcp --mcp
```

---

## The 3 Pillars of MCP

`antigravity-mcp` fully implements all 3 core pillars of the Model Context Protocol:

### 1. Tools
- **`ask-antigravity` / `ask-gemini`**: Executes tasks using the Google Antigravity coding agent with Read/Write/Bash tools and session resumption (`resume=True`).
- **`review-diff`**: Automated, read-only code review of current git changes (`git diff`).
- **`check-quota`**: Displays live remaining quota percentages and health status for all Antigravity models.
- **`generate-tests`**: Generates comprehensive unit test suites for a given source code file.
- **`ping`**: Quick health check verifying Antigravity responsiveness.
- **`list-models`**: Lists all available models provided by the Antigravity backend.

### 2. Resources
Clients can read real-time context directly via MCP URIs:
- **`antigravity://quota`**: Live remaining quota table in Markdown format.
- **`antigravity://models`**: Complete JSON list of all available Antigravity models.
- **`antigravity://status`**: Real-time runtime configuration, active profile, and engine status.

### 3. Prompts
Standardized prompt templates ready for one-click use in Claude, Cursor, and Codex:
- **`code-review`**: Senior principal engineer code review template covering changes, bugs, edge cases, and optimizations.
- **`tdd-feature`**: Test-Driven Development template following the strict Red-Green-Refactor cycle.
- **`security-audit`**: In-depth security audit template focusing on OWASP Top 10 vulnerabilities, sanitization, and CVEs.

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `AGY_MODEL` | `gemini-3.8-flash-high` | Default model ID used by the agent. |
| `AGY_EXE` | Auto-detected | Explicit path to `agy.exe`. |
| `AGY_AGENT_TIMEOUT` | `900` | Timeout in seconds before terminating long runs. |
| `AGY_DEFAULT_WORKSPACE` | `process.cwd()` | Default workspace granted to the agent. Set to `"none"` to isolate. |
| `CLOUD_CODE_URL` | None | Set only if using a custom local unlock proxy. |

---

## Cross-Platform Compatibility
- **Windows**: Full native support with Windows Credential Manager and Win32 file locking.
- **Linux & macOS**: POSIX advisory file locking via `fcntl` and token auth via `AGY_ACCESS_TOKEN`.

---

## License
MIT
