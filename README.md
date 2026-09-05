# antigravity-mcp (Pure Python Edition)

An MCP (Model Context Protocol) server that exposes the **Google Antigravity (Gemini 3.7 Flash / Gemini 3.1 Pro)** coding **agent** to **Codex CLI**, **Claude Code**, and any MCP-compatible AI environment.

> **A real coding agent, not just an LLM API.** `ask-antigravity` / `ask-gemini` invoke the actual `agy` CLI agent, equipped with **Read / Write / Bash tools**. It can read files, inspect repositories, run commands, and modify code directly. By default, it operates in the directory from which the caller runs (`--add-dir`), seeing the codebase just like Codex.

---

## What's New in Pure Python Edition

1. **Pure Python Architecture**: Eliminated Node.js (`npm`, `node_modules`, `src/index.js`). Runs directly with Python and the official `mcp` SDK.
2. **Modern Model Support**: Defaulted to high-performance **Gemini 3.7 Flash** (`gemini-3.7-flash-high`) for blazing-fast speed and low cost, with full support for **Gemini 3.1 Pro** (`gemini-3.1-pro-high`).
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
git clone https://github.com/Laimusp/antigravity-mcp.git
cd antigravity-mcp
pip install -r requirements.txt
```

### 3. Verify Server
```powershell
python -m unittest tests/test_server.py
```

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
| `workspace` | string | Directory granted to the agent via `--add-dir`. Default: current working directory. Set to `"none"` for temp-only isolation. |
| `conversation_id` | string | Conversation ID to resume a specific historical session. |
| `effort` | string | Reasoning effort (`low`, `medium`, `high`). |
| `system` / `system_file` | string | System instructions (inline or file path). |
| `cleanup` | boolean | Automatically delete `prompt_file` / `system_file` after execution. |
| `save_artifact` | boolean | If true, saves full response to an artifact file in `.antigravity/artifacts/`. |

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