"""Frozen calibration (spec I6): MODIS -> VIIRS ratio k per (cy, cx, calendar month), the
inter-VIIRS factor r per calendar month, and the S-NPP end date. `firms_poll` only reads one;
refitting is `monthly_reconcile`'s job, behind a gate.

k lookup is the one in pipeline/calibrate.harmonize: the cell's k, else the calendar month's
regional k_month, else 1.0; support (training MODIS detections behind k) else 0.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Mapping

import pandas as pd

from pipeline import calibrate as _calibrate

from .common import KEY_COLS, as_date
from .params import DEFAULT_PARAMS

K_COLS = ["cy", "cx", "month", "k", "support"]
_DEFAULT_SNPP_END = DEFAULT_PARAMS.snpp_end


@dataclass(frozen=True, eq=False)
class Calibration:
    version: str
    k_table: pd.DataFrame                     # cy, cx, month, k, support; one row per (cy, cx, month)
    k_month: Mapping[int, float]              # calendar month -> regional k
    snpp_end: date | None                     # last day of S-NPP coverage (None = still flying)
    r: Mapping[int, float] | None = None      # approved S-NPP / NOAA-20 factor per calendar month
    r_global: float | None = None             # used for a calendar month missing from r
    source: str = ""
    _fingerprint: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("a calibration needs a version")
        kt = self.k_table[K_COLS].copy()
        kt = kt.astype({"cy": "int64", "cx": "int64", "month": "int64", "k": "float64", "support": "float64"})
        if kt.duplicated(["cy", "cx", "month"]).any():
            raise ValueError("k table has more than one row per (cy, cx, month)")
        if not (kt.k.map(math.isfinite).all() and (kt.k >= 0).all()):
            raise ValueError("k must be finite and >= 0")
        kt = kt.sort_values(["cy", "cx", "month"]).reset_index(drop=True)
        object.__setattr__(self, "k_table", kt)
        object.__setattr__(self, "k_month", MappingProxyType({int(m): float(v) for m, v in self.k_month.items()}))
        if self.snpp_end is not None:
            object.__setattr__(self, "snpp_end", as_date(self.snpp_end))
        if self.r is not None:
            r = {int(m): float(v) for m, v in self.r.items()}
            if not all(1 <= m <= 12 and math.isfinite(v) and v > 0 for m, v in r.items()):
                raise ValueError("r must map calendar months 1..12 to finite factors > 0")
            if self.r_global is None or not (math.isfinite(self.r_global) and self.r_global > 0):
                raise ValueError("an approved r needs a finite r_global > 0")
            object.__setattr__(self, "r", MappingProxyType(r))
            object.__setattr__(self, "r_global", float(self.r_global))
        elif self.r_global is not None:
            raise ValueError("r_global given without r")
        object.__setattr__(self, "_fingerprint", self._compute_fingerprint())

    @property
    def r_status(self) -> str:
        """"approved" when an approved r is attached, else "missing" (NOAA-20 values stay null)."""
        return "approved" if self.r is not None else "missing"

    def r_for(self, month: int) -> float | None:
        if self.r is None:
            return None
        return self.r.get(int(month), self.r_global)

    def lookup(self, keys: pd.DataFrame) -> pd.DataFrame:
        """k and support for each row of keys (needs cy, cx, month); same order as keys."""
        m = keys[["cy", "cx", "month"]].astype("int64").merge(self.k_table, on=["cy", "cx", "month"], how="left")
        k = m.k.fillna(m.month.map(dict(self.k_month))).fillna(1.0)
        return pd.DataFrame({"k": k.to_numpy(dtype=float), "support": m.support.fillna(0).to_numpy(dtype=float)},
                            index=keys.index)

    def with_inter_viirs(self, r: Mapping[int, float], r_global: float, *, version: str) -> "Calibration":
        """A new calibration version with an approved r (k and snpp_end unchanged)."""
        return Calibration(version=version, k_table=self.k_table, k_month=self.k_month, snpp_end=self.snpp_end,
                           r=r, r_global=r_global, source=self.source)

    def with_global_r(self, r_global: float, *, version: str) -> "Calibration":
        """A new calibration version applying one approved factor to every calendar month (spec §5:
        only the global r is used; per-month values are reported by the fit, never applied).
        `r_global` is typically `inter_viirs.approved_r_from_fit(...)`."""
        return self.with_inter_viirs({m: r_global for m in range(1, 13)}, r_global, version=version)

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def _compute_fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(pd.util.hash_pandas_object(self.k_table, index=False).to_numpy().tobytes())
        h.update(repr(sorted(self.k_month.items())).encode())
        h.update(repr((self.snpp_end, sorted(self.r.items()) if self.r else None, self.r_global)).encode())
        return h.hexdigest()

    def summary(self) -> dict:
        return {"version": self.version, "source": self.source, "fingerprint": self.fingerprint,
                "k_cells": int(len(self.k_table)), "k_month": {str(m): v for m, v in sorted(self.k_month.items())},
                "snpp_end": self.snpp_end.isoformat() if self.snpp_end else None, "r_status": self.r_status,
                "r": {str(m): v for m, v in sorted(self.r.items())} if self.r else None, "r_global": self.r_global}

    @classmethod
    def from_fit(cls, cells: pd.DataFrame, *, version: str, snpp_end: date | None = _DEFAULT_SNPP_END,
                 fit_years: tuple[int, int] = DEFAULT_PARAMS.calibration_fit_years,
                 r: Mapping[int, float] | None = None, r_global: float | None = None) -> "Calibration":
        """Fit k with pipeline.calibrate.fit on fit_years, as calibrate.harmonize does (2012-2021).

        `cells` is in pipeline/ingest.py's schema (cy, cx, year, month, sensor MODIS/VIIRS, n);
        grid.to_ingest_cells converts processing counts to it.
        """
        first, last = fit_years
        k_cell, k_month = _calibrate.fit(cells, range(first, last + 1))
        return cls(version=version, k_table=k_cell[K_COLS], k_month=k_month.to_dict(), snpp_end=snpp_end,
                   r=r, r_global=r_global, source=f"fit:{first}-{last}")

    @classmethod
    def from_harmonized_parquet(cls, path, *, version: str | None = None,
                                snpp_end: date | None = _DEFAULT_SNPP_END,
                                r: Mapping[int, float] | None = None,
                                r_global: float | None = None) -> "Calibration":
        """k from a harmonized.parquet written by pipeline/calibrate.py (rows carry k, k_month,
        support per cell-month-year; deduplicated to one row per (cy, cx, month))."""
        raw = open(path, "rb").read()
        df = pd.read_parquet(path)
        missing = [c for c in ("cy", "cx", "month", "k", "k_month", "support") if c not in df]
        if missing:
            raise ValueError(f"{path} lacks column(s) {', '.join(missing)}")
        table = df[K_COLS].drop_duplicates()
        if table.duplicated(["cy", "cx", "month"]).any():
            raise ValueError(f"{path} has conflicting k or support for the same (cy, cx, month)")
        k_month = df.dropna(subset=["k_month"]).drop_duplicates(["month", "k_month"])
        if k_month.month.duplicated().any():
            raise ValueError(f"{path} has more than one k_month for a calendar month")
        sha = hashlib.sha256(raw).hexdigest()
        return cls(version=version or f"parquet-{sha[:12]}", k_table=table,
                   k_month=dict(zip(k_month.month.astype(int), k_month.k_month.astype(float))),
                   snpp_end=snpp_end, r=r, r_global=r_global, source=f"parquet:{path} sha256:{sha}")


def keys_with_k(keys: pd.DataFrame, calibration: Calibration) -> pd.DataFrame:
    """keys (cy, cx, year, month) with k and support columns."""
    out = keys[KEY_COLS].reset_index(drop=True)
    return pd.concat([out, calibration.lookup(out)], axis=1)
