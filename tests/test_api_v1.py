"""Endpoint tests for ui.search.api_v1 — 對外即時行情 API v1。"""

from __future__ import annotations

import datetime as dt

import pytest

from ui.search import api_v1


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
