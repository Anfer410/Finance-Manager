"""
components/dashboard_registry.py  [COMPATIBILITY SHIM]

Re-exports REGISTRY and REGISTRY_BY_ID from the new widget system so that
any code still importing from this module continues to work without changes.

All new code should import from components.widgets directly:
    from components.widgets import REGISTRY, REGISTRY_BY_ID, RenderContext
"""

from components.widgets.registry import REGISTRY, REGISTRY_BY_ID
from components.widgets.base     import RenderContext

# Legacy alias — ChartDef was the old descriptor dataclass.
# REGISTRY now contains Widget instances, which expose the same attributes
# (id, title, description, icon, category, default_col_span, default_row_span,
#  has_own_header, supports_person_filter, config_schema).
ChartDef = None   # no longer used; kept as a marker so old imports don't error

__all__ = ['REGISTRY', 'REGISTRY_BY_ID', 'RenderContext', 'ChartDef']
