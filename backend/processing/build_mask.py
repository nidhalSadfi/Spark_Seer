"""Build the static-source mask from FIRMS archive CSVs (SP rows with type 1, 2 or 3).

Usage (from backend/):
    python -m processing.build_mask <raw_csv_dir> <out.json> [--min-count N] [--res-deg R]

Reads every *.csv under raw_csv_dir (yearly country files carry `type`), writes the versioned
mask JSON and prints its summary. Point STATIC_MASK_FILE at the result.
"""

from __future__ import annotations

import argparse
import json
import sys

from .archive import csv_files, read_firms_csvs
from .mask import build_static_mask
from .params import DEFAULT_PARAMS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m processing.build_mask", description=__doc__.splitlines()[0])
    ap.add_argument("raw_csv_dir")
    ap.add_argument("out_json")
    ap.add_argument("--min-count", type=int, default=DEFAULT_PARAMS.mask_min_count,
                    help=f"detections a 0.01 deg square needs (default {DEFAULT_PARAMS.mask_min_count})")
    ap.add_argument("--res-deg", type=float, default=DEFAULT_PARAMS.mask_res_deg, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    files = csv_files(args.raw_csv_dir)
    if not files:
        print(f"no *.csv files under {args.raw_csv_dir}", file=sys.stderr)
        return 2
    mask = build_static_mask(read_firms_csvs(args.raw_csv_dir), min_count=args.min_count, res_deg=args.res_deg)
    file_sha = mask.save(args.out_json)
    print(json.dumps({"out": args.out_json, "file_sha256": file_sha, "files": len(files), **mask.summary(),
                      "input": mask.input}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
