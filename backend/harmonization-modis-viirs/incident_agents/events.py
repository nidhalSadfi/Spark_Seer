"""Step events. Every pipeline step reports through Reporter so a UI can follow it."""

from __future__ import annotations

import sys

from .config import redact
from .store import Store


class Reporter:
    def __init__(self, store: Store, run_id: int | None, echo: bool = True):
        self.store, self.run_id, self.echo = store, run_id, echo

    def __call__(self, step: str, status: str, message: str = "", source: str | None = None,
                 data: dict | None = None) -> None:
        message = redact(message)
        self.store.add_event(self.run_id, step, status, message, source, data)
        if self.echo:
            tag = f"{step}/{source}" if source else step
            print(f"[run {self.run_id}] {tag:<22} {status:<8} {message}", file=sys.stderr, flush=True)
