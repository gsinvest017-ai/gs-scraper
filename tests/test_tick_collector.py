"""Unit tests for ui.search.tick_collector — MIS parse / dedup / ring / 持久化。"""

from __future__ import annotations

import json

import pytest

from ui.search import tick_collector as tc


def _msg(c="2330", z="100.5", tv="10", v="500", tlong=1780641000000, **kw):
    base = {
        "c": c, "n": "台積電", "ex": "tse", "d": "20260606", "%": "09:00:05",
        "tlong": str(tlong), "z": z, "tv": tv, "v": v,
        "a": "101.0000_101.5000_102.0000_", "b": "100.0000_99.5000_99.0000_",
        "o": "99.0", "h": "101.0", "l": "98.5", "y": "99.5",
    }
    base.update(kw)
    return base


@pytest.fixture
def rt_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "REALTIME_DIR", tmp_path)
    return tmp_path


# ── parse_tick ──────────────────────────────────────────────────────────────

def test_parse_tick_normal():
    t = tc.parse_tick(_msg())
    assert t["symbol"] == "2330" and t["price"] == 100.5
    assert t["tick_vol"] == 10.0 and t["cum_vol"] == 500.0
    assert t["bid"] == 100.0 and t["ask"] == 101.0
    assert t["tlong"] == 1780641000000
    assert t["prev_close"] == 99.5


def test_parse_tick_no_trade_returns_none():
    assert tc.parse_tick(_msg(z="-")) is None
    assert tc.parse_tick(_msg(z="")) is None


def test_parse_tick_dash_fields_none():
    t = tc.parse_tick(_msg(tv="-", v="-", a="", b=""))
    assert t["tick_vol"] is None and t["cum_vol"] is None
    assert t["bid"] is None and t["ask"] is None


# ── resolve_ex_ch ───────────────────────────────────────────────────────────

def test_resolve_alias():
    col = tc.TickCollector(fetcher=lambda chs: [])
    assert col.resolve_ex_ch("TAIEX") == "tse_t00.tw"
    assert col.resolve_ex_ch("otc") == "otc_o00.tw"


def test_resolve_tse_then_otc_fallback():
    def fake(chs):
        # 上櫃股 5483 只在 otc 有資料
        if chs == ["tse_5483.tw"]:
            return []
        if chs == ["otc_5483.tw"]:
            return [_msg(c="5483", ex="otc")]
        return [_msg()]
    col = tc.TickCollector(fetcher=fake)
    assert col.resolve_ex_ch("2330") == "tse_2330.tw"
    assert col.resolve_ex_ch("5483") == "otc_5483.tw"


def test_resolve_unknown_none():
    col = tc.TickCollector(fetcher=lambda chs: [])
    assert col.resolve_ex_ch("ZZZZ") is None
    assert col.resolve_ex_ch("") is None


# ── poll / dedup / ring ─────────────────────────────────────────────────────

def _collector_with_feed(rt_dir, initial=None):
    """fake fetcher 一律回 holder['cur']；probe 階段先放一筆通用 msg。"""
    holder = {"cur": initial if initial is not None else [_msg()]}

    def fake(chs):
        return holder["cur"]

    col = tc.TickCollector(fetcher=fake)
    return col, holder


def test_poll_dedup_and_new_ticks(rt_dir):
    col, holder = _collector_with_feed(rt_dir)
    col.start(["2330", "0050"])
    col.stop()

    holder["cur"] = [_msg(tlong=1, v="100")]
    col.poll_once()
    ticks, seq = col.get_ticks()
    assert len(ticks) == 1 and seq == 1

    col.poll_once()  # 同快照 → dedup
    ticks, seq = col.get_ticks()
    assert len(ticks) == 1 and seq == 1

    holder["cur"] = [_msg(tlong=2, v="120", z="100.7", tv="20")]
    col.poll_once()
    ticks, seq = col.get_ticks()
    assert len(ticks) == 2 and seq == 2
    assert ticks[-1]["price"] == 100.7


def test_get_ticks_since_seq_and_symbol_filter(rt_dir):
    col, holder = _collector_with_feed(rt_dir)
    col.start(["2330", "0050"])
    col.stop()
    holder["cur"] = [_msg(c="2330", tlong=1, v="10"),
                     _msg(c="0050", tlong=1, v="20", z="105.0")]
    col.poll_once()
    holder["cur"] = [_msg(c="2330", tlong=2, v="30")]
    col.poll_once()

    all_ticks, seq = col.get_ticks()
    assert len(all_ticks) == 3 and seq == 3

    inc, _ = col.get_ticks(since_seq=2)
    assert len(inc) == 1 and inc[0]["symbol"] == "2330"

    only_0050, _ = col.get_ticks(symbol="0050")
    assert len(only_0050) == 1 and only_0050[0]["price"] == 105.0


def test_poll_error_captured_not_raised(rt_dir):
    mode = {"probe": True}

    def boom(chs):
        if mode["probe"]:
            return [_msg()]
        raise OSError("network down")
    col = tc.TickCollector(fetcher=boom)
    col.start(["2330"])
    col.stop()
    mode["probe"] = False
    col.poll_once()
    st = col.status()
    assert "network down" in (st["last_error"] or "")
    assert st["ticks_in_ring"] == 0


# ── 持久化 + backfill ───────────────────────────────────────────────────────

def test_ticks_persisted_to_jsonl(rt_dir):
    col, holder = _collector_with_feed(rt_dir)
    col.start(["2330"])
    col.stop()
    holder["cur"] = [_msg(tlong=1, v="10")]
    col.poll_once()
    fp = tc.tick_file()
    assert fp.is_file()
    rows = [json.loads(x) for x in fp.read_text().splitlines()]
    assert rows[0]["symbol"] == "2330" and rows[0]["price"] == 100.5


def test_backfill_restores_ring_and_dedup_base(rt_dir):
    fp = tc.tick_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    t = tc.parse_tick(_msg(tlong=5, v="55"))
    fp.write_text(json.dumps(t) + "\n", encoding="utf-8")

    col, holder = _collector_with_feed(rt_dir)
    col.start(["2330"])   # start 觸發 backfill
    col.stop()
    ticks, seq = col.get_ticks()
    assert len(ticks) == 1 and seq == 1

    # backfill 的 (tlong, cum_vol) 是 dedup 基準 → 同快照不重複
    holder["cur"] = [_msg(tlong=5, v="55")]
    col.poll_once()
    assert col.get_ticks()[1] == 1


# ── start/stop ──────────────────────────────────────────────────────────────

def test_start_reports_unknown_symbols(rt_dir):
    def fake(chs):
        return [_msg()] if "2330" in chs[0] else []
    col = tc.TickCollector(fetcher=fake)
    res = col.start(["2330", "NOPE"])
    try:
        assert res["symbols"] == ["2330"] and res["unknown"] == ["NOPE"]
        assert res["running"] is True
    finally:
        col.stop()


def test_stop_idempotent(rt_dir):
    col = tc.TickCollector(fetcher=lambda chs: [])
    assert col.stop()["running"] is False
    assert col.running is False


def test_parse_tick_index_alias_normalized():
    t = tc.parse_tick(_msg(c="t00", n="發行量加權股價指數"))
    assert t["symbol"] == "TAIEX"


# ── e2e: /api/live/ticks routes ─────────────────────────────────────────────

@pytest.fixture
def tick_app(app_client, rt_dir, monkeypatch):
    """app_client + 注入 fake-fetcher collector 單例。"""
    col, holder = _collector_with_feed(rt_dir)
    monkeypatch.setattr(tc, "_collector", col)
    return app_client, col, holder


def test_api_ticks_start_status_stop(tick_app):
    client, col, holder = tick_app
    r = client.post("/api/live/ticks/start", json={"symbols": ["2330"]})
    assert r.status_code == 200
    assert r.get_json()["running"] is True

    st = client.get("/api/live/ticks/status").get_json()
    assert st["running"] is True and st["symbols"] == ["2330"]

    assert client.post("/api/live/ticks/stop").get_json()["running"] is False


def test_api_ticks_start_validation(tick_app):
    client, _, _ = tick_app
    assert client.post("/api/live/ticks/start", json={}).status_code == 400
    assert client.post("/api/live/ticks/start",
                       json={"symbols": []}).status_code == 400
    assert client.post("/api/live/ticks/start",
                       json={"symbols": ["  "]}).status_code == 400


def test_api_ticks_incremental(tick_app):
    client, col, holder = tick_app
    client.post("/api/live/ticks/start", json={"symbols": ["2330"]})
    client.post("/api/live/ticks/stop")
    holder["cur"] = [_msg(tlong=1, v="10")]
    col.poll_once()

    d = client.get("/api/live/ticks?symbol=2330").get_json()
    assert len(d["ticks"]) == 1 and d["seq"] == 1

    holder["cur"] = [_msg(tlong=2, v="20", z="101.0")]
    col.poll_once()
    d2 = client.get(f"/api/live/ticks?symbol=2330&since_seq={d['seq']}").get_json()
    assert len(d2["ticks"]) == 1 and d2["ticks"][0]["price"] == 101.0


def test_api_ticks_bad_params_400(tick_app):
    client, _, _ = tick_app
    assert client.get("/api/live/ticks?since_seq=abc").status_code == 400


# ── latest_snapshot ───────────────────────────────────────────────────────

def _fake_collector_with_ring(ticks):
    """建一個不啟動 thread 的 collector，直接灌 ring（seq 遞增）。"""
    c = tc.TickCollector(fetcher=lambda ex_chs: [])
    for t in ticks:
        c._append_tick(t, persist=False)
    return c


def test_latest_snapshot_returns_newest_per_symbol():
    c = _fake_collector_with_ring([
        {"symbol": "2330", "price": 100.0, "tlong": 1, "cum_vol": 10.0},
        {"symbol": "0050", "price": 50.0, "tlong": 2, "cum_vol": 20.0},
        {"symbol": "2330", "price": 101.0, "tlong": 3, "cum_vol": 11.0},  # 較新
    ])
    snap = c.latest_snapshot()
    assert snap["2330"]["price"] == 101.0
    assert snap["2330"]["tlong"] == 3
    assert snap["0050"]["price"] == 50.0


def test_latest_snapshot_filters_to_requested_symbols():
    c = _fake_collector_with_ring([
        {"symbol": "2330", "price": 100.0, "tlong": 1, "cum_vol": 10.0},
        {"symbol": "0050", "price": 50.0, "tlong": 2, "cum_vol": 20.0},
    ])
    snap = c.latest_snapshot(["2330", "TAIEX"])
    assert set(snap) == {"2330"}          # TAIEX 不在 ring → 不出現
    assert snap["2330"]["price"] == 100.0


def test_latest_snapshot_empty_ring():
    c = tc.TickCollector(fetcher=lambda ex_chs: [])
    assert c.latest_snapshot() == {}
    assert c.latest_snapshot(["2330"]) == {}
