"""
tests/test_custom_chart_query.py

Unit tests for services/custom_chart_query.py
"""

from __future__ import annotations

import sys
import os
from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure app/ is on the path when running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import services.custom_chart_query as cq


# ─────────────────────────────────────────────────────────────────────────────
# 1. get_available_sources returns sorted list
# ─────────────────────────────────────────────────────────────────────────────

def test_get_available_sources():
    sources = cq.get_available_sources()
    assert isinstance(sources, list)
    assert sources == sorted(sources)
    expected = sorted([
        'v_all_spend', 'v_credit_spend', 'v_debit_spend',
        'v_income', 'v_credit_payments',
    ])
    assert sources == expected


# ─────────────────────────────────────────────────────────────────────────────
# 2. Unknown source raises ValueError
# ─────────────────────────────────────────────────────────────────────────────

@patch('services.custom_chart_query.get_engine')
@patch('services.custom_chart_query.get_schema', return_value='public')
def test_validate_source_rejects_invalid(mock_schema, mock_engine):
    with pytest.raises(ValueError, match='Unknown data_source'):
        cq.execute_chart_query({'data_source': 'evil_table; DROP TABLE users;--'})


# ─────────────────────────────────────────────────────────────────────────────
# 3. Unknown x_column raises ValueError
# ─────────────────────────────────────────────────────────────────────────────

@patch('services.custom_chart_query.get_engine')
@patch('services.custom_chart_query.get_schema', return_value='public')
def test_validate_column_rejects_invalid(mock_schema, mock_engine):
    # Seed column cache so the test doesn't need a real DB
    cq._col_cache['v_all_spend'] = ['transaction_date', 'amount', 'category']

    with pytest.raises(ValueError, match='Unknown x_column'):
        cq.execute_chart_query({
            'data_source': 'v_all_spend',
            'x_column':    'evil_col; DROP TABLE users;--',
            'y_column':    'amount',
        })


# ─────────────────────────────────────────────────────────────────────────────
# 4. Unknown aggregation raises ValueError
# ─────────────────────────────────────────────────────────────────────────────

@patch('services.custom_chart_query.get_engine')
@patch('services.custom_chart_query.get_schema', return_value='public')
def test_validate_aggregation_rejects_invalid(mock_schema, mock_engine):
    cq._col_cache['v_all_spend'] = ['transaction_date', 'amount', 'category']

    with pytest.raises(ValueError, match='Unknown aggregation'):
        cq.execute_chart_query({
            'data_source': 'v_all_spend',
            'x_column':    'transaction_date',
            'y_column':    'amount',
            'y_agg':       'delete',
        })


# ─────────────────────────────────────────────────────────────────────────────
# 5. Invalid filter op is silently skipped
# ─────────────────────────────────────────────────────────────────────────────

@patch('services.custom_chart_query.get_engine')
@patch('services.custom_chart_query.get_schema', return_value='public')
def test_validate_filter_op_skips_invalid(mock_schema, mock_engine):
    cq._col_cache['v_all_spend'] = ['transaction_date', 'amount', 'category']

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchall.return_value = []
    mock_engine.return_value.connect.return_value = mock_conn

    result = cq.execute_chart_query({
        'data_source': 'v_all_spend',
        'x_column':    'transaction_date',
        'y_column':    'amount',
        'y_agg':       'sum',
        'filters': [
            {'column': 'amount', 'op': 'INJECT_ME', 'value': '100'},
        ],
    })

    # Should succeed and return empty data (no rows)
    assert result == {'x': [], 'series': {'amount': []}}

    # The executed SQL should NOT contain the invalid operator
    executed_sql = str(mock_conn.execute.call_args[0][0])
    assert 'INJECT_ME' not in executed_sql


# ─────────────────────────────────────────────────────────────────────────────
# 6. Simple query with no series column
# ─────────────────────────────────────────────────────────────────────────────

@patch('services.custom_chart_query.get_engine')
@patch('services.custom_chart_query.get_schema', return_value='public')
def test_simple_query_no_series(mock_schema, mock_engine):
    cq._col_cache['v_all_spend'] = ['transaction_date', 'amount', 'category']

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchall.return_value = [
        (date(2024, 1, 1), 100.0),
        (date(2024, 2, 1), 200.0),
    ]
    mock_engine.return_value.connect.return_value = mock_conn

    result = cq.execute_chart_query({
        'data_source': 'v_all_spend',
        'x_column':    'transaction_date',
        'y_column':    'amount',
        'y_agg':       'sum',
        'date_trunc':  'month',
    })

    assert result['x'] == ['Jan 2024', 'Feb 2024']
    assert 'amount' in result['series']
    assert result['series']['amount'] == [100.0, 200.0]


# ─────────────────────────────────────────────────────────────────────────────
# 7. Query with series column → pivot
# ─────────────────────────────────────────────────────────────────────────────

@patch('services.custom_chart_query.get_engine')
@patch('services.custom_chart_query.get_schema', return_value='public')
def test_query_with_series_column(mock_schema, mock_engine):
    cq._col_cache['v_all_spend'] = ['transaction_date', 'amount', 'category']

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchall.return_value = [
        (date(2024, 1, 1), 'Food', 50.0),
        (date(2024, 1, 1), 'Gas',  30.0),
    ]
    mock_engine.return_value.connect.return_value = mock_conn

    result = cq.execute_chart_query({
        'data_source':   'v_all_spend',
        'x_column':      'transaction_date',
        'y_column':      'amount',
        'y_agg':         'sum',
        'series_column': 'category',
        'date_trunc':    'month',
    })

    assert result['x'] == ['Jan 2024']
    assert 'Food' in result['series']
    assert 'Gas' in result['series']
    assert result['series']['Food'] == [50.0]
    assert result['series']['Gas']  == [30.0]


# ─────────────────────────────────────────────────────────────────────────────
# 8. DATE_TRUNC is used when x_column is transaction_date
# ─────────────────────────────────────────────────────────────────────────────

@patch('services.custom_chart_query.get_engine')
@patch('services.custom_chart_query.get_schema', return_value='public')
def test_date_trunc_formatting(mock_schema, mock_engine):
    cq._col_cache['v_all_spend'] = ['transaction_date', 'amount', 'category']

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchall.return_value = []
    mock_engine.return_value.connect.return_value = mock_conn

    cq.execute_chart_query({
        'data_source': 'v_all_spend',
        'x_column':    'transaction_date',
        'y_column':    'amount',
        'y_agg':       'sum',
        'date_trunc':  'month',
    })

    executed_sql = str(mock_conn.execute.call_args[0][0])
    assert 'DATE_TRUNC' in executed_sql
    assert 'month' in executed_sql
