"""Endpoint tests for ui.search.api_v1 — 對外即時行情 API v1。"""

from __future__ import annotations

import datetime as dt

import pytest

from ui.search import api_v1
from ui.search import live_timeseries as lt


TPE = dt.timezone(dt.timedelta(hours=8))
FIXED_NOW = dt.datetime(2026, 6, 9, 13, 25, 7, tzinfo=TPE)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_v1, "_now", lambda: FIXED_NOW)
    from ui.search.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class FakeCollector:
    """測試用假 collector（不起 thread）。"""

    def __init__(self, snap=None, symbols=None, running=True,
                 last_poll_at="2026-06-09T13:25:05", ticks=None):
        self._snap = snap or {}
        self._symbols = list(symbols or [])
        self._running = running
        self._last_poll = last_poll_at
        self._ticks = ticks or []
        self.started_with = []

    def status(self):
        return {"running": self._running, "symbols": sorted(self._symbols),
                "poll_sec": 3.0, "started_at": "2026-06-09T09:00:01",
                "last_poll_at": self._last_poll, "poll_count": 5204,
                "ticks_in_ring": len(self._snap), "seq": 48213,
                "last_error": None}

    def start(self, syms):
        self.started_with.append(list(syms))
        self._symbols = list(syms)[:20]
        self._running = True
        return {"running": True, "symbols": sorted(self._symbols), "unknown": []}

    def latest_snapshot(self, symbols=None):
        if symbols is None:
            return dict(self._snap)
        return {s.upper(): self._snap[s.upper()] for s in symbols
                if s.upper() in self._snap}

    def get_ticks(self, symbol=None, since_seq=0, limit=5000):
        return list(self._ticks), 48213


def _patch_collector(monkeypatch, fake):
    from ui.search import tick_collector
    monkeypatch.setattr(tick_collector, "get_collector", lambda: fake)


def test_health_shape_and_envelope(client, monkeypatch):
    _patch_collector(monkeypatch, FakeCollector(symbols=["2330", "TAIEX"]))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["server_time"] == "2026-06-09T13:25:07+08:00"
    c = body["collector"]
    assert c["running"] is True
    assert c["collected_symbols"] == ["2330", "TAIEX"]
    assert c["seconds_since_poll"] == 2.0      # 13:25:07 - 13:25:05
    assert c["seq"] == 48213
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    assert r.headers["Cache-Control"] == "no-store"


def test_health_null_last_poll(client, monkeypatch):
    _patch_collector(monkeypatch, FakeCollector(last_poll_at=None, running=False))
    body = client.get("/api/v1/health").get_json()
    assert body["collector"]["seconds_since_poll"] is None
    assert body["collector"]["running"] is False


# ── /snapshot ────────────────────────────────────────────────────────────

_TICK_2330 = {"symbol": "2330", "name": "台積電", "price": 1085.0,
              "bid": 1085.0, "ask": 1090.0, "open": 1080.0, "high": 1095.0,
              "low": 1078.0, "prev_close": 1075.0, "cum_vol": 18234.0,
              "tick_vol": 3.0, "time": "13:24:58",
              "tlong": 1780982698000}  # FIXED_NOW(=epoch 1780982707) 之前約 9 秒


def test_snapshot_happy(client, monkeypatch):
    fake = FakeCollector(snap={"2330": _TICK_2330}, symbols=["2330"])
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols=2330").get_json()
    s = body["snapshots"]["2330"]
    assert s["price"] == 1085.0
    assert s["change"] == 10.0                      # 1085 - 1075
    assert s["change_pct"] == pytest.approx(0.93, abs=0.01)
    assert s["live"] is True and s["warming"] is False
    assert s["age_sec"] == pytest.approx(9.0, abs=0.5)
    assert body["not_collected"] == [] and body["dropped"] == []


def test_snapshot_missing_symbols_param(client, monkeypatch):
    _patch_collector(monkeypatch, FakeCollector())
    r = client.get("/api/v1/snapshot")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_snapshot_symbol_parsing_comma_space_case(client, monkeypatch):
    fake = FakeCollector(snap={"2330": _TICK_2330}, symbols=["2330", "0050", "TAIEX"])
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols= 2330 , 0050;taiex").get_json()
    # 三檔都在 watchlist，但只有 2330 有 tick → 另兩檔 warming
    assert set(body["snapshots"]) == {"2330", "0050", "TAIEX"}
    assert body["snapshots"]["0050"]["warming"] is True
    assert body["snapshots"]["0050"]["live"] is False


def test_snapshot_ensure_lazy_start(client, monkeypatch):
    fake = FakeCollector(snap={}, symbols=[], running=True)
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols=2330&ensure=1").get_json()
    assert fake.started_with               # start() 被呼叫（懶啟動）
    assert "2330" in fake.started_with[-1]
    assert body["snapshots"]["2330"]["warming"] is True


def test_snapshot_ensure_off_lists_not_collected(client, monkeypatch):
    fake = FakeCollector(snap={}, symbols=[], running=True)
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols=2330&ensure=0").get_json()
    assert not fake.started_with           # 純讀，不啟動
    assert body["not_collected"] == ["2330"]
    assert "2330" not in body["snapshots"]


def test_snapshot_over_20_reports_dropped(client, monkeypatch):
    syms = [f"{1000 + i}" for i in range(25)]
    fake = FakeCollector(snap={}, symbols=[], running=True)
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols=" + ",".join(syms)
                      + "&ensure=1").get_json()
    assert len(body["dropped"]) == 5       # 25 - 20


def test_snapshot_503_when_cannot_start(client, monkeypatch):
    fake = FakeCollector(snap={}, symbols=[], running=False)
    # start() 模擬 MIS 掛掉：不改 running、symbols 維持空
    fake.start = lambda syms: {"running": False, "symbols": [], "unknown": syms}
    _patch_collector(monkeypatch, fake)
    r = client.get("/api/v1/snapshot?symbols=2330&ensure=1")
    assert r.status_code == 503
    assert "error" in r.get_json()


def test_snapshot_dropped_not_double_reported(client, monkeypatch):
    syms = [f"{1000 + i}" for i in range(25)]
    fake = FakeCollector(snap={}, symbols=[], running=True)
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols=" + ",".join(syms)
                      + "&ensure=1").get_json()
    # 被 drop 的 5 檔只出現在 dropped，不應同時出現在 not_collected
    assert len(body["dropped"]) == 5
    assert set(body["dropped"]) & set(body["not_collected"]) == set()


def test_snapshot_returns_stale_tick_instead_of_503(client, monkeypatch):
    # collector 啟動失敗(running=False)，但 ring 仍有 last-known tick →
    # 回 200 + age_sec，而非 503（消費者自行判斷新鮮度）
    fake = FakeCollector(snap={"2330": _TICK_2330}, symbols=["2330"], running=False)
    fake.start = lambda s: {"running": False, "symbols": ["2330"], "unknown": []}
    _patch_collector(monkeypatch, fake)
    r = client.get("/api/v1/snapshot?symbols=2330&ensure=1")
    assert r.status_code == 200
    assert r.get_json()["snapshots"]["2330"]["price"] == 1085.0


# ── /ticks ───────────────────────────────────────────────────────────────

def test_ticks_happy(client, monkeypatch):
    ticks = [{"symbol": "2330", "price": 100.0, "tlong": 1, "cum_vol": 10.0}]
    _patch_collector(monkeypatch, FakeCollector(ticks=ticks, symbols=["2330"]))
    body = client.get("/api/v1/ticks?symbol=2330&since_seq=5").get_json()
    assert body["symbol"] == "2330"
    assert body["seq"] == 48213
    assert body["ticks"][0]["price"] == 100.0
    assert body["server_time"] == "2026-06-09T13:25:07+08:00"


def test_ticks_bad_since_seq(client, monkeypatch):
    _patch_collector(monkeypatch, FakeCollector())
    r = client.get("/api/v1/ticks?since_seq=abc")
    assert r.status_code == 400


def test_ticks_limit_clamped(client, monkeypatch):
    captured = {}

    class Cap(FakeCollector):
        def get_ticks(self, symbol=None, since_seq=0, limit=5000):
            captured["limit"] = limit
            captured["since_seq"] = since_seq
            return [], 0

    _patch_collector(monkeypatch, Cap())
    client.get("/api/v1/ticks?limit=99999&since_seq=3")
    assert captured["limit"] == 20000        # 上限 clamp
    assert captured["since_seq"] == 3


def test_ticks_negative_since_seq_clamped(client, monkeypatch):
    captured = {}

    class Cap(FakeCollector):
        def get_ticks(self, symbol=None, since_seq=0, limit=5000):
            captured["since_seq"] = since_seq
            return [], 0

    _patch_collector(monkeypatch, Cap())
    client.get("/api/v1/ticks?since_seq=-5")
    assert captured["since_seq"] == 0          # max(0, -5)


def test_ticks_zero_limit_clamped(client, monkeypatch):
    captured = {}

    class Cap(FakeCollector):
        def get_ticks(self, symbol=None, since_seq=0, limit=5000):
            captured["limit"] = limit
            return [], 0

    _patch_collector(monkeypatch, Cap())
    client.get("/api/v1/ticks?limit=0")
    assert captured["limit"] == 1              # max(1, 0)


def test_ticks_symbol_uppercased(client, monkeypatch):
    _patch_collector(monkeypatch, FakeCollector(ticks=[], symbols=[]))
    body = client.get("/api/v1/ticks?symbol=tsmc").get_json()
    assert body["symbol"] == "TSMC"


# ── /ticks/history ─────────────────────────────────────────────────────────

def test_ticks_history_happy(client, monkeypatch):
    from ui.search import tick_history
    monkeypatch.setattr(tick_history, "get_history_ticks",
                        lambda date, symbol: {"date": date, "symbol": symbol,
                                              "source": "self_jsonl", "count": 2,
                                              "ticks": [{"price": 1.0}, {"price": 2.0}]})
    body = client.get("/api/v1/ticks/history?date=2026-06-06&symbol=2330").get_json()
    assert body["source"] == "self_jsonl"
    assert body["count"] == 2
    assert body["server_time"] == "2026-06-09T13:25:07+08:00"


def test_ticks_history_missing_params(client, monkeypatch):
    r = client.get("/api/v1/ticks/history?date=2026-06-06")
    assert r.status_code == 400


def test_ticks_history_bad_date_propagates_400(client, monkeypatch):
    from ui.search import tick_history

    def boom(date, symbol):
        raise ValueError("date 必須是 YYYY-MM-DD")

    monkeypatch.setattr(tick_history, "get_history_ticks", boom)
    r = client.get("/api/v1/ticks/history?date=xx&symbol=2330")
    assert r.status_code == 400
    assert "error" in r.get_json()


# ── /bars ──────────────────────────────────────────────────────────────────

def test_bars_happy(client, monkeypatch):
    monkeypatch.setattr(lt, "get_timeseries",
                        lambda symbol, days: {"symbol": symbol, "asset_class": "tw_stock",
                                              "days": days,
                                              "series": {"dates": ["2026-06-08"],
                                                         "open": [1.0], "high": [2.0],
                                                         "low": [0.5], "close": [1.5],
                                                         "volume": [100]},
                                              "latest": {"close": 1.5}})
    body = client.get("/api/v1/bars?symbol=2330&days=30").get_json()
    assert body["symbol"] == "2330"
    assert body["days"] == 30
    assert body["latest"]["close"] == 1.5
    assert body["server_time"] == "2026-06-09T13:25:07+08:00"


def test_bars_missing_symbol(client, monkeypatch):
    r = client.get("/api/v1/bars")
    assert r.status_code == 400


def test_bars_not_found_404(client, monkeypatch):
    monkeypatch.setattr(lt, "get_timeseries", lambda symbol, days: None)
    r = client.get("/api/v1/bars?symbol=ZZZ")
    assert r.status_code == 404


def test_bars_days_clamped(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(lt, "get_timeseries",
                        lambda symbol, days: captured.update(days=days) or
                        {"symbol": symbol, "asset_class": "x", "days": days,
                         "series": {}, "latest": {}})
    client.get("/api/v1/bars?symbol=2330&days=9999")
    assert captured["days"] == 365          # MAX_DAYS clamp


# ── error handling ─────────────────────────────────────────────────────────

def test_unexpected_exception_returns_json_500(client, monkeypatch):
    # 強制 /health 內部丟非預期例外 → 應回 JSON 500 而非 HTML / stack
    client.application.config["PROPAGATE_EXCEPTIONS"] = False
    from ui.search import tick_collector

    def boom():
        raise RuntimeError("catalog locked")

    monkeypatch.setattr(tick_collector, "get_collector", boom)
    r = client.get("/api/v1/health")
    assert r.status_code == 500
    body = r.get_json()
    assert body == {"error": "internal error"}      # JSON envelope, no stack
    # CORS still applied on the error response (after_request runs)
    assert r.headers["Access-Control-Allow-Origin"] == "*"


def test_explicit_http_errors_still_pass_through(client, monkeypatch):
    # 明確的 400 不應被 Exception handler 變成 500
    client.application.config["PROPAGATE_EXCEPTIONS"] = False
    _patch_collector(monkeypatch, FakeCollector())
    r = client.get("/api/v1/snapshot")          # 缺 symbols → 400
    assert r.status_code == 400
    assert "error" in r.get_json()


# ── OpenAPI / Swagger docs ───────────────────────────────────────────────────

def test_openapi_json_served(client):
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    spec = r.get_json()
    assert spec["openapi"].startswith("3.0")
    # 5 個資料端點都在 spec 內
    assert set(spec["paths"]) == {
        "/health", "/snapshot", "/ticks", "/ticks/history", "/bars"}
    assert spec["servers"][0]["url"] == "/api/v1"
    # 對外只讀 GET 仍帶 CORS
    assert r.headers["Access-Control-Allow-Origin"] == "*"


def test_docs_page_served(client):
    r = client.get("/api/v1/docs")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    html = r.get_data(as_text=True)
    # 引用 vendored 本地資產（非 CDN）+ 指向本機 openapi.json
    assert "/static/swagger/swagger-ui-bundle.js" in html
    assert "/static/swagger/swagger-ui.css" in html
    assert "/api/v1/openapi.json" in html
    assert "SwaggerUIBundle" in html
