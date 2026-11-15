"""GET /processing/status, /processing/jobs, /processing/jobs/{id}, /processing/datasets (spec §10).

Read-only views of what the worker wrote (schedules, processing_jobs with its `decision` JSONB,
job_steps, llm_attempts, dataset_versions, calibration_versions), for the Processing Center
(spec §11). Every query runs in a READ ONLY transaction (api/database.py, I1). The JSON shapes
are documented in backend/README.md ("Processing endpoints").
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from api import database
from api.database import iso

router = APIRouter(prefix="/processing", tags=["processing"])

JOB_STATUSES = ("queued", "running", "succeeded", "no_change", "held", "rejected", "failed", "abandoned")
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_LIMIT = 200

JOB_COLUMNS = """j.id, j.kind, j.status, j.trigger, j.created_at, j.started_at, j.finished_at, j.fetched_count,
    j.new_count, j.modified_count, j.unchanged_count, j.pending_count, j.affected_count, j.detections_count,
    j.staged_count, j.dataset_version_id, j.calibration_version_id, j.ai_review, j.error,
    j.decision ->> 'decision' AS decision_value"""


def _duration(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def job_summary(r: dict) -> dict:
    """One processing_jobs row as a job list item."""
    return {
        "id": r["id"], "kind": r["kind"], "status": r["status"], "trigger": r["trigger"],
        "created_at": iso(r["created_at"]), "started_at": iso(r["started_at"]), "finished_at": iso(r["finished_at"]),
        "duration_s": _duration(r["started_at"], r["finished_at"]),
        "counts": {"fetched": r["fetched_count"], "new": r["new_count"], "modified": r["modified_count"],
                   "unchanged": r["unchanged_count"], "pending": r["pending_count"],
                   "affected": r["affected_count"], "detections": r["detections_count"],
                   "staged": r["staged_count"]},
        "decision": r["decision_value"], "ai_review": r["ai_review"],
        "dataset_version_id": r["dataset_version_id"], "calibration_version_id": r["calibration_version_id"],
        "error": r["error"],
    }


def _limit(limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise HTTPException(400, f"limit must be between 1 and {MAX_LIMIT}")
    if offset < 0:
        raise HTTPException(400, "offset must be >= 0")


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


# ----------------------------------------------------------------------------- status

def _dataset(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {"id": r["id"], "parent_id": r["parent_id"], "job_id": r["job_id"],
            "calibration_version_id": r["calibration_version_id"], "status": r["status"],
            "created_at": iso(r["created_at"]), "approved_at": iso(r["approved_at"]), "summary": r["summary"]}


def _calibration(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {"id": r["id"], "status": r["status"], "label": r["label"], "k_table_ref": r["k_table_ref"],
            "snpp_end": iso(r["snpp_end"]), "r_global": r["r_global"], "mask_version": r["mask_version"],
            "created_at": iso(r["created_at"]), "approved_at": iso(r["approved_at"])}


CALIBRATION_COLUMNS = "id, status, label, k_table_ref, snpp_end, r_global, mask_version, created_at, approved_at"
DATASET_COLUMNS = "id, parent_id, job_id, calibration_version_id, status, created_at, approved_at, summary"


def _last_error(c, kind: str | None = None) -> dict | None:
    r = database.one(c, f"""
        SELECT id, kind, status, error, finished_at FROM processing_jobs
        WHERE status IN ('failed', 'abandoned', 'rejected') {"AND kind = :kind" if kind else ""}
        ORDER BY coalesce(finished_at, created_at) DESC, id DESC LIMIT 1""", kind=kind)
    if r is None:
        return None
    return {"job_id": r["id"], "kind": r["kind"], "status": r["status"], "error": r["error"],
            "finished_at": iso(r["finished_at"])}


@router.get("/status")
def status():
    with database.read(isolation_level="REPEATABLE READ") as c:
        now = database.scalar(c, "SELECT now()")
        schedules = database.rows(c, """
            SELECT kind, cadence, interval_seconds, day_of_month, next_run_at, enabled, last_claimed_at, last_job_id,
                   updated_at
            FROM schedules ORDER BY kind""")
        kinds = []
        for s in schedules:
            active = database.one(c, f"""
                SELECT {JOB_COLUMNS}, j.lease_owner, j.lease_expires_at, j.heartbeat_at FROM processing_jobs AS j
                WHERE j.kind = :kind AND j.status IN ('queued', 'running') ORDER BY j.id DESC LIMIT 1""",
                kind=s["kind"])
            last = database.one(c, f"""
                SELECT {JOB_COLUMNS} FROM processing_jobs AS j
                WHERE j.kind = :kind AND j.status NOT IN ('queued', 'running')
                ORDER BY coalesce(j.finished_at, j.created_at) DESC, j.id DESC LIMIT 1""", kind=s["kind"])
            last_ok = database.one(c, """
                SELECT id, finished_at FROM processing_jobs
                WHERE kind = :kind AND status IN ('succeeded', 'no_change')
                ORDER BY coalesce(finished_at, created_at) DESC, id DESC LIMIT 1""", kind=s["kind"])
            kinds.append({
                "kind": s["kind"], "enabled": s["enabled"], "cadence": s["cadence"],
                "interval_seconds": s["interval_seconds"], "day_of_month": s["day_of_month"],
                "next_run_at": iso(s["next_run_at"]), "overdue": bool(s["enabled"] and s["next_run_at"] < now),
                "last_claimed_at": iso(s["last_claimed_at"]), "last_job_id": s["last_job_id"],
                "active_job": None if active is None else {
                    **job_summary(active), "lease_owner": active["lease_owner"],
                    "lease_expires_at": iso(active["lease_expires_at"]), "heartbeat_at": iso(active["heartbeat_at"])},
                "last_run": None if last is None else job_summary(last),
                "last_success": None if last_ok is None else {"job_id": last_ok["id"],
                                                              "finished_at": iso(last_ok["finished_at"])},
                "last_error": _last_error(c, s["kind"]),
            })
        dataset = database.one(c, f"SELECT {DATASET_COLUMNS} FROM dataset_versions WHERE status = 'approved'")
        calibration = database.one(c, f"SELECT {CALIBRATION_COLUMNS} FROM calibration_versions "
                                      "WHERE status = 'approved'")
        counts = database.one(c, """
            SELECT (SELECT count(*) FROM raw_detections) AS raw_detections,
                   (SELECT count(*) FROM raw_detections WHERE needs_processing) AS pending_detections,
                   (SELECT count(*) FROM cell_month) AS approved_cell_months""")
        last_error = _last_error(c)
    return {
        "generated_at": iso(now or datetime.now(timezone.utc)),
        "scheduler": {"kinds": kinds},
        "approved": {"dataset_version": _dataset(dataset), "calibration_version": _calibration(calibration)},
        "data": counts,
        "last_error": last_error,
    }


# ----------------------------------------------------------------------------- jobs

@router.get("/jobs")
def jobs(kind: str | None = Query(None, description="job kind, or several separated by commas"),
         status: str | None = Query(None, description="job status, or several separated by commas"),
         limit: int = 50, offset: int = 0):
    _limit(limit, offset)
    kinds, statuses = _split(kind), _split(status)
    bad = [k for k in kinds if not KIND_RE.match(k)]
    if bad:
        raise HTTPException(400, f"invalid kind {bad[0]!r}")
    bad = [s for s in statuses if s not in JOB_STATUSES]
    if bad:
        raise HTTPException(400, f"unknown status {bad[0]!r}; one of {', '.join(JOB_STATUSES)}")
    where = []
    if kinds:
        where.append("j.kind = ANY(:kinds)")
    if statuses:
        where.append("j.status = ANY(:statuses)")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with database.read(isolation_level="REPEATABLE READ") as c:
        total = database.scalar(c, f"SELECT count(*) FROM processing_jobs AS j {clause}",
                                kinds=kinds, statuses=statuses)
        found = database.rows(c, f"SELECT {JOB_COLUMNS} FROM processing_jobs AS j {clause} "
                                 "ORDER BY j.id DESC LIMIT :limit OFFSET :offset",
                              kinds=kinds, statuses=statuses, limit=limit, offset=offset)
    return {"jobs": [job_summary(r) for r in found], "total": total, "limit": limit, "offset": offset}


def _gates(decision: dict) -> dict:
    gates = decision.get("gates")
    return {"passed": decision.get("gates_passed"),
            "results": [{"name": g.get("name"), "passed": g.get("passed"), "detail": g.get("detail")}
                        for g in gates] if isinstance(gates, list) else []}


def _verdict(decision: dict) -> dict | None:
    v = decision.get("verdict")
    if not isinstance(v, dict):
        return None
    return {"decision": v.get("decision"), "reasons": v.get("reasons") or [], "flagged_keys": v.get("flagged_keys") or []}


@router.get("/jobs/{job_id}")
def job_detail(job_id: int):
    with database.read(isolation_level="REPEATABLE READ") as c:
        job = database.one(c, f"""
            SELECT {JOB_COLUMNS}, j.lease_owner, j.lease_expires_at, j.heartbeat_at, j.decision
            FROM processing_jobs AS j WHERE j.id = :id""", id=job_id)
        if job is None:
            raise HTTPException(404, f"job {job_id} not found")
        steps = database.rows(c, "SELECT id, name, status, started_at, finished_at, summary FROM job_steps "
                                 "WHERE job_id = :id ORDER BY started_at, id", id=job_id)
        attempts = database.rows(c, """
            SELECT id, step, attempt_no, model, started_at, latency_ms, outcome, error_class, error,
                   prompt_tokens, completion_tokens, total_tokens
            FROM llm_attempts WHERE job_id = :id ORDER BY step, attempt_no, id""", id=job_id)
        dataset = database.one(c, f"SELECT {DATASET_COLUMNS} FROM dataset_versions WHERE job_id = :id "
                                  "ORDER BY id DESC LIMIT 1", id=job_id)
    decision = job["decision"] if isinstance(job["decision"], dict) else {}
    review = decision.get("review") if isinstance(decision.get("review"), dict) else None
    return {
        **job_summary(job),
        "lease": {"owner": job["lease_owner"], "expires_at": iso(job["lease_expires_at"]),
                  "heartbeat_at": iso(job["heartbeat_at"])},
        "decision_basis": decision.get("basis"),
        "gates": _gates(decision),
        "soft_flags": decision.get("soft_flags") or [],
        "verdict": _verdict(decision),
        "review": None if review is None else {"model": review.get("model"), "attempts": review.get("attempts"),
                                               "reason": review.get("reason")},
        "steps": [{"id": s["id"], "name": s["name"], "status": s["status"], "started_at": iso(s["started_at"]),
                   "finished_at": iso(s["finished_at"]), "duration_s": _duration(s["started_at"], s["finished_at"]),
                   "summary": s["summary"]} for s in steps],
        "llm_attempts": [{"id": a["id"], "step": a["step"], "attempt_no": a["attempt_no"], "model": a["model"],
                          "started_at": iso(a["started_at"]), "latency_ms": a["latency_ms"], "outcome": a["outcome"],
                          "error_class": a["error_class"], "error": a["error"],
                          "tokens": {"prompt": a["prompt_tokens"], "completion": a["completion_tokens"],
                                     "total": a["total_tokens"]}} for a in attempts],
        "dataset_version": _dataset(dataset),
    }


# ----------------------------------------------------------------------------- datasets

@router.get("/datasets")
def datasets(limit: int = 50, offset: int = 0):
    _limit(limit, offset)
    with database.read(isolation_level="REPEATABLE READ") as c:
        total = database.scalar(c, "SELECT count(*) FROM dataset_versions")
        found = database.rows(c, f"""
            SELECT {", ".join("d." + col.strip() for col in DATASET_COLUMNS.split(","))},
                   (SELECT count(*) FROM cell_month AS m WHERE m.dataset_version_id = d.id) AS current_rows
            FROM dataset_versions AS d ORDER BY d.id DESC LIMIT :limit OFFSET :offset""", limit=limit, offset=offset)
        calibrations = database.rows(c, f"SELECT {CALIBRATION_COLUMNS} FROM calibration_versions ORDER BY id DESC")
    return {"datasets": [{**_dataset(r), "current_rows": r["current_rows"]} for r in found],
            "total": total, "limit": limit, "offset": offset,
            "calibrations": [_calibration(r) for r in calibrations]}
