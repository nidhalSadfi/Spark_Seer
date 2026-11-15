"""Community fire reports: GET/POST/PATCH/DELETE /reports (spec §10, amendment 2026-11-15).

Writes go only to `community_reports` (I1 amendment); GET runs in a READ ONLY transaction.
PATCH and DELETE need the X-Admin-Token header when REPORTS_ADMIN_TOKEN is set (401 without it,
403 with a wrong one, compared in constant time); unset (local development), they are open.
POST is always open. Ids are server-generated UUIDs.
"""
from __future__ import annotations

import hmac
import math
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api import database
from api.database import iso

router = APIRouter(prefix="/reports", tags=["reports"])

NOTE_MAX = 280
LIST_MAX = 500
COLUMNS = "id, lat, lon, observed, note, status, created_at, reviewed_at"

Lat = Annotated[float, Field(strict=True, ge=-90, le=90, allow_inf_nan=False)]
Lon = Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # a client cannot set id, status or times
    lat: Lat
    lon: Lon
    observed: Literal["smoke", "flames", "both", "unsure"]
    note: str | None = ""

    @field_validator("note")
    @classmethod
    def trimmed_note(cls, v: str | None) -> str:
        v = (v or "").strip()
        if len(v) > NOTE_MAX:
            raise ValueError(f"note must be at most {NOTE_MAX} characters after trimming (got {len(v)})")
        return v


class StatusIn(BaseModel):
    status: Literal["pending", "confirmed", "false"]


def report_json(r: dict) -> dict:
    return {"id": str(r["id"]), "lat": r["lat"], "lon": r["lon"], "observed": r["observed"], "note": r["note"],
            "status": r["status"], "createdAt": iso(r["created_at"]), "reviewedAt": iso(r["reviewed_at"])}


def require_admin(x_admin_token: str | None = Header(None)) -> None:
    """X-Admin-Token check for PATCH/DELETE; a no-op while REPORTS_ADMIN_TOKEN is unset."""
    expected = database.settings().reports_admin_token
    if expected is None:
        return
    if not x_admin_token:
        raise HTTPException(401, "reviewer token required: send it in the X-Admin-Token header")
    if not hmac.compare_digest(x_admin_token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(403, "reviewer token not accepted")


def _report_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, f"report {raw!r} not found") from None


@router.get("")
def list_reports():
    with database.read() as c:
        found = database.rows(c, f"SELECT {COLUMNS} FROM community_reports ORDER BY created_at DESC, id "
                                 "LIMIT :n", n=LIST_MAX)
    return {"reports": [report_json(r) for r in found]}


@router.post("", status_code=201)
def create_report(body: ReportIn):
    if not (math.isfinite(body.lat) and math.isfinite(body.lon)):  # belt and braces: Field rejects inf/nan
        raise HTTPException(422, "lat and lon must be finite numbers")
    with database.write() as c:
        created = database.one(c, f"""
            INSERT INTO community_reports (id, lat, lon, observed, note, status, created_at, reviewed_at)
            VALUES (:id, :lat, :lon, :observed, :note, 'pending', :now, NULL) RETURNING {COLUMNS}""",
            id=uuid.uuid4(), lat=float(body.lat), lon=float(body.lon), observed=body.observed, note=body.note,
            now=datetime.now(timezone.utc))
    return report_json(created)


@router.patch("/{report_id}", dependencies=[Depends(require_admin)])
def set_status(report_id: str, body: StatusIn):
    rid = _report_id(report_id)
    reviewed = None if body.status == "pending" else datetime.now(timezone.utc)
    with database.write() as c:
        updated = database.one(c, f"""
            UPDATE community_reports SET status = :status, reviewed_at = :reviewed WHERE id = :id
            RETURNING {COLUMNS}""", id=rid, status=body.status, reviewed=reviewed)
    if updated is None:
        raise HTTPException(404, f"report {report_id!r} not found")
    return report_json(updated)


@router.delete("/{report_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_report(report_id: str):
    rid = _report_id(report_id)
    with database.write() as c:
        deleted = database.scalar(c, "DELETE FROM community_reports WHERE id = :id RETURNING id", id=rid)
    if deleted is None:
        raise HTTPException(404, f"report {report_id!r} not found")
    return Response(status_code=204)
