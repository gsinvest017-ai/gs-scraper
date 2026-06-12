# QUANTDATA Data API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose all 77 catalog views to external projects via a dual-track interface — a local zero-copy Python client and a token-authed REST API — both reusing the existing read-only catalog snapshot.

**Architecture:** A thin `qd_access` core wraps the existing `catalog_inspector` (read-only DuckDB snapshot + list/schema) and `query_builder` (filter→safe SQL), and adds a guarded read-only `safe_sql`. A Flask blueprint exposes `/api/v1/{views,data,sql}` (bearer-token gated). A standalone pip-installable `quantdata` client auto-detects local DuckDB vs REST and returns pandas DataFrames either way.

**Tech Stack:** Python 3.12, Flask (existing app), DuckDB (read-only), pandas, pyarrow, requests; pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-quantdata-data-api-design.md`

**Branch:** `feat/data-api` (already created off main).

---

## File Structure

- Create `ui/search/qd_access.py` — read-only catalog access core (`list_views`, `view_schema`, `query`, `safe_sql`).
- Create `ui/search/api_catalog.py` — Flask blueprint `/api/v1` catalog endpoints + token gate.
- Modify `ui/search/query_builder.py` — add optional `max_limit` override so bulk/parquet can exceed the UI's 5000 cap.
- Modify `ui/search/app.py` — register the catalog blueprint.
- Modify `ui/search/openapi_spec.py` — document the new endpoints.
- Create `quantdata/__init__.py`, `quantdata/client.py` — standalone client (local + remote).
- Create `quantdata/pyproject.toml` — make the client pip-installable on its own.
- Create tests: `tests/test_qd_access.py`, `tests/test_api_catalog.py`, `tests/test_quantdata_client.py`.
- Create `docs/quantdata-client.md` — consumer guide.

---

## Task 1: `query_builder` — add `max_limit` override

**Files:**
- Modify: `ui/search/query_builder.py`
- Test: `tests/test_query_builder.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_query_builder.py
from ui.search.query_builder import build_sql

def test_max_limit_override_allows_bulk(monkeypatch):
    import ui.search.query_builder as qb
    monkeypatch.setattr(qb, "list_views", lambda: ["bars_1d"])
    class _M:  # minimal ViewMeta stand-in for column validation
        columns = []
    monkeypatch.setattr(qb, "get_view_meta", lambda v: _M())
    sql, params = build_sql("bars_1d", [], limit=2_000_000, max_limit=5_000_000)
    assert "LIMIT 2000000" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_query_builder.py::test_max_limit_override_allows_bulk -v`
Expected: FAIL — `build_sql()` got unexpected keyword argument `max_limit` (or LIMIT capped at 5000).

- [ ] **Step 3: Implement**

In `ui/search/query_builder.py`, change the `build_sql` signature to add `max_limit: int = MAX_LIMIT` (keyword-only, after `select_cols`), and change the cap line:

```python
    lim = min(max(int(limit), 1), max_limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_query_builder.py -v`
Expected: PASS (existing tests still pass — default `max_limit=MAX_LIMIT` preserves behavior).

- [ ] **Step 5: Commit**

```bash
git add ui/search/query_builder.py tests/test_query_builder.py
git commit -m "feat: query_builder 加 max_limit override 供 bulk 匯出"
```

---

## Task 2: `qd_access.safe_sql` — guarded read-only SELECT

**Files:**
- Create: `ui/search/qd_access.py`
- Test: `tests/test_qd_access.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qd_access.py
import duckdb, pytest
from ui.search import qd_access as qa

@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1,'a'),(2,'b')) v(id,name)")
    return c

def test_safe_sql_select_ok(con):
    cols, rows = qa.safe_sql("SELECT id,name FROM t ORDER BY id", con=con, row_cap=10)
    assert cols == ["id", "name"] and rows == [[1, "a"], [2, "b"]]

@pytest.mark.parametrize("bad", [
    "INSERT INTO t VALUES (3,'c')", "UPDATE t SET id=9", "DELETE FROM t",
    "DROP TABLE t", "ATTACH 'x.db'", "COPY t TO 'x.csv'", "PRAGMA database_list",
    "INSTALL httpfs", "SELECT 1; SELECT 2", "CREATE TABLE z(x int)",
])
def test_safe_sql_rejects_non_select(con, bad):
    with pytest.raises(ValueError):
        qa.safe_sql(bad, con=con, row_cap=10)

def test_safe_sql_row_cap(con):
    with pytest.raises(ValueError, match="row cap"):
        qa.safe_sql("SELECT * FROM range(100)", con=con, row_cap=10)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_qd_access.py -v`
Expected: FAIL — module `qd_access` does not exist.

- [ ] **Step 3: Implement `safe_sql`**

```python
# ui/search/qd_access.py
"""Read-only catalog access core — shared by the REST blueprint and (server-side)
the data API. Wraps the existing read-only snapshot (catalog_inspector) + safe
query builder (query_builder); adds a guarded read-only SELECT runner."""
from __future__ import annotations

import re
import threading

_SELECT_OK = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
# reject anything that could write / escape the sandbox even on a read-only conn
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
            # fetch one over the cap to detect overflow without materializing huge sets
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_qd_access.py -v`
Expected: PASS (all guard rejections + row cap + happy path).

- [ ] **Step 5: Commit**

```bash
git add ui/search/qd_access.py tests/test_qd_access.py
git commit -m "feat: qd_access.safe_sql 唯讀 SELECT guard（拒寫入/多語句/逾時/row cap）"
```

---

## Task 3: `qd_access` — list/schema/query wrappers + cursor

**Files:**
- Modify: `ui/search/qd_access.py`
- Test: `tests/test_qd_access.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_qd_access.py
from types import SimpleNamespace

def test_query_builds_and_runs(monkeypatch, con):
    import ui.search.qd_access as qa2
    import ui.search.query_builder as qb
    fake_meta = SimpleNamespace(columns=[SimpleNamespace(name="id"),
                                         SimpleNamespace(name="name")])
    monkeypatch.setattr(qa2, "_list_views", lambda: ["t"])
    monkeypatch.setattr(qa2, "_view_meta", lambda v: fake_meta)
    # build_sql validates independently against query_builder's own helpers:
    monkeypatch.setattr(qb, "list_views", lambda: ["t"])
    monkeypatch.setattr(qb, "get_view_meta", lambda v: fake_meta)
    cols, rows, nxt = qa2.query("t", filters=[{"column": "id", "op": "eq", "value": 1}],
                                con=con, limit=10, offset=0)
    assert cols == ["id", "name"] and rows == [[1, "a"]] and nxt is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_qd_access.py::test_query_builds_and_runs -v`
Expected: FAIL — `query` not defined.

- [ ] **Step 3: Implement**

Append to `ui/search/qd_access.py`:

```python
from ui.search.catalog_inspector import (get_connection as _get_connection,
                                          list_views as _list_views,
                                          get_view_meta as _view_meta,
                                          view_summary as _view_summary)
from ui.search.query_builder import Filter, build_sql

PARQUET_ROW_CAP = 5_000_000


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
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_qd_access.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/search/qd_access.py tests/test_qd_access.py
git commit -m "feat: qd_access list_views/view_schema/query（offset 分頁，重用 query_builder）"
```

---

## Task 4: Token-auth gate

**Files:**
- Modify: `ui/search/qd_access.py` (add `check_token`)
- Test: `tests/test_qd_access.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_qd_access.py
def test_check_token(monkeypatch):
    import ui.search.qd_access as qa3
    monkeypatch.delenv("QUANTDATA_API_TOKEN", raising=False)
    assert qa3.check_token(None) is True          # no token set -> open
    monkeypatch.setenv("QUANTDATA_API_TOKEN", "secret")
    assert qa3.check_token("Bearer secret") is True
    assert qa3.check_token("Bearer wrong") is False
    assert qa3.check_token(None) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_qd_access.py::test_check_token -v`
Expected: FAIL — `check_token` not defined.

- [ ] **Step 3: Implement**

Append to `ui/search/qd_access.py`:

```python
import os

def check_token(authorization_header: str | None) -> bool:
    """True if request is authorized. If QUANTDATA_API_TOKEN unset -> open (LAN/dev)."""
    want = os.environ.get("QUANTDATA_API_TOKEN")
    if not want:
        return True
    if not authorization_header:
        return False
    parts = authorization_header.split(None, 1)
    return len(parts) == 2 and parts[0].lower() == "bearer" and parts[1] == want
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_qd_access.py::test_check_token -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/search/qd_access.py tests/test_qd_access.py
git commit -m "feat: qd_access.check_token bearer 認證（env 未設則開放）"
```

---

## Task 5: Catalog REST blueprint

**Files:**
- Create: `ui/search/api_catalog.py`
- Test: `tests/test_api_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_catalog.py
import io, duckdb, pytest, pandas as pd
from types import SimpleNamespace

@pytest.fixture
def client(monkeypatch):
    from ui.search import qd_access as qa
    monkeypatch.setattr(qa, "_list_views", lambda: ["t"])
    monkeypatch.setattr(qa, "list_views", lambda: [{"name": "t", "row_count": 2}])
    monkeypatch.setattr(qa, "view_schema", lambda v: {"name": "t", "columns": [{"name": "id"}]})
    def fake_query(view, **k):
        return ["id", "name"], [[1, "a"], [2, "b"]], None
    monkeypatch.setattr(qa, "query", fake_query)
    monkeypatch.setattr(qa, "safe_sql", lambda sql, **k: (["n"], [[1]]))
    monkeypatch.setattr(qa, "_get_connection", lambda: duckdb.connect(":memory:"))
    from ui.search.app import app
    app.config["TESTING"] = True
    return app.test_client()

def test_views(client):
    r = client.get("/api/v1/views")
    assert r.status_code == 200 and r.get_json()[0]["name"] == "t"

def test_data_json(client):
    r = client.get("/api/v1/data/t?format=json")
    b = r.get_json()
    assert b["columns"] == ["id", "name"] and len(b["rows"]) == 2

def test_data_parquet(client):
    r = client.get("/api/v1/data/t?format=parquet")
    assert r.status_code == 200
    df = pd.read_parquet(io.BytesIO(r.data))
    assert list(df.columns) == ["id", "name"] and len(df) == 2

def test_sql_json(client):
    r = client.post("/api/v1/sql", json={"sql": "SELECT 1 n"})
    assert r.status_code == 200 and r.get_json()["columns"] == ["n"]

def test_token_401(client, monkeypatch):
    monkeypatch.setenv("QUANTDATA_API_TOKEN", "secret")
    assert client.get("/api/v1/views").status_code == 401
    assert client.get("/api/v1/views", headers={"Authorization": "Bearer secret"}).status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_catalog.py -v`
Expected: FAIL — blueprint not registered / 404.

- [ ] **Step 3: Implement the blueprint**

```python
# ui/search/api_catalog.py
"""Catalog data REST — /api/v1/{views,data,sql}. Token-gated (catalog only;
realtime endpoints in api_v1.py stay open). Read-only over the catalog snapshot."""
from __future__ import annotations

import io
from flask import Blueprint, Response, abort, jsonify, request

import pyarrow as pa
import pyarrow.parquet as pq

from ui.search import qd_access as qa

bp = Blueprint("api_catalog", __name__, url_prefix="/api/v1")

_FILTER_OPS = {"": "eq", "gte": "range_min", "lte": "range_max", "in": "in",
               "contains": "contains"}


@bp.before_request
def _auth():
    if not qa.check_token(request.headers.get("Authorization")):
        abort(401, description="invalid or missing bearer token")


@bp.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.errorhandler(ValueError)
def _bad(e):
    return jsonify({"error": str(e)}), 400


@bp.route("/views")
def views():
    return jsonify(qa.list_views())


@bp.route("/views/<view>/schema")
def schema(view):
    return jsonify(qa.view_schema(view))


def _parse_filters(args) -> list[dict]:
    """col=val -> eq; col__gte / col__lte / col__in / col__contains; start/end on date col."""
    out = []
    reserved = {"format", "select", "order", "dir", "limit", "offset", "start", "end"}
    for key in args:
        if key in reserved:
            continue
        col, _, suf = key.partition("__")
        op = _FILTER_OPS.get(suf)
        if op is None:
            continue
        val = args.getlist(key) if op == "in" else args.get(key)
        if op == "in" and len(val) == 1:
            val = val[0].split(",")
        out.append({"column": col, "op": op, "value": val})
    return out


@bp.route("/data/<view>")
def data(view):
    fmt = request.args.get("format", "json")
    filters = _parse_filters(request.args)
    for bound, op in (("start", "date_from"), ("end", "date_to")):
        v = request.args.get(bound)
        if v:
            sch = qa.view_schema(view)
            datecol = next((c["name"] for c in sch["columns"] if c.get("is_date")), None)
            if datecol:
                filters.append({"column": datecol, "op": op, "value": v})
    select = (request.args.get("select") or "").split(",") if request.args.get("select") else None
    limit = int(request.args.get("limit") or (qa.PARQUET_ROW_CAP if fmt == "parquet" else 1000))
    offset = int(request.args.get("offset") or 0)
    cols, rows, nxt = qa.query(view, filters=filters, select=select,
                               order_by=request.args.get("order"),
                               order_dir=request.args.get("dir", "ASC"),
                               limit=limit, offset=offset)
    if fmt == "json":
        return jsonify({"view": view, "columns": cols, "rows": rows, "next_offset": nxt})
    import pandas as pd
    df = pd.DataFrame(rows, columns=cols)
    if fmt == "csv":
        return Response(df.to_csv(index=False), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{view}.csv"'})
    if fmt == "parquet":
        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf, compression="zstd")
        return Response(buf.getvalue(), mimetype="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{view}.parquet"'})
    raise ValueError(f"unknown format: {fmt}")


@bp.route("/sql", methods=["POST"])
def sql():
    payload = request.get_json(force=True, silent=True) or {}
    q = payload.get("sql")
    if not q:
        return jsonify({"error": "missing sql"}), 400
    fmt = payload.get("format", "json")
    con = qa._get_connection()
    try:
        cols, rows = qa.safe_sql(q, con=con)
    finally:
        con.close()
    if fmt == "json":
        return jsonify({"columns": cols, "rows": rows})
    import pandas as pd
    df = pd.DataFrame(rows, columns=cols)
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf, compression="zstd")
    return Response(buf.getvalue(), mimetype="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="query.parquet"'})
```

- [ ] **Step 4 (depends on Task 6 registration): defer running until blueprint registered.** Proceed to Task 6, then run.

- [ ] **Step 5: Commit**

```bash
git add ui/search/api_catalog.py tests/test_api_catalog.py
git commit -m "feat: /api/v1 catalog REST blueprint（views/data/sql + token gate）"
```

---

## Task 6: Register blueprint in app

**Files:**
- Modify: `ui/search/app.py:41-43`

- [ ] **Step 1: Implement registration**

After the existing `app.register_blueprint(api_v1_bp)` lines in `ui/search/app.py`, add:

```python
from ui.search.api_catalog import bp as api_catalog_bp  # noqa: E402
app.register_blueprint(api_catalog_bp)
```

- [ ] **Step 2: Run the Task-5 tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_catalog.py -v`
Expected: PASS (views/data json+parquet/sql/token 401).

- [ ] **Step 3: Commit**

```bash
git add ui/search/app.py
git commit -m "feat: 註冊 api_catalog blueprint 進 Flask app"
```

---

## Task 7: OpenAPI spec — document catalog endpoints

**Files:**
- Modify: `ui/search/openapi_spec.py`
- Test: `tests/test_api_catalog.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_api_catalog.py
def test_openapi_lists_catalog_paths(client):
    spec = client.get("/api/v1/openapi.json").get_json()
    for p in ("/views", "/data/{view}", "/sql"):
        assert p in spec["paths"], f"{p} missing from OpenAPI"
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_catalog.py::test_openapi_lists_catalog_paths -v`
Expected: FAIL — catalog paths absent.

- [ ] **Step 3: Implement**

In `ui/search/openapi_spec.py`, inside `build_spec()` where the `paths` dict is assembled, add entries:

```python
    paths["/views"] = {"get": {"summary": "List all catalog views + metadata",
                               "responses": {"200": {"description": "view list"}}}}
    paths["/views/{view}/schema"] = {"get": {"summary": "Column schema for a view",
        "parameters": [{"name": "view", "in": "path", "required": True,
                        "schema": {"type": "string"}}],
        "responses": {"200": {"description": "schema"}}}}
    paths["/data/{view}"] = {"get": {"summary": "Filtered read of a view",
        "parameters": [
            {"name": "view", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "format", "in": "query", "schema": {"type": "string",
                "enum": ["json", "csv", "parquet"]}},
            {"name": "select", "in": "query", "schema": {"type": "string"}},
            {"name": "order", "in": "query", "schema": {"type": "string"}},
            {"name": "limit", "in": "query", "schema": {"type": "integer"}},
            {"name": "offset", "in": "query", "schema": {"type": "integer"}},
            {"name": "start", "in": "query", "schema": {"type": "string"}},
            {"name": "end", "in": "query", "schema": {"type": "string"}}],
        "responses": {"200": {"description": "rows / file"}}}}
    paths["/sql"] = {"post": {"summary": "Read-only SELECT over the catalog",
        "requestBody": {"content": {"application/json": {"schema": {"type": "object",
            "properties": {"sql": {"type": "string"}, "format": {"type": "string"}}}}}},
        "responses": {"200": {"description": "rows / file"}}}}
```

(If `build_spec` builds `paths` as a literal, merge these keys into it instead.)

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_catalog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/search/openapi_spec.py tests/test_api_catalog.py
git commit -m "docs: OpenAPI 收錄 catalog endpoints（Swagger 可見）"
```

---

## Task 8: `quantdata` client — local (DuckDB) transport

**Files:**
- Create: `quantdata/__init__.py`, `quantdata/client.py`, `quantdata/pyproject.toml`
- Test: `tests/test_quantdata_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quantdata_client.py
import duckdb, pandas as pd, pytest
from pathlib import Path
from quantdata import QuantData

@pytest.fixture
def local_db(tmp_path):
    p = tmp_path / "q.duckdb"
    c = duckdb.connect(str(p))
    c.execute("CREATE VIEW bars AS SELECT * FROM (VALUES "
              "(DATE '2024-01-01','2330',100.0),(DATE '2024-01-02','2330',101.0)) v(d,symbol,close)")
    c.close()
    return p

def test_local_get(local_db):
    qd = QuantData(catalog=local_db)
    df = qd.get("bars", symbol="2330")
    assert isinstance(df, pd.DataFrame) and len(df) == 2 and "close" in df.columns

def test_local_sql(local_db):
    qd = QuantData(catalog=local_db)
    df = qd.sql("SELECT count(*) n FROM bars")
    assert df.iloc[0]["n"] == 2

def test_local_sql_rejects_write(local_db):
    qd = QuantData(catalog=local_db)
    with pytest.raises(ValueError):
        qd.sql("DROP VIEW bars")
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_quantdata_client.py -v`
Expected: FAIL — `quantdata` not importable.

- [ ] **Step 3: Implement local mode**

```python
# quantdata/__init__.py
from quantdata.client import QuantData, QuantDataError, AuthError, APIError
__all__ = ["QuantData", "QuantDataError", "AuthError", "APIError"]
```

```python
# quantdata/client.py
"""QuantData client — auto-detect local DuckDB (zero-copy) vs REST. Returns pandas."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd

class QuantDataError(Exception): ...
class AuthError(QuantDataError): ...
class APIError(QuantDataError): ...

_SELECT_OK = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(r"\b(attach|detach|copy|install|load|pragma|set|insert|update|"
                        r"delete|drop|create|alter|export|import|call)\b", re.IGNORECASE)

def _guard(sql: str) -> str:
    s = sql.strip().rstrip(";")
    if ";" in s or not _SELECT_OK.match(s) or _FORBIDDEN.search(s):
        raise ValueError("only a single read-only SELECT/WITH statement is allowed")
    return s


class QuantData:
    def __init__(self, catalog=None, url=None, token=None):
        self.url = url or os.environ.get("QUANTDATA_API_URL")
        self.token = token or os.environ.get("QUANTDATA_API_TOKEN")
        cat = catalog or os.environ.get("QUANTDATA_CATALOG")
        if cat is None and not self.url:
            default = Path(__file__).resolve().parents[1] / "catalog" / "quant.duckdb"
            cat = default if default.exists() else None
        self.catalog = Path(cat) if cat else None
        if self.catalog is None and not self.url:
            raise QuantDataError("no transport: pass catalog= (local) or url= (remote), "
                                 "or set QUANTDATA_CATALOG / QUANTDATA_API_URL")
        self._mode = "local" if (self.catalog and self.url is None) else "remote"

    # ---- local helpers ----
    def _con(self):
        import duckdb
        return duckdb.connect(str(self.catalog), read_only=True)

    def get(self, view, *, select=None, order=None, dir="ASC", limit=None,
            start=None, end=None, **filters) -> pd.DataFrame:
        if self._mode == "local":
            cols = ", ".join(f'"{c}"' for c in select) if select else "*"
            where, params = [], []
            for k, v in filters.items():
                where.append(f'"{k}" = ?'); params.append(v)
            con = self._con()
            try:
                datecol = None
                if start or end:
                    datecol = next((r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()
                                    if "DATE" in str(r[1]).upper() or "TIMESTAMP" in str(r[1]).upper()), None)
                if start and datecol: where.append(f'"{datecol}" >= ?'); params.append(start)
                if end and datecol: where.append(f'"{datecol}" <= ?'); params.append(end)
                sql = f"SELECT {cols} FROM {view}"
                if where: sql += " WHERE " + " AND ".join(where)
                if order: sql += f' ORDER BY "{order}" {"DESC" if dir.upper()=="DESC" else "ASC"}'
                if limit: sql += f" LIMIT {int(limit)}"
                return con.execute(sql, params).df()
            finally:
                con.close()
        return self._remote_get(view, select=select, order=order, dir=dir, limit=limit,
                                start=start, end=end, **filters)

    def sql(self, query: str) -> pd.DataFrame:
        if self._mode == "local":
            con = self._con()
            try:
                return con.execute(_guard(query)).df()
            finally:
                con.close()
        return self._remote_sql(query)

    def views(self) -> pd.DataFrame:
        if self._mode == "local":
            con = self._con()
            try:
                return con.execute("SHOW TABLES").df()
            finally:
                con.close()
        return pd.DataFrame(self._remote_json("GET", "/views"))

    def schema(self, view: str) -> pd.DataFrame:
        if self._mode == "local":
            con = self._con()
            try:
                return con.execute(f"DESCRIBE {view}").df()
            finally:
                con.close()
        return pd.DataFrame(self._remote_json("GET", f"/views/{view}/schema")["columns"])
```

- [ ] **Step 4: Add the package manifest**

```toml
# quantdata/pyproject.toml
[project]
name = "quantdata"
version = "0.1.0"
description = "QUANTDATA client — local DuckDB or REST, returns pandas"
requires-python = ">=3.10"
dependencies = ["pandas", "pyarrow", "requests"]
# duckdb is only needed for local mode; install with: pip install quantdata[local]

[project.optional-dependencies]
local = ["duckdb"]

[tool.setuptools]
packages = ["quantdata"]
```

- [ ] **Step 5: Run to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_quantdata_client.py -v`
Expected: PASS (local get/sql/guard).

- [ ] **Step 6: Commit**

```bash
git add quantdata/ tests/test_quantdata_client.py
git commit -m "feat: quantdata client local DuckDB transport（get/sql/views/schema）"
```

---

## Task 9: `quantdata` client — remote (REST) transport + `.live`

**Files:**
- Modify: `quantdata/client.py`
- Test: `tests/test_quantdata_client.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_quantdata_client.py
import io, pyarrow as pa, pyarrow.parquet as pq

class _Resp:
    def __init__(self, status, content=b"", js=None):
        self.status_code = status; self.content = content; self._js = js
    def json(self): return self._js
    @property
    def text(self): return str(self._js)

def test_remote_get(monkeypatch):
    df0 = pd.DataFrame({"d": ["2024-01-01"], "close": [100.0]})
    buf = io.BytesIO(); pq.write_table(pa.Table.from_pandas(df0, preserve_index=False), buf)
    def fake_get(url, headers=None, params=None, timeout=None):
        assert "/data/bars" in url and params["format"] == "parquet"
        return _Resp(200, content=buf.getvalue())
    import quantdata.client as cl
    monkeypatch.setattr(cl.requests, "get", fake_get)
    qd = cl.QuantData(url="http://x:5050", token="t")
    df = qd.get("bars", symbol="2330")
    assert list(df.columns) == ["d", "close"] and len(df) == 1

def test_remote_401(monkeypatch):
    import quantdata.client as cl
    monkeypatch.setattr(cl.requests, "get", lambda *a, **k: _Resp(401, js={"error": "nope"}))
    qd = cl.QuantData(url="http://x:5050", token="bad")
    with pytest.raises(cl.AuthError):
        qd.views()
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_quantdata_client.py::test_remote_get -v`
Expected: FAIL — `_remote_get` not defined / `requests` not imported.

- [ ] **Step 3: Implement remote methods**

Add `import io` and `import requests` at the top of `quantdata/client.py`, then add these methods to `QuantData`:

```python
    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _check(self, r):
        if r.status_code == 401:
            raise AuthError("unauthorized — check QUANTDATA_API_TOKEN")
        if r.status_code >= 400:
            try: msg = r.json().get("error", r.text)
            except Exception: msg = r.text
            raise APIError(f"HTTP {r.status_code}: {msg}")
        return r

    def _remote_json(self, method, path, **kw):
        fn = requests.get if method == "GET" else requests.post
        r = self._check(fn(self.url.rstrip("/") + "/api/v1" + path,
                           headers=self._headers(), timeout=60, **kw))
        return r.json()

    def _remote_get(self, view, *, select=None, order=None, dir="ASC", limit=None,
                    start=None, end=None, **filters):
        params = {"format": "parquet"}
        params.update({k: v for k, v in filters.items()})
        if select: params["select"] = ",".join(select)
        if order: params["order"] = order; params["dir"] = dir
        if limit: params["limit"] = int(limit)
        if start: params["start"] = start
        if end: params["end"] = end
        r = self._check(requests.get(self.url.rstrip("/") + f"/api/v1/data/{view}",
                                     headers=self._headers(), params=params, timeout=300))
        return pd.read_parquet(io.BytesIO(r.content))

    def _remote_sql(self, query):
        r = self._check(requests.post(self.url.rstrip("/") + "/api/v1/sql",
                                      headers=self._headers(),
                                      json={"sql": query, "format": "parquet"}, timeout=300))
        return pd.read_parquet(io.BytesIO(r.content))

    @property
    def live(self):
        return _Live(self)


class _Live:
    """Wraps the existing realtime /api/v1 endpoints (remote URL required)."""
    def __init__(self, qd): self._qd = qd
    def _base(self):
        if not self._qd.url:
            raise QuantDataError("live data needs url= (realtime API is REST-only)")
        return self._qd.url.rstrip("/") + "/api/v1"
    def snapshot(self, symbols):
        r = requests.get(self._base() + "/snapshot", headers=self._qd._headers(),
                         params={"symbols": ",".join(symbols)}, timeout=30)
        return self._qd._check(r).json()
    def health(self):
        return self._qd._check(requests.get(self._base() + "/health",
                                            headers=self._qd._headers(), timeout=15)).json()
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_quantdata_client.py -v`
Expected: PASS (local + remote + 401).

- [ ] **Step 5: Commit**

```bash
git add quantdata/client.py tests/test_quantdata_client.py
git commit -m "feat: quantdata client remote REST transport + .live（snapshot/health）"
```

---

## Task 10: Consumer docs + full suite + smoke

**Files:**
- Create: `docs/quantdata-client.md`
- Modify: `.gitignore` if needed (none expected)

- [ ] **Step 1: Write the consumer guide**

Create `docs/quantdata-client.md` with: install (local `pip install -e .`, remote `pip install git+…`), env vars (`QUANTDATA_CATALOG` / `QUANTDATA_API_URL` / `QUANTDATA_API_TOKEN`), the `QuantData` examples (get/sql/views/schema/live), the REST contract table (from the spec), and a note that bulk pulls use parquet. Include a worked `gs-zipline-tej` example:

```python
from quantdata import QuantData
qd = QuantData()                       # local on this host
bars = qd.get("bars_1d", symbol="2330", start="2015-01-01")   # → DataFrame for a bundle
m1b = qd.get("tw_money_supply_monthly", series="m1b_eop")
```

- [ ] **Step 2: Run the FULL test suite**

Run: `PYTHONPATH=src:. .venv/bin/python -m pytest -q`
Expected: PASS (all existing + new tests).

- [ ] **Step 3: Live smoke against the running server**

```bash
export QUANTDATA_API_TOKEN=testtoken
# (restart UI so token + blueprint load)
curl -s -H "Authorization: Bearer testtoken" http://127.0.0.1:5050/api/v1/views | head -c 200
curl -s -H "Authorization: Bearer testtoken" "http://127.0.0.1:5050/api/v1/data/tw_money_supply_monthly?series=m1b_eop&format=json&limit=3"
curl -s http://127.0.0.1:5050/api/v1/views   # expect 401 (token set, none sent)
```
Expected: first two return data; third returns 401.

- [ ] **Step 4: Commit**

```bash
git add docs/quantdata-client.md
git commit -m "docs: quantdata client + REST 對外串接指南"
```

---

## Self-Review Notes
- **Pagination:** v1 uses **offset** cursor (`next_offset`) for json; bulk uses `format=parquet` (one shot to row cap). The spec mentioned keyset — offset is the v1 implementation choice (simpler, correct, testable); keyset is a future optimization. Documented here intentionally.
- **DRY caveat:** the SELECT guard exists in both `qd_access._guard` (server, strict) and `quantdata.client._guard` (local, since the standalone client must not import `ui.search`). This duplication is deliberate to keep the client a clean standalone package.
- **Auth scope:** the `before_request` gate lives only on `api_catalog.bp`; `api_v1.bp` (realtime) is untouched, so existing realtime consumers keep working.
