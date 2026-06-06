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
            summaries.append({"name": v, "row_count": 0, "error": str(e),
                              "data_source": "other", "long_description": ""})
    # sort: time-series first, then by row_count desc
    summaries.sort(key=lambda s: (not s.get("is_time_series"), -(s.get("row_count") or 0)))
    # 收集 dashboard 用的 source 清單（只列實際出現的）
    from qd_ingest.common.dataset_meta import DATA_SOURCES
    present_sources = sorted({s.get("data_source", "other") for s in summaries
                              if s.get("data_source")},
                              key=lambda x: (x not in DATA_SOURCES, x))
    return render_template("index.html", views=summaries, sources=present_sources,
                           catalog_path=str(CATALOG))


@app.route("/view/<view>")
def view_page(view):
    if view not in list_views():
        abort(404)
    meta = get_view_meta(view, with_distinct=True)
    from qd_ingest.common.dataset_meta import get_meta
    data_source, long_description = get_meta(view)
    return render_template(
        "view.html",
        meta=meta,
        data_source=data_source,
        long_description=long_description,
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


@app.route("/migrate")
def migrate_page():
    """Data migration dashboard — 填表把整個 repo + 資料湖鏡像到另一台主機。"""
    return render_template("migrate.html", catalog_path=str(CATALOG),
                           default_path=str(Path(__file__).resolve().parents[2]))


@app.route("/api/migrate", methods=["POST"])
def api_migrate():
    """POST 表單 → 串流 migrate_to_host.sh 的 log（text/plain，逐行 flush）。

    password 只在此 request 的 subprocess env 中使用，不持久化、不寫 log、不回傳。
    """
    from ui.search.migrate_runner import ValidationError, stream_migration, validate

    payload = request.get_json(force=True, silent=True) or {}
    password = payload.get("password") or None  # 不放進 validate 回傳，避免外洩
    try:
        params = validate(payload)
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400

    def gen():
        yield from stream_migration(params, password)

    headers = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    return Response(stream_with_context(gen()),
                    mimetype="text/plain; charset=utf-8", headers=headers)


# --- Live crawl monitor: 當日增量爬蟲即時監控 ---------------------------

@app.route("/live")
def live_page():
    """當日增量爬蟲 dashboard — 即時顯示 ingest audit 事件，實盤監控用。"""
    from ui.search import live_monitor as lm
    date = request.args.get("date") or lm.today_str()
    try:
        lm.audit_path(date)
    except ValueError:
        abort(400, description="date 必須是 YYYY-MM-DD")
    return render_template("live.html", date=date, today=lm.today_str(),
                           dates=lm.available_dates(), catalog_path=str(CATALOG))


@app.route("/api/live/summary")
def api_live_summary():
    """GET ?date=YYYY-MM-DD&offset=N → {summary, events(新增), offset, date}。

    offset 是上次回傳的 byte offset；client 輪詢時帶上，server 只讀新增部分。
    summary 一律基於整天事件重算（events 數量小，重算成本可忽略）。
    """
    from ui.search import live_monitor as lm
    date = request.args.get("date") or lm.today_str()
    try:
        offset = max(0, int(request.args.get("offset") or 0))
        all_events, new_offset = lm.read_events(date, 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if offset > 0:
        new_events, _ = lm.read_events(date, offset)
    else:
        new_events = all_events
    return jsonify({
        "date": date,
        "offset": new_offset,
        "summary": lm.summarize(all_events),
        "new_events": new_events,
    })


@app.route("/api/live/symbols")
def api_live_symbols():
    """GET → 可查標的清單 [{symbol, asset_class, latest_date}]（autocomplete 用）。"""
    from ui.search import live_timeseries as lt
    refresh = request.args.get("refresh") == "1"
    return jsonify({"symbols": lt.list_symbols(refresh=refresh)})


@app.route("/api/live/timeseries")
def api_live_timeseries():
    """GET ?symbol=2330&days=60 → 近 N 個交易日 OHLCV + 最新交易日統計。"""
    from ui.search import live_timeseries as lt
    symbol = (request.args.get("symbol") or "").strip()
    if not symbol:
        return jsonify({"error": "missing symbol"}), 400
    try:
        days = int(request.args.get("days") or lt.DEFAULT_DAYS)
    except ValueError:
        return jsonify({"error": "days 必須是整數"}), 400
    data = lt.get_timeseries(symbol, days)
    if data is None:
        return jsonify({"error": f"查無標的: {symbol}"}), 404
    return jsonify(data)


# --- 逐 tick 實盤監控（TWSE MIS collector） -----------------------------

@app.route("/api/live/ticks/status")
def api_ticks_status():
    from ui.search.tick_collector import get_collector
    return jsonify(get_collector().status())


@app.route("/api/live/ticks/start", methods=["POST"])
def api_ticks_start():
    """POST {"symbols": ["2330", "TAIEX", ...]} → 啟動/更新 collector watchlist。"""
    from ui.search.tick_collector import get_collector
    payload = request.get_json(force=True, silent=True) or {}
    symbols = payload.get("symbols") or []
    if not isinstance(symbols, list) or not symbols:
        return jsonify({"error": "symbols 必須是非空 list"}), 400
    symbols = [str(s).strip() for s in symbols if str(s).strip()]
    if not symbols:
        return jsonify({"error": "symbols 必須是非空 list"}), 400
    res = get_collector().start(symbols)
    return jsonify(res)


@app.route("/api/live/ticks/stop", methods=["POST"])
def api_ticks_stop():
    from ui.search.tick_collector import get_collector
    return jsonify(get_collector().stop())


@app.route("/api/live/ticks/dates")
def api_ticks_dates():
    """GET → 可回看的 tick 日期清單 + 最後交易日 + FinMind 可抓性。"""
    from ui.search import tick_history as th
    return jsonify(th.available_dates())


@app.route("/api/live/ticks/history")
def api_ticks_history():
    """GET ?date=YYYY-MM-DD&symbol=2330 → 該日該標的逐 tick（三層 fallback）。

    來源依序：自收 JSONL → FinMind cache → FinMind sqlite → FinMind API
    （API 命中後自動 cache；回應的 source 欄位標明來源）。
    """
    from ui.search import tick_history as th
    date = (request.args.get("date") or "").strip()
    symbol = (request.args.get("symbol") or "").strip()
    if not date or not symbol:
        return jsonify({"error": "需要 date 與 symbol"}), 400
    try:
        return jsonify(th.get_history_ticks(date, symbol))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/live/ticks")
def api_ticks():
    """GET ?symbol=2330&since_seq=N&limit=M → 增量逐 tick（ring buffer）。

    回 {ticks, seq}；client 下次帶 since_seq=seq 只拿新 tick。
    """
    from ui.search.tick_collector import get_collector
    symbol = request.args.get("symbol") or None
    try:
        since_seq = max(0, int(request.args.get("since_seq") or 0))
        limit = min(20000, max(1, int(request.args.get("limit") or 5000)))
    except ValueError:
        return jsonify({"error": "since_seq/limit 必須是整數"}), 400
    ticks, seq = get_collector().get_ticks(symbol=symbol, since_seq=since_seq,
                                           limit=limit)
    return jsonify({"ticks": ticks, "seq": seq})


@app.route("/api/live/stream")
def api_live_stream():
    """SSE — 每 2 秒檢查 audit 檔，有新事件就推一個 update。"""
    import time as _time

    from ui.search import live_monitor as lm
    date = request.args.get("date") or lm.today_str()
    try:
        lm.audit_path(date)
    except ValueError:
        return jsonify({"error": "date 必須是 YYYY-MM-DD"}), 400

    def gen():
        offset = 0
        heartbeat = 0
        while True:
            events, new_offset = lm.read_events(date, offset)
            if events or offset == 0:
                all_events, _ = lm.read_events(date, 0)
                payload = json.dumps({
                    "date": date,
                    "offset": new_offset,
                    "summary": lm.summarize(all_events),
                    "new_events": events,
                }, ensure_ascii=False, default=str)
                yield f"data: {payload}\n\n"
                offset = new_offset
                heartbeat = 0
            else:
                heartbeat += 1
                if heartbeat >= 10:  # ~20s 沒事件 → comment 行保活
                    yield ": keepalive\n\n"
                    heartbeat = 0
            _time.sleep(2)

    headers = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    return Response(stream_with_context(gen()),
                    mimetype="text/event-stream", headers=headers)


@app.route("/gap_dashboard.html")
def gap_dashboard():
    """Serve the latest gap_dashboard.html generated by scripts/gap_report.py."""
    repo_root = Path(__file__).resolve().parents[2]
    fp = repo_root / "docs" / "gap_dashboard.html"
    if not fp.is_file():
        abort(404, description="gap_dashboard.html 尚未生成；先跑 python scripts/gap_report.py --format all")
    return send_file(fp, mimetype="text/html; charset=utf-8")


# --- Gap-comments: 手動編輯每條 view 的註解 -----------------------------

def _gap_comments_path() -> Path:
    return Path(__file__).resolve().parents[2] / "meta" / "gap_comments.json"


def _load_gap_comments() -> dict:
    fp = _gap_comments_path()
    if not fp.is_file():
        return {"_schema_version": 1, "updated_at": None, "comments": {}}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {"_schema_version": 1, "updated_at": None, "comments": {}}


@app.route("/api/gap_comments", methods=["GET"])
def api_gap_comments_get():
    return jsonify(_load_gap_comments())


@app.route("/api/gap_comments", methods=["POST"])
def api_gap_comments_post():
    """POST {"view": "<view_name>", "comment": "<text>"} → upsert that single entry.

    Returns the updated payload. Atomically writes via tmpfile + os.replace.
    """
    import os
    import tempfile
    from datetime import datetime, timezone

    payload = request.get_json(force=True, silent=True) or {}
    view = (payload.get("view") or "").strip()
    comment = payload.get("comment", "")
    if not view:
        return jsonify({"error": "missing 'view'"}), 400
    if not isinstance(comment, str):
        return jsonify({"error": "'comment' must be a string"}), 400
    if len(comment) > 2000:
        return jsonify({"error": "'comment' too long (max 2000 chars)"}), 400

    data = _load_gap_comments()
    data.setdefault("_schema_version", 1)
    data.setdefault("comments", {})
    if comment.strip() == "":
        # 空字串視為刪除該 view 的註解
        data["comments"].pop(view, None)
    else:
        data["comments"][view] = comment
    data["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    fp = _gap_comments_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    # atomic write
    fd, tmp = tempfile.mkstemp(prefix=".gap_comments_", suffix=".json", dir=str(fp.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, fp)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise

    return jsonify(data)


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
    from qd_ingest.common.dataset_meta import DATA_SOURCES, get_meta
    views = list_views()
    summaries = []
    for v in views:
        ds, ld = get_meta(v)
        try:
            s = view_summary(v)
            summaries.append({
                "name": v, "row_count": s.get("row_count") or 0,
                "data_source": ds, "long_description": ld,
            })
        except Exception:
            summaries.append({"name": v, "row_count": 0,
                              "data_source": ds, "long_description": ld})
    summaries.sort(key=lambda s: -s["row_count"])
    present_sources = sorted({s["data_source"] for s in summaries},
                              key=lambda x: (x not in DATA_SOURCES, x))
    return render_template("downloads.html", views=summaries,
                           sources=present_sources, catalog_path=str(CATALOG))


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
