"""Safe SQL builder: turns form filters into a parameterized SELECT.

Inputs come from the browser; we never trust them. We:
- Whitelist view names against `list_views()` (so injection via view name impossible)
- Whitelist column names against the view's columns
- All values pass through DuckDB's `executemany`-style params (no string concat)
- Cap LIMIT at 5000
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog_inspector import ViewMeta, get_view_meta, list_views

MAX_LIMIT = 5000
DEFAULT_LIMIT = 1000


@dataclass
class Filter:
    column: str
    op: str   # 'eq' / 'contains' / 'in' / 'range_min' / 'range_max' / 'date_from' / 'date_to' / 'is_true' / 'is_false' / 'isnull' / 'notnull'
    value: Any | None = None  # str / number / list / date
    value2: Any | None = None # for range / between


def _validate_column(meta: ViewMeta, column: str) -> str:
    """Return the column name verbatim if it exists in the view, else raise."""
    valid = {c.name for c in meta.columns}
    if column not in valid:
        raise ValueError(f"Unknown column: {column!r} in {meta.name}")
    return column


def build_sql(
    view: str,
    filters: list[Filter],
    *,
    order_by: str | None = None,
    order_dir: str = "DESC",
    limit: int = DEFAULT_LIMIT,
    select_cols: list[str] | None = None,
    max_limit: int = MAX_LIMIT,
) -> tuple[str, list[Any]]:
    """Return (sql, params) for a safe parameterized query.

    Raises ValueError on any whitelist violation (caller maps to 400 response).
    """
    if view not in list_views():
        raise ValueError(f"Unknown view: {view!r}")
    meta = get_view_meta(view)

    if select_cols:
        for c in select_cols:
            _validate_column(meta, c)
        col_expr = ", ".join(f'"{c}"' for c in select_cols)
    else:
        col_expr = "*"

    where_parts: list[str] = []
    params: list[Any] = []

    for f in filters:
        col = _validate_column(meta, f.column)
        op = f.op
        if op == "eq":
            where_parts.append(f'"{col}" = ?')
            params.append(f.value)
        elif op == "contains":
            where_parts.append(f'CAST("{col}" AS VARCHAR) ILIKE ?')
            params.append(f"%{f.value}%")
        elif op == "in":
            vals = f.value if isinstance(f.value, list) else [f.value]
            placeholders = ",".join(["?"] * len(vals))
            where_parts.append(f'"{col}" IN ({placeholders})')
            params.extend(vals)
        elif op == "range_min":
            where_parts.append(f'"{col}" >= ?')
            params.append(f.value)
        elif op == "range_max":
            where_parts.append(f'"{col}" <= ?')
            params.append(f.value)
        elif op == "date_from":
            where_parts.append(f'"{col}" >= ?')
            params.append(f.value)
        elif op == "date_to":
            where_parts.append(f'"{col}" <= ?')
            params.append(f.value)
        elif op == "is_true":
            where_parts.append(f'"{col}" = TRUE')
        elif op == "is_false":
            where_parts.append(f'"{col}" = FALSE')
        elif op == "isnull":
            where_parts.append(f'"{col}" IS NULL')
        elif op == "notnull":
            where_parts.append(f'"{col}" IS NOT NULL')
        else:
            raise ValueError(f"Unsupported op: {op!r}")

    where = " AND ".join(where_parts)
    where_clause = f"WHERE {where}" if where else ""

    order_clause = ""
    if order_by:
        _validate_column(meta, order_by)
        dir_ = "DESC" if order_dir.upper() == "DESC" else "ASC"
        order_clause = f'ORDER BY "{order_by}" {dir_}'

    # Cap limit
    lim = min(max(int(limit), 1), max_limit)
    sql = f'SELECT {col_expr} FROM {view} {where_clause} {order_clause} LIMIT {lim}'
    return sql, params
