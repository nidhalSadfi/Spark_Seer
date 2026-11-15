"""EmberLedger API. Logic lives in api/core.py; this file only parses requests.

Run:  uvicorn api.main:app --reload --port 8000
Call: GET /calendar?bbox=8.0,35.0,11.0,37.5          (minLon,minLat,maxLon,maxLat)
      GET /calendar?area=bizerte                     (ids from GET /areas)

With DATABASE_URL set, /calendar reads the approved cell_month table (api/core.py) and the
database endpoints work: /processing/* (api/processing_status.py), /fires/live (api/fires.py),
/reports (api/reports.py). Without a usable database those answer 503 and the calendar endpoints
keep serving the parquet file. This process never imports worker, crewai or an LLM client (I1).
"""
import importlib.util
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api import core, database
from api.fires import router as fires_router
from api.processing_status import router as processing_router
from api.reports import router as reports_router

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
METEO_DIR = ROOT / "meteomatics-weather"

log = logging.getLogger("uvicorn.error")
app = FastAPI(title="EmberLedger")
# The reports page sends PATCH/DELETE with an X-Admin-Token header ("*" headers covers it).
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PATCH", "DELETE"],
                   allow_headers=["*"])
# Static files: the demo page (web/demo/) and the offline backups (web/backup/).
app.mount("/web", StaticFiles(directory=WEB_DIR, html=True), name="web")

app.include_router(processing_router)
app.include_router(fires_router)
app.include_router(reports_router)

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


def load_meteomatics():
    """The Meteomatics router (meteomatics-weather/, teammate), or None if it cannot load.

    Its modules import `pipeline.grid`, `pipeline.weather`... from their own folder. Neither
    pipeline/ folder has an __init__.py, so with that folder on sys.path Python merges both into
    one `pipeline` namespace (no module names overlap).
    """
    try:
        if str(METEO_DIR) not in sys.path:
            sys.path.append(str(METEO_DIR))
        spec = importlib.util.spec_from_file_location(
            "meteomatics_router", METEO_DIR / "api" / "weather_router_example.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (ImportError, OSError) as e:
        log.warning("meteomatics router not loaded: %s", e)
        return None


# Mounted under /meteomatics: its own GET /weather would clash with api/weather.py's.
# Its fire layer reads NASA_FIRMS_CSV: default to our real export (scripts/export_fires.py).
os.environ.setdefault("NASA_FIRMS_CSV", str(ROOT / "data" / "fires.csv"))
meteomatics = load_meteomatics()
if meteomatics is not None:
    app.include_router(meteomatics.router, prefix="/meteomatics", tags=["meteomatics"])


@app.exception_handler(FileNotFoundError)
def missing_data(request, exc):
    # open() sets exc.filename; pyarrow (read_parquet) puts the path in the message instead.
    return JSONResponse(status_code=503, content={"detail": f"data file not found: {exc.filename or exc}"})


@app.exception_handler(database.DatabaseUnavailable)
def database_unavailable(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/web/demo/")  # a browser on the base URL gets the demo (API docs: /docs)


@app.get("/health")
def health():
    rows = len(core.get_data())
    return {"status": "ok", "data_file": core.data_path(), "rows": rows, **core.data_source()}


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
        box = core.parse_bbox(bbox)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return core.calendar_for_bbox(box, start, end)
