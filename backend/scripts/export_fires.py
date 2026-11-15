"""Real FIRMS detections for the Meteomatics map's fire layer (GET /meteomatics/fires/calendar).

    python scripts/export_fires.py [raw_dir] [out]   # defaults: data/raw data/fires.csv

Reads the VIIRS S-NPP yearly CSVs that the FIRMS downloader wrote under raw_dir and keeps the
detections inside the Meteomatics grid (meteomatics-weather/pipeline/grid.py: 35-37.5N, 8-11.5E),
with the same filters as the calendar: type 0 (vegetation) and confidence nominal or high.
The columns are the ones meteomatics-weather/pipeline/fires.py reads.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GRID = {"lat": (35.0, 37.5), "lon": (8.0, 11.5)}  # half-open, like the grid cells
COLUMNS = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence", "frp"]


def main(raw_dir: Path, out: Path) -> int:
    files = sorted(raw_dir.rglob("viirs-snpp_*.csv"))
    if not files:
        sys.exit(f"no viirs-snpp_*.csv under {raw_dir}: run the FIRMS download first (README)")
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    (lat0, lat1), (lon0, lon1) = GRID["lat"], GRID["lon"]
    keep = (df.latitude.between(lat0, lat1, inclusive="left")
            & df.longitude.between(lon0, lon1, inclusive="left")
            & (df.type == 0) & df.confidence.astype(str).str.lower().isin(["n", "h"]))
    kept = df.loc[keep, COLUMNS].sort_values(["acq_date", "acq_time"])
    kept.to_csv(out, index=False)
    print(f"wrote {out}  rows={len(kept)}  from {len(files)} files")
    return len(kept)


if __name__ == "__main__":
    args = sys.argv[1:]
    main(Path(args[0]) if args else ROOT / "data" / "raw",
         Path(args[1]) if len(args) > 1 else ROOT / "data" / "fires.csv")
