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
    if request.method == "OPTIONS":
        return
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
