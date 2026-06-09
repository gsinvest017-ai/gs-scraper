"""對外即時行情 API v1 — 給風控系統等跨機器消費者的只讀穩定契約。

設計：docs/superpowers/specs/2026-06-09-realtime-api-v1-design.md
- 只讀（不含任何 collector start/stop 寫入端點）
- 無認證，靠 Tailnet / 內網防火牆做邊界
- 複用 tick_collector / tick_history / live_timeseries
- 回應一律含 server_time（+08:00），供消費者自算 staleness；GET 加 CORS allow-all
"""

from __future__ import annotations

import datetime as dt
import re

from flask import Blueprint, jsonify, request

from ui.search import live_timeseries as lt
from ui.search import tick_collector
from ui.search import tick_history as th

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

TPE = dt.timezone(dt.timedelta(hours=8))
MAX_SYMBOLS = tick_collector.MAX_SYMBOLS   # 20


def _now() -> dt.datetime:
    """可注入的「現在」（aware, +08:00）。測試 monkeypatch 此函式取得確定值。"""
    return dt.datetime.now(TPE)


def _server_time() -> str:
    return _now().isoformat(timespec="seconds")


def _seconds_since(iso_naive: str | None) -> float | None:
    """collector 的 last_poll_at / started_at 是 naive local ISO 字串。"""
    if not iso_naive:
        return None
    try:
        then = dt.datetime.fromisoformat(iso_naive)
    except ValueError:
        return None
    now_naive = _now().replace(tzinfo=None)
    return round((now_naive - then).total_seconds(), 1)


@bp.after_request
def _add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/health")
def health():
    st = tick_collector.get_collector().status()
    return jsonify({
        "server_time": _server_time(),
        "collector": {
            "running": st["running"],
            "collected_symbols": st["symbols"],
            "poll_sec": st["poll_sec"],
            "started_at": st["started_at"],
            "last_poll_at": st["last_poll_at"],
            "seconds_since_poll": _seconds_since(st["last_poll_at"]),
            "poll_count": st["poll_count"],
            "ticks_in_ring": st["ticks_in_ring"],
            "seq": st["seq"],
            "last_error": st["last_error"],
        },
    })


def _parse_symbols(raw: str) -> list[str]:
    """逗號/空白/分號分隔 → 去空白、大寫、去重保序。"""
    parts = re.split(r"[,\s;]+", raw.strip())
    out: list[str] = []
    for p in parts:
        s = p.strip().upper()
        if s and s not in out:
            out.append(s)
    return out


def _age_sec(tlong) -> float | None:
    if not tlong:
        return None
    return round(_now().timestamp() - tlong / 1000.0, 1)


def _enrich(t: dict) -> dict:
    price, prev = t.get("price"), t.get("prev_close")
    change = change_pct = None
    if price is not None and prev not in (None, 0):
        change = round(price - prev, 4)
        change_pct = round(change / prev * 100, 4)
    return {
        "symbol": t.get("symbol"), "name": t.get("name"),
        "price": price, "bid": t.get("bid"), "ask": t.get("ask"),
        "open": t.get("open"), "high": t.get("high"), "low": t.get("low"),
        "prev_close": prev, "cum_vol": t.get("cum_vol"),
        "tick_vol": t.get("tick_vol"), "change": change,
        "change_pct": change_pct, "time": t.get("time"),
        "tlong": t.get("tlong"), "age_sec": _age_sec(t.get("tlong")),
        "live": True, "warming": False,
    }


def _warming_stub(sym: str) -> dict:
    return {"symbol": sym, "name": None, "price": None, "bid": None,
            "ask": None, "open": None, "high": None, "low": None,
            "prev_close": None, "cum_vol": None, "tick_vol": None,
            "change": None, "change_pct": None, "time": None, "tlong": None,
            "age_sec": None, "live": False, "warming": True}


@bp.route("/snapshot")
def snapshot():
    raw = request.args.get("symbols") or ""
    symbols = _parse_symbols(raw)
    if not symbols:
        return jsonify({"error": "需要 symbols 參數（逗號分隔）"}), 400
    ensure = (request.args.get("ensure") or "1") != "0"

    collector = tick_collector.get_collector()
    st = collector.status()
    collected = set(st["symbols"])
    dropped: list[str] = []
    started = False

    if ensure and any(s not in collected for s in symbols):
        merged = list(dict.fromkeys(sorted(collected) + symbols))
        if len(merged) > MAX_SYMBOLS:
            dropped = merged[MAX_SYMBOLS:]
        collector.start(merged[:MAX_SYMBOLS])
        st = collector.status()
        collected = set(st["symbols"])
        started = True

    snaps = collector.latest_snapshot(symbols)
    # 啟動失敗且完全沒有可回報的 tick → 503。
    # 若有（即使過期）last-known tick 仍照常回傳，消費者用 age_sec / /health
    # 的 seconds_since_poll 自行判斷新鮮度，比硬回 503 更有用。
    if started and not st["running"] and not snaps:
        return jsonify({"error": "collector 無法啟動（MIS 來源可能無回應）",
                        "not_collected": symbols}), 503

    out: dict[str, dict] = {}
    not_collected: list[str] = []
    for s in symbols:
        if s in dropped:
            continue            # 已在 dropped 回報，不重複列入 not_collected
        if s in snaps:
            out[s] = _enrich(snaps[s])
        elif s in collected:
            out[s] = _warming_stub(s)
        else:
            not_collected.append(s)

    return jsonify({"server_time": _server_time(), "snapshots": out,
                    "not_collected": not_collected, "dropped": dropped})


@bp.route("/ticks")
def ticks():
    symbol = (request.args.get("symbol") or "").strip().upper() or None
    try:
        since_seq = max(0, int(request.args.get("since_seq") or 0))
        limit = min(20000, max(1, int(request.args.get("limit") or 5000)))
    except ValueError:
        return jsonify({"error": "since_seq / limit 必須是整數"}), 400
    rows, seq = tick_collector.get_collector().get_ticks(
        symbol=symbol, since_seq=since_seq, limit=limit)
    return jsonify({"server_time": _server_time(),
                    "symbol": symbol, "ticks": rows, "seq": seq})


@bp.route("/ticks/history")
def ticks_history():
    date = (request.args.get("date") or "").strip()
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not date or not symbol:
        return jsonify({"error": "需要 date 與 symbol"}), 400
    try:
        data = th.get_history_ticks(date, symbol)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    data["server_time"] = _server_time()
    return jsonify(data)


@bp.route("/bars")
def bars():
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "需要 symbol"}), 400
    try:
        days = min(lt.MAX_DAYS, max(1, int(request.args.get("days") or lt.DEFAULT_DAYS)))
    except ValueError:
        return jsonify({"error": "days 必須是整數"}), 400
    data = lt.get_timeseries(symbol, days)
    if data is None:
        return jsonify({"error": f"查無標的: {symbol}"}), 404
    data["server_time"] = _server_time()
    return jsonify(data)
