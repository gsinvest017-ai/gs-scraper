"""P0 unit tests for ui.search.app CSV/JSON helpers.

Covers U-042..046:
- _csv_escape: None, commas, quotes, newlines, CJK
- _jsonify_cell: NaN/Inf, Timestamp, ISO datetime
"""
from __future__ import annotations

import math

import pandas as pd

from ui.search.app import _csv_escape, _jsonify_cell


# ── _csv_escape ─────────────────────────────────────────────────────────────

def test_U042_csv_escape_none_is_empty():
    assert _csv_escape(None) == ""


def test_U043_csv_escape_comma_wraps_in_quotes():
    assert _csv_escape("a,b") == '"a,b"'


def test_U043_csv_escape_newline_wraps_in_quotes():
    assert _csv_escape("line1\nline2") == '"line1\nline2"'
    assert _csv_escape("with\rCR") == '"with\rCR"'


def test_U043_csv_escape_internal_quote_doubled():
    # Standard CSV: " inside a quoted field becomes ""
    assert _csv_escape('he said "hi"') == '"he said ""hi"""'


def test_U043_csv_escape_simple_string_unquoted():
    # No special chars → no quoting (smaller CSV output)
    assert _csv_escape("hello") == "hello"
    assert _csv_escape("a b c") == "a b c"  # space alone doesn't trigger


def test_U044_csv_escape_cjk_preserved():
    # Traditional Chinese must pass through verbatim (utf-8 caller assumed)
    assert _csv_escape("台積電") == "台積電"
    assert _csv_escape("台積電,2330") == '"台積電,2330"'


def test_U042_csv_escape_numbers_stringified():
    assert _csv_escape(123) == "123"
    assert _csv_escape(3.14) == "3.14"
    assert _csv_escape(True) == "True"


# ── _jsonify_cell ───────────────────────────────────────────────────────────

def test_U046_jsonify_cell_nan_becomes_none():
    assert _jsonify_cell(float("nan")) is None


def test_U046_jsonify_cell_inf_becomes_none():
    assert _jsonify_cell(float("inf")) is None
    assert _jsonify_cell(float("-inf")) is None


def test_U046_jsonify_cell_none_passthrough():
    assert _jsonify_cell(None) is None


def test_U045_jsonify_cell_timestamp_midnight_is_date_string():
    ts = pd.Timestamp("2026-05-29")
    out = _jsonify_cell(ts)
    assert out == "2026-05-29"


def test_U045_jsonify_cell_timestamp_with_time_is_isoformat():
    ts = pd.Timestamp("2026-05-29 14:30:00")
    out = _jsonify_cell(ts)
    # The current implementation checks `hour == 0 and minute == 0` (so 14:30 → iso)
    assert out.startswith("2026-05-29")
    assert "14:30" in out or "T" in out


def test_U042_jsonify_cell_primitives_passthrough():
    assert _jsonify_cell(42) == 42
    assert _jsonify_cell(3.14) == 3.14
    assert _jsonify_cell("hello") == "hello"
    assert _jsonify_cell(True) is True
