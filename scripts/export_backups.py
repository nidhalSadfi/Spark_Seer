"""Offline backups for the demo, in case the server dies.

Writes web/backup/<area_id>.json (= GET /calendar?area=<id>&start=2001&end=2025) for every area in
data/areas.json, and web/backup/validation.json (= GET /validation).

Run after the REAL data/harmonized.parquet exists (or set EMBER_DATA):
    python scripts/export_backups.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from api import core  # noqa: E402

START, END = 2001, 2025


def write(path: Path, obj) -> None:
    # Same encoding as FastAPI's JSONResponse, so a backup is byte-identical to the live response.
    path.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    print("wrote", path)


def main(out_dir: Path) -> None:
    # Build everything first, so a missing data file never leaves a half-written backup folder.
    files = {f"{a['id']}.json": core.calendar_for_bbox(tuple(a["bbox"]), START, END)
             for a in core.get_areas()}
    files["validation.json"] = core.get_validation(include_series=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, obj in files.items():
        write(out_dir / name, obj)


if __name__ == "__main__":
    os.environ.setdefault("EMBER_DATA", str(ROOT / "data" / "harmonized.parquet"))
    if not os.path.exists(core.data_path()):
        sys.exit(f"No data at {core.data_path()}: run pipeline/calibrate.py first (or set EMBER_DATA).")
    main(ROOT / "web" / "backup")
