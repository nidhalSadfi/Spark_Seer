"""EmberGuard processing science (spec docs/processing/ARCHITECTURE.md §5-§6), pure Python.

No database, network, worker or CrewAI imports: pandas, numpy and shapely only. Phase 2b feeds it
from PostgreSQL, Phase 3 wraps its stages as tools, Phase 4 persists its output.
"""

from .boundary import Boundary, BoundaryError, load_boundary
from .filters import DROP_REASONS, apply_filters
from .mask import StaticMask, build_static_mask, load_mask
from .params import DEFAULT_PARAMS, ProcessingParams

__all__ = ["Boundary", "BoundaryError", "DEFAULT_PARAMS", "DROP_REASONS", "ProcessingParams", "StaticMask",
           "apply_filters", "build_static_mask", "load_boundary", "load_mask"]
