"""EmberLedger API. Logic lives in api/core.py; this file only parses requests.

Run:  uvicorn api.main:app --reload --port 8000
Call: GET /calendar?bbox=8.0,35.0,11.0,37.5          (minLon,minLat,maxLon,maxLat)
      GET /calendar?area=bizerte                     (ids from GET /areas)
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api import core

app = FastAPI(title="EmberLedger")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.get("/areas")
def areas():
    return {"areas": core.get_areas()}


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
