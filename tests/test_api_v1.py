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
