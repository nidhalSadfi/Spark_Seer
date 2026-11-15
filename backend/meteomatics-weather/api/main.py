"""Demo application; serves only the built frontend, never the repository root."""
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.weather_router_example import router

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
app = FastAPI(title="EmberGuard weather context", version="1.0.0")
app.include_router(router)


@app.middleware("http")
async def hide_private_paths(request, call_next):
    if any(part.startswith(".") for part in request.url.path.split("/") if part):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"status": "ok"}


if (ROOT / "web/dist").is_dir():
    app.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True), name="web")
