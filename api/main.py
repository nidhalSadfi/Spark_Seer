"""EmberLedger API. Logic lives in api/core.py; this file only parses requests.

Run:  uvicorn api.main:app --reload --port 8000
Call: GET /calendar?bbox=8.0,35.0,11.0,37.5          (minLon,minLat,maxLon,maxLat)
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api import core

app = FastAPI(title="EmberLedger")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.get("/calendar")
def calendar(bbox: str, start: int = 2001, end: int = 2025):
    try:
        x0, y0, x1, y1 = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat")
    return core.calendar_for_bbox((x0, y0, x1, y1), start, end)
