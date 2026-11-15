"""Structured (JSON lines) logging to stdout, with secrets redacted from every line."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Callable

_RECORD_FIELDS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, redact: Callable[[str], str] | None = None, static: dict | None = None):
        super().__init__()
        self.redact = redact or (lambda s: s)
        self.static = dict(static or {})

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name, "msg": record.getMessage(), **self.static}
        out.update({k: v for k, v in vars(record).items() if k not in _RECORD_FIELDS and not k.startswith("_")})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        # Redact the whole line: the key could appear in the message, an extra field or a traceback.
        return self.redact(json.dumps(out, default=str))


def setup_logging(level: str = "INFO", redact: Callable[[str], str] | None = None,
                  static: dict | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(redact, static))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("urllib3", "alembic"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
