"""Smoke + correctness tests for TEJ stock_daily ingester."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qd_ingest.sources.tej import (
    _normalize_stock_id,
    _to_ts_utc,
    _transform_ewprcd_chunk,
)

ROOT = Path(__file__).resolve().parents[1]


def test_normalize_stock_id_with_name():
    raw = pd.Series(["1101 台泥", "2330 台積電", "0050 元大台灣50"])
    sid, name = _normalize_stock_id(raw)
    assert sid.tolist() == ["1101", "2330", "0050"]
    assert name.tolist() == ["台泥", "台積電", "元大台灣50"]


def test_normalize_stock_id_no_name():
    raw = pd.Series(["1101", "2330"])
    sid, name = _normalize_stock_id(raw)
    assert sid.tolist() == ["1101", "2330"]


def test_to_ts_utc_anchors_at_close_taipei():
    s = pd.Series([20240102, 20240103])
    ts = _to_ts_utc(s)
    # 13:30 Asia/Taipei == 05:30 UTC
    assert ts.iloc[0] == pd.Timestamp("2024-01-02 05:30:00", tz="UTC")
    assert ts.iloc[1] == pd.Timestamp("2024-01-03 05:30:00", tz="UTC")


def test_transform_handles_nonzero_index():
    """Chunked reads give non-zero index; transform must not double rows."""
    raw = pd.DataFrame({
        "證券碼": ["1101 台泥", "2330 台積電"],
        "日期":   [20240102, 20240102],
        "開盤價": [50.0, 593.0],
        "最高價": [50.5, 595.0],
        "最低價": [49.5, 589.0],
        "收盤價": [50.2, 593.0],
        "成交量(千股)": [10_000, 28_000],
        "開盤價-除權息": [None, None],
        "最高價-除權息": [None, None],
        "最低價-除權息": [None, None],
        "收盤價-除權息": [None, None],
    }, index=pd.RangeIndex(start=200_000, stop=200_002))  # mimic chunk 2 of read_csv
    out = _transform_ewprcd_chunk(raw)
    assert len(out) == 2
    assert out["symbol"].tolist() == ["1101", "2330"]
    assert out["volume"].tolist() == [10_000_000, 28_000_000]   # 千股 -> shares
    assert out["asset_class"].iloc[0] == "tw_stock"
    assert out["session"].iloc[0] == "day"


@pytest.mark.skipif(
    not (ROOT / "silver/bars/bars_1d/asset_class=tw_stock/year=2024").exists(),
    reason="silver not yet built — run ingester first",
)
def test_silver_2024_readable_via_duckdb():
    import duckdb

    con = duckdb.connect()
    df = con.sql(f"""
        SELECT trading_date, symbol, open, close, volume
        FROM read_parquet(
            '{ROOT}/silver/bars/bars_1d/asset_class=tw_stock/year=2024/*.parquet'
        )
        WHERE symbol = '2330'
        ORDER BY trading_date
        LIMIT 5
    """).df()
    assert len(df) == 5
    assert df["symbol"].iloc[0] == "2330"
    assert df["open"].iloc[0] > 0
