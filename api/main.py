"""EmberLedger API. Logic lives in api/core.py; this file only parses requests.

Run:  uvicorn api.main:app --reload --port 8000
Call: GET /calendar?bbox=8.0,35.0,11.0,37.5          (minLon,minLat,maxLon,maxLat)
      GET /calendar?area=bizerte                     (ids from GET /areas)
"""
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api import core

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

log = logging.getLogger("uvicorn.error")
app = FastAPI(title="EmberLedger")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])
# Static files: the demo page (web/demo/) and the offline backups (web/backup/).
app.mount("/web", StaticFiles(directory=WEB_DIR, html=True), name="web")

# Teammate routers (api/passport.py, api/weather.py): optional, so the app starts without them.
try:
    from api.passport import router as passport_router
    app.include_router(passport_router)
except ImportError as e:
    log.warning("passport router not loaded: %s", e)
try:
    from api.weather import router as weather_router
    app.include_router(weather_router)
except ImportError as e:
    log.warning("weather router not loaded: %s", e)


@app.exception_handler(FileNotFoundError)
def missing_data(request, exc):
    # open() sets exc.filename; pyarrow (read_parquet) puts the path in the message instead.
    return JSONResponse(status_code=503, content={"detail": f"data file not found: {exc.filename or exc}"})


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/web/demo/")  # a browser on the base URL gets the demo (API docs: /docs)


@app.get("/health")
def health():
    return {"status": "ok", "data_file": core.data_path(), "rows": len(core.get_data())}


@app.get("/areas")
def areas():
    return {"areas": core.get_areas()}


@app.get("/validation")
def validation():
    return core.get_validation(include_series=True)  # "series" only if the file contains one


@app.get("/calendar")
def calendar(bbox: str | None = None, area: str | None = None, start: int = 2001, end: int = 2025):
    if (bbox is None) == (area is None):
        raise HTTPException(400, "give exactly one of bbox=minLon,minLat,maxLon,maxLat or area=<id>")
    if area is not None:
        found = core.get_area(area)
        if found is None:
            raise HTTPException(400, f"unknown area '{area}', see GET /areas")
        return core.calendar_for_bbox(tuple(found["bbox"]), start, end)
    try:
        x0, y0, x1, y1 = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat")
    return core.calendar_for_bbox((x0, y0, x1, y1), start, end)
