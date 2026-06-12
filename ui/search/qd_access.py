"""Read-only catalog access core — shared by the REST blueprint and (server-side)
the data API. Wraps the existing read-only snapshot (catalog_inspector) + safe
query builder (query_builder); adds a guarded read-only SELECT runner."""
from __future__ import annotations

import re
import threading

_SELECT_OK = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(attach|detach|copy|install|load|pragma|set|insert|update|delete|drop|"
    r"create|alter|export|import|call)\b", re.IGNORECASE)
DEFAULT_ROW_CAP = 5_000_000
SQL_TIMEOUT_SEC = 30


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
