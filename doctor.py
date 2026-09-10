"""Offline environment checks; never reads credentials or invokes the agent."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys


def diagnose():
    executable = os.environ.get("AGY_EXE") or shutil.which("agy")
    if not executable and sys.platform == "win32":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "agy/bin/agy.exe"
        executable = str(candidate) if candidate.is_file() else None
    dependencies = {name: importlib.util.find_spec(name) is not None
                    for name in ("mcp", "anyio", "requests")}
    switcher = os.environ.get("AGY_SWITCH_SCRIPT") or str(
        Path(__file__).resolve().parent.parent / "antigravity-auth-manager/agy_switch.py")
    return {
        "platform": platform.system(),
        "wsl": "microsoft" in platform.release().lower(),
        "python": sys.version.split()[0],
        "dependencies": dependencies,
        "server_dependencies_ready": all(dependencies.values()),
        "agy_executable": executable,
        "agy_executable_found": bool(executable and shutil.which(executable)),
        "cli_authentication": "not_checked; authenticate native agy separately",
        "backend_auth_source": ("environment" if os.environ.get("AGY_ACCESS_TOKEN")
                                else "windows_credential_manager" if sys.platform == "win32"
                                else "not_configured"),
        "backend_authentication": "not_verified",
        "refresh_token_configured": bool(os.environ.get("AGY_REFRESH_TOKEN")),
        "account_switcher_found": Path(switcher).is_file(),
    }


def main():
    result = diagnose()
    print(json.dumps(result, indent=2))
    return 0 if result["server_dependencies_ready"] and result["agy_executable_found"] else 1


if __name__ == "__main__":
    sys.exit(main())
