"""P0 unit tests for ui.search.query_builder.build_sql.

Covers U-032..041. build_sql calls list_views() and get_view_meta() which hit
the live catalog; we monkeypatch both with a single fake view for hermetic tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ui.search import query_builder as qb
from ui.search.query_builder import DEFAULT_LIMIT, MAX_LIMIT, Filter, build_sql


@dataclass
class _Col:
    name: str
    type: str = "VARCHAR"


@dataclass
class _Meta:
    name: str
    columns: list[_Col]


_FAKE_META = _Meta(
    name="my_view",
    columns=[_Col("date"), _Col("symbol"), _Col("value"), _Col("flag")],
)


@pytest.fixture(autouse=True)
def _stub_catalog(monkeypatch):
    """Stub list_views + get_view_meta so unit tests don't touch real DB."""
    monkeypatch.setattr(qb, "list_views", lambda: ["my_view"])
    monkeypatch.setattr(qb, "get_view_meta", lambda v: _FAKE_META if v == "my_view" else (_ for _ in ()).throw(ValueError(v)))
    yield


# ── happy paths ─────────────────────────────────────────────────────────────

def test_U032_empty_filters_select_star():
    sql, params = build_sql("my_view", [])
    assert sql.startswith("SELECT * FROM my_view")
    assert f"LIMIT {DEFAULT_LIMIT}" in sql
    assert params == []


def test_U033_two_filters_and_order_and_limit():
    sql, params = build_sql(
        "my_view",
        [Filter("symbol", "eq", "2330"), Filter("value", "range_min", 100)],
        order_by="date", order_dir="DESC", limit=50,
    )
    assert '"symbol" = ?' in sql
    assert '"value" >= ?' in sql
    assert " AND " in sql
    assert 'ORDER BY "date" DESC' in sql
    assert "LIMIT 50" in sql
    assert params == ["2330", 100]


@pytest.mark.parametrize("op, value, expect_param", [
    ("eq",        "2330", "2330"),
    ("contains",  "ts",   "%ts%"),
    ("range_min", 50,     50),
    ("range_max", 200,    200),
    ("date_from", "2026-01-01", "2026-01-01"),
    ("date_to",   "2026-12-31", "2026-12-31"),
])
def test_U035_each_op_emits_correct_clause(op, value, expect_param):
    sql, params = build_sql("my_view", [Filter("symbol", op, value)])
    assert params == [expect_param]


def test_U035_op_in_with_list_value():
    sql, params = build_sql("my_view", [Filter("symbol", "in", ["2330", "2317", "1101"])])
    assert '"symbol" IN (?,?,?)' in sql
    assert params == ["2330", "2317", "1101"]


def test_U035_op_in_with_scalar_wraps_as_list():
    sql, params = build_sql("my_view", [Filter("symbol", "in", "2330")])
    assert '"symbol" IN (?)' in sql
    assert params == ["2330"]


@pytest.mark.parametrize("op, expected_clause", [
    ("is_true",  '"flag" = TRUE'),
    ("is_false", '"flag" = FALSE'),
    ("isnull",   '"flag" IS NULL'),
    ("notnull",  '"flag" IS NOT NULL'),
])
def test_U035_zero_param_ops(op, expected_clause):
    sql, params = build_sql("my_view", [Filter("flag", op)])
    assert expected_clause in sql
    assert params == []


# ── security / whitelist ────────────────────────────────────────────────────

def test_U034_unknown_column_raises():
    with pytest.raises(ValueError, match="Unknown column"):
        build_sql("my_view", [Filter("nope", "eq", 1)])


def test_U034_injection_via_column_name_blocked():
    # Even a column name containing SQL fragments must fail whitelist
    with pytest.raises(ValueError, match="Unknown column"):
        build_sql("my_view", [Filter('"; DROP TABLE users; --', "eq", 1)])


def test_U036_unsupported_op_raises():
    with pytest.raises(ValueError, match="Unsupported op"):
        build_sql("my_view", [Filter("symbol", "GROUP BY date --", "x")])


def test_U034_unknown_view_raises():
    with pytest.raises(ValueError, match="Unknown view"):
        build_sql("not_a_view", [])


def test_U040_select_cols_whitelisted():
    sql, _ = build_sql("my_view", [], select_cols=["date", "value"])
    assert "SELECT" in sql and '"date"' in sql and '"value"' in sql


def test_U040_select_cols_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown column"):
        build_sql("my_view", [], select_cols=["date", "haxxor"])


# ── edge: limit clipping ────────────────────────────────────────────────────

def test_U037_limit_clipped_to_max():
    sql, _ = build_sql("my_view", [], limit=999_999)
    assert f"LIMIT {MAX_LIMIT}" in sql


def test_U038_limit_floor_at_one():
    # implementation does min(max(int(limit), 1), MAX_LIMIT)
    sql, _ = build_sql("my_view", [], limit=0)
    assert "LIMIT 1" in sql
    sql, _ = build_sql("my_view", [], limit=-100)
    assert "LIMIT 1" in sql


def test_U039_order_dir_normalised():
    # Impl is: dir_ = "DESC" if order_dir.upper() == "DESC" else "ASC"
    # i.e. ONLY exact 'DESC' (case-insensitive) → DESC; everything else → ASC
    sql, _ = build_sql("my_view", [], order_by="date", order_dir="DESC")
    assert 'ORDER BY "date" DESC' in sql
    sql, _ = build_sql("my_view", [], order_by="date", order_dir="desc")
    assert 'ORDER BY "date" DESC' in sql
    sql, _ = build_sql("my_view", [], order_by="date", order_dir="asc")
    assert 'ORDER BY "date" ASC' in sql
    sql, _ = build_sql("my_view", [], order_by="date", order_dir="UNKNOWN")
    assert 'ORDER BY "date" ASC' in sql  # fallback is ASC, not DESC


def test_U039_order_by_must_be_whitelisted():
    with pytest.raises(ValueError, match="Unknown column"):
        build_sql("my_view", [], order_by="haxxor")


# ── params type fidelity ────────────────────────────────────────────────────

def test_params_preserve_int_and_string():
    _, params = build_sql("my_view", [Filter("value", "range_min", 42)])
    assert params == [42] and isinstance(params[0], int)


def test_max_limit_override_allows_bulk(monkeypatch):
    import ui.search.query_builder as qb
    monkeypatch.setattr(qb, "list_views", lambda: ["bars_1d"])
    class _M:  # minimal ViewMeta stand-in for column validation
        columns = []
    monkeypatch.setattr(qb, "get_view_meta", lambda v: _M())
    sql, params = build_sql("bars_1d", [], limit=2_000_000, max_limit=5_000_000)
    assert "LIMIT 2000000" in sql
