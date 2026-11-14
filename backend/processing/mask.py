"""Static-source mask (spec §5): 0.01° squares with at least N historical SP detections of FIRMS
type 1, 2 or 3 (volcano, other static land source, offshore). NRT rows carry no `type`, so an NRT
detection inside the mask is dropped.

Square index: floor(deg / res_deg + EPS). EPS absorbs binary rounding (0.29 / 0.01 is
28.999999999999996) without moving real coordinates: FIRMS gives at most 5 decimals, i.e. index
steps of 1e-3 at 0.01°, far above EPS. A point on a square's south or west edge belongs to that
square. Build and lookup use the same function and the EPS stored in the file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .common import as_date, family_series
from .params import DEFAULT_PARAMS

MASK_KIND = "emberguard.static_mask"
MASK_FORMAT = 1
EPS = 1e-6
_OFFSET = 1 << 31
_MUL = 1 << 32


def square_index(deg, res_deg: float, eps: float = EPS) -> np.ndarray:
    return np.floor(np.asarray(deg, dtype=float) / res_deg + eps).astype(np.int64)


def _encode(iy: np.ndarray, ix: np.ndarray) -> np.ndarray:
    return iy.astype(np.int64) * _MUL + (ix.astype(np.int64) + _OFFSET)


def _params(res_deg: float, min_count: int, types, eps: float) -> dict:
    return {"res_deg": float(res_deg), "min_count": int(min_count), "types": [int(t) for t in types],
            "eps": float(eps)}


def _content_sha256(params: dict, squares) -> str:
    canonical = json.dumps({"params": params, "squares": [list(s) for s in squares]},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, eq=False)
class StaticMask:
    version: str
    res_deg: float
    min_count: int
    types: tuple[int, ...]
    squares: tuple[tuple[int, int, int], ...]  # (iy, ix, detections), sorted
    input: dict
    eps: float = EPS
    file_sha256: str | None = None
    path: str | None = None
    _keys: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        arr = np.array([(iy, ix) for iy, ix, _ in self.squares], dtype=np.int64).reshape(-1, 2)
        object.__setattr__(self, "_keys", np.sort(_encode(arr[:, 0], arr[:, 1])))

    @property
    def params(self) -> dict:
        return _params(self.res_deg, self.min_count, self.types, self.eps)

    @property
    def content_sha256(self) -> str:
        """sha256 of the parameters and squares (what decides which points are masked)."""
        return _content_sha256(self.params, self.squares)

    def contains(self, lat, lon) -> np.ndarray:
        """Vectorised: True where (lat, lon) falls in a mask square."""
        iy = square_index(lat, self.res_deg, self.eps)
        ix = square_index(lon, self.res_deg, self.eps)
        if iy.size == 0 or self._keys.size == 0:
            return np.zeros(iy.shape, dtype=bool)
        return np.isin(_encode(iy, ix), self._keys)

    def to_dict(self) -> dict:
        return {"kind": MASK_KIND, "format": MASK_FORMAT, "version": self.version,
                "content_sha256": self.content_sha256, "params": self.params, "input": self.input,
                "squares": [list(s) for s in self.squares]}

    def save(self, path) -> str:
        """Write the mask as JSON; returns the file's sha256."""
        body = json.dumps(self.to_dict(), indent=1, sort_keys=True) + "\n"
        Path(path).write_text(body)
        return hashlib.sha256(body.encode()).hexdigest()

    @classmethod
    def load(cls, path) -> "StaticMask":
        raw = Path(path).read_bytes()
        doc = json.loads(raw)
        if doc.get("kind") != MASK_KIND or doc.get("format") != MASK_FORMAT:
            raise ValueError(f"{path} is not a {MASK_KIND} file (format {MASK_FORMAT})")
        p = doc["params"]
        mask = cls(version=doc["version"], res_deg=float(p["res_deg"]), min_count=int(p["min_count"]),
                   types=tuple(int(t) for t in p["types"]), eps=float(p["eps"]),
                   squares=tuple((int(a), int(b), int(c)) for a, b, c in doc["squares"]),
                   input=doc.get("input", {}), file_sha256=hashlib.sha256(raw).hexdigest(), path=str(path))
        if mask.content_sha256 != doc.get("content_sha256"):
            raise ValueError(f"{path}: content_sha256 does not match its squares (file edited or corrupt)")
        return mask

    def summary(self) -> dict:
        return {"status": "enabled", "version": self.version, "squares": len(self.squares),
                "params": self.params, "content_sha256": self.content_sha256,
                "file_sha256": self.file_sha256, "path": self.path}


def build_static_mask(sp_detections: pd.DataFrame, min_count: int = DEFAULT_PARAMS.mask_min_count,
                      res_deg: float = DEFAULT_PARAMS.mask_res_deg, *,
                      types: tuple[int, ...] = DEFAULT_PARAMS.mask_types,
                      version: str | None = None) -> StaticMask:
    """Mask from SP rows with `type` in `types`; squares with >= min_count of them.

    Rows whose `product` is not SP (when the column exists) and rows without a type are ignored.
    No other filter is applied: a low-confidence gas flare is still a static source.
    """
    if min_count < 1:
        raise ValueError("min_count must be >= 1")
    df = sp_detections
    rows_in = len(df)
    is_sp = (df["product"].astype("string").str.strip().str.upper() == "SP") if "product" in df else \
        pd.Series(True, index=df.index)
    typ = pd.to_numeric(df["type"], errors="coerce") if "type" in df else pd.Series(np.nan, index=df.index)
    sel = df[is_sp.fillna(False).to_numpy() & typ.isin(list(types)).to_numpy()]
    sel_type = typ[sel.index].astype("int64")
    iy = square_index(pd.to_numeric(sel["latitude"]), res_deg)
    ix = square_index(pd.to_numeric(sel["longitude"]), res_deg)
    counts = pd.Series(1, index=pd.MultiIndex.from_arrays([iy, ix])).groupby(level=[0, 1]).sum() \
        if len(sel) else pd.Series(dtype="int64")
    kept = counts[counts >= min_count]
    squares = tuple((int(a), int(b), int(n)) for (a, b), n in sorted(kept.items()))
    dates = pd.to_datetime(sel["acq_date"]) if len(sel) else None
    fam = family_series(sel["source"]) if "source" in sel and len(sel) else pd.Series(dtype=object)
    summary = {
        "rows": int(rows_in),
        "sp_rows": int(is_sp.fillna(False).sum()),
        "typed_rows": int(len(sel)),
        "by_type": {str(t): int((sel_type == t).sum()) for t in types},
        "by_family": {str(k): int(v) for k, v in sorted(fam.fillna("unknown").value_counts().items())},
        "acq_date_min": as_date(dates.min()).isoformat() if dates is not None else None,
        "acq_date_max": as_date(dates.max()).isoformat() if dates is not None else None,
        "candidate_squares": int(len(counts)),
        "detections_in_mask": int(kept.sum()) if len(kept) else 0,
    }
    version = version or f"mask-{_content_sha256(_params(res_deg, min_count, types, EPS), squares)[:12]}"
    return StaticMask(version=version, res_deg=float(res_deg), min_count=int(min_count),
                      types=tuple(int(t) for t in types), squares=squares, input=summary)


def load_mask(path) -> StaticMask:
    return StaticMask.load(path)
