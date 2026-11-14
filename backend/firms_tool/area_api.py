"""FIRMS MAP_KEY web services: key status, data availability and resumable area downloads."""

from __future__ import annotations

import csv
import io
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import requests

from .portal import BASE, new_session
from .sensors import Sensor

MAP_KEY_ENV_VARS = ("FIRMS_MAP_KEY_1", "FIRMS_MAP_KEY")
MAX_DAYS = 5
RETRY_STATUS = {429, 500, 502, 503, 504}
LIMIT_WINDOW_S = 600
DEFAULT_LIMIT = 5000
# Measured 2026-11-14: a call costs 2 transactions per day per 60x60-degree tile of the bbox extent
# (ceil on each side), independent of how many rows come back. World = 6x3 tiles = 36/day.
TILE_DEG = 60.0
COST_PER_TILE_DAY = 2


def resolve_map_key(cli_value: str | None = None) -> str | None:
    if cli_value:
        return cli_value
    for name in MAP_KEY_ENV_VARS:
        if os.environ.get(name):
            return os.environ[name]
    return None


class ApiError(RuntimeError):
    pass


class RateLimited(ApiError):
    pass


@dataclass(frozen=True)
class Window:
    source: str
    start: date
    days: int

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.days - 1)


def bbox_arg(bbox: tuple[float, float, float, float] | str) -> str:
    return bbox if isinstance(bbox, str) else ",".join(f"{v:g}" for v in bbox)


def bbox_tiles(bbox: str) -> int:
    if bbox == "world":
        w, s, e, n = -180.0, -90.0, 180.0, 90.0
    else:
        w, s, e, n = (float(v) for v in bbox.split(","))
    return max(1, math.ceil((e - w) / TILE_DEG - 1e-9)) * max(1, math.ceil((n - s) / TILE_DEG - 1e-9))


def call_cost(w: "Window", bbox: str) -> int:
    return COST_PER_TILE_DAY * w.days * bbox_tiles(bbox)


def windows_for(source: str, start: date, end: date) -> list[Window]:
    out = []
    cur = start
    while cur <= end:
        days = min(MAX_DAYS, (end - cur).days + 1)
        out.append(Window(source, cur, days))
        cur += timedelta(days=days)
    return out


def plan_sensor(sensor: Sensor, start: date, end: date, mode: str,
                availability: dict[str, tuple[date, date]] | None) -> list[Window]:
    """Split a date range into <=5-day windows, using SP where it exists and NRT after it (mode=auto)."""
    start = max(start, sensor.coverage_start)
    if sensor.coverage_end:
        end = min(end, sensor.coverage_end)
    if start > end:
        return []
    if mode == "sp":
        return windows_for(sensor.api_sp, start, end)
    if mode == "nrt":
        return windows_for(sensor.api_nrt, start, end)
    if not availability or sensor.api_sp not in availability:
        # Without availability we cannot know where SP stops; assume SP covers everything.
        return windows_for(sensor.api_sp, start, end)
    sp_min, sp_max = availability[sensor.api_sp]
    out = windows_for(sensor.api_sp, max(start, sp_min), min(end, sp_max)) if start <= sp_max else []
    nrt = availability.get(sensor.api_nrt)
    if end > sp_max and nrt:
        out += windows_for(sensor.api_nrt, max(start, sp_max + timedelta(days=1), nrt[0]), min(end, nrt[1]))
    return out


class Client:
    def __init__(self, map_key: str, session: requests.Session | None = None, log=None):
        self.key = map_key
        self.s = session or new_session()
        self.log = log or (lambda msg: print(msg, file=sys.stderr))

    def redact(self, text: str) -> str:
        return text.replace(self.key, "<MAP_KEY>") if self.key else text

    def status(self) -> dict:
        r = self.s.get(f"{BASE}/mapserver/mapkey_status/", params={"MAP_KEY": self.key}, timeout=60)
        if r.status_code == 403:
            raise RateLimited(r.text.strip()[:200])
        r.raise_for_status()
        return r.json()

    def availability(self) -> dict[str, tuple[date, date]]:
        text = self._get_text(f"{BASE}/api/data_availability/csv/{self.key}/ALL")
        out = {}
        for row in csv.DictReader(io.StringIO(text)):
            try:
                out[row["data_id"]] = (date.fromisoformat(row["min_date"]), date.fromisoformat(row["max_date"]))
            except (KeyError, ValueError):
                continue
        return out

    def area_url(self, w: Window, bbox: str) -> str:
        return f"{BASE}/api/area/csv/{self.key}/{w.source}/{bbox}/{w.days}/{w.start.isoformat()}"

    def _get_text(self, url: str, attempts: int = 6) -> str:
        delay = 5.0
        for attempt in range(1, attempts + 1):
            try:
                r = self.s.get(url, timeout=300)
            except (requests.ConnectionError, requests.Timeout) as e:
                err = f"{type(e).__name__}"
            else:
                body = r.text
                if re.search(r"exceed", body[:300], re.I) or r.status_code == 429:
                    raise RateLimited(self.redact(body.strip()[:200]) or "HTTP 429")
                if r.status_code not in RETRY_STATUS:
                    if body.startswith("Invalid") or r.status_code >= 400:
                        raise ApiError(self.redact(f"HTTP {r.status_code}: {body.strip()[:200]}"))
                    return body
                err = f"HTTP {r.status_code}"
            if attempt == attempts:
                raise ApiError(self.redact(f"{url}: giving up after {attempts} attempts ({err})"))
            self.log(f"  retry {attempt}/{attempts - 1} in {delay:.0f}s ({err})")
            time.sleep(delay)
            delay = min(delay * 2, 300)
        raise AssertionError("unreachable")

    def wait_for_budget(self, reserve: int) -> tuple[int, int]:
        """Block until the key has at least `reserve` transactions left; return (used, limit)."""
        deadline = time.monotonic() + 2 * LIMIT_WINDOW_S
        while True:
            try:
                st = self.status()
                limit = int(st.get("transaction_limit", DEFAULT_LIMIT))
                used = int(st.get("current_transactions", 0))
                if used + min(reserve, limit) <= limit:
                    return used, limit
                self.log(f"  key budget {used}/{limit} used; pausing 60s")
            except RateLimited:
                self.log("  key over limit; pausing 60s")
            if time.monotonic() > deadline:
                raise RateLimited("transaction budget did not recover after 20 minutes")
            time.sleep(60)

    def fetch_window(self, w: Window, bbox: str) -> str:
        while True:
            try:
                return self._get_text(self.area_url(w, bbox))
            except RateLimited as e:
                self.log(f"  rate limited ({e}); waiting for the 10-minute window to reset")
                self.wait_for_budget(reserve=call_cost(w, bbox))


def cache_path(cache_dir: Path, bbox: str, w: Window) -> Path:
    tag = bbox.replace(",", "_").replace("-", "m")
    return cache_dir / w.source / tag / f"{w.start.isoformat()}_{w.days}d.csv"


def download(client: Client, windows: list[Window], bbox: str, cache_dir: Path,
             delay: float = 0.5, check_every: int = 25, reserve: int = 500) -> Iterator[tuple[Window, Path, int, bool]]:
    """Fetch each window into its own cache file (skipping ones already cached).

    Yields (window, path, rows, cached). Re-running resumes where it stopped. The key budget is
    re-checked every `check_every` calls, or sooner when the predicted spend would cut into `reserve`.
    """
    fetched = 0
    used: int | None = None
    limit = DEFAULT_LIMIT
    for w in windows:
        path = cache_path(cache_dir, bbox, w)
        if path.exists():
            yield w, path, _count_rows(path), True
            continue
        cost = call_cost(w, bbox)
        if used is None or fetched % check_every == 0 or used + cost + reserve > limit:
            used, limit = client.wait_for_budget(reserve + cost)
        text = client.fetch_window(w, bbox)
        used += cost
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(text)
        tmp.replace(path)
        fetched += 1
        yield w, path, max(0, len(text.splitlines()) - 1), False
        time.sleep(delay)


def _count_rows(path: Path) -> int:
    with path.open() as fh:
        return max(0, sum(1 for _ in fh) - 1)


def merge(paths: list[Path], dest: Path, add_source: bool = True) -> int:
    """Concatenate cached chunks into one CSV; optionally tag rows with their API source.

    SP chunks carry a `type` column that NRT chunks lack, so the output header is the union of all
    chunk headers (first-seen order) and missing fields are left empty.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    header: list[str] = []
    for p in paths:
        with p.open(newline="") as fh:
            for c in next(csv.reader(fh), []):
                if c not in header:
                    header.append(c)
    rows = 0
    with dest.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=header + (["source"] if add_source else []), restval="")
        writer.writeheader()
        for p in paths:
            source = p.parent.parent.name
            with p.open(newline="") as fh:
                for row in csv.DictReader(fh):
                    if add_source:
                        row["source"] = source
                    writer.writerow(row)
                    rows += 1
    return rows


def estimate(windows: list[Window]) -> dict[str, int]:
    out: dict[str, int] = {}
    for w in windows:
        out[w.source] = out.get(w.source, 0) + 1
    return out


def estimate_transactions(windows: list[Window], bbox: str) -> int:
    return sum(call_cost(w, bbox) for w in windows)
