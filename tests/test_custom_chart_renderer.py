"""
tests/test_custom_chart_renderer.py

Unit tests for components/custom_chart_renderer.py.
NiceGUI UI calls are mocked so tests run outside a browser/event-loop context.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# Ensure nicegui can be imported even without a running event loop.
# We stub just enough for the module-level import to resolve.
if 'nicegui' not in sys.modules:
    sys.modules['nicegui'] = MagicMock()
if 'nicegui.ui' not in sys.modules:
    sys.modules['nicegui.ui'] = MagicMock()

import components.custom_chart_renderer as renderer


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ui_mock():
    m = MagicMock()
    m.echart.return_value = MagicMock()
    m.label.return_value  = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# 1. Empty data shows a label, not an echart
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_data_shows_no_data_label():
    ui = _ui_mock()
    with patch.object(renderer, 'ui', ui):
        renderer.render_custom_chart({'chart_type': 'bar'}, {'x': [], 'series': {}})
    ui.label.assert_called_once()
    ui.echart.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Bar chart produces an echart with 'bar' series type
# ─────────────────────────────────────────────────────────────────────────────

def test_bar_chart_creates_echart():
    ui = _ui_mock()
    data = {'x': ['Jan 2024', 'Feb 2024'], 'series': {'amount': [100.0, 200.0]}}
    config = {'chart_type': 'bar', 'label_format': 'dollar', 'show_legend': True}

    with patch.object(renderer, 'ui', ui):
        renderer.render_custom_chart(config, data)

    ui.echart.assert_called_once()
    opts = ui.echart.call_args[0][0]
    assert len(opts['series']) == 1
    assert opts['series'][0]['type'] == 'bar'


# ─────────────────────────────────────────────────────────────────────────────
# 3. Donut chart produces an echart with 'pie' series type
# ─────────────────────────────────────────────────────────────────────────────

def test_donut_chart_creates_echart():
    ui = _ui_mock()
    data = {'x': ['Food', 'Gas'], 'series': {'amount': [50.0, 30.0]}}
    config = {'chart_type': 'donut', 'show_legend': True}

    with patch.object(renderer, 'ui', ui):
        renderer.render_custom_chart(config, data)

    ui.echart.assert_called_once()
    opts = ui.echart.call_args[0][0]
    assert len(opts['series']) == 1
    assert opts['series'][0]['type'] == 'pie'


# ─────────────────────────────────────────────────────────────────────────────
# 4. Line chart with 2 series produces 2 series entries
# ─────────────────────────────────────────────────────────────────────────────

def test_line_chart_multiple_series():
    ui = _ui_mock()
    data = {
        'x': ['Jan 2024', 'Feb 2024'],
        'series': {'Food': [50.0, 60.0], 'Gas': [30.0, 40.0]},
    }
    config = {'chart_type': 'line', 'show_legend': True}

    with patch.object(renderer, 'ui', ui):
        renderer.render_custom_chart(config, data)

    opts = ui.echart.call_args[0][0]
    assert len(opts['series']) == 2
    for s in opts['series']:
        assert s['type'] == 'line'


# ─────────────────────────────────────────────────────────────────────────────
# 5. label_format='dollar' includes dollar formatter string
# ─────────────────────────────────────────────────────────────────────────────

def test_label_format_dollar():
    ui = _ui_mock()
    data = {'x': ['Jan 2024'], 'series': {'amount': [100.0]}}
    config = {'chart_type': 'bar', 'label_format': 'dollar', 'show_legend': False}

    with patch.object(renderer, 'ui', ui), \
         patch.object(renderer._auth, 'current_currency_prefix', return_value='$ '):
        renderer.render_custom_chart(config, data)

    opts = ui.echart.call_args[0][0]
    fmt_str = opts['series'][0]['label'].get(':formatter', '')
    assert '$' in fmt_str


# ─────────────────────────────────────────────────────────────────────────────
# 6. label_format=None produces no special formatter on series
# ─────────────────────────────────────────────────────────────────────────────

def test_label_format_none():
    ui = _ui_mock()
    data = {'x': ['Jan 2024'], 'series': {'amount': [100.0]}}
    config = {'chart_type': 'bar', 'label_format': None, 'show_legend': False}

    with patch.object(renderer, 'ui', ui):
        renderer.render_custom_chart(config, data)

    opts = ui.echart.call_args[0][0]
    assert 'label' not in opts['series'][0]
