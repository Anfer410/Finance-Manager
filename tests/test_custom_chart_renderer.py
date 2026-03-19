"""
tests/test_custom_chart_renderer.py

Unit tests for components/custom_chart_renderer.py.
NiceGUI UI calls are mocked so tests run outside a browser/event-loop context.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch, call

import pytest


# Stub out nicegui.ui before importing the renderer so the module-level import
# of `from nicegui import ui` resolves to our mock namespace.
_ui_mock = MagicMock()
sys.modules.setdefault('nicegui', MagicMock(ui=_ui_mock))
sys.modules.setdefault('nicegui.ui', _ui_mock)

import components.custom_chart_renderer as renderer


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _reset_mocks():
    _ui_mock.reset_mock()
    _ui_mock.echart.return_value = MagicMock()
    _ui_mock.label.return_value  = MagicMock()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Empty data shows a label, not an echart
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_data_shows_no_data_label():
    _reset_mocks()
    with patch('nicegui.ui', _ui_mock):
        renderer.render_custom_chart({'chart_type': 'bar'}, {'x': [], 'series': {}})
    _ui_mock.label.assert_called_once()
    _ui_mock.echart.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Bar chart produces an echart with 'bar' series type
# ─────────────────────────────────────────────────────────────────────────────

def test_bar_chart_creates_echart():
    _reset_mocks()
    data = {'x': ['Jan 2024', 'Feb 2024'], 'series': {'amount': [100.0, 200.0]}}
    config = {'chart_type': 'bar', 'label_format': 'dollar', 'show_legend': True}

    with patch('nicegui.ui', _ui_mock):
        renderer.render_custom_chart(config, data)

    _ui_mock.echart.assert_called_once()
    opts = _ui_mock.echart.call_args[0][0]
    series = opts['series']
    assert len(series) == 1
    assert series[0]['type'] == 'bar'


# ─────────────────────────────────────────────────────────────────────────────
# 3. Donut chart produces an echart with 'pie' series type
# ─────────────────────────────────────────────────────────────────────────────

def test_donut_chart_creates_echart():
    _reset_mocks()
    data = {'x': ['Food', 'Gas'], 'series': {'amount': [50.0, 30.0]}}
    config = {'chart_type': 'donut', 'show_legend': True}

    with patch('nicegui.ui', _ui_mock):
        renderer.render_custom_chart(config, data)

    _ui_mock.echart.assert_called_once()
    opts = _ui_mock.echart.call_args[0][0]
    series = opts['series']
    assert len(series) == 1
    assert series[0]['type'] == 'pie'


# ─────────────────────────────────────────────────────────────────────────────
# 4. Line chart with 2 series produces 2 series entries
# ─────────────────────────────────────────────────────────────────────────────

def test_line_chart_multiple_series():
    _reset_mocks()
    data = {
        'x': ['Jan 2024', 'Feb 2024'],
        'series': {
            'Food': [50.0, 60.0],
            'Gas':  [30.0, 40.0],
        },
    }
    config = {'chart_type': 'line', 'show_legend': True}

    with patch('nicegui.ui', _ui_mock):
        renderer.render_custom_chart(config, data)

    opts = _ui_mock.echart.call_args[0][0]
    assert len(opts['series']) == 2
    for s in opts['series']:
        assert s['type'] == 'line'


# ─────────────────────────────────────────────────────────────────────────────
# 5. label_format='dollar' includes dollar formatter string
# ─────────────────────────────────────────────────────────────────────────────

def test_label_format_dollar():
    _reset_mocks()
    data = {'x': ['Jan 2024'], 'series': {'amount': [100.0]}}
    config = {'chart_type': 'bar', 'label_format': 'dollar', 'show_legend': False}

    with patch('nicegui.ui', _ui_mock):
        renderer.render_custom_chart(config, data)

    opts = _ui_mock.echart.call_args[0][0]
    series = opts['series']
    assert 'label' in series[0]
    fmt_str = series[0]['label'].get(':formatter', '')
    assert '$' in fmt_str


# ─────────────────────────────────────────────────────────────────────────────
# 6. label_format=None produces no special formatter on series
# ─────────────────────────────────────────────────────────────────────────────

def test_label_format_none():
    _reset_mocks()
    data = {'x': ['Jan 2024'], 'series': {'amount': [100.0]}}
    config = {'chart_type': 'bar', 'label_format': None, 'show_legend': False}

    with patch('nicegui.ui', _ui_mock):
        renderer.render_custom_chart(config, data)

    opts = _ui_mock.echart.call_args[0][0]
    series = opts['series']
    assert 'label' not in series[0]
