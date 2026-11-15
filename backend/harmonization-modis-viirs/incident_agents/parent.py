"""Stop when the process that started us is gone.

The backend (backend/api/incidents.py) launches `python -m incident_agents serve --exit-with-parent <pid>`
and restarts it if it dies. If the backend is killed instead of stopped (an auto-reload, a closed terminal),
this keeps the agents from lingering on their port with the scheduler running.
"""

from __future__ import annotations

import os
import threading
import time


def parent_alive(pid: int) -> bool:
    """Is process `pid` still running?"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        # Not os.kill(pid, 0): on Windows any signal other than Ctrl-C/Ctrl-Break terminates the process.
        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # it exists, it is just not ours
        return True
    return True


def exit_with_parent(pid: int, interval: float = 2.0, on_gone=lambda: os._exit(0)) -> threading.Thread:
    """Call `on_gone` (default: exit at once) when process `pid` disappears. Runs in a daemon thread."""

    def watch() -> None:
        while parent_alive(pid):
            time.sleep(interval)
        on_gone()

    t = threading.Thread(target=watch, name="exit-with-parent", daemon=True)
    t.start()
    return t
