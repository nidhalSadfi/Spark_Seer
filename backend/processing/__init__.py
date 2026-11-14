"""EmberGuard processing science (spec docs/processing/ARCHITECTURE.md §5-§6), pure Python.

No database, network, worker or CrewAI imports: pandas, numpy and shapely only. Phase 2b feeds it
from PostgreSQL, Phase 3 wraps its stages as tools, Phase 4 persists its output.
"""

from .boundary import Boundary, BoundaryError, load_boundary
from .calibration import Calibration
from .completeness import completeness, month_status
from .coverage import Coverage
from .filters import DROP_REASONS, apply_filters
from .grid import aggregate, cell_index, to_ingest_cells
from .harmonize import STAGED_COLUMNS, harmonize
from .inter_viirs import fit_inter_viirs, load_outages, outage_months
from .mask import StaticMask, build_static_mask, load_mask
from .params import DEFAULT_PARAMS, ProcessingParams

__all__ = ["Boundary", "BoundaryError", "Calibration", "Coverage", "DEFAULT_PARAMS", "DROP_REASONS",
           "ProcessingParams", "STAGED_COLUMNS", "StaticMask", "aggregate", "apply_filters", "build_static_mask",
           "cell_index", "completeness", "fit_inter_viirs", "harmonize", "load_boundary", "load_mask",
           "load_outages", "month_status", "outage_months", "to_ingest_cells"]
