"""Build the files the API serves from the data person's real FIRMS cells.

    python scripts/build_data.py [cells] [out]   # defaults: data/cells.parquet data/harmonized.parquet

Writes data/harmonized.parquet and data/harmonized_validation.json with the unchanged
pipeline/calibrate.py. The cells come from harmonization-modis-viirs/pipeline/ingest.py (see README),
which names the 0.5 degree cell indexes lat_i / lon_i. calibrate.py calls them cy / cx. The values
are the same (floor(coord * 2) == floor(coord / 0.5)), so this only renames the two columns.
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import calibrate  # noqa: E402

RENAME = {"lat_i": "cy", "lon_i": "cx"}


def main(cells: Path, out: Path) -> None:
    df = pd.read_parquet(cells).rename(columns=RENAME)
    missing = {"cy", "cx", "year", "month", "sensor", "n"} - set(df.columns)
    if missing:
        sys.exit(f"{cells} is missing columns {sorted(missing)}: is it ingest.py output?")
    with tempfile.TemporaryDirectory() as tmp:
        renamed = Path(tmp) / "cells.parquet"
        df.to_parquet(renamed, index=False)
        calibrate.main(str(renamed), str(out))  # also writes <out>_validation.json next to out


if __name__ == "__main__":
    args = sys.argv[1:]
    main(Path(args[0]) if args else ROOT / "data" / "cells.parquet",
         Path(args[1]) if len(args) > 1 else ROOT / "data" / "harmonized.parquet")
