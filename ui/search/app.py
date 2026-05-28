"""QUANTDATA Search Web UI — Flask app.

Run:
    .venv/bin/python -m ui.search.app
    # open http://127.0.0.1:5050

Or via the launcher:
    scripts/run_search_ui.sh
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from flask import Flask, Response, abort, after_this_request, jsonify, render_template, request, send_file, stream_with_context

# Allow `python -m ui.search.app` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ui.search.catalog_inspector import (  # noqa: E402
    CATALOG,
    get_connection,
    get_view_meta,
    list_views,
    refresh_catalog_copy,
    view_summary,
)
from ui.search.query_builder import (  # noqa: E402
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Filter,
    build_sql,
)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["JSON_AS_ASCII"] = False


@app.route("/")
def index():
    views = list_views()
    summaries = []
    for v in views:
        try:
            summaries.append(view_summary(v))
        except Exception as e:
            summaries.append({"name": v, "row_count": 0, "error": str(e)})
    # sort: time-series first, then by row_count desc
    summaries.sort(key=lambda s: (not s.get("is_time_series"), -(s.get("row_count") or 0)))
    return render_template("index.html", views=summaries, catalog_path=str(CATALOG))


@app.route("/view/<view>")
def view_page(view):
    if view not in list_views():
        abort(404)
    meta = get_view_meta(view, with_distinct=True)
    return render_template(
        "view.html",
        meta=meta,
        meta_json=json.dumps({
            "name": meta.name,
            "row_count": meta.row_count,
            "is_time_series": meta.is_time_series,
            "date_columns": meta.date_columns,
            "numeric_columns": meta.numeric_columns,
            "string_columns": meta.string_columns,
            "columns": [asdict(c) for c in meta.columns],
        }, default=str),
        default_limit=DEFAULT_LIMIT,
        max_limit=MAX_LIMIT,
    )


@app.route("/api/query", methods=["POST"])
def api_query():
    """POST { view, filters: [{column, op, value, value2}], order_by, order_dir, limit }."""
    payload = request.get_json(force=True, silent=True) or {}
    view = payload.get("view")
    if not view:
        return jsonify({"error": "missing view"}), 400
    raw_filters = payload.get("filters") or []
    filters = [
        Filter(
            column=f.get("column"),
            op=f.get("op"),
            value=f.get("value"),
            value2=f.get("value2"),
        )
        for f in raw_filters
    ]
    try:
        sql, params = build_sql(
            view, filters,
            order_by=payload.get("order_by") or None,
            order_dir=payload.get("order_dir") or "DESC",
            limit=int(payload.get("limit") or DEFAULT_LIMIT),
            select_cols=payload.get("select_cols") or None,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    con = get_connection()
    try:
        df = con.execute(sql, params).fetchdf()
    except Exception as e:
        con.close()
        return jsonify({"error": f"query failed: {e}", "sql": sql}), 500
    con.close()

    # Convert DataFrame to JSON-safe rows. Use string format for non-JSON types.
    cols = list(df.columns)
    rows = []
    for _, row in df.iterrows():
        rows.append([_jsonify_cell(row[c]) for c in cols])
    return jsonify({
        "view": view,
        "sql": sql,
        "params": [str(p) for p in params],
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "truncated": len(rows) >= MAX_LIMIT,
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    refresh_catalog_copy()
    return jsonify({"ok": True, "n_views": len(list_views())})


# --- Bulk download (single CSV + zip bundle) ------------------------------

_CSV_CHUNK = 50_000  # rows per fetchmany batch


def _csv_escape(v) -> str:
    if v is None:
        return ""
    s = str(v)
    if any(c in s for c in (",", '"', "\n", "\r")):
        return '"' + s.replace('"', '""') + '"'
    return s


def _stream_view_csv(view: str):
    """Yield CSV chunks for a view — header + fetchmany batches."""
    con = get_connection()
    try:
        cur = con.execute(f'SELECT * FROM "{view}"')
        cols = [d[0] for d in cur.description]
        yield ",".join(_csv_escape(c) for c in cols) + "\n"
        while True:
            rows = cur.fetchmany(_CSV_CHUNK)
            if not rows:
                break
            yield "\n".join(",".join(_csv_escape(c) for c in r) for r in rows) + "\n"
    finally:
        con.close()


@app.route("/downloads")
def downloads_page():
    views = list_views()
    summaries = []
    for v in views:
        try:
            s = view_summary(v)
            summaries.append({"name": v, "row_count": s.get("row_count") or 0})
        except Exception:
            summaries.append({"name": v, "row_count": 0})
    summaries.sort(key=lambda s: -s["row_count"])
    return render_template("downloads.html", views=summaries, catalog_path=str(CATALOG))


@app.route("/download/view/<view>.csv")
def download_view_csv(view: str):
    if view not in list_views():
        abort(404)
    headers = {"Content-Disposition": f'attachment; filename="{view}.csv"'}
    return Response(stream_with_context(_stream_view_csv(view)),
                    mimetype="text/csv; charset=utf-8", headers=headers)


@app.route("/download/bundle.zip")
def download_bundle_zip():
    import os
    import tempfile
    import zipfile
    requested = request.args.getlist("v")
    if not requested:
        return jsonify({"error": "no views selected (pass ?v=name&v=name...)"}), 400
    valid = set(list_views())
    bad = [v for v in requested if v not in valid]
    if bad:
        return jsonify({"error": f"unknown views: {bad}"}), 400

    tmp = tempfile.NamedTemporaryFile(prefix="quantdata_", suffix=".zip", delete=False)
    tmp_path = tmp.name
    tmp.close()

    errors: dict[str, str] = {}
    try:
        con = get_connection()
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for v in requested:
                try:
                    with zf.open(f"{v}.csv", mode="w", force_zip64=True) as entry:
                        cur = con.execute(f'SELECT * FROM "{v}"')
                        cols = [d[0] for d in cur.description]
                        entry.write((",".join(_csv_escape(c) for c in cols) + "\n").encode("utf-8"))
                        while True:
                            rows = cur.fetchmany(_CSV_CHUNK)
                            if not rows:
                                break
                            buf = "\n".join(",".join(_csv_escape(c) for c in r) for r in rows) + "\n"
                            entry.write(buf.encode("utf-8"))
                except Exception as e:
                    errors[v] = str(e)
            if errors:
                zf.writestr("_errors.txt", "\n".join(f"{k}: {v}" for k, v in errors.items()))
        con.close()
    except Exception:
        try: os.unlink(tmp_path)
        except OSError: pass
        raise

    @after_this_request
    def _cleanup(resp):
        try: os.unlink(tmp_path)
        except OSError: pass
        return resp

    name = "quantdata_bundle.zip" if len(requested) > 1 else f"{requested[0]}.csv.zip"
    return send_file(tmp_path, as_attachment=True, download_name=name, mimetype="application/zip")


# --- helpers --------------------------------------------------------------

def _jsonify_cell(v):
    if v is None:
        return None
    import math
    import pandas as pd
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (pd.Timestamp, )):
        return str(v.date()) if v.time().hour == 0 and v.time().minute == 0 else v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


def main():
    host = "0.0.0.0"
    port = 5050
    print(f"[search-ui] starting on http://{host}:{port}", flush=True)
    print(f"[search-ui] catalog: {CATALOG}", flush=True)
    refresh_catalog_copy()
    n = len(list_views())
    print(f"[search-ui] {n} views available", flush=True)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
