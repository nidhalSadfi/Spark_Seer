"""The API starts and watches the fire incident agents (api/incidents.py) without importing them (I1)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from api import incidents
from api.incidents import MAX_FAILURES, STARTUP_TICKS, AgentsSupervisor

BACKEND = Path(__file__).resolve().parent.parent


class FakeProc:
    """Stands in for subprocess.Popen: alive until `exit(code)` or terminate()."""
    made: list = []

    def __init__(self, cmd, **kwargs):
        self.cmd, self.kwargs, self.pid, self.returncode = cmd, kwargs, 4000 + len(FakeProc.made), None
        self.terminated = self.killed = False
        FakeProc.made.append(self)

    def poll(self):
        return self.returncode

    def exit(self, code=1):
        self.returncode = code

    def terminate(self):
        self.terminated, self.returncode = True, 0

    def kill(self):
        self.killed, self.returncode = True, -9

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture(autouse=True)
def fresh():
    FakeProc.made = []


def make(listening, **kw):
    """A supervisor with a fake Popen and a controllable 'is the port open' answer (a one-item list)."""
    return AgentsSupervisor(port=8765, popen=FakeProc, listening=lambda port: listening[0], python="py", pid=777, **kw)


# --- settings -------------------------------------------------------------------------------------
def test_autostart_defaults_on_locally_and_off_on_render():
    assert incidents.autostart_enabled({}) is True
    assert incidents.autostart_enabled({"RENDER": "true"}) is False            # not next to a public API
    assert incidents.autostart_enabled({"RENDER": "true", "INCIDENTS_AUTOSTART": "1"}) is True   # unless asked
    for off in ("0", "false", "No", "OFF"):
        assert incidents.autostart_enabled({"INCIDENTS_AUTOSTART": off}) is False
    assert incidents.autostart_enabled({"INCIDENTS_AUTOSTART": "maybe"}) is True  # unreadable: the default


def test_port_setting():
    assert incidents.agents_port({}) == 8765
    assert incidents.agents_port({"INCIDENT_PORT": "9100"}) == 9100
    for bad in ("abc", "0", "70000", "-3"):
        assert incidents.agents_port({"INCIDENT_PORT": bad}) == 8765


# --- starting and restarting ----------------------------------------------------------------------
def test_starts_the_agents_with_the_right_command_when_nothing_listens():
    s = make([False])
    s.tick()
    assert len(FakeProc.made) == 1
    p = FakeProc.made[0]
    assert p.cmd == ["py", "-m", "incident_agents", "serve", "--port", "8765", "--exit-with-parent", "777"]
    assert Path(p.kwargs["cwd"]) == BACKEND / "harmonization-modis-viirs"
    assert p.kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert s.status()["managed"] and s.status()["pid"] == p.pid and s.spawns == 1


def test_leaves_agents_that_are_already_running_alone():
    up = [True]
    s = make(up)
    s.tick()
    s.tick()
    assert FakeProc.made == []                       # started by hand: not ours, not touched
    s.stop()
    assert s.status()["running"] is True and s.status()["managed"] is False


def test_waits_for_a_starting_child_then_replaces_one_that_never_listens():
    s = make([False])
    s.tick()                                         # spawned
    for _ in range(STARTUP_TICKS):
        s.tick()                                     # still starting: no second child
    assert len(FakeProc.made) == 1
    s.tick()                                         # stuck for too long: stopped and started again
    assert FakeProc.made[0].terminated and len(FakeProc.made) == 2
    assert "never listened" in s.status()["last_error"] and s.failures == 1


def test_restarts_a_child_that_exits_and_gives_up_after_repeated_failures():
    s = make([False])
    s.tick()
    for n in range(1, MAX_FAILURES):
        FakeProc.made[-1].exit(1)
        s.tick()
        assert len(FakeProc.made) == n + 1 and not s.gave_up
        assert s.status()["last_error"] == "the agents exited with code 1"
    FakeProc.made[-1].exit(1)
    s.tick()                                         # the last allowed failure: stop trying
    assert s.gave_up and len(FakeProc.made) == MAX_FAILURES
    s.tick()
    assert len(FakeProc.made) == MAX_FAILURES        # and it stays stopped
    assert s.status()["gave_up"] is True


def test_a_working_child_clears_earlier_trouble():
    listening = [False]
    s = make(listening)
    s.tick()
    FakeProc.made[-1].exit(2)
    s.tick()                                         # restarted once
    assert s.failures == 1
    listening[0] = True                              # the new child is up
    s.tick()
    assert s.failures == 0 and s.last_error is None


def test_a_start_that_fails_to_launch_is_reported(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError("no such python")
    s = AgentsSupervisor(port=8765, popen=boom, listening=lambda p: False, python="py", pid=1)
    s.tick()
    assert s.failures == 1 and "no such python" in s.last_error and s.proc is None and s.spawns == 0


# --- stopping -------------------------------------------------------------------------------------
def test_stop_ends_only_the_child_it_started():
    s = make([False])
    s.tick()
    child = FakeProc.made[0]
    s.stop()
    assert child.terminated and s.proc is None


def test_start_is_a_no_op_when_autostart_is_off(monkeypatch):
    monkeypatch.setenv("INCIDENTS_AUTOSTART", "0")
    s = make([False])
    s.start()
    assert s._thread is None and FakeProc.made == [] and s.status()["autostart"] is False
    s.stop()


def test_start_runs_the_supervision_thread_and_stop_ends_it(monkeypatch):
    monkeypatch.setenv("INCIDENTS_AUTOSTART", "1")
    s = make([False])
    s.start()
    s.start()                                        # a second start does not add a thread
    assert s._thread is not None
    for _ in range(200):                             # the first look happens at once, in the thread
        if FakeProc.made:
            break
        import time
        time.sleep(0.01)
    assert len(FakeProc.made) == 1
    s.stop()
    assert s._thread is None and FakeProc.made[0].terminated


# --- the endpoint and invariant I1 ------------------------------------------------------------------
def test_status_endpoint(client):
    r = client.get("/incidents/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"autostart", "running", "managed", "pid", "port", "url", "spawns", "failures", "gave_up", "last_error"}
    assert body["url"] == f"http://127.0.0.1:{body['port']}" and isinstance(body["running"], bool)


def test_the_api_process_does_not_import_the_agents_or_any_llm_module():
    """I1, for the new module: a fresh interpreter that imports the API (and serves /incidents/status)."""
    code = (
        "import json, sys\n"
        "from fastapi.testclient import TestClient\n"
        "from api.main import app\n"
        "assert TestClient(app).get('/incidents/status').status_code == 200\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        "('incident_agents', 'worker', 'crewai', 'crewai_core', 'openai', 'litellm'))\n"
        "print(json.dumps(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []
