"""FIRMS MAP_KEY web services: key status, data availability and resumable area downloads."""

from __future__ import annotations

import csv
import io
import math
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import requests

from .portal import BASE, new_session
from .sensors import Sensor, get_sensor

MAP_KEY_ENV_VARS = ("FIRMS_MAP_KEY_1", "FIRMS_MAP_KEY")
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
_env_file_cache: tuple[Path, dict[str, str]] | None = None
MAX_DAYS = 5
RETRY_STATUS = {429, 500, 502, 503, 504}
LIMIT_WINDOW_S = 600
DEFAULT_LIMIT = 5000
# Measured 2026-11-14: a call costs 2 transactions per day per 60x60-degree tile of the bbox extent
# (ceil on each side), independent of how many rows come back. World = 6x3 tiles = 36/day.
TILE_DEG = 60.0
COST_PER_TILE_DAY = 2


def _env_file_values() -> dict[str, str]:
    """MAP_KEY entries from the project .env. Does not log values."""
    global _env_file_cache
    if _env_file_cache is not None and _env_file_cache[0] == ENV_FILE:
        return _env_file_cache[1]
    found: dict[str, str] = {}
    try:
        text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.is_file() else ""
    except OSError:
        text = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in MAP_KEY_ENV_VARS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value and name not in found:
            found[name] = value
    _env_file_cache = (ENV_FILE, found)
    return found


def resolve_map_key(cli_value: str | None = None) -> str | None:
    """CLI value, then each MAP_KEY env var, then the same name in the project .env."""
    if cli_value:
        return cli_value
    file_values = None
    for name in MAP_KEY_ENV_VARS:
        current = os.environ.get(name)
        if current:
            return current
        if file_values is None:
            file_values = _env_file_values()
        if file_values.get(name):
            return file_values[name]
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


_WORLD_BBOX = (
    "viewport queries take west,south,east,north; "
    "world files stay on the public /data/country archive"
)


def parse_viewport_bbox(
    bbox: tuple[float, float, float, float] | str,
) -> tuple[float, float, float, float]:
    """One map viewport as west, south, east, north. Rejects a world pull."""
    if isinstance(bbox, str):
        if bbox.strip().lower() == "world":
            raise ValueError(_WORLD_BBOX)
        try:
            parts = tuple(float(v) for v in bbox.split(","))
        except ValueError:
            raise ValueError("bbox must be west,south,east,north") from None
    else:
        try:
            parts = tuple(float(v) for v in bbox)
        except (TypeError, ValueError):
            raise ValueError("bbox must be west,south,east,north") from None
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north")
    west, south, east, north = parts
    if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
        raise ValueError(
            "bbox must have west < east in [-180, 180] and south < north in [-90, 90]"
        )
    if east - west >= 360.0 - 1e-6 and north - south >= 180.0 - 1e-6:
        raise ValueError(_WORLD_BBOX)
    return west, south, east, north


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
        """Transaction usage. Error text is redacted and does not include the request URL."""
        try:
            r = self.s.get(
                f"{BASE}/mapserver/mapkey_status/",
                params={"MAP_KEY": self.key},
                timeout=60,
            )
        except requests.RequestException as e:
            raise ApiError(self.redact(str(e))) from None
        body = r.text or ""
        if r.status_code == 403:
            raise RateLimited(self.redact(body.strip()[:200]))
        if r.status_code >= 400:
            raise ApiError(self.redact(f"HTTP {r.status_code}: {body.strip()[:200]}"))
        try:
            return r.json()
        except ValueError:
            raise ApiError(self.redact(f"HTTP {r.status_code}: {body.strip()[:200]}")) from None

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


def _merge_texts(texts: list[str], windows: list[Window], bbox: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = []
        for w, text in zip(windows, texts):
            path = cache_path(root, bbox, w)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            paths.append(path)
        dest = root / "viewport.csv"
        merge(paths, dest)
        return dest.read_text(encoding="utf-8")


def query_viewport(
    bbox: tuple[float, float, float, float] | str,
    sensor: Sensor | str,
    start: date,
    end: date,
    *,
    mode: str = "auto",
    map_key: str | None = None,
    client: Client | None = None,
    availability: dict[str, tuple[date, date]] | None = None,
) -> str:
    """Keyed FIRMS ``/api/area`` query for one map viewport.

    This is the call a later interactive map makes. The MAP_KEY comes from
    ``resolve_map_key()`` (``FIRMS_MAP_KEY_1``, then ``FIRMS_MAP_KEY``, then the
    project ``.env``) unless ``client`` is already built. The key is not logged.
    A world extent is rejected; bulk history stays on the public country archive.
    HTTP goes through ``Client.fetch_window`` (5-day windows). With ``mode="auto"``
    the client also reads data availability so days after standard processing use NRT.
    """
    box = parse_viewport_bbox(bbox)
    if isinstance(sensor, str):
        sensor = get_sensor(sensor)
    if mode not in {"auto", "sp", "nrt"}:
        raise ValueError("mode must be auto, sp, or nrt")
    if start > end:
        raise ValueError("start must be on or before end")
    if client is None:
        key = resolve_map_key(map_key)
        if not key:
            raise ApiError(
                "no MAP_KEY: set FIRMS_MAP_KEY_1 (or FIRMS_MAP_KEY) in the environment "
                "or in the project .env"
            )
        client = Client(key)
    if mode == "auto" and availability is None:
        availability = client.availability()
    windows = plan_sensor(sensor, start, end, mode, availability)
    if not windows:
        raise ApiError(f"no {sensor.key} coverage for {start.isoformat()}..{end.isoformat()}")
    bbox_s = bbox_arg(box)
    return _merge_texts([client.fetch_window(w, bbox_s) for w in windows], windows, bbox_s)
