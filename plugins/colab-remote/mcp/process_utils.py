"""Cross-platform helpers for silent, persistent background processes."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence


def _is_windows() -> bool:
    return os.name == "nt"


def windowless_python(executable: str | None = None) -> str:
    """Prefer the GUI Python launcher on Windows so no console can be created."""
    selected = Path(executable or sys.executable)
    if not _is_windows():
        return str(selected)
    candidates = [
        selected.with_name("pythonw.exe"),
        selected.with_name(selected.name.lower().replace("python", "pythonw", 1)),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return str(selected)


def _windows_startupinfo() -> Any:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return startupinfo


def background_popen(
    command: Sequence[str | os.PathLike[str]], *, windowless_python_entrypoint: bool = False
) -> subprocess.Popen[Any]:
    """Start a persistent helper without opening a terminal or inheriting MCP stdio."""
    args = [str(part) for part in command]
    if not args:
        raise ValueError("background command cannot be empty")
    if windowless_python_entrypoint:
        args[0] = windowless_python(args[0])
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if _is_windows():
        # CREATE_NO_WINDOW is ignored when combined with DETACHED_PROCESS.
        # A new Windows process survives its parent without DETACHED_PROCESS.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        kwargs["startupinfo"] = _windows_startupinfo()
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)
