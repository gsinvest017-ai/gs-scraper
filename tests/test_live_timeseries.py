"""Unit tests for ui.search.live_timeseries — 標的序列查詢與最新日統計。"""

from __future__ import annotations

import duckdb
import pytest

from ui.search import live_timeseries as lt


@pytest.fixture
def ts_catalog(tmp_path, monkeypatch):
    """獨立 mini DuckDB：bars_1d（2330 三天）+ macro_daily（TAIEX 兩天 + 重名 0050）。"""
    db = tmp_path / "ts.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE bars_1d AS SELECT * FROM (VALUES
          (DATE '2026-05-27', 980.0, 1000.0, 975.0, 990.0, 100.0, 'tw_stock', '2330'),
          (DATE '2026-05-28', 990.0, 1005.0, 985.0, 995.0, 105.0, 'tw_stock', '2330'),
          (DATE '2026-05-29', 995.0, 1008.0, 990.0, 1000.0, 108.0, 'tw_stock', '2330'),
          (DATE '2026-06-02', 1000.0, 1009.0, 992.0, 1002.0, 109.0, 'tw_stock', '2330'),
          (DATE '2026-06-03', 1000.0, 1010.0, 990.0, 1005.0, 111.0, 'tw_stock', '2330'),
          (DATE '2026-06-04', 1005.0, 1020.0, 1000.0, 1010.0, 222.0, 'tw_stock', '2330'),
          (DATE '2026-06-05', 1010.0, 1030.0, 1005.0, 1020.0, 333.0, 'tw_stock', '2330'),
          (DATE '2026-06-05',  100.0,  101.0,   99.0,  100.5,  10.0, 'tw_stock', '0050')
        ) t(trading_date, open, high, low, close, volume, asset_class, symbol)
    """)
    con.execute("""
        CREATE TABLE macro_daily AS SELECT * FROM (VALUES
          (DATE '2026-06-04', 45000.0, 45100.0, 44900.0, 45050.0, 1.0, 'tw_index', 'TAIEX'),
          (DATE '2026-06-05', 45050.0, 45200.0, 44800.0, 44900.0, 2.0, 'tw_index', 'TAIEX'),
          (DATE '2026-06-05',   104.0,   105.0,   103.0,   104.5, 3.0, 'tw_index', '0050')
        ) t(trading_date, open, high, low, close, volume, category, symbol)
    """)
    con.close()

    def fake_connection():
        return duckdb.connect(str(db), read_only=True)

    monkeypatch.setattr(lt, "get_connection", fake_connection)
    monkeypatch.setattr(lt, "_SYMBOL_CACHE", None)
    return db


def test_list_symbols_union_and_dedup(ts_catalog):
    syms = lt.list_symbols()
    by_name = {s["symbol"]: s for s in syms}
    assert by_name["2330"]["asset_class"] == "tw_stock"
    assert by_name["TAIEX"]["asset_class"] == "macro/tw_index"
    # 0050 兩源重名：bars_1d 優先，macro 版掛 macro: 前綴
    assert by_name["0050"]["asset_class"] == "tw_stock"
    assert by_name["macro:0050"]["asset_class"] == "macro/tw_index"


def test_list_symbols_cached(ts_catalog):
    first = lt.list_symbols()
    assert lt.list_symbols() is first          # cache hit
    assert lt.list_symbols(refresh=True) is not first


def test_timeseries_bars_with_change(ts_catalog):
    d = lt.get_timeseries("2330", days=60)
    assert d["asset_class"] == "tw_stock"
    assert d["days"] == 7
    assert d["series"]["dates"][-3:] == ["2026-06-03", "2026-06-04", "2026-06-05"]
    assert d["series"]["close"][-3:] == [1005.0, 1010.0, 1020.0]
    L = d["latest"]
    assert L["trading_date"] == "2026-06-05"
    assert L["close"] == 1020.0 and L["prev_close"] == 1010.0
    assert L["change"] == pytest.approx(10.0)
    assert L["change_pct"] == pytest.approx(10.0 / 1010.0 * 100, rel=1e-4)


def test_timeseries_days_limit(ts_catalog):
    # days clamp 下限 5 → 取最近 5 個交易日
    d = lt.get_timeseries("2330", days=2)
    assert d["days"] == 5
    assert d["series"]["dates"][0] == "2026-05-29"
    assert d["series"]["dates"][-1] == "2026-06-05"


def test_timeseries_macro_fallback(ts_catalog):
    d = lt.get_timeseries("TAIEX", days=60)
    assert d["asset_class"] == "macro/tw_index"
    assert d["latest"]["change_pct"] == pytest.approx(-150 / 45050 * 100, abs=1e-3)


def test_timeseries_macro_prefix_forces_macro(ts_catalog):
    d = lt.get_timeseries("macro:0050", days=60)
    assert d["asset_class"] == "macro/tw_index"
    assert d["latest"]["close"] == 104.5


def test_timeseries_single_day_no_change(ts_catalog):
    d = lt.get_timeseries("0050", days=60)  # bars_1d 只有一天
    assert d["days"] == 1
    assert d["latest"]["change"] is None and d["latest"]["change_pct"] is None


def test_timeseries_unknown_symbol_none(ts_catalog):
    assert lt.get_timeseries("NOPE") is None
    assert lt.get_timeseries("   ") is None


# ── e2e routes（mini_catalog 沒有 bars_1d/macro_daily → graceful） ──────────

def test_api_symbols_route(app_client, ts_catalog):
    r = app_client.get("/api/live/symbols")
    assert r.status_code == 200
    syms = {s["symbol"] for s in r.get_json()["symbols"]}
    assert {"2330", "TAIEX"} <= syms


def test_api_timeseries_route(app_client, ts_catalog):
    r = app_client.get("/api/live/timeseries?symbol=2330&days=10")
    assert r.status_code == 200
    assert r.get_json()["latest"]["close"] == 1020.0


def test_api_timeseries_missing_symbol_400(app_client):
    assert app_client.get("/api/live/timeseries").status_code == 400


def test_api_timeseries_bad_days_400(app_client):
    r = app_client.get("/api/live/timeseries?symbol=2330&days=abc")
    assert r.status_code == 400


def test_api_timeseries_unknown_404(app_client, ts_catalog):
    assert app_client.get("/api/live/timeseries?symbol=ZZZ9").status_code == 404
