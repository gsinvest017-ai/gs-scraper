"""Catalog introspection helpers.

Caches view metadata + column types so the UI doesn't hit DuckDB on every
request. Refresh by sending POST /api/refresh.
"""

from __future__ import annotations

import datetime as dt
import shutil
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "catalog" / "quant.duckdb"


@dataclass
class Column:
    name: str
    dtype: str
    sql_type: str  # raw DuckDB type
    is_date: bool = False
    is_numeric: bool = False
    is_string: bool = False
    is_bool: bool = False
    distinct_values: list[str] = field(default_factory=list)  # populated only for low-cardinality string cols


@dataclass
class ViewMeta:
    name: str
    row_count: int
    columns: list[Column]
    date_columns: list[str]
    numeric_columns: list[str]
    string_columns: list[str]
    max_date: str | None = None
    is_time_series: bool = False
    error: str | None = None


# ---- type classification ---------------------------------------------------

_DATE_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIME"}
_NUMERIC_TYPES = {
    "BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT",
    "UBIGINT", "UINTEGER", "USMALLINT", "UTINYINT",
    "DOUBLE", "REAL", "FLOAT", "DECIMAL",
}
_STRING_TYPES = {"VARCHAR", "TEXT", "STRING"}
_BOOL_TYPES = {"BOOLEAN", "BOOL"}


def classify(sql_type: str) -> Column:
    """Return a Column with type flags set."""
    up = sql_type.upper().split("(")[0].strip()
    # DuckDB sometimes prefixes with TIMESTAMP_TZ etc; normalize a bit
    if "TIMESTAMP" in up or up == "DATE" or up == "TIME":
        return Column(name="", dtype="date", sql_type=sql_type, is_date=True)
    if up in _NUMERIC_TYPES or up.startswith("DECIMAL"):
        return Column(name="", dtype="numeric", sql_type=sql_type, is_numeric=True)
    if up in _BOOL_TYPES:
        return Column(name="", dtype="bool", sql_type=sql_type, is_bool=True)
    if up in _STRING_TYPES or up.startswith("VARCHAR"):
        return Column(name="", dtype="string", sql_type=sql_type, is_string=True)
    # Fallback — treat as string for filter purposes
    return Column(name="", dtype="other", sql_type=sql_type, is_string=True)


# ---- catalog connection ----------------------------------------------------

_lock = threading.Lock()
_meta_cache: dict[str, ViewMeta] = {}
_views_cache: list[str] = []
_temp_catalog: Path | None = None


def _ensure_temp_catalog() -> Path:
    """Copy the live catalog to a tmp file we can open read-only."""
    global _temp_catalog
    tmp = REPO / "tmp" / "search_ui_catalog.duckdb"
    tmp.parent.mkdir(exist_ok=True)
    shutil.copy(CATALOG, tmp)
    _temp_catalog = tmp
    return tmp


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a read-only DuckDB connection to a temp-copy of the catalog.

    Using a temp copy means UI never contends with the live `duckdb -ui`
    session or builders writing to the catalog.
    """
    tmp = _temp_catalog or _ensure_temp_catalog()
    return duckdb.connect(str(tmp), read_only=True)


def refresh_catalog_copy():
    """Re-copy the catalog so the UI reflects latest writes."""
    global _meta_cache, _views_cache
    with _lock:
        _ensure_temp_catalog()
        _meta_cache = {}
        _views_cache = []


def list_views() -> list[str]:
    global _views_cache
    if _views_cache:
        return _views_cache
    con = get_connection()
    try:
        rows = con.execute("SHOW TABLES").fetchall()
        _views_cache = sorted([r[0] for r in rows])
    finally:
        con.close()
    return _views_cache


def get_view_meta(view: str, *, with_distinct: bool = False) -> ViewMeta:
    """Return ViewMeta with column types and (optionally) low-cardinality distinct values."""
    if view in _meta_cache and not with_distinct:
        return _meta_cache[view]

    con = get_connection()
    try:
        # column types
        cols_raw = con.execute(f"DESCRIBE {view}").fetchall()
        columns: list[Column] = []
        for name, sql_type, *_ in cols_raw:
            col = classify(sql_type)
            col.name = name
            columns.append(col)

        # row count + max date if any date column
        try:
            row_count = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        except Exception as e:
            return ViewMeta(name=view, row_count=0, columns=columns,
                            date_columns=[], numeric_columns=[], string_columns=[],
                            error=str(e))

        date_cols = [c.name for c in columns if c.is_date]
        numeric_cols = [c.name for c in columns if c.is_numeric]
        string_cols = [c.name for c in columns if c.is_string]

        max_date = None
        if date_cols:
            try:
                # prefer trading_date / date / fiscal_month if present
                preferred = next((c for c in ("trading_date", "date", "fiscal_month",
                                              "publish_date", "ex_date", "adjust_date")
                                  if c in date_cols), date_cols[0])
                max_date = con.execute(
                    f"SELECT max({preferred}) FROM {view}"
                ).fetchone()[0]
                max_date = str(max_date) if max_date else None
            except Exception:
                pass

        is_time_series = bool(date_cols) and bool(numeric_cols) and row_count > 1

        # low-cardinality distinct values for string cols (cap at 50)
        if with_distinct:
            for col in columns:
                if col.is_string and not col.distinct_values:
                    try:
                        # cheap: count distinct first
                        n = con.execute(
                            f'SELECT count(DISTINCT "{col.name}") FROM {view}'
                        ).fetchone()[0]
                        if n is not None and n <= 50:
                            vals = con.execute(
                                f'SELECT DISTINCT "{col.name}" FROM {view} '
                                f'WHERE "{col.name}" IS NOT NULL ORDER BY 1 LIMIT 50'
                            ).fetchall()
                            col.distinct_values = [str(v[0]) for v in vals if v[0] is not None]
                    except Exception:
                        pass

        meta = ViewMeta(
            name=view, row_count=row_count, columns=columns,
            date_columns=date_cols, numeric_columns=numeric_cols, string_columns=string_cols,
            max_date=max_date, is_time_series=is_time_series,
        )
        if not with_distinct:
            _meta_cache[view] = meta
        return meta
    finally:
        con.close()


def view_summary(view: str) -> dict:
    """Lightweight summary for the index page."""
    from qd_ingest.common.dataset_meta import get_meta
    meta = get_view_meta(view)
    data_source, long_description = get_meta(view)
    return {
        "name": meta.name,
        "row_count": meta.row_count,
        "max_date": meta.max_date,
        "n_columns": len(meta.columns),
        "is_time_series": meta.is_time_series,
        "error": meta.error,
        "data_source": data_source,
        "long_description": long_description,
    }
