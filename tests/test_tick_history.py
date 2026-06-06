"""Unit + e2e tests for ui.search.tick_history — 三層 fallback 歷史 tick。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from ui.search import tick_collector as tc
from ui.search import tick_history as th


@pytest.fixture
def hist_env(tmp_path, monkeypatch):
    """隔離 REALTIME_DIR + FinMind sqlite + 不打真 API/catalog。"""
    rt = tmp_path / "realtime"
    rt.mkdir()
    monkeypatch.setattr(tc, "REALTIME_DIR", rt)
    monkeypatch.setattr(th, "REALTIME_DIR", rt)

    db = tmp_path / "finmind.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE taiwan_stock_price_tick "
                "(date TEXT, stock_id TEXT, deal_price REAL, volume REAL, "
                " Time TEXT, TickType TEXT)")
    con.executemany(
        "INSERT INTO taiwan_stock_price_tick VALUES (?,?,?,?,?,?)",
        [("2026-05-20", "2330", 1000.0, 5, "09:00:01.000000", "1"),
         ("2026-05-20", "2330", 1001.0, 3, "09:00:02.500000", "2"),
         ("2026-05-20", "0050", 100.0, 9, "09:00:03.000000", "1")])
    con.commit(); con.close()
    monkeypatch.setattr(th, "FINMIND_SQLITE", db)
    monkeypatch.setattr(th, "_finmind_token", lambda: None)   # 預設不可打 API
    monkeypatch.setattr(th, "last_trading_day", lambda: "2026-06-05")
    monkeypatch.setattr(th, "_prev_close_for", lambda s, d: 999.0)
    return rt


def _write_collected(rt, date, ticks):
    fp = rt / f"ticks_{date}.jsonl"
    with open(fp, "a", encoding="utf-8") as f:
        for t in ticks:
            f.write(json.dumps(t) + "\n")


# ── available_dates ─────────────────────────────────────────────────────────

def test_available_dates_union(hist_env):
    _write_collected(hist_env, "2026-06-03", [{"symbol": "2330"}])
    d = th.available_dates()
    assert "2026-06-03" in d["dates"]          # 自收
    assert "2026-05-20" in d["dates"]          # sqlite
    assert d["last_trading_day"] == "2026-06-05"
    assert d["finmind_fetchable"] is False
    assert "2026-06-05" not in d["dates"]      # 沒 token → 最後交易日不可抓


def test_available_dates_with_token(hist_env, monkeypatch):
    monkeypatch.setattr(th, "_finmind_token", lambda: "tok")
    d = th.available_dates()
    assert d["finmind_fetchable"] is True
    assert "2026-06-05" in d["dates"]          # 有 token → 最後交易日可即抓


# ── get_history_ticks 三層 fallback ────────────────────────────────────────

def test_history_from_collected_first(hist_env):
    _write_collected(hist_env, "2026-05-20",
                     [{"symbol": "2330", "price": 555.0, "prev_close": None}])
    r = th.get_history_ticks("2026-05-20", "2330")
    assert r["source"] == "collected" and r["count"] == 1
    assert r["ticks"][0]["price"] == 555.0
    assert r["ticks"][0]["prev_close"] == 999.0   # bars_1d 補昨收


def test_history_from_sqlite_with_cumvol(hist_env):
    r = th.get_history_ticks("2026-05-20", "2330")
    assert r["source"] == "finmind_sqlite" and r["count"] == 2
    t0, t1 = r["ticks"]
    assert t0["price"] == 1000.0 and t0["cum_vol"] == 5.0
    assert t1["cum_vol"] == 8.0                   # 累積量
    assert t1["tick_type"] == "2"
    assert t0["time"] == "09:00:01"
    assert t0["tlong"] > 0
    assert t0["prev_close"] == 999.0


def test_history_from_api_and_cached(hist_env, monkeypatch):
    calls = {"n": 0}

    def fake_api(date, symbol, timeout=30.0):
        calls["n"] += 1
        out = [th._finmind_row_to_tick(symbol, date, "09:00:01.000000",
                                       2395.0, 10, "1", 10.0)]
        # 模擬真實 _from_finmind_api 的 cache 行為
        th.REALTIME_DIR.mkdir(parents=True, exist_ok=True)
        with open(th._finmind_cache_file(date, symbol), "w") as f:
            for t in out:
                f.write(json.dumps(t) + "\n")
        return out

    monkeypatch.setattr(th, "_from_finmind_api", fake_api)
    r = th.get_history_ticks("2026-06-05", "2330")
    assert r["source"] == "finmind_api" and r["count"] == 1 and calls["n"] == 1

    # 第二次讀 → 命中 cache，不再打 API
    r2 = th.get_history_ticks("2026-06-05", "2330")
    assert r2["source"] == "finmind_cache" and calls["n"] == 1


def test_history_empty_when_no_source(hist_env):
    r = th.get_history_ticks("2026-01-01", "9999")
    assert r["source"] is None and r["ticks"] == []


def test_history_validation():
    with pytest.raises(ValueError):
        th.get_history_ticks("not-a-date", "2330")
    with pytest.raises(ValueError):
        th.get_history_ticks("2026-06-05", "../etc")


# ── _finmind_row_to_tick ────────────────────────────────────────────────────

def test_row_to_tick_tlong_taipei():
    t = th._finmind_row_to_tick("2330", "2026-06-05", "09:00:04.891440",
                                2395.0, 2484, "1", 2484.0)
    assert t["price"] == 2395.0 and t["tick_vol"] == 2484.0
    assert t["time"] == "09:00:04"
    assert t["date"] == "20260605"
    # 2026-06-05 09:00:04.891 台北 = 01:00:04.891 UTC
    import datetime as dt
    utc = dt.datetime.fromtimestamp(t["tlong"] / 1000, tz=dt.timezone.utc)
    assert (utc.hour, utc.minute, utc.second) == (1, 0, 4)


# ── e2e routes ──────────────────────────────────────────────────────────────

def test_api_dates_route(app_client, hist_env):
    r = app_client.get("/api/live/ticks/dates")
    assert r.status_code == 200
    d = r.get_json()
    assert "2026-05-20" in d["dates"]
    assert d["last_trading_day"] == "2026-06-05"


def test_api_history_route(app_client, hist_env):
    r = app_client.get("/api/live/ticks/history?date=2026-05-20&symbol=2330")
    assert r.status_code == 200
    d = r.get_json()
    assert d["source"] == "finmind_sqlite" and d["count"] == 2


def test_api_history_validation(app_client, hist_env):
    assert app_client.get("/api/live/ticks/history").status_code == 400
    assert app_client.get(
        "/api/live/ticks/history?date=bad&symbol=2330").status_code == 400
    assert app_client.get(
        "/api/live/ticks/history?date=2026-05-20&symbol=..%2Fetc").status_code == 400
