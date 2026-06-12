"""Read-only catalog access core — shared by the REST blueprint and (server-side)
the data API. Wraps the existing read-only snapshot (catalog_inspector) + safe
query builder (query_builder); adds a guarded read-only SELECT runner."""
from __future__ import annotations

import os
import re
import threading

from ui.search.catalog_inspector import (get_connection as _get_connection,
                                          list_views as _list_views,
                                          get_view_meta as _view_meta,
                                          view_summary as _view_summary)
from ui.search.query_builder import Filter, build_sql

_SELECT_OK = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(attach|detach|copy|install|load|pragma|set|insert|update|delete|drop|"
    r"create|alter|export|import|call)\b", re.IGNORECASE)
DEFAULT_ROW_CAP = 5_000_000
SQL_TIMEOUT_SEC = 30
PARQUET_ROW_CAP = 5_000_000


def _guard(sql: str) -> str:
    s = sql.strip().rstrip(";")
    if ";" in s:
        raise ValueError("only a single statement is allowed")
    if not _SELECT_OK.match(s):
        raise ValueError("only SELECT / WITH queries are allowed")
    if _FORBIDDEN.search(s):
        raise ValueError("statement contains a forbidden keyword")
    return s


def safe_sql(sql: str, *, con, row_cap: int = DEFAULT_ROW_CAP):
    """Run a read-only SELECT on `con`. Returns (columns, rows). Raises ValueError
    on guard violation / timeout / row cap. `con` MUST be a read-only connection."""
    stmt = _guard(sql)
    result: dict = {}

    def _run():
        try:
            cur = con.execute(stmt)
            result["cols"] = [d[0] for d in cur.description]
            rows = cur.fetchmany(row_cap + 1)
            if len(rows) > row_cap:
                result["err"] = ValueError(f"result exceeds row cap ({row_cap})")
            else:
                result["rows"] = [list(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            result["err"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(SQL_TIMEOUT_SEC)
    if t.is_alive():
        try:
            con.interrupt()
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(f"query exceeded {SQL_TIMEOUT_SEC}s timeout")
    if "err" in result:
        e = result["err"]
        raise ValueError(str(e)) if not isinstance(e, ValueError) else e
    return result["cols"], result["rows"]


def list_views() -> list[dict]:
    return [_view_summary(v) for v in _list_views()]


def view_schema(view: str) -> dict:
    if view not in _list_views():
        raise ValueError(f"unknown view: {view!r}")
    m = _view_meta(view)
    return {"name": m.name, "row_count": m.row_count, "max_date": m.max_date,
            "columns": [{"name": c.name, "dtype": c.dtype, "is_date": c.is_date,
                         "is_numeric": c.is_numeric, "is_string": c.is_string}
                        for c in m.columns]}


def query(view: str, *, filters=None, select=None, order_by=None, order_dir="ASC",
          limit=1000, offset=0, con=None, max_limit=None):
    """Filtered read. filters: list of {column,op,value,value2}. Returns
    (columns, rows, next_offset). Pass con for tests; else opens a read-only one."""
    if view not in _list_views():
        raise ValueError(f"unknown view: {view!r}")
    flts = [Filter(column=f["column"], op=f["op"], value=f.get("value"),
                   value2=f.get("value2")) for f in (filters or [])]
    cap = max_limit if max_limit is not None else PARQUET_ROW_CAP
    page = max(1, int(limit))
    sql, params = build_sql(view, flts, order_by=order_by, order_dir=order_dir,
                            limit=page + 1, select_cols=select, max_limit=cap)
    if int(offset) > 0:
        sql += f" OFFSET {int(offset)}"
    own = con is None
    c = con or _get_connection()
    try:
        cur = c.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
    finally:
        if own:
            c.close()
    nxt = (int(offset) + page) if len(rows) > page else None
    return cols, rows[:page], nxt


def check_token(authorization_header: str | None) -> bool:
    """True if request is authorized. If QUANTDATA_API_TOKEN unset -> open (LAN/dev)."""
    want = os.environ.get("QUANTDATA_API_TOKEN")
    if not want:
        return True
    if not authorization_header:
        return False
    parts = authorization_header.split(None, 1)
    return len(parts) == 2 and parts[0].lower() == "bearer" and parts[1] == want
