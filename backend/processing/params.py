"""Scientific constants of the processing stages (spec §5), in one frozen object with defaults.

These are not environment variables: changing one changes the science, so it is a code change
with tests. Deployment settings (file paths, the drift thresholds) live in backend/settings.py; the
worker copies `DRIFT_WARN_PCT` and `DRIFT_MIN_COUNT` into the params it builds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from firms_tool.sensors import SENSORS


@dataclass(frozen=True)
class ProcessingParams:
    # Filters (spec §5; pipeline/ingest.py with min_lat = 34 as the README runs it). None = no
    # latitude cut (the world, spec §16: the static-source mask instead).
    min_lat: float | None = 34.0
    modis_min_confidence: float = 30.0
    viirs_confidences: tuple[str, ...] = ("n", "h", "nominal", "high")  # compared trimmed, lower case
    sp_keep_type: int = 0  # FIRMS type 0 = presumed vegetation fire
    # Grid (= pipeline/ingest.py CELL_DEG = api/core.py CELL_DEG).
    cell_deg: float = 0.5
    # Harmonized value: MODIS x k before this year, the VIIRS reference from it.
    first_viirs_year: int = 2012
    # Years pipeline/calibrate.harmonize refits k on (inclusive).
    calibration_fit_years: tuple[int, int] = (2012, 2021)
    # Last day of S-NPP coverage (sensor catalogue; spec D2 says to confirm with data_availability).
    snpp_end: date | None = SENSORS["viirs_snpp"].coverage_end
    # Inter-VIIRS factor r = S-NPP / NOAA-20 (inclusive year ranges).
    inter_viirs_train_years: tuple[int, int] = (2019, 2023)
    inter_viirs_test_years: tuple[int, int] = (2024, 2025)
    inter_viirs_pseudo: float = 20.0  # in NOAA-20 detections, like calibrate.M_PSEUDO
    # Static-source mask.
    mask_res_deg: float = 0.01
    mask_min_count: int = 3
    mask_types: tuple[int, ...] = (1, 2, 3)  # FIRMS type 1 volcano, 2 other static land source, 3 offshore
    # Country clip (ISO 3166-1 alpha-3).
    country_codes: tuple[str, ...] = ("TUN", "DZA")
    # Soft flags. drift_warn_pct / drift_min_count mirror the DRIFT_WARN_PCT (default 40) and
    # DRIFT_MIN_COUNT (default 50) settings: below drift_min_count summed VIIRS reference detections,
    # a region-month is "insufficient_data" (small counts swing by chance) and never flagged.
    drift_warn_pct: float = 40.0
    drift_min_count: float = 50.0
    # A clip or mask drop share is "far above its recent level" when it exceeds both
    # factor x the recent share and the recent share + min_pp percentage points.
    drop_share_factor: float = 3.0
    drop_share_min_pp: float = 5.0

    def __post_init__(self) -> None:
        for name in ("calibration_fit_years", "inter_viirs_train_years", "inter_viirs_test_years"):
            first, last = getattr(self, name)
            if first > last:
                raise ValueError(f"{name} must be (first, last) with first <= last, got {(first, last)}")
        if self.cell_deg <= 0 or self.mask_res_deg <= 0:
            raise ValueError("cell_deg and mask_res_deg must be > 0")
        if self.mask_min_count < 1:
            raise ValueError("mask_min_count must be >= 1")
        if self.drift_warn_pct <= 0:
            raise ValueError("drift_warn_pct must be > 0")
        if self.drift_min_count < 0:
            raise ValueError("drift_min_count must be >= 0")


DEFAULT_PARAMS = ProcessingParams()
