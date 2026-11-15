"""Run saved queries when they are due. One run at a time, so free quotas are not hit in parallel."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from . import config
from .pipeline import run
from .store import Store, now_iso

TICK_S = 30


class Scheduler:
    def __init__(self, store: Store, runner=run):
        self.store = store
        self.runner = runner
        self.run_lock = threading.Lock()
        self._stop = threading.Event()

    def due(self) -> list[dict]:
        return self.store.q("SELECT * FROM schedules WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at",
                            (now_iso(),))

    def tick(self) -> int:
        n = 0
        for s in self.due():
            interval = max(config.caps().MIN_SCHEDULE_MINUTES, s["interval_min"])
            nxt = (datetime.now(timezone.utc) + timedelta(minutes=interval)).replace(microsecond=0)
            self.store.x("UPDATE schedules SET last_run_at=?, next_run_at=? WHERE id=?",
                         (now_iso(), nxt.isoformat().replace("+00:00", "Z"), s["id"]))
            with self.run_lock:
                self.runner(s["query"], s["hours"], self.store, schedule_id=s["id"])
            n += 1
        return n

    def loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # keep the loop alive; the run itself already logged an event
                self.store.add_event(None, "scheduler", "error", config.redact(f"{type(e).__name__}: {e}"))
            self._stop.wait(TICK_S)

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.loop, name="incident-scheduler", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
