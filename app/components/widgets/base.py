"""
components/widgets/base.py

Core abstractions for the widget system.

  WidgetType   — enum of rendering categories (kpi, bar, line, mixed, …)
  TimeMode     — enum of time-range strategies
  ConfigField  — schema descriptor for one user-settable widget field
  RenderContext— resolved context passed to every Widget.render()
  Widget       — abstract base class all widgets inherit from

Design notes
────────────
  • Widgets are defined as singleton instances and registered in
    components/widgets/registry.py.
  • The dashboard grid creates a RenderContext via RenderContext.build(),
    which resolves the page-level year / persons against any per-widget
    overrides stored in the widget's config JSONB.
  • Widget.render_standalone() lets non-dashboard pages (loans, planning)
    render any widget without a full dashboard context.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


# ── Enums ─────────────────────────────────────────────────────────────────────

class WidgetType(str, Enum):
    KPI         = 'kpi'
    BAR         = 'bar'          # simple grouped bar chart
    LINE        = 'line'         # multi-series line chart
    MIXED       = 'mixed'        # bar + line combo (e.g. spend vs income)
    STACKED_BAR = 'stacked_bar'  # stacked bars (category trend, weekly txns)
    DONUT       = 'donut'        # pie / donut chart
    AREA_LINE   = 'area_line'    # area + line curves (loan balances)
    TABLE       = 'table'        # data table


class TimeMode(str, Enum):
    PAGE_YEAR  = 'page_year'   # inherit the dashboard's selected year (default)
    TRAILING   = 'trailing'    # N trailing months back from today
    YEAR       = 'year'        # a specific year chosen in settings
    DATE_RANGE = 'date_range'  # explicit from / to dates
    ALL_TIME   = 'all_time'    # no time filter


# ── Config field descriptor ────────────────────────────────────────────────────

@dataclass
class ConfigField:
    """
    Describes one user-configurable field for a widget's settings dialog.

    type values
    ───────────
      'number'  — numeric input; honour min/max
      'select'  — dropdown; options + option_labels must be set
      'toggle'  — boolean checkbox
    """
    key:           str
    label:         str
    type:          str        # 'number' | 'select' | 'toggle'
    default:       Any  = None
    description:   str  = ''
    # number
    min:           int | None = None
    max:           int | None = None
    # select
    options:       list | None       = None
    option_labels: list[str] | None  = None


# ── Render context ─────────────────────────────────────────────────────────────

@dataclass
class RenderContext:
    """
    Fully resolved context passed to Widget.render().

    Build it via RenderContext.build() which applies widget config
    overrides on top of page-level defaults.
    """
    year:            int
    persons:         list[int] | None  # resolved person filter (None = all)
    config:          dict               # raw config JSONB blob
    shared_state:    dict               # page-level callbacks / category filter

    # Resolved time fields
    time_mode:       TimeMode    = TimeMode.PAGE_YEAR
    date_from:       date | None = None
    date_to:         date | None = None
    trailing_months: int | None  = None

    # Loan context (resolved from config.loan_id)
    loan_id:         int | None  = None

    # Multi-tenancy
    family_id:       int | None  = None

    @classmethod
    def build(
        cls,
        page_year:    int,
        page_persons: list[int] | None,
        config:       dict,
        shared_state: dict,
    ) -> 'RenderContext':
        """
        Build a RenderContext by resolving widget-level config overrides
        against the page-level defaults.

        Priority:
          persons  → widget config['persons'] > page persons
          time     → widget config['time_mode'] > page_year
        """
        from datetime import date as _date

        # ── Persons ───────────────────────────────────────────────────────────
        persons_cfg = config.get('persons')
        persons = [int(p) for p in persons_cfg] if persons_cfg else page_persons

        # ── Time mode ─────────────────────────────────────────────────────────
        raw_mode = config.get('time_mode', TimeMode.PAGE_YEAR.value)
        try:
            mode = TimeMode(raw_mode)
        except ValueError:
            mode = TimeMode.PAGE_YEAR

        year            = page_year
        date_from       = None
        date_to         = None
        trailing_months = None

        if mode == TimeMode.TRAILING:
            trailing_months = int(config.get('trailing_months', 12))
            today = _date.today()
            year  = today.year
            # First day of (trailing_months) months ago
            total = today.year * 12 + (today.month - 1) - trailing_months
            y, m  = divmod(total, 12)
            date_from = _date(y, m + 1, 1)
            date_to   = today

        elif mode == TimeMode.YEAR:
            year = int(config.get('year', page_year))

        elif mode == TimeMode.DATE_RANGE:
            df = config.get('date_from')
            dt = config.get('date_to')
            if df:
                date_from = _date.fromisoformat(str(df))
            if dt:
                date_to = _date.fromisoformat(str(dt))
            year = date_from.year if date_from else page_year

        # ── Loan ──────────────────────────────────────────────────────────────
        loan_id = config.get('loan_id')
        if loan_id is not None:
            loan_id = int(loan_id)

        try:
            import services.auth as _auth
            family_id = _auth.current_family_id()
        except Exception:
            family_id = None

        return cls(
            year=year,
            persons=persons,
            config=config,
            shared_state=shared_state,
            time_mode=mode,
            date_from=date_from,
            date_to=date_to,
            trailing_months=trailing_months,
            loan_id=loan_id,
            family_id=family_id,
        )


# ── Abstract Widget base ───────────────────────────────────────────────────────

class Widget(ABC):
    """
    Abstract base class for all dashboard widgets.

    Class-level attributes
    ──────────────────────
      id          — unique slug stored in app_dashboard_widgets.chart_id
      title       — display name shown in the card header and widget picker
      description — shown in the "Add widget" panel
      icon        — Material icon name
      category    — grouping in the picker ('overview'|'spend'|'income'|'trends'|'loans')
      widget_type — WidgetType enum value (drives icon hints and future UI grouping)

      default_col_span / default_row_span  — initial size on the 4-column grid
      has_own_header     — True → render() draws its own title/controls header;
                           False → the grid renders a standard "Label / year" header
      supports_person_filter — show per-widget person override in settings
      supports_time_range    — show time_mode selector in settings dialog
      supports_loan_select   — show loan picker in settings dialog
      config_schema          — list of widget-specific ConfigField descriptors

    Render contract
    ───────────────
      render(ctx)            — renders inner content into the current NiceGUI context
      render_standalone(...) — convenience wrapper for non-dashboard pages

    Registration
    ────────────
      Concrete widgets are registered as singleton instances in
      components/widgets/registry.py.  Nothing else needs to change —
      the dashboard page iterates REGISTRY at render time.
    """

    # ── Required class attributes (no enforcement — rely on convention) ────────
    id:          str
    title:       str
    description: str
    icon:        str
    category:    str
    widget_type: WidgetType

    # ── Optional overrides ────────────────────────────────────────────────────
    default_col_span:       int  = 4
    default_row_span:       int  = 1
    has_own_header:         bool = False
    supports_person_filter: bool = True
    supports_time_range:    bool = True
    supports_loan_select:   bool = False
    config_schema:          list[ConfigField] = []

    @abstractmethod
    def render(self, ctx: RenderContext) -> None:
        """Render widget inner content into the current NiceGUI context."""
        ...

    def render_standalone(
        self,
        year:    int,
        persons: list[int] | None = None,
        config:  dict | None      = None,
    ) -> None:
        """
        Convenience method to render this widget outside a full dashboard.
        Used by the loans and planning pages.
        """
        ctx = RenderContext.build(year, persons, config or {}, {})
        self.render(ctx)
