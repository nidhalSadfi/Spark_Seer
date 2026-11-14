"""Evidence Passport: POST /investigate.

The passport is built deterministically from our own data (calendar_for_bbox,
get_validation). Optional AI wording is added only if ANTHROPIC_API_KEY is set,
and only kept if every number it contains appears in the passport.
"""
from __future__ import annotations

import json
import os
import re
import statistics
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

try:  # api/core.py is owned by a teammate; it may not be merged yet.
    from api.core import calendar_for_bbox, get_validation
except ImportError:  # pragma: no cover - only hit before core.py lands
    def calendar_for_bbox(*_args, **_kwargs):
        raise HTTPException(503, "Calendar backend (api/core.py) is not available yet.")

    def get_validation():
        raise HTTPException(503, "Validation backend (api/core.py) is not available yet.")


router = APIRouter()

AREAS_PATH = Path(__file__).resolve().parent.parent / "data" / "areas.json"
BASELINE_START_YEAR = 2001
BASELINE_END_YEAR = 2025
VIIRS_START_YEAR = 2012
# core.calendar_for_bbox takes int years (it filters H.year.between(start, end)), not "YYYY-MM" strings.
CAL_START = BASELINE_START_YEAR
CAL_END = BASELINE_END_YEAR

DEFAULT_LLM_MODEL = "claude-haiku-5-5"
LLM_TIMEOUT_S = 15.0

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

SOURCES = [
    "NASA FIRMS MODIS Collection 6.x active fire / hotspot detections",
    "NASA FIRMS VIIRS 375 m (Suomi NPP) active fire / hotspot detections",
]

WARN_HOTSPOT = ("A hotspot is a satellite thermal detection, not one wildfire. "
                "No detection does not mean no fire (clouds, overpass timing, small fires).")
WARN_PRE2012 = ("Values before 2012 are MODIS detections converted to VIIRS terms. They are a "
                "lower bound: small fires that MODIS missed cannot be recovered.")
WARN_LOW_CONF = "Not enough data to interpret this month."
WARN_LAT = ("Only detections north of 34°N are used, to exclude gas-flare hotspots "
            "in the south.")

CONFIDENCE_TEMPLATES = {
    "low_confidence": "Not enough data to interpret: too few detections or cells support this month.",
    "high": "This month is unusually active compared with the same month in other years.",
    "low": "This month is unusually quiet compared with the same month in other years.",
    "normal": "This month is within the usual range for the same month in other years.",
}
CONFIDENCE_FALLBACK = "This month is flagged '{flag}' relative to the same month in other years."


# ----------------------------------------------------------------------------- input

class InvestigateRequest(BaseModel):
    bbox: Optional[list[float]] = Field(None, description="[minLon, minLat, maxLon, maxLat]")
    area: Optional[str] = None
    year: int
    month: int


def load_areas() -> list[dict]:
    try:
        return json.loads(AREAS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(503, "data/areas.json not found.")


def find_area(area_id: str) -> dict:
    for a in load_areas():
        if a.get("id") == area_id:
            return a
    raise HTTPException(404, f"Unknown area '{area_id}'.")


def validate_bbox(bbox: list[float]) -> list[float]:
    if len(bbox) != 4:
        raise HTTPException(422, "bbox must be [minLon, minLat, maxLon, maxLat].")
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise HTTPException(422, "bbox must satisfy minLon < maxLon and minLat < maxLat.")
    return list(bbox)


def resolve_area(req: InvestigateRequest) -> dict:
    if req.area:
        a = find_area(req.area)
        return {"id": a["id"], "name": a.get("name", a["id"]), "bbox": validate_bbox(a["bbox"])}
    if req.bbox:
        return {"id": None, "name": "Custom box", "bbox": validate_bbox(req.bbox)}
    raise HTTPException(422, "Provide either 'bbox' or 'area'.")


# ----------------------------------------------------------------------------- passport

def _r(x: Any, nd: int = 1) -> Any:
    return round(x, nd) if isinstance(x, (int, float)) and not isinstance(x, bool) else x


def _fmt(x: Any) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.1f}"
    return str(x)


def median_same_month(months: list[dict], month: int) -> Optional[float]:
    vals = [m["harmonized"] for m in months
            if m.get("month") == month and m.get("harmonized") is not None]
    return round(statistics.median(vals), 1) if vals else None


def build_passport(area: dict, year: int, month: int, calendar: dict, validation: dict) -> dict:
    months = calendar.get("months", [])
    meta = calendar.get("meta", {})
    row = next((m for m in months if m.get("year") == year and m.get("month") == month), None)
    if row is None:
        raise HTTPException(404, f"No calendar data for {year}-{month:02d} in this area.")

    flag = row.get("flag")
    pre2012 = year < VIIRS_START_YEAR
    month_name = MONTH_NAMES[month - 1]

    obs = {
        "modis_raw": row.get("modis_raw"),
        "viirs_raw": row.get("viirs_raw"),
        "harmonized": _r(row.get("harmonized")),
        "source": "MODIS*k" if pre2012 else "VIIRS",
    }
    if pre2012 and validation.get("k_global") is not None:
        obs["k_global"] = validation["k_global"]
    for key in ("low", "high"):
        if row.get(key) is not None:
            obs[key] = _r(row[key])

    median = median_same_month(months, month)
    seasonal = {
        "median_same_month": median,
        "median_years": [BASELINE_START_YEAR, BASELINE_END_YEAR],
        "z": _r(row.get("z"), 2),
        "flag": flag,
        "critical_months": meta.get("critical_months", []),
    }

    warnings = [WARN_HOTSPOT, WARN_LAT]
    if pre2012:
        warnings.insert(0, WARN_PRE2012)
    if flag == "low_confidence":
        warnings.insert(0, WARN_LOW_CONF)

    confidence = CONFIDENCE_TEMPLATES.get(flag) or CONFIDENCE_FALLBACK.format(flag=flag)

    passport = {
        "question": f"How unusual was satellite hotspot activity in {area['name']} in {month_name} {year}?",
        "area": area,
        "period": {"year": year, "month": month, "label": f"{month_name} {year}"},
        "observations": obs,
        "seasonal": seasonal,
        "data_support": {"support": row.get("support"), "cells": meta.get("cells")},
        "model_quality": validation,
        "sources": SOURCES,
        "quality_warnings": warnings,
        "confidence_statement": confidence,
    }
    passport["summary_template"] = build_summary(passport)
    passport.update({"ai_text": None, "ai_checked": False, "ai_reason": None})
    return passport


def build_summary(p: dict) -> str:
    obs, sea = p["observations"], p["seasonal"]
    label = p["period"]["label"]
    month_name = label.split()[0]
    if sea["flag"] == "low_confidence":
        return (f"In {label}, {p['area']['name']} had {_fmt(obs['harmonized'])} harmonized hotspot "
                f"detections. Not enough data to interpret this month.")
    s = (f"In {label}, {p['area']['name']} had {_fmt(obs['harmonized'])} harmonized hotspot "
         f"detections (VIIRS terms). The median for {month_name} over "
         f"{BASELINE_START_YEAR}-{BASELINE_END_YEAR} is {_fmt(sea['median_same_month'])}, "
         f"and the seasonal z-score is {_fmt(sea['z'])}.")
    if obs["source"] == "MODIS*k":
        s += " This pre-2012 value is converted from MODIS and is a lower bound."
    return s


# ----------------------------------------------------------------------------- AI wording

LLM_SYSTEM = """You rewrite a fire-hotspot "evidence passport" (JSON) into plain language.
Rules:
- At most 3 sentences.
- Use only numbers that appear in the JSON, quoted exactly as given. Do not compute new numbers.
- A hotspot is a satellite thermal detection, not one wildfire.
- Never mention causes (weather, arson, politics, people), never give a probability of fire
  or an official danger level.
- If seasonal.flag is "low_confidence", say there is "not enough data" to interpret the month.
- If observations.source is "MODIS*k", say the value is a lower bound.
Output only the sentences."""

NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numbers_in_text(text: str) -> list[float]:
    out = []
    for tok in NUM_RE.findall(text):
        tok = tok.replace(",", "")
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def allowed_numbers(obj: Any) -> set[float]:
    """Every number in the passport (values and numbers inside strings), with 0/1-dp roundings."""
    raw: list[float] = []

    def walk(o: Any) -> None:
        if isinstance(o, bool) or o is None:
            return
        if isinstance(o, (int, float)):
            raw.append(float(o))
        elif isinstance(o, str):
            raw.extend(_numbers_in_text(o))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    allowed = set()
    for v in raw:
        v = abs(v)
        allowed.add(v)
        for nd in (0, 1):  # accept both banker's and half-up rounding
            allowed.add(float(round(v, nd)))
            q = Decimal(1).scaleb(-nd)
            allowed.add(float(Decimal(repr(v)).quantize(q, rounding=ROUND_HALF_UP)))
    return allowed


def check_numbers(text: str, passport: dict) -> tuple[bool, list[float]]:
    allowed = allowed_numbers(passport)
    bad = [x for x in _numbers_in_text(text)
           if not any(abs(abs(x) - a) < 1e-9 for a in allowed)]
    return (not bad, bad)


def _call_llm(system: str, user: str) -> str:
    """One Messages API call. Key comes from the environment only (never logged)."""
    import anthropic

    client = anthropic.Anthropic()
    resp = client.with_options(timeout=LLM_TIMEOUT_S, max_retries=0).messages.create(
        model=os.environ.get("EMBER_LLM_MODEL", DEFAULT_LLM_MODEL),
        max_tokens=2000,
        output_config={"effort": "low"},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model refused")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("empty response")
    return text


def add_ai_wording(passport: dict) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        passport["ai_reason"] = "AI wording disabled (ANTHROPIC_API_KEY not set)."
        return passport
    source = {k: v for k, v in passport.items() if not k.startswith("ai_")}
    try:
        text = _call_llm(LLM_SYSTEM, json.dumps(source, ensure_ascii=False))
    except Exception as e:  # timeout, network, API error, refusal: never fail the passport
        passport.update(ai_text=None, ai_checked=False,
                        ai_reason=f"AI call failed: {type(e).__name__}")
        return passport
    ok, bad = check_numbers(text, source)
    if not ok:
        passport.update(ai_text=None, ai_checked=False,
                        ai_reason=f"AI text rejected: numbers not in passport {bad}")
        return passport
    passport.update(ai_text=text, ai_checked=True, ai_reason=None)
    return passport


# ----------------------------------------------------------------------------- endpoint

@router.post("/investigate")
def investigate(req: InvestigateRequest) -> dict:
    if not BASELINE_START_YEAR <= req.year <= BASELINE_END_YEAR:
        raise HTTPException(422, f"year must be in {BASELINE_START_YEAR}-{BASELINE_END_YEAR}.")
    if not 1 <= req.month <= 12:
        raise HTTPException(422, "month must be 1-12.")
    area = resolve_area(req)
    calendar = calendar_for_bbox(area["bbox"], CAL_START, CAL_END)
    passport = build_passport(area, req.year, req.month, calendar, get_validation())
    return add_ai_wording(passport)
