"""Local page and JSON API. Bound to 127.0.0.1 by the CLI: it holds keys and triggers paid calls.

GET  /                      the page (query box, live steps, tickets)
POST /api/runs              {"query", "hours"} -> starts a run in the background
                            {"query", "replay_from", "replay_to"} -> replays a past window instead
GET  /api/events/stream     server-sent events from the events table (?after=<id>)
GET  /api/tickets           tickets with every source URL, publish time and fetch time
GET  /api/runs              recent runs
GET  /api/status            which keys the agents can see (booleans only) and which .env files exist
GET|POST|DELETE /api/schedules

Started by the backend (backend/api/incidents.py) alongside the API, or by hand: python -m incident_agents serve.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import config
from .pipeline import replay, run
from .scheduler import Scheduler
from .store import Store

PAGE = Path(__file__).with_name("static") / "index.html"


class RunIn(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    hours: int = Field(24, ge=1, le=72)
    replay_from: date | None = None  # both set: replay that past window instead of a live run
    replay_to: date | None = None


class ScheduleIn(BaseModel):  # schedules are live only; no replay fields
    query: str = Field(min_length=2, max_length=300)
    hours: int = Field(24, ge=1, le=72)
    interval_min: int = Field(60, ge=1, le=1440)


def create_app(store: Store | None = None, scheduler: bool = True, runner=run, replay_runner=replay) -> FastAPI:
    store = store or Store()
    sched = Scheduler(store, runner)
    app = FastAPI(title="Fire incident agents")
    # The dashboard (another origin) runs the Social Alerts page, including its agent console, so the
    # listed origins (localhost:5173/4173 by default, INCIDENT_CORS_ORIGINS to change) may read AND
    # start runs / edit schedules. Every other origin is refused by the browser. Runs spend paid
    # quotas, so keep this list to dashboards you trust; the server itself listens on 127.0.0.1 only.
    app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins(),
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])

    if scheduler:
        @app.on_event("startup")
        def _start():
            sched.start_thread()

        @app.on_event("shutdown")
        def _stop():
            sched.stop()

    @app.get("/", response_class=HTMLResponse)
    def page():
        return PAGE.read_text(encoding="utf-8")

    @app.post("/api/runs")
    def start_run(body: RunIn):
        is_replay = bool(body.replay_from or body.replay_to)
        if is_replay and not (body.replay_from and body.replay_to):
            raise HTTPException(422, "replay needs both replay_from and replay_to")
        if not sched.run_lock.acquire(blocking=False):
            raise HTTPException(409, "a run is already in progress")
        last = store.q("SELECT COALESCE(MAX(id), 0) AS m FROM events")[0]["m"]

        def work():
            try:
                if is_replay:
                    replay_runner(body.query, body.replay_from, body.replay_to, store)
                else:
                    runner(body.query, body.hours, store)
            finally:
                sched.run_lock.release()
        threading.Thread(target=work, daemon=True).start()
        return {"started": True, "events_after": last}

    @app.get("/api/status")
    def status():
        """Which keys the agents can see (booleans only, never a value) and which .env files exist."""
        return {"keys": config.key_status(), "env_files": config.env_files_found(), "model": config.GROQ_MODEL,
                "scheduler": scheduler}

    @app.get("/api/runs")
    def runs(limit: int = 20):
        rows = store.q("SELECT id, query, hours, schedule_id, started_at, finished_at, status, mode, summary "
                       "FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["summary"] = json.loads(r["summary"]) if r["summary"] else None
        return rows

    @app.get("/api/tickets")
    def tickets(limit: int = 100):
        return store.tickets_with_sources(limit)

    @app.get("/api/events/stream")
    async def stream(request: Request, after: int = 0):
        resume = request.headers.get("last-event-id", "")
        if resume.isdigit():  # browser reconnect: continue where it stopped
            after = int(resume)

        async def gen():
            last = after
            if last <= 0:  # first connect: replay only the latest 300 events
                last = max(0, store.q("SELECT COALESCE(MAX(id), 0) AS m FROM events")[0]["m"] - 300)
            idle = 0
            while idle < 3600:  # close after ~30 min without events; the page reconnects
                rows = store.events_after(last)
                for e in rows:
                    last = e["id"]
                    e["data"] = json.loads(e["data"]) if e["data"] else None
                    yield f"id: {e['id']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
                idle = 0 if rows else idle + 1
                if not rows:
                    yield ": keep-alive\n\n" if idle % 30 == 0 else ""
                await asyncio.sleep(0.5)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/schedules")
    def schedules():
        return store.q("SELECT * FROM schedules ORDER BY id")

    @app.post("/api/schedules")
    def add_schedule(body: ScheduleIn):
        interval = max(config.caps().MIN_SCHEDULE_MINUTES, body.interval_min)
        sid = store.add_schedule(body.query, body.hours, interval)
        return {"id": sid, "interval_min": interval}

    @app.delete("/api/schedules/{sid}")
    def delete_schedule(sid: int):
        store.x("DELETE FROM schedules WHERE id=?", (sid,))
        return {"deleted": sid}

    return app
