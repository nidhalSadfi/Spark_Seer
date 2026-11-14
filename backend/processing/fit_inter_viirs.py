"""Fit and validate the inter-VIIRS factor r (S-NPP / NOAA-20) from FIRMS archive CSVs.

Usage (from backend/):
    python -m processing.fit_inter_viirs <raw_csv_dir> <out.json>
        [--outages FILE] [--mask FILE] [--boundary FILE]

Applies the shared filter (the mask and the country clip only when given; the output says which),
counts per region-month, fits r on the train years and reports held-out errors (spec §5). The
result is a proposal: an r is used only once a calibration version carrying it is approved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .archive import csv_files, read_firms_csvs
from .boundary import load_boundary
from .common import jsonable
from .filters import apply_filters
from .grid import aggregate
from .inter_viirs import OUTAGES_FILE, fit_inter_viirs, load_outages
from .mask import load_mask
from .params import DEFAULT_PARAMS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m processing.fit_inter_viirs", description=__doc__.splitlines()[0])
    ap.add_argument("raw_csv_dir")
    ap.add_argument("out_json")
    ap.add_argument("--outages", default=str(OUTAGES_FILE), help="known outages JSON (default: the committed file)")
    ap.add_argument("--mask", help="static-source mask JSON (default: rule disabled)")
    ap.add_argument("--boundary", help="country boundary GeoJSON (default: clip disabled)")
    args = ap.parse_args(argv)
    files = csv_files(args.raw_csv_dir)
    if not files:
        print(f"no *.csv files under {args.raw_csv_dir}", file=sys.stderr)
        return 2
    params = DEFAULT_PARAMS
    outages = load_outages(args.outages)
    kept, f_sum = apply_filters(read_firms_csvs(args.raw_csv_dir),
                                mask=load_mask(args.mask) if args.mask else None,
                                boundary=load_boundary(args.boundary) if args.boundary else None, params=params)
    counts, _ = aggregate(kept, params=params)
    fit = fit_inter_viirs(counts, outages, params.inter_viirs_train_years, params.inter_viirs_test_years,
                          params.inter_viirs_pseudo)
    out = {"kind": "emberguard.inter_viirs_fit", "status": "proposed", **fit.to_dict(),
           "outages": {"file": args.outages, "sha256": hashlib.sha256(Path(args.outages).read_bytes()).hexdigest(),
                       "source": outages["source"]},
           "input": {"raw_csv_dir": args.raw_csv_dir, "files": len(files)}, "filter": f_sum.to_dict()}
    Path(args.out_json).write_text(json.dumps(jsonable(out), indent=2, sort_keys=True) + "\n")
    print(json.dumps(jsonable(fit.report), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
