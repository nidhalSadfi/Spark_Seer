"""EmberGuard processing science (spec docs/processing/ARCHITECTURE.md §5-§6), pure Python.

No database, network, worker or CrewAI imports: pandas, numpy and shapely only. Phase 2b feeds it
from PostgreSQL, Phase 3 wraps its stages as tools, Phase 4 persists its output.
"""

from .boundary import Boundary, BoundaryError, load_boundary
from .calibration import Calibration
from .completeness import completeness, month_status
from .coverage import Coverage
from .drift import drift_monitor
from .filters import DROP_REASONS, apply_filters
from .gates import (GateResult, gate_conservation, gate_frozen_calibration, gate_missing_not_zero, gate_schema,
                    run_gates, soft_flags)
from .grid import aggregate, cell_index, to_ingest_cells
from .harmonize import STAGED_COLUMNS, harmonize
# fit_inter_viirs (the function) stays in processing.inter_viirs: processing.fit_inter_viirs is its CLI module.
from .inter_viirs import load_outages, outage_months
from .mask import StaticMask, build_static_mask, load_mask
from .params import DEFAULT_PARAMS, ProcessingParams
from .pipeline import STAGE_ORDER, ProcessingResult, ProcessingRun, process

__all__ = [
    "Boundary", "BoundaryError", "Calibration", "Coverage", "DEFAULT_PARAMS", "DROP_REASONS", "GateResult",
    "ProcessingParams", "ProcessingResult", "ProcessingRun", "STAGED_COLUMNS", "STAGE_ORDER", "StaticMask",
    "aggregate", "apply_filters", "build_static_mask", "cell_index", "completeness", "drift_monitor",
    "gate_conservation", "gate_frozen_calibration", "gate_missing_not_zero", "gate_schema", "harmonize",
    "load_boundary", "load_mask", "load_outages", "month_status", "outage_months", "process", "run_gates",
    "soft_flags", "to_ingest_cells",
]
