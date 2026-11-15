"""Source connectors. Plain code: they fetch, record URLs and times, and never call a model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


@dataclass
class Record:
    """One item from one source, before judging."""

    source: str                 # bluesky | x | gdelt | kanari_signal
    kind: str                   # post | news | verified_signal
    record_id: str
    url: str
    text: str
    published_at: str | None    # UTC ISO, as the source reports it (self-reported for social posts)
    fetched_at: str             # UTC ISO, when we fetched it
    author: str | None = None
    lang: str | None = None
    lat: float | None = None
    lon: float | None = None
    place: str | None = None
    country: str | None = None
    meta: dict = field(default_factory=dict)


class SourceError(RuntimeError):
    pass


def utc_iso(value) -> str | None:
    """Normalise ISO strings, RFC-2822 dates ('Sat Nov 14 22:12:55 +0000 2026'), GDELT
    '20261114T221500Z' and unix seconds to 'YYYY-MM-DDTHH:MM:SSZ'."""
    if value in (None, ""):
        return None
    dt = None
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), timezone.utc)
    else:
        s = str(value).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                try:
                    dt = parsedate_to_datetime(s)
                except (TypeError, ValueError):
                    return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
