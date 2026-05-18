"""Fetch the latest TEJ data (API-flavored subscription) and write it to
RAW_SOURCES/TEJ資料/*.csv in the EW Chinese-header format that
qd_ingest.sources.tej.* expects.

Background
----------
The original version targeted TEJ's EW datatables (TWN/EWPRCD / EWTINST1 /
EWGIN / EWIFINQ). The current subscription ("TQ高手過招-期貨+TQ初入江湖-個股")
does not include those — it ships the *API-flavored* tables (TWN/APIxxx,
TWN/Axxx). One bright spot: TWN/APISHRACT bundles three-institution flow
AND margin/short-sale into a single 62-column table, so two EW CSVs are
populated from one API call.

Required env vars:
  TEJAPI_KEY      your TEJ API token
  TEJAPI_BASE     https://api.tej.com.tw  (default)

Usage:
  .venv/bin/python scripts/fetch_tej.py --table all --append-since-silver
  .venv/bin/python scripts/fetch_tej.py --table stock_daily --start 20260101
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
RAW = Path(os.environ.get("QUANTDATA_RAW", REPO.parent / "RAW_SOURCES")) / "TEJ資料"
SILVER = REPO / "silver"


# Logical tables exposed to the user.
#   stock_daily / inst_stock / margin — write EW-format CSVs to RAW for the
#       existing qd_ingest.sources.tej.* ingesters to consume.
#   futures_daily / futures_large_trader / revenue_monthly — write Parquet
#       directly to silver/ partitioned by year (no RAW intermediate).
LOGICAL_TABLES = [
    "stock_daily", "inst_stock", "margin",
    "futures_daily", "futures_large_trader", "revenue_monthly",
]

# Where the existing ingester reads from (must match qd_ingest.sources.tej rename maps)
OUT_CSV = {
    "stock_daily": "TWN_EWPRCD_股價.csv",
    "inst_stock":  "TWN_EWTINST1_三大法人.csv",
    "margin":      "TWN_EWGIN_融資融券.csv",
}

# EW column order expected by the ingester (extracted from
# qd_ingest.sources.tej.EWPRCD_RENAME / EWTINST1_RENAME / EWGIN_RENAME)
EW_COLS = {
    "stock_daily": [
        "證券碼", "日期",
        "開盤價", "最高價", "最低價", "收盤價",
        "成交量(千股)", "交易所本益比", "流通股數(千股)", "交易所股價淨值比", "現金股利率",
        "開盤價-除權息", "最高價-除權息", "最低價-除權息", "收盤價-除權息",
    ],
    "inst_stock": [
        "證券碼", "日期",
        "外資買賣超(千股)", "投信買賣超(千股)", "自營買賣超(千股)", "合計買賣超(千股)",
        "外資買進張數", "投信買進張數", "外資賣出張數", "投信賣出張數",
        "自營買進張數", "自營賣出張數",
        "外資總持股數(千股)", "投信總持股數(千股)", "自營總持股數(千股)",
        "外資總持股率(%)", "投信總持股率(%)", "自營總持股率(%)",
    ],
    "margin": [
        "證券碼", "日期",
        "融資買進(張)", "融資賣出(張)", "融券買入(張)", "融券賣出(張)",
        "融資餘額(張)", "融券餘額(張)", "融資餘額(千元)", "融券餘額(千元)",
        "融資使用率", "融券使用率", "券資比",
        "融資維持率", "融券維持率", "整戶維持率",
    ],
}


# ---------------------------------------------------------------------------
# TEJ client
# ---------------------------------------------------------------------------

def _check_env() -> None:
    if not os.environ.get("TEJAPI_KEY"):
        sys.exit(
            "ERROR: TEJAPI_KEY env var is required.\n"
            "  set -Ux TEJAPI_KEY <your_key>\n"
            "  set -Ux TEJAPI_BASE https://api.tej.com.tw"
        )
    os.environ.setdefault("TEJAPI_BASE", "https://api.tej.com.tw")


def _tej_get(dataset: str, **params) -> pd.DataFrame:
    import tejapi

    tejapi.ApiConfig.api_key = os.environ["TEJAPI_KEY"]
    tejapi.ApiConfig.api_base = os.environ["TEJAPI_BASE"]
    return tejapi.get(dataset, paginate=True, chinese_column_name=True, **params)


# ---------------------------------------------------------------------------
# Schema adapters: API table -> EW CSV row format
# ---------------------------------------------------------------------------

def _fmt_yyyymmdd(s: pd.Series) -> pd.Series:
    """Convert TEJ tz-aware/naive datetime to 'YYYYMMDD' int string."""
    return pd.to_datetime(s).dt.strftime("%Y%m%d")


def _fmt_iso_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.strftime("%Y-%m-%d")


def adapt_apiprcd_to_ew_stock(df: pd.DataFrame) -> pd.DataFrame:
    """TWN/APIPRCD (Chinese cols) -> EW 股價 CSV format.

    adjfac in TEJ is the cumulative split/dividend factor (price * adjfac =
    adjusted price). Multiply raw OHLC by adjfac to reproduce EW's
    "OHLC-除權息" columns.
    """
    out = pd.DataFrame()
    out["證券碼"] = df["證券名稱"].astype(str)
    out["日期"] = _fmt_yyyymmdd(df["資料日"])
    out["開盤價"] = df["開盤價"]
    out["最高價"] = df["最高價"]
    out["最低價"] = df["最低價"]
    out["收盤價"] = df["收盤價"]
    out["成交量(千股)"] = df["成交量(千股)"]
    out["交易所本益比"] = df["本益比"]
    out["流通股數(千股)"] = df["流通在外股數(千股)"]
    out["交易所股價淨值比"] = df["股價淨值比"]
    out["現金股利率"] = df["現金股利率(TEJ)"]
    adjfac = pd.to_numeric(df["調整係數"], errors="coerce")
    out["開盤價-除權息"] = pd.to_numeric(df["開盤價"], errors="coerce") * adjfac
    out["最高價-除權息"] = pd.to_numeric(df["最高價"], errors="coerce") * adjfac
    out["最低價-除權息"] = pd.to_numeric(df["最低價"], errors="coerce") * adjfac
    out["收盤價-除權息"] = pd.to_numeric(df["收盤價"], errors="coerce") * adjfac
    return out[EW_COLS["stock_daily"]]


def adapt_apishract_to_ew_inst_stock(shract: pd.DataFrame, prcd: pd.DataFrame) -> pd.DataFrame:
    """TWN/APISHRACT -> EW 三大法人 CSV format.

    Needs APIPRCD joined for `流通在外股數` to back-compute the
    `外資/投信/自營總持股數(千股)` columns (= 持股率% × 流通股數 / 100).
    """
    out = pd.DataFrame()
    out["證券碼"] = shract["證券名稱"].astype(str)
    out["日期"] = _fmt_yyyymmdd(shract["資料日"])

    # 1 張 = 1 千股, numeric value identical.
    out["外資買賣超(千股)"] = shract["外資買賣超張數"]
    out["投信買賣超(千股)"] = shract["投信買賣超張數"]
    # 自營 = 自行 + 避險 (EW omits the split)
    out["自營買賣超(千股)"] = (
        pd.to_numeric(shract["自營買賣超張數(自行)"], errors="coerce").fillna(0)
        + pd.to_numeric(shract["自營買賣超張數(避險)"], errors="coerce").fillna(0)
    )
    out["合計買賣超(千股)"] = shract["合計買賣超張數"]
    out["外資買進張數"] = shract["外資買進張數"]
    out["投信買進張數"] = shract["投信買進張數"]
    out["外資賣出張數"] = shract["外資賣出張數"]
    out["投信賣出張數"] = shract["投信賣出張數"]
    out["自營買進張數"] = (
        pd.to_numeric(shract["自營商買進張數(自行)"], errors="coerce").fillna(0)
        + pd.to_numeric(shract["自營商買進張數(避險)"], errors="coerce").fillna(0)
    )
    out["自營賣出張數"] = (
        pd.to_numeric(shract["自營商賣出張數(自行)"], errors="coerce").fillna(0)
        + pd.to_numeric(shract["自營商賣出張數(避險)"], errors="coerce").fillna(0)
    )

    # Holdings: API provides only the rate; back-compute shares from APIPRCD.流通在外股數
    if prcd is not None and not prcd.empty:
        join_key = ["證券名稱", "資料日"]
        shares = prcd[join_key + ["流通在外股數(千股)"]].copy()
        merged = shract.merge(shares, on=join_key, how="left")
        outstanding = pd.to_numeric(merged["流通在外股數(千股)"], errors="coerce")
    else:
        outstanding = pd.Series([float("nan")] * len(shract))

    out["外資總持股率(%)"] = shract["外資持股率"]
    out["投信總持股率(%)"] = shract["投信持股率"]
    out["自營總持股率(%)"] = shract["自營商持股率"]
    out["外資總持股數(千股)"] = (
        pd.to_numeric(out["外資總持股率(%)"], errors="coerce") * outstanding / 100.0
    ).round().astype("Int64")
    out["投信總持股數(千股)"] = (
        pd.to_numeric(out["投信總持股率(%)"], errors="coerce") * outstanding / 100.0
    ).round().astype("Int64")
    out["自營總持股數(千股)"] = (
        pd.to_numeric(out["自營總持股率(%)"], errors="coerce") * outstanding / 100.0
    ).round().astype("Int64")
    return out[EW_COLS["inst_stock"]]


def adapt_apishract_to_ew_margin(shract: pd.DataFrame) -> pd.DataFrame:
    """TWN/APISHRACT -> EW 融資融券 CSV format.

    1 張 = 1 千股, numeric value identical between the units, so we just
    rename columns. 融資/融券使用率 = 餘額 / 限額 × 100 (back-computed; EW's
    EWGIN exposes this directly).
    """
    out = pd.DataFrame()
    out["證券碼"] = shract["證券名稱"].astype(str)
    out["日期"] = _fmt_iso_date(shract["資料日"])
    out["融資買進(張)"] = shract["融資買進(千股)"]
    out["融資賣出(張)"] = shract["融資賣出(千股)"]
    out["融券買入(張)"] = shract["融券買進(千股)"]
    out["融券賣出(張)"] = shract["融券賣出(千股)"]
    out["融資餘額(張)"] = shract["融資餘額(千股)"]
    out["融券餘額(張)"] = shract["融券餘額(千股)"]
    out["融資餘額(千元)"] = shract["融資餘額(千元)"]
    out["融券餘額(千元)"] = shract["融券餘額(千元)"]
    margin_lim = pd.to_numeric(shract["融資限額(千股)"], errors="coerce")
    short_lim = pd.to_numeric(shract["融券限額(千股)"], errors="coerce")
    out["融資使用率"] = (
        pd.to_numeric(shract["融資餘額(千股)"], errors="coerce") / margin_lim * 100.0
    )
    out["融券使用率"] = (
        pd.to_numeric(shract["融券餘額(千股)"], errors="coerce") / short_lim * 100.0
    )
    out["券資比"] = shract["資券比"]
    out["融資維持率"] = shract["融資維持率"]
    out["融券維持率"] = shract["融券維持率"]
    out["整戶維持率"] = shract["整戶維持率"]
    return out[EW_COLS["margin"]]


# ---------------------------------------------------------------------------
# Silver max date lookup (for --append-since-silver)
# ---------------------------------------------------------------------------

def _silver_max_date(table: str) -> dt.date | None:
    import shutil
    import tempfile

    catalog_src = REPO / "catalog" / "quant.duckdb"
    if not catalog_src.exists():
        return None
    tmp = Path(tempfile.mkdtemp()) / "snap.duckdb"
    try:
        shutil.copy(catalog_src, tmp)
        import duckdb
        con = duckdb.connect(str(tmp), read_only=True)
        con.execute(f"SET file_search_path='{REPO}'")
        view_map = {
            "stock_daily": ("tw_stock_bars", "trading_date"),
            "inst_stock":  ("tw_inst_stock_daily", "trading_date"),
            "margin":      ("tw_margin_daily", "trading_date"),
            # P0 silver tables — written directly by fetch_tej.py
            "futures_daily":        ("bars_1d", "trading_date"),
            "futures_large_trader": ("tw_futures_large_trader_daily", "trading_date"),
            "revenue_monthly":      ("revenue_monthly", "fiscal_month"),
        }
        if table not in view_map:
            return None
        view, col = view_map[table]
        row = con.execute(f"SELECT MAX({col}) FROM {view}").fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# P0: AFUTR / AFUTRHU / APISALE — write directly to silver Parquet
# ---------------------------------------------------------------------------

import re as _re
import pyarrow as _pa
import pyarrow.parquet as _pq
import shutil as _shutil

_STOCK_RE = _re.compile(r"^\d{4}$")  # 4-digit numeric = stock code (skip 個股期)


def adapt_afutr_to_bars_1d(df) -> "pd.DataFrame":
    """TWN/AFUTR (chinese cols) -> canonical bars_1d schema rows.

    Filters out individual-stock-futures contracts (underlying_id matches
    `^\\d{4}$`) since those already live in silver under
    asset_class=tw_stock_futures.
    """
    if len(df) == 0:
        return df
    underlying = df["標的證券碼"].astype(str)
    keep = ~underlying.str.match(_STOCK_RE)
    df = df.loc[keep].copy()
    if df.empty:
        return df

    contract_id = df["期貨名稱"].astype(str)
    # root code = first 3 chars (TXF, MXF, EXF, FXF, GXF, ZXF, …)
    symbol = contract_id.str.slice(0, 3)
    td = pd.to_datetime(df["日期"]).dt.tz_localize(None)
    # close at 13:45 Asia/Taipei
    ts_utc = (td + pd.Timedelta(hours=13, minutes=45)).dt.tz_localize("Asia/Taipei").dt.tz_convert("UTC")

    n = len(df)
    out = pd.DataFrame({
        "ts_utc":       ts_utc,
        "trading_date": td.dt.date,
        "asset_class":  "tw_futures",
        "exchange":     "TAIFEX",
        "symbol":       symbol.values,
        "contract_id":  contract_id.values,
        "session":      "day",
        "open":         pd.to_numeric(df["開盤價"], errors="coerce").values,
        "high":         pd.to_numeric(df["最高價"], errors="coerce").values,
        "low":          pd.to_numeric(df["最低價"], errors="coerce").values,
        "close":        pd.to_numeric(df["收盤價"], errors="coerce").values,
        "volume":       pd.array(pd.to_numeric(df["成交張數(量)"], errors="coerce"), dtype="Int64"),
        "open_interest": pd.array(pd.to_numeric(df["未平倉合約數"], errors="coerce"), dtype="Int64"),
        "vwap":         pd.to_numeric(df["標的證券價格"], errors="coerce").values,
        "settlement":   pd.to_numeric(df["每日結算價"], errors="coerce").values,
        "adj_open":     pd.Series([pd.NA] * n, dtype="Float64").astype(float),
        "adj_high":     pd.Series([pd.NA] * n, dtype="Float64").astype(float),
        "adj_low":      pd.Series([pd.NA] * n, dtype="Float64").astype(float),
        "adj_close":    pd.Series([pd.NA] * n, dtype="Float64").astype(float),
        "adj_factor":   pd.Series([pd.NA] * n, dtype="Float64").astype(float),
        "source":       "tej_afutr",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
        "quality_flag": "ok",
    })
    out["year"] = pd.to_datetime(out["ts_utc"]).dt.year.astype("int32")
    return out


_BARS_1D_SCHEMA = _pa.schema([
    ("ts_utc",        _pa.timestamp("ns", tz="UTC")),
    ("trading_date",  _pa.date32()),
    ("asset_class",   _pa.string()),
    ("exchange",      _pa.string()),
    ("symbol",        _pa.string()),
    ("contract_id",   _pa.string()),
    ("session",       _pa.string()),
    ("open",          _pa.float64()),
    ("high",          _pa.float64()),
    ("low",           _pa.float64()),
    ("close",         _pa.float64()),
    ("volume",        _pa.int64()),
    ("open_interest", _pa.int64()),
    ("vwap",          _pa.float64()),
    ("settlement",    _pa.float64()),
    ("adj_open",      _pa.float64()),
    ("adj_high",      _pa.float64()),
    ("adj_low",       _pa.float64()),
    ("adj_close",     _pa.float64()),
    ("adj_factor",    _pa.float64()),
    ("source",        _pa.string()),
    ("ingestion_ts",  _pa.timestamp("ns", tz="UTC")),
    ("quality_flag",  _pa.string()),
    ("year",          _pa.int32()),
])


def write_silver_futures_daily(out_df: "pd.DataFrame", *, mode: str) -> None:
    """Write AFUTR rows to silver/bars/bars_1d/asset_class=tw_futures/symbol=<X>/year=<YYYY>/."""
    if out_df.empty:
        print("[silver] futures_daily: nothing to write")
        return
    dest_root = SILVER / "bars" / "bars_1d" / "asset_class=tw_futures"
    written = 0
    for (sym, yr), group in out_df.groupby(["symbol", "year"]):
        # MXF is also covered by the existing MXF cleaned-parquet ingest. To
        # avoid the two sources double-writing the same year, we skip MXF in
        # AFUTR fetch when that destination directory already has data from
        # the mxf_clean source.
        if sym == "MXF":
            mxf_dir = dest_root / f"symbol={sym}" / f"year={yr}"
            if mxf_dir.exists() and any(mxf_dir.iterdir()):
                continue
        sub_dir = dest_root / f"symbol={sym}" / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        tbl = _pa.Table.from_pandas(
            group[[f.name for f in _BARS_1D_SCHEMA]],
            schema=_BARS_1D_SCHEMA, preserve_index=False,
        )
        # Append-friendly: one parquet per fetch session
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"afutr_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] futures_daily: wrote {written:,} rows across {dest_root}")


def adapt_afutrhu_to_silver(df) -> "pd.DataFrame":
    if len(df) == 0:
        return df
    # Same individual-stock-futures skip
    contract_id = df["期貨名稱"].astype(str)
    keep = ~contract_id.str.slice(0, 3).isin([])  # placeholder — no skip here, want all
    df = df.loc[keep].copy()
    contract_id = df["期貨名稱"].astype(str)
    product = contract_id.str.slice(0, 3)
    td = pd.to_datetime(df["日期"]).dt.tz_localize(None)
    ts_utc = (td + pd.Timedelta(hours=13, minutes=45)).dt.tz_localize("Asia/Taipei").dt.tz_convert("UTC")
    out = pd.DataFrame({
        "trading_date": td.dt.date,
        "ts_utc":       ts_utc,
        "product":      product.values,
        "contract_id":  contract_id.values,
        "expiry_month": df["到期月"].astype(str).values,
        "total_oi":     pd.array(pd.to_numeric(df["全市場未沖銷部位"], errors="coerce"), dtype="Int64"),
        "top5_buy_traders":   pd.array(pd.to_numeric(df["前五大買方未沖銷部位-交易人"], errors="coerce"), dtype="Int64"),
        "top5_sell_traders":  pd.array(pd.to_numeric(df["前五大賣方未沖銷部位-交易人"], errors="coerce"), dtype="Int64"),
        "top10_buy_traders":  pd.array(pd.to_numeric(df["前十大買方未沖銷部位-交易人"], errors="coerce"), dtype="Int64"),
        "top10_sell_traders": pd.array(pd.to_numeric(df["前十大賣方未沖銷部位-交易人"], errors="coerce"), dtype="Int64"),
        "top5_buy_traders_pct":   pd.to_numeric(df["前五大買方未沖銷部位%-交易人"], errors="coerce").values,
        "top5_sell_traders_pct":  pd.to_numeric(df["前五大賣方未沖銷部位%-交易人"], errors="coerce").values,
        "top10_buy_traders_pct":  pd.to_numeric(df["前十大買方未沖銷部位%-交易人"], errors="coerce").values,
        "top10_sell_traders_pct": pd.to_numeric(df["前十大賣方未沖銷部位%-交易人"], errors="coerce").values,
        "top5_buy_institutional":   pd.array(pd.to_numeric(df["前五大買方未沖銷部位-特定法人"], errors="coerce"), dtype="Int64"),
        "top5_sell_institutional":  pd.array(pd.to_numeric(df["前五大賣方未沖銷部位-特定法人"], errors="coerce"), dtype="Int64"),
        "top10_buy_institutional":  pd.array(pd.to_numeric(df["前十大買方未沖銷部位-特定法人"], errors="coerce"), dtype="Int64"),
        "top10_sell_institutional": pd.array(pd.to_numeric(df["前十大賣方未沖銷部位-特定法人"], errors="coerce"), dtype="Int64"),
        "top5_buy_institutional_pct":   pd.to_numeric(df["前五大買方未沖銷部位%-特定法人"], errors="coerce").values,
        "top5_sell_institutional_pct":  pd.to_numeric(df["前五大賣方未沖銷部位%-特定法人"], errors="coerce").values,
        "top10_buy_institutional_pct":  pd.to_numeric(df["前十大買方未沖銷部位%-特定法人"], errors="coerce").values,
        "top10_sell_institutional_pct": pd.to_numeric(df["前十大賣方未沖銷部位%-特定法人"], errors="coerce").values,
        "source":       "tej_afutrhu",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = pd.to_datetime(out["trading_date"]).dt.year.astype("int32")
    return out


_LARGE_TRADER_SCHEMA = _pa.schema([
    ("trading_date", _pa.date32()),
    ("ts_utc",       _pa.timestamp("ns", tz="UTC")),
    ("product",      _pa.string()),
    ("contract_id",  _pa.string()),
    ("expiry_month", _pa.string()),
    ("total_oi",     _pa.int64()),
    ("top5_buy_traders",   _pa.int64()),
    ("top5_sell_traders",  _pa.int64()),
    ("top10_buy_traders",  _pa.int64()),
    ("top10_sell_traders", _pa.int64()),
    ("top5_buy_traders_pct",   _pa.float64()),
    ("top5_sell_traders_pct",  _pa.float64()),
    ("top10_buy_traders_pct",  _pa.float64()),
    ("top10_sell_traders_pct", _pa.float64()),
    ("top5_buy_institutional",   _pa.int64()),
    ("top5_sell_institutional",  _pa.int64()),
    ("top10_buy_institutional",  _pa.int64()),
    ("top10_sell_institutional", _pa.int64()),
    ("top5_buy_institutional_pct",   _pa.float64()),
    ("top5_sell_institutional_pct",  _pa.float64()),
    ("top10_buy_institutional_pct",  _pa.float64()),
    ("top10_sell_institutional_pct", _pa.float64()),
    ("source",       _pa.string()),
    ("ingestion_ts", _pa.timestamp("ns", tz="UTC")),
    ("year",         _pa.int32()),
])


def write_silver_large_trader(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] futures_large_trader: nothing to write")
        return
    dest_root = SILVER / "flows" / "tw_futures_large_trader_daily"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        tbl = _pa.Table.from_pandas(
            group[[f.name for f in _LARGE_TRADER_SCHEMA]],
            schema=_LARGE_TRADER_SCHEMA, preserve_index=False,
        )
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"afutrhu_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] futures_large_trader: wrote {written:,} rows under {dest_root}")


def adapt_apisale_to_silver(df) -> "pd.DataFrame":
    if len(df) == 0:
        return df
    fiscal = pd.to_datetime(df["年月"]).dt.tz_localize(None).dt.date
    publish = pd.to_datetime(df["營收發布日"]).dt.tz_localize(None).dt.date
    out = pd.DataFrame({
        "stock_id":      df["公司"].astype(str).values,
        "fiscal_month":  fiscal,
        "publish_date":  publish,
        "revenue_monthly_ktwd":      pd.array(pd.to_numeric(df["單月營收(千元)"], errors="coerce"), dtype="Int64"),
        "revenue_yoy_ktwd":          pd.array(pd.to_numeric(df["去年單月營收(千元)"], errors="coerce"), dtype="Int64"),
        "revenue_yoy_growth_pct":    pd.to_numeric(df["單月營收成長率％"], errors="coerce").values,
        "revenue_mom_growth_pct":    pd.to_numeric(df["單月營收與上月比％"], errors="coerce").values,
        "revenue_cum_ktwd":          pd.array(pd.to_numeric(df["累計營收(千元)"], errors="coerce"), dtype="Int64"),
        "revenue_cum_yoy_ktwd":      pd.array(pd.to_numeric(df["去年累計營收(千元)"], errors="coerce"), dtype="Int64"),
        "revenue_cum_yoy_growth_pct": pd.to_numeric(df["累計營收成長率％"], errors="coerce").values,
        "revenue_ttm_ktwd":          pd.array(pd.to_numeric(df["近12月累計營收(千元)"], errors="coerce"), dtype="Int64"),
        "revenue_ttm_growth_pct":    pd.to_numeric(df["近12月累計營收成長率％"], errors="coerce").values,
        "revenue_3m_ktwd":           pd.array(pd.to_numeric(df["近 3月累計營收(千元)"], errors="coerce"), dtype="Int64"),
        "revenue_3m_growth_pct":     pd.to_numeric(df["近3月累計營收成長率％"], errors="coerce").values,
        "shares_outstanding_kshare": pd.array(pd.to_numeric(df["流通在外股數(千股)"], errors="coerce"), dtype="Int64"),
        "revenue_monthly_per_share": pd.to_numeric(df["單月每股營收(元)"], errors="coerce").values,
        "revenue_cum_per_share":     pd.to_numeric(df["累計每股營收(元)"], errors="coerce").values,
        "revenue_ttm_per_share":     pd.to_numeric(df["近12月每股營收(元)"], errors="coerce").values,
        "revenue_3m_per_share":      pd.to_numeric(df["近 3月每股營收(元)"], errors="coerce").values,
        "source":       "tej_apisale",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = pd.DatetimeIndex(out["fiscal_month"]).year.astype("int32")
    return out


_REVENUE_SCHEMA = _pa.schema([
    ("stock_id",                  _pa.string()),
    ("fiscal_month",              _pa.date32()),
    ("publish_date",              _pa.date32()),
    ("revenue_monthly_ktwd",      _pa.int64()),
    ("revenue_yoy_ktwd",          _pa.int64()),
    ("revenue_yoy_growth_pct",    _pa.float64()),
    ("revenue_mom_growth_pct",    _pa.float64()),
    ("revenue_cum_ktwd",          _pa.int64()),
    ("revenue_cum_yoy_ktwd",      _pa.int64()),
    ("revenue_cum_yoy_growth_pct", _pa.float64()),
    ("revenue_ttm_ktwd",          _pa.int64()),
    ("revenue_ttm_growth_pct",    _pa.float64()),
    ("revenue_3m_ktwd",           _pa.int64()),
    ("revenue_3m_growth_pct",     _pa.float64()),
    ("shares_outstanding_kshare", _pa.int64()),
    ("revenue_monthly_per_share", _pa.float64()),
    ("revenue_cum_per_share",     _pa.float64()),
    ("revenue_ttm_per_share",     _pa.float64()),
    ("revenue_3m_per_share",      _pa.float64()),
    ("source",                    _pa.string()),
    ("ingestion_ts",              _pa.timestamp("ns", tz="UTC")),
    ("year",                      _pa.int32()),
])


def write_silver_revenue(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] revenue_monthly: nothing to write")
        return
    dest_root = SILVER / "fundamentals" / "revenue_monthly"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        tbl = _pa.Table.from_pandas(
            group[[f.name for f in _REVENUE_SCHEMA]],
            schema=_REVENUE_SCHEMA, preserve_index=False,
        )
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"apisale_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] revenue_monthly: wrote {written:,} rows under {dest_root}")


# ---------------------------------------------------------------------------
# CSV merge
# ---------------------------------------------------------------------------

def _merge_and_write(table: str, new_df: pd.DataFrame, *, mode: str = "merge") -> Path:
    out_fp = RAW / OUT_CSV[table]
    out_fp.parent.mkdir(parents=True, exist_ok=True)

    if mode == "overwrite" or not out_fp.exists():
        new_df.to_csv(out_fp, index=False, encoding="utf-8-sig")
        print(f"[write] {out_fp.name}: wrote {len(new_df):,} rows (overwrite)")
        return out_fp

    existing = pd.read_csv(out_fp, dtype=str)
    key_cols = [c for c in ("證券碼", "日期", "財報發布日") if c in existing.columns]
    if len(key_cols) < 2:
        merged = pd.concat([existing, new_df.astype(str)], ignore_index=True).drop_duplicates()
    else:
        new_str = new_df.astype(str)
        mask = ~existing.set_index(key_cols).index.isin(new_str.set_index(key_cols).index)
        kept = existing[mask]
        merged = pd.concat([kept, new_str], ignore_index=True).drop_duplicates(
            subset=key_cols, keep="last"
        )

    merged.to_csv(out_fp, index=False, encoding="utf-8-sig")
    print(
        f"[write] {out_fp.name}: merged -> {len(merged):,} rows "
        f"(was {len(existing):,}, +{len(merged) - len(existing):,})"
    )
    return out_fp


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def fetch(tables: list[str], start: str, end: str, *, mode: str) -> None:
    # APIPRCD is needed by both stock_daily AND inst_stock (for 流通股數 join).
    prcd_df = None
    shract_df = None

    if "stock_daily" in tables or "inst_stock" in tables:
        print(f"[fetch] TWN/APIPRCD {start}..{end}")
        prcd_df = _tej_get("TWN/APIPRCD", mdate={"gte": start, "lte": end})
        print(f"  -> {len(prcd_df):,} rows")

    if "inst_stock" in tables or "margin" in tables:
        print(f"[fetch] TWN/APISHRACT {start}..{end}")
        shract_df = _tej_get("TWN/APISHRACT", mdate={"gte": start, "lte": end})
        print(f"  -> {len(shract_df):,} rows")

    if "stock_daily" in tables:
        ew = adapt_apiprcd_to_ew_stock(prcd_df)
        _merge_and_write("stock_daily", ew, mode=mode)

    if "inst_stock" in tables:
        ew = adapt_apishract_to_ew_inst_stock(shract_df, prcd_df)
        _merge_and_write("inst_stock", ew, mode=mode)

    if "margin" in tables:
        ew = adapt_apishract_to_ew_margin(shract_df)
        _merge_and_write("margin", ew, mode=mode)

    # --- P0: direct-to-silver datasets ---

    if "futures_daily" in tables:
        print(f"[fetch] TWN/AFUTR {start}..{end}", flush=True)
        afutr = _tej_get("TWN/AFUTR", mdate={"gte": start, "lte": end})
        print(f"  -> {len(afutr):,} rows", flush=True)
        out = adapt_afutr_to_bars_1d(afutr)
        print(f"  -> {len(out):,} rows after filtering individual-stock-futures", flush=True)
        write_silver_futures_daily(out, mode=mode)

    if "futures_large_trader" in tables:
        print(f"[fetch] TWN/AFUTRHU {start}..{end}", flush=True)
        afutrhu = _tej_get("TWN/AFUTRHU", mdate={"gte": start, "lte": end})
        print(f"  -> {len(afutrhu):,} rows", flush=True)
        out = adapt_afutrhu_to_silver(afutrhu)
        write_silver_large_trader(out, mode=mode)

    if "revenue_monthly" in tables:
        print(f"[fetch] TWN/APISALE {start}..{end}", flush=True)
        apisale = _tej_get("TWN/APISALE", mdate={"gte": start, "lte": end})
        print(f"  -> {len(apisale):,} rows", flush=True)
        out = adapt_apisale_to_silver(apisale)
        write_silver_revenue(out, mode=mode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table",
        choices=LOGICAL_TABLES + ["all"],
        default="all",
        help="Which logical table to fetch (default: all)",
    )
    parser.add_argument("--start", default="20260101", help="YYYYMMDD inclusive")
    parser.add_argument(
        "--end",
        default=dt.date.today().strftime("%Y%m%d"),
        help="YYYYMMDD inclusive (default today)",
    )
    parser.add_argument(
        "--append-since-silver",
        action="store_true",
        help="Override --start to (silver max date + 1) per table.",
    )
    parser.add_argument(
        "--mode",
        choices=["merge", "overwrite"],
        default="merge",
        help="merge (default): dedupe + append; overwrite: replace.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan without calling TEJ.",
    )
    args = parser.parse_args()

    tables = LOGICAL_TABLES if args.table == "all" else [args.table]

    if args.append_since_silver:
        # Each table can have its own start; take the earliest so APIPRCD/APISHRACT
        # are pulled once for the union.
        starts = []
        for t in tables:
            mx = _silver_max_date(t)
            if mx is not None:
                starts.append(mx + dt.timedelta(days=1))
                print(f"[plan] {t}: silver max={mx} -> start {starts[-1]}")
            else:
                starts.append(dt.datetime.strptime(args.start, "%Y%m%d").date())
        eff_start = min(starts).strftime("%Y%m%d")
    else:
        eff_start = args.start

    if eff_start > args.end:
        print(f"[skip] all tables already at or beyond {args.end}")
        return

    if args.dry_run:
        print(f"[dry-run] would fetch {tables} from {eff_start} to {args.end} mode={args.mode}")
        return

    _check_env()
    fetch(tables, eff_start, args.end, mode=args.mode)

    print("\n[done] Next: run ingest + rebuild catalog:")
    print("  .venv/bin/python -m qd_ingest.cli tej-stock        --csv ../RAW_SOURCES/TEJ資料/TWN_EWPRCD_股價.csv")
    print("  .venv/bin/python -m qd_ingest.cli tej-inst-stock   --csv ../RAW_SOURCES/TEJ資料/TWN_EWTINST1_三大法人.csv")
    print("  .venv/bin/python -m qd_ingest.cli tej-margin       --csv ../RAW_SOURCES/TEJ資料/TWN_EWGIN_融資融券.csv")
    print("  .venv/bin/python -m qd_ingest.cli build-catalog")


if __name__ == "__main__":
    main()
