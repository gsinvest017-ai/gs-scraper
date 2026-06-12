"""QuantData client — auto-detect local DuckDB (zero-copy) vs REST. Returns pandas."""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pandas as pd
import requests


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
