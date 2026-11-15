"""SQLite store for runs, step events, tickets, ticket sources and schedules."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query TEXT NOT NULL, schedule_id INTEGER, hours INTEGER NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  plan TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, ts TEXT NOT NULL, step TEXT NOT NULL, source TEXT,
  status TEXT NOT NULL, message TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, run_id INTEGER,
  title TEXT, place TEXT, country TEXT, lat REAL, lon REAL,
  precision TEXT, radius_km REAL, first_seen TEXT, last_seen TEXT,
  status TEXT NOT NULL, satellite TEXT, category TEXT
);
CREATE TABLE IF NOT EXISTS ticket_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL REFERENCES tickets(id),
  source TEXT NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL,
  author TEXT, published_at TEXT, fetched_at TEXT NOT NULL,
  excerpt TEXT, place_phrase TEXT, judge TEXT,
  UNIQUE(ticket_id, url)
);
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query TEXT NOT NULL, hours INTEGER NOT NULL, interval_min INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, last_run_at TEXT, next_run_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
  day TEXT NOT NULL, source TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, source)
);
CREATE TABLE IF NOT EXISTS verdicts (
  url TEXT PRIMARY KEY, judged_at TEXT NOT NULL, model TEXT, verdict TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id, id);
-- columns added later are created by Store._migrate()
CREATE INDEX IF NOT EXISTS ix_sources_url ON ticket_sources(url);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = str(path or config.DB_PATH)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL") if self.path != ":memory:" else None
        self.conn.executescript(SCHEMA)
        self._migrate()

    # Columns added after the first release; ALTER keeps existing databases working.
    _ADDED = {"tickets": {"mode": "TEXT NOT NULL DEFAULT 'live'", "history": "TEXT"},
              "runs": {"mode": "TEXT NOT NULL DEFAULT 'live'"},
              "ticket_sources": {"precision": "TEXT"}}

    def _migrate(self) -> None:
        for table, cols in self._ADDED.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.conn.commit()

    def x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # runs -------------------------------------------------------------
    def start_run(self, query: str, hours: int, schedule_id: int | None = None, mode: str = "live") -> int:
        return self.x("INSERT INTO runs(query, schedule_id, hours, started_at, status, mode) VALUES (?,?,?,?,?,?)",
                      (query, schedule_id, hours, now_iso(), "running", mode)).lastrowid

    def finish_run(self, run_id: int, status: str, plan: dict | None, summary: dict) -> None:
        self.x("UPDATE runs SET finished_at=?, status=?, plan=?, summary=? WHERE id=?",
               (now_iso(), status, json.dumps(plan, ensure_ascii=False) if plan else None,
                json.dumps(summary, ensure_ascii=False), run_id))

    # events -----------------------------------------------------------
    def add_event(self, run_id: int | None, step: str, status: str, message: str = "",
                  source: str | None = None, data: dict | None = None) -> int:
        return self.x("INSERT INTO events(run_id, ts, step, source, status, message, data) VALUES (?,?,?,?,?,?,?)",
                      (run_id, now_iso(), step, source, status, message,
                       json.dumps(data, ensure_ascii=False) if data else None)).lastrowid

    def events_after(self, after_id: int, run_id: int | None = None, limit: int = 200) -> list[dict]:
        if run_id is None:
            return self.q("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (after_id, limit))
        return self.q("SELECT * FROM events WHERE id>? AND run_id=? ORDER BY id LIMIT ?", (after_id, run_id, limit))

    # usage caps -------------------------------------------------------
    def usage_today(self, source: str) -> int:
        rows = self.q("SELECT calls FROM usage WHERE day=? AND source=?", (now_iso()[:10], source))
        return rows[0]["calls"] if rows else 0

    def add_usage(self, source: str, n: int = 1) -> None:
        self.x("INSERT INTO usage(day, source, calls) VALUES (?,?,?) "
               "ON CONFLICT(day, source) DO UPDATE SET calls = calls + excluded.calls",
               (now_iso()[:10], source, n))

    # verdict cache: scheduled runs see the same posts again; never pay to judge one twice
    def cached_verdicts(self, urls: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            rows = self.q(f"SELECT url, verdict FROM verdicts WHERE url IN ({','.join('?' * len(chunk))})",
                          tuple(chunk))
            out.update({r["url"]: json.loads(r["verdict"]) for r in rows})
        return out

    def save_verdict(self, url: str, verdict: dict, model: str) -> None:
        self.x("INSERT OR REPLACE INTO verdicts(url, judged_at, model, verdict) VALUES (?,?,?,?)",
               (url, now_iso(), model, json.dumps(verdict, ensure_ascii=False)))

    # tickets ----------------------------------------------------------
    def recent_tickets(self, since_iso: str, mode: str = "live", until_iso: str | None = None) -> list[dict]:
        """Tickets a new report may join. Replay tickets and live tickets never merge."""
        if until_iso:
            return self.q("SELECT * FROM tickets WHERE mode=? AND last_seen>=? AND first_seen<=? ORDER BY id",
                          (mode, since_iso, until_iso))
        return self.q("SELECT * FROM tickets WHERE mode=? AND last_seen>=? ORDER BY id", (mode, since_iso))

    def url_ticket(self, url: str, mode: str = "live") -> int | None:
        rows = self.q("SELECT s.ticket_id FROM ticket_sources s JOIN tickets t ON t.id=s.ticket_id "
                      "WHERE s.url=? AND t.mode=? LIMIT 1", (url, mode))
        return rows[0]["ticket_id"] if rows else None

    def tickets_with_sources(self, limit: int = 100) -> list[dict]:
        ts = self.q("SELECT * FROM tickets ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,))
        for t in ts:
            t["satellite"] = json.loads(t["satellite"]) if t["satellite"] else None
            t["history"] = json.loads(t["history"]) if t.get("history") else None
            t["sources"] = self.q("SELECT source, kind, url, author, published_at, fetched_at, excerpt, place_phrase "
                                  "FROM ticket_sources WHERE ticket_id=? ORDER BY published_at", (t["id"],))
        return ts

    # schedules --------------------------------------------------------
    def add_schedule(self, query: str, hours: int, interval_min: int) -> int:
        return self.x("INSERT INTO schedules(query, hours, interval_min, next_run_at) VALUES (?,?,?,?)",
                      (query, hours, interval_min, now_iso())).lastrowid
