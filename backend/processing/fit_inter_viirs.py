"""Fit and validate the inter-VIIRS factor r (S-NPP / NOAA-20) from FIRMS archive CSVs.

Usage (from backend/):
    python -m processing.fit_inter_viirs <raw_csv_dir> <out.json>
        [--outages FILE] [--mask FILE] [--boundary FILE] [--validate-dir DIR]

Applies the shared filter (the mask and the country clip only when given; the output says which),
counts per region-month, fits r on the train years and reports held-out errors (spec §5). The
result is a proposal: an r is used only once a calibration version carrying it is approved.

With --validate-dir, <raw_csv_dir> is the fit pool (spec §5: the yearly country files of
Morocco, Spain, Portugal, Italy, Greece and Turkey): r is fitted on it over the train years and
validated on DIR (Tunisia + Algeria) over the test years, with the same filter, outage exclusion
and pseudo-count. The output then also has the validation errors of r = 1, the global r and the
per-month r, the acceptance rule's `approved_r`, and both file lists with their hash and the
countries named in the file names.
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
from .inter_viirs import (OUTAGES_FILE, accept_r, countries_from_names, fit_inter_viirs, load_outages,
                          validate_inter_viirs)
from .mask import load_mask
from .params import DEFAULT_PARAMS

SPATIAL_RULES_NOTE = ("Neither the static-source mask nor the country clip was applied (filter.rules): the "
                      "per-country FIRMS files are used as delivered, with the other filter rules (north of the "
                      "minimum latitude, confidence, SP type).")


def _counts(raw_dir, *, mask, boundary, params):
    kept, f_sum = apply_filters(read_firms_csvs(raw_dir), mask=mask, boundary=boundary, params=params)
    counts, _ = aggregate(kept, params=params)
    return counts, f_sum


def file_inventory(raw_dir, files: list[Path]) -> dict:
    """The files under raw_dir (relative paths), a sha256 over their names and contents, and the
    countries named in the file names."""
    root = Path(raw_dir)
    h = hashlib.sha256()
    names = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        names.append(rel)
        h.update(f"{rel}\t{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return {"file_list": names, "files_sha256": h.hexdigest(), "countries": countries_from_names(files)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m processing.fit_inter_viirs", description=__doc__.splitlines()[0])
    ap.add_argument("raw_csv_dir", help="FIRMS archive CSVs to fit r on (with --validate-dir: the fit pool)")
    ap.add_argument("out_json")
    ap.add_argument("--outages", default=str(OUTAGES_FILE), help="known outages JSON (default: the committed file)")
    ap.add_argument("--mask", help="static-source mask JSON (default: rule disabled)")
    ap.add_argument("--boundary", help="country boundary GeoJSON (default: clip disabled)")
    ap.add_argument("--validate-dir", help="FIRMS archive CSVs to validate the pool's r on over the test years "
                                           "(Tunisia + Algeria), and apply the acceptance rule")
    args = ap.parse_args(argv)
    files = csv_files(args.raw_csv_dir)
    if not files:
        print(f"no *.csv files under {args.raw_csv_dir}", file=sys.stderr)
        return 2
    v_files = csv_files(args.validate_dir) if args.validate_dir else []
    if args.validate_dir and not v_files:
        print(f"no *.csv files under {args.validate_dir}", file=sys.stderr)
        return 2
    params = DEFAULT_PARAMS
    outages = load_outages(args.outages)
    mask = load_mask(args.mask) if args.mask else None
    boundary = load_boundary(args.boundary) if args.boundary else None
    counts, f_sum = _counts(args.raw_csv_dir, mask=mask, boundary=boundary, params=params)
    fit = fit_inter_viirs(counts, outages, params.inter_viirs_train_years, params.inter_viirs_test_years,
                          params.inter_viirs_pseudo)
    out = {"kind": "emberguard.inter_viirs_fit", "status": "proposed", **fit.to_dict(),
           "outages": {"file": args.outages, "sha256": hashlib.sha256(Path(args.outages).read_bytes()).hexdigest(),
                       "source": outages["source"]},
           "input": {"raw_csv_dir": args.raw_csv_dir, "files": len(files)}, "filter": f_sum.to_dict()}
    printed = fit.report
    if args.validate_dir:
        v_counts, v_sum = _counts(args.validate_dir, mask=mask, boundary=boundary, params=params)
        try:
            validation = validate_inter_viirs(v_counts, outages, fit, params.inter_viirs_test_years)
            acceptance = accept_r(fit, validation)
        except ValueError as e:
            print(f"validation on {args.validate_dir} failed: {e}", file=sys.stderr)
            return 2
        out["input"].update(role="fit pool", **file_inventory(args.raw_csv_dir, files))
        out["validation"] = {"raw_csv_dir": args.validate_dir, "files": len(v_files),
                             **file_inventory(args.validate_dir, v_files), **validation, "filter": v_sum.to_dict()}
        out["acceptance"] = acceptance
        out["approved_r"] = acceptance["approved_r"]
        if mask is None and boundary is None:
            out["note"] = SPATIAL_RULES_NOTE
        printed = {"pool": {"countries": out["input"]["countries"], "files": len(files), **fit.report},
                   "validation": {k: v for k, v in out["validation"].items() if k not in ("file_list", "filter")},
                   "acceptance": acceptance}
    Path(args.out_json).write_text(json.dumps(jsonable(out), indent=2, sort_keys=True) + "\n")
    print(json.dumps(jsonable(printed), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
