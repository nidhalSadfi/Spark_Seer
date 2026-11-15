"""Starts and watches the fire incident agents together with the API.

The agents (harmonization-modis-viirs/incident_agents) search public posts and news for fires with a
language model and spend paid quotas. Invariant I1 keeps the model out of this process, so the API does
not import them: it launches `python -m incident_agents serve` as a separate child process, bound to
127.0.0.1 (the dashboard's Social Alerts page talks to it directly), restarts it if it dies, and stops it
with the API. Nothing from incident_agents is imported here.

Their keys come from the repo-root `.env` (the same file as the rest of the backend), read by the agents
themselves, or from the environment this process was started in.

Settings (environment):
    INCIDENTS_AUTOSTART   1 / 0. Default: on, except on Render (RENDER is set there), where the agents are
                          not started next to a public API.
    INCIDENT_PORT         the agents' port (default 8765); the dashboard's VITE_INCIDENTS_URL must match.

GET /incidents/status says whether they are running.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import APIRouter

log = logging.getLogger("uvicorn.error")

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "harmonization-modis-viirs"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
CHECK_EVERY_S = 5.0     # how often the supervisor looks
STARTUP_TICKS = 8       # a child that is alive but not listening after this many looks (40 s) is stuck
MAX_FAILURES = 5        # exits or failed starts in a row before it stops trying (the status says so)

_ON, _OFF = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


def autostart_enabled(env=None) -> bool:
    """Whether starting the API should also start the agents."""
    env = os.environ if env is None else env
    flag = (env.get("INCIDENTS_AUTOSTART") or "").strip().lower()
    if flag in _ON:
        return True
    if flag in _OFF:
        return False
    return not env.get("RENDER") and AGENTS_DIR.is_dir()  # hosted deployments opt in explicitly


def agents_port(env=None) -> int:
    env = os.environ if env is None else env
    try:
        port = int((env.get("INCIDENT_PORT") or "").strip() or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def is_listening(port: int, host: str = HOST, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class AgentsSupervisor:
    """Keeps one agents process listening on `port`. Agents someone else started are left alone."""

    def __init__(self, port: int | None = None, popen=subprocess.Popen, listening=is_listening,
                 python: str | None = None, pid: int | None = None):
        self.port = port or agents_port()
        self._popen = popen
        self._listening = listening
        self._python = python or sys.executable
        self._pid = pid or os.getpid()
        self.proc = None
        self.spawns = 0
        self.failures = 0
        self.waiting = 0
        self.gave_up = False
        self.last_error: str | None = None
        self.enabled = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def command(self) -> list[str]:
        return [self._python, "-m", "incident_agents", "serve", "--port", str(self.port),
                "--exit-with-parent", str(self._pid)]

    def _ours(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _spawn(self) -> None:
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            self.proc = self._popen(self.command(), cwd=str(AGENTS_DIR), env=env)
        except OSError as e:
            self.proc = None
            self.failures += 1
            self.last_error = f"could not start the agents: {e}"
            log.warning(self.last_error)
            return
        self.spawns += 1
        self.waiting = 0
        log.info("incident agents starting on http://%s:%s (pid %s)", HOST, self.port, self.proc.pid)

    def tick(self) -> None:
        """One look: make sure the agents are listening, starting them if they are not."""
        if self.gave_up:
            return
        if self._listening(self.port):
            if self._ours():
                self.failures = 0  # up, and ours: any earlier trouble is over
                self.last_error = None
            return  # up, but someone else's: leave them alone
        if self._ours():  # started by us, not listening yet
            self.waiting += 1
            if self.waiting <= STARTUP_TICKS:
                return
            self.last_error = "the agents started but never listened; they were stopped and will be started again"
            self.proc.terminate()
            self.failures += 1
        elif self.proc is not None:  # it exited
            self.last_error = f"the agents exited with code {self.proc.returncode}"
            self.failures += 1
            log.warning("%s (see their output above)", self.last_error)
        self.proc = None
        if self.failures >= MAX_FAILURES:
            self.gave_up = True
            log.error("incident agents: giving up after %s failed starts (%s). Fix it and restart the API, or "
                      "start them by hand: cd backend/harmonization-modis-viirs && python -m incident_agents serve",
                      self.failures, self.last_error)
            return
        self._spawn()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # a supervisor must not die of its own bug
                log.exception("incident agents supervisor")
            self._stop.wait(CHECK_EVERY_S)

    def start(self) -> None:
        """Begin supervising (a no-op when autostart is off or already begun)."""
        self.enabled = autostart_enabled()
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="incident-agents-supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop supervising and stop the agents this supervisor started (never ones started by hand)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._ours():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def status(self) -> dict:
        return {
            "autostart": self.enabled or autostart_enabled(),
            "running": self._listening(self.port),
            "managed": self._ours(),
            "pid": self.proc.pid if self._ours() else None,
            "port": self.port,
            "url": f"http://{HOST}:{self.port}",
            "spawns": self.spawns,
            "failures": self.failures,
            "gave_up": self.gave_up,
            "last_error": self.last_error,
        }


supervisor = AgentsSupervisor()
router = APIRouter()


@router.get("/incidents/status", tags=["incidents"])
def incidents_status() -> dict:
    """Are the fire incident agents running (and did this API start them)? Used by the dashboard's Social Alerts page."""
    return supervisor.status()
