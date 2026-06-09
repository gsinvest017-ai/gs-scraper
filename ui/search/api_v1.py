"""對外即時行情 API v1 — 給風控系統等跨機器消費者的只讀穩定契約。

設計：docs/superpowers/specs/2026-06-09-realtime-api-v1-design.md
- 只讀（不含任何 collector start/stop 寫入端點）
- 無認證，靠 Tailnet / 內網防火牆做邊界
- 複用 tick_collector / tick_history / live_timeseries
- 回應一律含 server_time（+08:00），供消費者自算 staleness；GET 加 CORS allow-all
"""

from __future__ import annotations

import datetime as dt

from flask import Blueprint, jsonify, request  # noqa: F401

from ui.search import live_timeseries as lt  # noqa: F401
from ui.search import tick_collector
from ui.search import tick_history as th  # noqa: F401

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
