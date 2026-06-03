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
#   Everything else — write Parquet directly to silver/ partitioned by year.
LOGICAL_TABLES = [
    "stock_daily", "inst_stock", "margin",
    "futures_daily", "futures_large_trader", "revenue_monthly",
    # P1
    "chip_dist", "cash_dividend", "stock_futures_corp_actions", "inst_futures_full",
    # P2
    "security_attrs", "stock_trading_attrs", "accounting_raw", "capital_changes",
    "stock_valuation",
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
# Resilient fetch: signal timeout + exponential backoff + date chunking
# ---------------------------------------------------------------------------

class _TejTimeout(Exception):
    pass


def _tej_get_with_timeout(dataset: str, timeout_sec: int = 120, **params):
    """Wrap tejapi.get with SIGALRM-based timeout. Required because the TEJ
    server silently rate-limits by hanging the TCP connection — no exception
    is raised even after minutes of no response."""
    import signal

    def _handler(signum, frame):
        raise _TejTimeout(f"tejapi.get({dataset}) timed out after {timeout_sec}s")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout_sec)
    try:
        return _tej_get(dataset, **params)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _tej_get_resilient(dataset: str, *, timeout_sec: int = 120,
                        max_retries: int = 5, backoff_base: int = 60,
                        **params):
    """tejapi.get with timeout + exponential backoff on hang.

    Does NOT retry LimitExceededError (those mean the caller should chunk
    smaller). Retries _TejTimeout and generic connection errors.
    """
    import time

    last_err = None
    for attempt in range(max_retries):
        try:
            return _tej_get_with_timeout(dataset, timeout_sec=timeout_sec, **params)
        except _TejTimeout as e:
            last_err = e
            wait = backoff_base * (2 ** attempt)
            print(f"  [retry {attempt+1}/{max_retries}] {e}, sleep {wait}s", flush=True)
            time.sleep(wait)
        except Exception as e:
            # LimitExceededError or other library errors — re-raise immediately
            if "Limit" in type(e).__name__ or "Limit" in str(e):
                raise
            # Network errors: same backoff
            last_err = e
            wait = backoff_base * (2 ** attempt)
            print(f"  [retry {attempt+1}/{max_retries}] {type(e).__name__}: {e}, sleep {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"tejapi.get({dataset}) failed after {max_retries} retries; last: {last_err}")


def _tej_get_chunked(dataset: str, start: str, end: str, *,
                      chunk_days: int = 30, sleep_between: float = 3.0,
                      timeout_sec: int = 180, **params) -> pd.DataFrame:
    """Fetch a date range in chunks, concatenating results.

    Use for AFUTR / AFUTRHU (~per-day high volume). chunk_days picks the
    largest window that fits comfortably under TEJ's per-call row limit:
      - AFUTR raw is ~2.4K rows/day → 30-day chunk ≈ 72K rows (over limit)
      - At chunk_days=15 → ~36K rows (still over). Use 10 days.
    Callers should tune per-dataset.
    """
    import time

    sd = dt.datetime.strptime(start, "%Y%m%d").date()
    ed = dt.datetime.strptime(end, "%Y%m%d").date()
    dfs = []
    cur = sd
    chunk_i = 0
    while cur <= ed:
        chunk_end = min(cur + dt.timedelta(days=chunk_days - 1), ed)
        s_str = cur.strftime("%Y%m%d")
        e_str = chunk_end.strftime("%Y%m%d")
        chunk_i += 1
        print(f"  [chunk {chunk_i}] {dataset} {s_str}..{e_str}", flush=True)
        try:
            df = _tej_get_resilient(
                dataset, timeout_sec=timeout_sec,
                mdate={"gte": s_str, "lte": e_str}, **params,
            )
        except Exception as e:
            if "Limit" in type(e).__name__ or "Limit" in str(e):
                # Halve chunk window and retry recursively
                if chunk_days <= 3:
                    raise RuntimeError(
                        f"  chunked fetch hit LimitExceeded even at {chunk_days}-day window; "
                        f"caller must add coid filter"
                    ) from e
                print(f"    LimitExceeded at chunk_days={chunk_days}, halving and retrying", flush=True)
                sub = _tej_get_chunked(
                    dataset, s_str, e_str,
                    chunk_days=max(chunk_days // 2, 3),
                    sleep_between=sleep_between, timeout_sec=timeout_sec, **params,
                )
                dfs.append(sub)
            else:
                raise
        else:
            print(f"    -> {len(df):,} rows", flush=True)
            if len(df):
                dfs.append(df)
        cur = chunk_end + dt.timedelta(days=1)
        if cur <= ed:
            time.sleep(sleep_between)

    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


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
            # P2 event-based — track max ex-right date for --append-since-silver
            "capital_changes":      ("capital_changes", "ex_right_date"),
            # APIPRCD valuation companion (daily per stock)
            "stock_valuation":      ("tw_stock_valuation_daily", "trading_date"),
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
_ROOT_RE = _re.compile(r"^([A-Z]+)")   # leading uppercase letters = product root


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

    df = df.reset_index(drop=True)
    contract_id = df["期貨名稱"].astype(str)
    # Product root = leading alphabetic prefix (TX, MTX, BRF, TWNF, …),
    # NOT the first 3 chars (which would give "TX2" for TX202605).
    # Stop at the 6-digit YYYYMM date boundary so roots like E4F/G2F/MTX
    # keep their embedded digits.
    symbol = contract_id.str.extract(r"^(.+?)(?=\d{6})", expand=False).fillna(contract_id.str.slice(0, 3))
    td = pd.to_datetime(df["日期"]).dt.tz_localize(None)
    ts_utc = (td + pd.Timedelta(hours=13, minutes=45)).dt.tz_localize("Asia/Taipei").dt.tz_convert("UTC")

    n = len(df)
    out = pd.DataFrame({
        "ts_utc":       ts_utc.values,
        "trading_date": td.dt.date.values,
        "asset_class":  ["tw_futures"] * n,
        "exchange":     ["TAIFEX"] * n,
        "symbol":       symbol.values,
        "contract_id":  contract_id.values,
        "session":      ["day"] * n,
        "open":         pd.to_numeric(df["開盤價"], errors="coerce").values,
        "high":         pd.to_numeric(df["最高價"], errors="coerce").values,
        "low":          pd.to_numeric(df["最低價"], errors="coerce").values,
        "close":        pd.to_numeric(df["收盤價"], errors="coerce").values,
        "volume":       pd.array(pd.to_numeric(df["成交張數(量)"], errors="coerce"), dtype="Int64"),
        "open_interest": pd.array(pd.to_numeric(df["未平倉合約數"], errors="coerce"), dtype="Int64"),
        "vwap":         pd.to_numeric(df["標的證券價格"], errors="coerce").values,
        "settlement":   pd.to_numeric(df["每日結算價"], errors="coerce").values,
        "adj_open":     [float("nan")] * n,
        "adj_high":     [float("nan")] * n,
        "adj_low":      [float("nan")] * n,
        "adj_close":    [float("nan")] * n,
        "adj_factor":   [float("nan")] * n,
        "source":       ["tej_afutr"] * n,
        "ingestion_ts": [pd.Timestamp.now(tz="UTC")] * n,
        "quality_flag": ["ok"] * n,
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
    df = df.reset_index(drop=True)
    contract_id = df["期貨名稱"].astype(str)
    product = contract_id.str.extract(r"^(.+?)(?=\d{6})", expand=False).fillna(contract_id.str.slice(0, 3))
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
# P1: APISHRACTW (集保庫存 / 千張大戶分布)
# ---------------------------------------------------------------------------

def adapt_apishractw_to_silver(df) -> "pd.DataFrame":
    if len(df) == 0:
        return df
    df = df.reset_index(drop=True)
    td = pd.to_datetime(df["資料日"]).dt.tz_localize(None)
    out = pd.DataFrame({
        "stock_id":      df["證券名稱"].astype(str).values,
        "trading_date":  td.dt.date.values,
        "exchange":      df["市場別"].astype(str).values,
        "publish_date_holdings": pd.to_datetime(df["公告日(集保庫存)"]).dt.tz_localize(None).dt.date.values,
        "holdings_total_kshare": pd.array(pd.to_numeric(df["集保庫存股數(千股)"], errors="coerce"), dtype="Int64"),
        "pledged_kshare":        pd.array(pd.to_numeric(df["設質股數(千股)"], errors="coerce"), dtype="Int64"),
        "publish_date_dist":     pd.to_datetime(df["公告日(集保股權)"]).dt.tz_localize(None).dt.date.values,
        "holders_under_400":     pd.array(pd.to_numeric(df["未滿400張集保人數"], errors="coerce"), dtype="Int64"),
        "shares_under_400":      pd.array(pd.to_numeric(df["未滿400張集保張數"], errors="coerce"), dtype="Int64"),
        "pct_under_400":         pd.to_numeric(df["未滿400張集保占比"], errors="coerce").values,
        "holders_400_600":       pd.array(pd.to_numeric(df["400-600張集保人數"], errors="coerce"), dtype="Int64"),
        "shares_400_600":        pd.array(pd.to_numeric(df["400-600張集保張數"], errors="coerce"), dtype="Int64"),
        "pct_400_600":           pd.to_numeric(df["400-600張集保占比"], errors="coerce").values,
        "holders_600_800":       pd.array(pd.to_numeric(df["600-800張集保人數"], errors="coerce"), dtype="Int64"),
        "shares_600_800":        pd.array(pd.to_numeric(df["600-800張集保張數"], errors="coerce"), dtype="Int64"),
        "pct_600_800":           pd.to_numeric(df["600-800張集保占比"], errors="coerce").values,
        "holders_800_1000":      pd.array(pd.to_numeric(df["800-1000張集保人數"], errors="coerce"), dtype="Int64"),
        "shares_800_1000":       pd.array(pd.to_numeric(df["800-1000張集保張數"], errors="coerce"), dtype="Int64"),
        "pct_800_1000":          pd.to_numeric(df["800-1000張集保占比"], errors="coerce").values,
        "holders_over_1000":     pd.array(pd.to_numeric(df["超過1000張集保人數"], errors="coerce"), dtype="Int64"),
        "shares_over_1000":      pd.array(pd.to_numeric(df["超過1000張集保張數"], errors="coerce"), dtype="Int64"),
        "pct_over_1000":         pd.to_numeric(df["超過1000張集保占比"], errors="coerce").values,
        "holders_over_400":      pd.array(pd.to_numeric(df["超過400張集保人數"], errors="coerce"), dtype="Int64"),
        "shares_over_400":       pd.array(pd.to_numeric(df["超過400張集保張數"], errors="coerce"), dtype="Int64"),
        "pct_over_400":          pd.to_numeric(df["超過400張集保占比"], errors="coerce").values,
        "source":       "tej_apishractw",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = pd.to_datetime(out["trading_date"]).year.astype("int32") if hasattr(pd.to_datetime(out["trading_date"]), "year") else pd.DatetimeIndex(out["trading_date"]).year.astype("int32")
    return out


_CHIP_DIST_SCHEMA = _pa.schema([
    ("stock_id",          _pa.string()),
    ("trading_date",      _pa.date32()),
    ("exchange",          _pa.string()),
    ("publish_date_holdings", _pa.date32()),
    ("holdings_total_kshare",  _pa.int64()),
    ("pledged_kshare",     _pa.int64()),
    ("publish_date_dist",  _pa.date32()),
    ("holders_under_400",  _pa.int64()),
    ("shares_under_400",   _pa.int64()),
    ("pct_under_400",      _pa.float64()),
    ("holders_400_600",    _pa.int64()),
    ("shares_400_600",     _pa.int64()),
    ("pct_400_600",        _pa.float64()),
    ("holders_600_800",    _pa.int64()),
    ("shares_600_800",     _pa.int64()),
    ("pct_600_800",        _pa.float64()),
    ("holders_800_1000",   _pa.int64()),
    ("shares_800_1000",    _pa.int64()),
    ("pct_800_1000",       _pa.float64()),
    ("holders_over_1000",  _pa.int64()),
    ("shares_over_1000",   _pa.int64()),
    ("pct_over_1000",      _pa.float64()),
    ("holders_over_400",   _pa.int64()),
    ("shares_over_400",    _pa.int64()),
    ("pct_over_400",       _pa.float64()),
    ("source",       _pa.string()),
    ("ingestion_ts", _pa.timestamp("ns", tz="UTC")),
    ("year",         _pa.int32()),
])


def write_silver_chip_dist(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] chip_dist: nothing to write")
        return
    dest_root = SILVER / "flows" / "tw_chip_dist_daily"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        tbl = _pa.Table.from_pandas(
            group[[f.name for f in _CHIP_DIST_SCHEMA]],
            schema=_CHIP_DIST_SCHEMA, preserve_index=False,
        )
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"apishractw_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] chip_dist: wrote {written:,} rows under {dest_root}")


# ---------------------------------------------------------------------------
# P1: ADIV (上市櫃現金股息)
# ---------------------------------------------------------------------------

def adapt_adiv_to_silver(df) -> "pd.DataFrame":
    if len(df) == 0:
        return df
    df = df.reset_index(drop=True)
    ex_date = pd.to_datetime(df["除息日"]).dt.tz_localize(None)
    out = pd.DataFrame({
        "stock_id":          df["公司"].astype(str).values,
        "ex_date":           ex_date.dt.date.values,
        "period_end":        pd.to_datetime(df["盈餘分派_迄日"]).dt.tz_localize(None).dt.date.values,
        "period_start":      pd.to_datetime(df["盈餘分派_起日"]).dt.tz_localize(None).dt.date.values,
        "dividend_type":     df["股息分配型態"].astype(str).values,
        "cash_div_earnings": pd.to_numeric(df["現金股利(元)_盈餘"], errors="coerce").values,
        "cash_div_reserve":  pd.to_numeric(df["現金股利(元)_公積"], errors="coerce").values,
        "interest_value":    pd.to_numeric(df["息值(元)"], errors="coerce").values,
        "special_dividend":  pd.to_numeric(df["特殊股利(元)"], errors="coerce").values,
        "currency":          df["發放幣別"].astype(str).values,
        "total_earnings_ktwd": pd.array(pd.to_numeric(df["股息總額-盈餘(千元)"], errors="coerce"), dtype="Int64"),
        "total_reserve_ktwd":  pd.array(pd.to_numeric(df["股息總額-公積(千元)"], errors="coerce"), dtype="Int64"),
        "total_special_ktwd":  pd.array(pd.to_numeric(df["股息總額-特殊(千元)"], errors="coerce"), dtype="Int64"),
        "total_cash_div_ktwd": pd.array(pd.to_numeric(df["股息總額(千元)"], errors="coerce"), dtype="Int64"),
        "pay_date":          pd.to_datetime(df["股息發放日"]).dt.tz_localize(None).dt.date.values,
        "prev_close":        pd.to_numeric(df["前一日收盤價"], errors="coerce").values,
        "ref_price":         pd.to_numeric(df["除息(權)參考價(元)"], errors="coerce").values,
        "announce_date":     pd.to_datetime(df["除息公告日"]).dt.tz_localize(None).dt.date.values,
        "source":       "tej_adiv",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = pd.DatetimeIndex(out["ex_date"]).year.astype("int32")
    return out


_DIV_SCHEMA = _pa.schema([
    ("stock_id",         _pa.string()),
    ("ex_date",          _pa.date32()),
    ("period_end",       _pa.date32()),
    ("period_start",     _pa.date32()),
    ("dividend_type",    _pa.string()),
    ("cash_div_earnings", _pa.float64()),
    ("cash_div_reserve",  _pa.float64()),
    ("interest_value",    _pa.float64()),
    ("special_dividend",  _pa.float64()),
    ("currency",         _pa.string()),
    ("total_earnings_ktwd", _pa.int64()),
    ("total_reserve_ktwd",  _pa.int64()),
    ("total_special_ktwd",  _pa.int64()),
    ("total_cash_div_ktwd", _pa.int64()),
    ("pay_date",         _pa.date32()),
    ("prev_close",       _pa.float64()),
    ("ref_price",        _pa.float64()),
    ("announce_date",    _pa.date32()),
    ("source",       _pa.string()),
    ("ingestion_ts", _pa.timestamp("ns", tz="UTC")),
    ("year",         _pa.int32()),
])


def write_silver_cash_dividend(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] cash_dividend: nothing to write")
        return
    dest_root = SILVER / "fundamentals" / "cash_dividend_events"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        tbl = _pa.Table.from_pandas(
            group[[f.name for f in _DIV_SCHEMA]],
            schema=_DIV_SCHEMA, preserve_index=False,
        )
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"adiv_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] cash_dividend: wrote {written:,} rows under {dest_root}")


# ---------------------------------------------------------------------------
# P1: AFUTRSTK (個股期貨除權息 / 契約調整)
# ---------------------------------------------------------------------------

def adapt_afutrstk_to_silver(df) -> "pd.DataFrame":
    if len(df) == 0:
        return df
    df = df.reset_index(drop=True)
    td = pd.to_datetime(df["調整日"]).dt.tz_localize(None)
    out = pd.DataFrame({
        "futures_code":  df["期貨代碼"].astype(str).values,
        "adjust_date":   td.dt.date.values,
        "adjust_reason": df["契約調整因"].astype(str).values,
        "stock_div_per_share": pd.to_numeric(df["每股股票股利(元)"], errors="coerce").values,
        "cash_div_per_share":  pd.to_numeric(df["每股現金股利(元)"], errors="coerce").values,
        "cash_adjusted_yn":    df["現金股利是否調整Y/N"].astype(str).values,
        "shares_per_lot":      pd.to_numeric(df["每口折算股數(股)"], errors="coerce").values,
        "cash_div_per_lot":    pd.to_numeric(df["每口折算現金股利(元)"], errors="coerce").values,
        "equity_value_per_lot": pd.to_numeric(df["每口折算現增價值(元)"], errors="coerce").values,
        "ref_price":           pd.to_numeric(df["調整日參考價(元)"], errors="coerce").values,
        "contract_type":       df["加掛標準/調整契約"].astype(str).values,
        "source":       "tej_afutrstk",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = pd.DatetimeIndex(out["adjust_date"]).year.astype("int32")
    return out


_AFUTRSTK_SCHEMA = _pa.schema([
    ("futures_code",  _pa.string()),
    ("adjust_date",   _pa.date32()),
    ("adjust_reason", _pa.string()),
    ("stock_div_per_share",  _pa.float64()),
    ("cash_div_per_share",   _pa.float64()),
    ("cash_adjusted_yn",     _pa.string()),
    ("shares_per_lot",       _pa.float64()),
    ("cash_div_per_lot",     _pa.float64()),
    ("equity_value_per_lot", _pa.float64()),
    ("ref_price",            _pa.float64()),
    ("contract_type",        _pa.string()),
    ("source",       _pa.string()),
    ("ingestion_ts", _pa.timestamp("ns", tz="UTC")),
    ("year",         _pa.int32()),
])


def write_silver_sfut_corp_actions(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] stock_futures_corp_actions: nothing to write")
        return
    dest_root = SILVER / "flows" / "tw_stock_futures_corp_actions"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        tbl = _pa.Table.from_pandas(
            group[[f.name for f in _AFUTRSTK_SCHEMA]],
            schema=_AFUTRSTK_SCHEMA, preserve_index=False,
        )
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"afutrstk_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] stock_futures_corp_actions: wrote {written:,} rows under {dest_root}")


# ---------------------------------------------------------------------------
# P1: AFINST (期交所三大法人完整版 — 含每身份×每商品的多空交易+未平倉口數金額)
# ---------------------------------------------------------------------------

def adapt_afinst_to_silver(df) -> "pd.DataFrame":
    if len(df) == 0:
        return df
    df = df.reset_index(drop=True)
    td = pd.to_datetime(df["年月日"]).dt.tz_localize(None)
    ts_utc = (td + pd.Timedelta(hours=13, minutes=45)).dt.tz_localize("Asia/Taipei").dt.tz_convert("UTC")
    out = pd.DataFrame({
        "trading_date":  td.dt.date.values,
        "ts_utc":        ts_utc.values,
        "identity_code": df["名稱"].astype(str).values,
        "identity_zh":   df["身份別名稱(中)"].astype(str).values,
        "identity_en":   df["身份別名稱(英)"].astype(str).values,
        "long_volume":         pd.array(pd.to_numeric(df["多方交易口數"], errors="coerce"), dtype="Int64"),
        "long_value_ktwd":     pd.array(pd.to_numeric(df["多方交易契約金額"], errors="coerce"), dtype="Int64"),
        "long_volume_pct":     pd.to_numeric(df["多方交易口數比重"], errors="coerce").values,
        "short_volume":        pd.array(pd.to_numeric(df["空方交易口數"], errors="coerce"), dtype="Int64"),
        "short_value_ktwd":    pd.array(pd.to_numeric(df["空方交易契約金額"], errors="coerce"), dtype="Int64"),
        "short_volume_pct":    pd.to_numeric(df["空方交易口數比重"], errors="coerce").values,
        "net_volume":          pd.array(pd.to_numeric(df["多空交易口數淨額"], errors="coerce"), dtype="Int64"),
        "net_value_ktwd":      pd.array(pd.to_numeric(df["多空交易契約金額淨額"], errors="coerce"), dtype="Int64"),
        "long_oi":             pd.array(pd.to_numeric(df["多方未平倉口數"], errors="coerce"), dtype="Int64"),
        "long_oi_value_ktwd":  pd.array(pd.to_numeric(df["多方未平倉契約金額"], errors="coerce"), dtype="Int64"),
        "long_oi_pct":         pd.to_numeric(df["多方未平倉口數持有比"], errors="coerce").values,
        "short_oi":            pd.array(pd.to_numeric(df["空方未平倉口數"], errors="coerce"), dtype="Int64"),
        "short_oi_value_ktwd": pd.array(pd.to_numeric(df["空方未平倉契約金額"], errors="coerce"), dtype="Int64"),
        "short_oi_pct":        pd.to_numeric(df["空方未平倉口數持有比"], errors="coerce").values,
        "net_oi":              pd.array(pd.to_numeric(df["多空未平倉口數淨額"], errors="coerce"), dtype="Int64"),
        "net_oi_value_ktwd":   pd.array(pd.to_numeric(df["多空未平倉契約淨額"], errors="coerce"), dtype="Int64"),
        "source":       "tej_afinst",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = pd.DatetimeIndex(out["trading_date"]).year.astype("int32")
    return out


_AFINST_SCHEMA = _pa.schema([
    ("trading_date",  _pa.date32()),
    ("ts_utc",        _pa.timestamp("ns", tz="UTC")),
    ("identity_code", _pa.string()),
    ("identity_zh",   _pa.string()),
    ("identity_en",   _pa.string()),
    ("long_volume",      _pa.int64()),
    ("long_value_ktwd",  _pa.int64()),
    ("long_volume_pct",  _pa.float64()),
    ("short_volume",     _pa.int64()),
    ("short_value_ktwd", _pa.int64()),
    ("short_volume_pct", _pa.float64()),
    ("net_volume",       _pa.int64()),
    ("net_value_ktwd",   _pa.int64()),
    ("long_oi",          _pa.int64()),
    ("long_oi_value_ktwd", _pa.int64()),
    ("long_oi_pct",      _pa.float64()),
    ("short_oi",         _pa.int64()),
    ("short_oi_value_ktwd", _pa.int64()),
    ("short_oi_pct",     _pa.float64()),
    ("net_oi",           _pa.int64()),
    ("net_oi_value_ktwd", _pa.int64()),
    ("source",       _pa.string()),
    ("ingestion_ts", _pa.timestamp("ns", tz="UTC")),
    ("year",         _pa.int32()),
])


def write_silver_inst_futures_full(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] inst_futures_full: nothing to write")
        return
    dest_root = SILVER / "flows" / "tw_inst_futures_full_daily"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        tbl = _pa.Table.from_pandas(
            group[[f.name for f in _AFINST_SCHEMA]],
            schema=_AFINST_SCHEMA, preserve_index=False,
        )
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"afinst_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] inst_futures_full: wrote {written:,} rows under {dest_root}")


# ---------------------------------------------------------------------------
# P2: APISTOCK (證券屬性資料表) — single row per stock, metadata
# ---------------------------------------------------------------------------

def adapt_apistock_to_silver(df) -> "pd.DataFrame":
    if len(df) == 0:
        return df
    df = df.reset_index(drop=True)
    out = pd.DataFrame({
        "stock_id":     df["公司簡稱"].astype(str).values,
        "current_status": df["目前狀態"].astype(str).values,
        "name_zh":      df["證券名稱"].astype(str).values,
        "name_full_zh": df["證券全稱"].astype(str).values,
        "name_en":      df["英文簡稱"].astype(str).values,
        "name_full_en": df["英文全稱"].astype(str).values,
        "unified_no":   df["統一編號"].astype(str).values,
        "list_date":            pd.to_datetime(df["最近一次上市日"], errors="coerce").dt.tz_localize(None).dt.date.values,
        "tse_first_list_date":  pd.to_datetime(df["首次TSE上市日"],  errors="coerce").dt.tz_localize(None).dt.date.values,
        "otc_first_list_date":  pd.to_datetime(df["首次OTC上市日"],  errors="coerce").dt.tz_localize(None).dt.date.values,
        "reg_first_list_date":  pd.to_datetime(df["首次REG上市日"],  errors="coerce").dt.tz_localize(None).dt.date.values,
        "delist_date":          pd.to_datetime(df["下市日"],         errors="coerce").dt.tz_localize(None).dt.date.values,
        "main_industry_zh":     df["主產業別(中)"].astype(str).values,
        "main_industry_en":     df["主產業別(英)"].astype(str).values,
        "sub_industry_zh":      df["子產業別(中)"].astype(str).values,
        "sub_industry_en":      df["子產業別(英)"].astype(str).values,
        "source":       "tej_apistock",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    return out


_APISTOCK_SCHEMA = _pa.schema([
    ("stock_id",         _pa.string()),
    ("current_status",   _pa.string()),
    ("name_zh",          _pa.string()),
    ("name_full_zh",     _pa.string()),
    ("name_en",          _pa.string()),
    ("name_full_en",     _pa.string()),
    ("unified_no",       _pa.string()),
    ("list_date",         _pa.date32()),
    ("tse_first_list_date", _pa.date32()),
    ("otc_first_list_date", _pa.date32()),
    ("reg_first_list_date", _pa.date32()),
    ("delist_date",       _pa.date32()),
    ("main_industry_zh", _pa.string()),
    ("main_industry_en", _pa.string()),
    ("sub_industry_zh",  _pa.string()),
    ("sub_industry_en",  _pa.string()),
    ("source",       _pa.string()),
    ("ingestion_ts", _pa.timestamp("ns", tz="UTC")),
])


def write_silver_security_attrs(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] security_attrs: nothing to write")
        return
    dest_root = SILVER / "reference" / "security_attrs"
    if mode == "overwrite" and dest_root.exists():
        _shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    tbl = _pa.Table.from_pandas(
        out_df[[f.name for f in _APISTOCK_SCHEMA]],
        schema=_APISTOCK_SCHEMA, preserve_index=False,
    )
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = dest_root / f"apistock_{ts}.parquet"
    _pq.write_table(tbl, fp, compression="zstd")
    print(f"[silver] security_attrs: wrote {len(out_df):,} rows -> {fp}")


# ---------------------------------------------------------------------------
# P2: APISTKATTR (個股日交易註記資訊) — daily flags
# ---------------------------------------------------------------------------

def adapt_apistkattr_to_silver(df) -> "pd.DataFrame":
    if len(df) == 0:
        return df
    df = df.reset_index(drop=True)
    td = pd.to_datetime(df["資料日"]).dt.tz_localize(None)
    out = pd.DataFrame({
        "stock_id":      df["證券名稱"].astype(str).values,
        "trading_date":  td.dt.date.values,
        "stock_type_zh": df["證券種類(中)"].astype(str).values,
        "stock_type_en": df["證券種類(英)"].astype(str).values,
        "market":        df["市場別"].astype(str).values,
        "board_zh":      df["板塊別(中)"].astype(str).values,
        "board_en":      df["板塊別(英)"].astype(str).values,
        "main_industry_zh": df["主產業別(中)"].astype(str).values,
        "main_industry_en": df["主產業別(英)"].astype(str).values,
        "sub_industry_zh":  df["子產業別(中)"].astype(str).values,
        "sub_industry_en":  df["子產業別(英)"].astype(str).values,
        "is_attention":    df["是否為注意股票"].astype(str).values,
        "is_disposition":  df["是否為處置股票"].astype(str).values,
        "match_interval_sec": pd.to_numeric(df["分盤間隔時間"], errors="coerce").values,
        "is_suspended":    df["是否暫停交易"].astype(str).values,
        "is_full_settle":  df["是否全額交割"].astype(str).values,
        "limit_flag":      df["漲跌停註記"].astype(str).values,
        "limit_open_flag": df["是否開盤即漲跌停"].astype(str).values,
        "no_daytrade_buy_first":  df["暫停當沖先買後賣註記"].astype(str).values,
        "no_daytrade_sell_first": df["暫停當沖先賣後買註記"].astype(str).values,
        "is_twn50":        df["是否為臺灣50成分股"].astype(str).values,
        "is_msci":         df["是否為MSCI成分股"].astype(str).values,
        "is_otc50":        df["是否為富櫃50成分股"].astype(str).values,
        "is_otc200":       df["是否為富櫃200成分股"].astype(str).values,
        "is_hdiv":         df["是否為高股息指數成分"].astype(str).values,
        "is_mcap":         df["是否為中型100成分股"].astype(str).values,
        "source":       "tej_apistkattr",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = pd.DatetimeIndex(out["trading_date"]).year.astype("int32")
    return out


_APISTKATTR_SCHEMA = _pa.schema([
    ("stock_id",        _pa.string()),
    ("trading_date",    _pa.date32()),
    ("stock_type_zh",   _pa.string()),
    ("stock_type_en",   _pa.string()),
    ("market",          _pa.string()),
    ("board_zh",        _pa.string()),
    ("board_en",        _pa.string()),
    ("main_industry_zh", _pa.string()),
    ("main_industry_en", _pa.string()),
    ("sub_industry_zh", _pa.string()),
    ("sub_industry_en", _pa.string()),
    ("is_attention",    _pa.string()),
    ("is_disposition",  _pa.string()),
    ("match_interval_sec", _pa.float64()),
    ("is_suspended",    _pa.string()),
    ("is_full_settle",  _pa.string()),
    ("limit_flag",      _pa.string()),
    ("limit_open_flag", _pa.string()),
    ("no_daytrade_buy_first",  _pa.string()),
    ("no_daytrade_sell_first", _pa.string()),
    ("is_twn50",        _pa.string()),
    ("is_msci",         _pa.string()),
    ("is_otc50",        _pa.string()),
    ("is_otc200",       _pa.string()),
    ("is_hdiv",         _pa.string()),
    ("is_mcap",         _pa.string()),
    ("source",       _pa.string()),
    ("ingestion_ts", _pa.timestamp("ns", tz="UTC")),
    ("year",         _pa.int32()),
])


def write_silver_stock_trading_attrs(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] stock_trading_attrs: nothing to write")
        return
    dest_root = SILVER / "flows" / "tw_stock_trading_attrs_daily"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        tbl = _pa.Table.from_pandas(
            group[[f.name for f in _APISTKATTR_SCHEMA]],
            schema=_APISTKATTR_SCHEMA, preserve_index=False,
        )
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"apistkattr_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] stock_trading_attrs: wrote {written:,} rows under {dest_root}")


# ---------------------------------------------------------------------------
# P2: AINVFINB (118 個會計簽證科目) — wide table, keep all Chinese cols
# ---------------------------------------------------------------------------

def adapt_ainvfinb_to_silver(df) -> "pd.DataFrame":
    """AINVFINB is 118 columns of raw accounting numbers + pre-computed ratios.
    Keep all original Chinese column names (we don't try to remap — strategies
    that need specific accounts can select by name). Only normalize the key
    cols + add partitioning column."""
    if len(df) == 0:
        return df
    df = df.reset_index(drop=True).copy()
    # Normalize key cols, rename to canonical English for primary key + year partition
    df["stock_id"]     = df["公司"].astype(str)
    df["fiscal_month"] = pd.to_datetime(df["年/月"]).dt.tz_localize(None).dt.date
    df["publish_date"] = pd.to_datetime(df["公告日"], errors="coerce").dt.tz_localize(None)
    df["period_type"]  = df["期間別(A/Q/TTM)"].astype(str)
    df["fiscal_quarter"] = pd.to_numeric(df["季別"], errors="coerce").astype("Int64")
    df["source"]       = "tej_ainvfinb"
    df["ingestion_ts"] = pd.Timestamp.now(tz="UTC")
    df["year"] = pd.DatetimeIndex(df["fiscal_month"]).year.astype("int32")
    return df


def write_silver_accounting_raw(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] accounting_raw: nothing to write")
        return
    dest_root = SILVER / "fundamentals" / "accounting_raw"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        # Let pyarrow infer schema — 118 wide cols with Chinese names is hard to
        # type explicitly. Drop the original 公司/年/月/公告日/期間別/季別 redundancy.
        drop_cols = ["公司", "年/月", "公告日", "期間別(A/Q/TTM)", "季別"]
        slim = group.drop(columns=[c for c in drop_cols if c in group.columns])
        tbl = _pa.Table.from_pandas(slim, preserve_index=False)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"ainvfinb_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] accounting_raw: wrote {written:,} rows under {dest_root}")


# ---------------------------------------------------------------------------
# P2: APISTK1 (資本形成 / 股本變動事件) — event-based, wide 75-col
# ---------------------------------------------------------------------------

def adapt_apistk1_to_silver(df) -> "pd.DataFrame":
    """TWN/APISTK1 (資本形成) -> silver.

    Event-based: one row per company per capital-change event, keyed on
    (公司, 除權日). Covers 現金增資 / 盈餘配股 / 公積增資 / 員工分紅 / 減資 /
    CB轉換 / 特別股轉換 / 庫藏股註銷 / 合併 / 受讓 / 員工認股權證 / IPO / 私募 等.

    Like accounting_raw (AINVFINB), we DON'T remap all 75 columns — only the
    primary key is normalized to English (stock_id, ex_right_date) and a year
    partition column added; the remaining ~73 Chinese columns are kept as-is
    so strategies select event fields by their documented Chinese names.
    """
    if len(df) == 0:
        return df
    df = df.reset_index(drop=True).copy()
    df["stock_id"]      = df["公司"].astype(str)
    df["ex_right_date"] = pd.to_datetime(df["除權日"]).dt.tz_localize(None).dt.date
    df["source"]        = "tej_apistk1"
    df["ingestion_ts"]  = pd.Timestamp.now(tz="UTC")
    df["year"] = pd.DatetimeIndex(df["ex_right_date"]).year.astype("int32")
    return df


def write_silver_capital_changes(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] capital_changes: nothing to write")
        return
    dest_root = SILVER / "fundamentals" / "capital_changes"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        # Drop the original key cols (normalized to stock_id/ex_right_date) to
        # avoid redundancy; let pyarrow infer schema for the wide remainder.
        drop_cols = ["公司", "除權日"]
        slim = group.drop(columns=[c for c in drop_cols if c in group.columns])
        tbl = _pa.Table.from_pandas(slim, preserve_index=False)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"apistk1_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] capital_changes: wrote {written:,} rows under {dest_root}")


# ---------------------------------------------------------------------------
# APIPRCD valuation/microstructure (估值 + 微結構欄) — companion to bars_1d
# ---------------------------------------------------------------------------

def adapt_apiprcd_to_valuation_silver(df) -> "pd.DataFrame":
    """TWN/APIPRCD (交易資料-股價資料) -> silver valuation table.

    OHLCV already lives in bars_1d (asset_class=tw_stock); this table captures
    the valuation + microstructure columns APIPRCD uniquely carries, keyed
    (stock_id, trading_date) so it joins back to bars. Unlike the wide
    accounting tables, APIPRCD is a tidy 29-col set so we map to English with
    explicit types.
    """
    if len(df) == 0:
        return df
    df = df.reset_index(drop=True)
    td = pd.to_datetime(df["資料日"]).dt.tz_localize(None)

    def _num(col):
        return pd.to_numeric(df[col], errors="coerce")

    def _int(col):
        return pd.array(pd.to_numeric(df[col], errors="coerce"), dtype="Int64")

    out = pd.DataFrame({
        "stock_id":           df["證券名稱"].astype(str).values,
        "trading_date":       td.dt.date.values,
        "market":             df["市場別"].astype(str).values,
        "roi_pct":            _num("報酬率").values,             # 日報酬率 %
        "high_low_spread_pct": _num("高低價差").values,           # (high-low)/... %
        "turnover_pct":       _num("周轉率").values,             # 周轉率 %
        "bid":                _num("最後揭示買價").values,
        "offer":              _num("最後揭示賣價").values,
        "avg_price":          _num("當日均價").values,
        "amount":             _int("成交金額(元)"),               # 成交金額 (元)
        "trades":             _int("成交筆數"),
        "shares_outstanding": _int("流通在外股數(千股)"),          # 千股
        "market_cap":         _int("個股市值(元)"),               # 元
        "market_cap_pct":     _num("市值比重").values,
        "amount_pct":         _num("成交金額比重").values,
        "per":                _num("本益比").values,
        "pbr":                _num("股價淨值比").values,
        "div_yield_pct":      _num("股利殖利率").values,
        "cash_div_yield_pct": _num("現金股利率(TEJ)").values,
        "per_tej":            _num("本益比(TEJ)").values,
        "pbr_tej":            _num("股價淨值比(TEJ)").values,
        "psr_tej":            _num("股價營收比(TEJ)").values,
        "adj_factor":         _num("調整係數").values,
        "adj_factor_exright": _num("調整係數(除權)").values,
        "source":             "tej_apiprcd",
        "ingestion_ts":       pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = pd.DatetimeIndex(out["trading_date"]).year.astype("int32")
    return out


_APIPRCD_VAL_SCHEMA = _pa.schema([
    ("stock_id",            _pa.string()),
    ("trading_date",        _pa.date32()),
    ("market",              _pa.string()),
    ("roi_pct",             _pa.float64()),
    ("high_low_spread_pct", _pa.float64()),
    ("turnover_pct",        _pa.float64()),
    ("bid",                 _pa.float64()),
    ("offer",               _pa.float64()),
    ("avg_price",           _pa.float64()),
    ("amount",              _pa.int64()),
    ("trades",              _pa.int64()),
    ("shares_outstanding",  _pa.int64()),
    ("market_cap",          _pa.int64()),
    ("market_cap_pct",      _pa.float64()),
    ("amount_pct",          _pa.float64()),
    ("per",                 _pa.float64()),
    ("pbr",                 _pa.float64()),
    ("div_yield_pct",       _pa.float64()),
    ("cash_div_yield_pct",  _pa.float64()),
    ("per_tej",             _pa.float64()),
    ("pbr_tej",             _pa.float64()),
    ("psr_tej",             _pa.float64()),
    ("adj_factor",          _pa.float64()),
    ("adj_factor_exright",  _pa.float64()),
    ("source",              _pa.string()),
    ("ingestion_ts",        _pa.timestamp("ns", tz="UTC")),
    ("year",                _pa.int32()),
])


def write_silver_stock_valuation(out_df: "pd.DataFrame", *, mode: str) -> None:
    if out_df.empty:
        print("[silver] stock_valuation: nothing to write")
        return
    dest_root = SILVER / "flows" / "tw_stock_valuation_daily"
    written = 0
    for yr, group in out_df.groupby("year"):
        sub_dir = dest_root / f"year={yr}"
        if mode == "overwrite" and sub_dir.exists():
            _shutil.rmtree(sub_dir)
        sub_dir.mkdir(parents=True, exist_ok=True)
        tbl = _pa.Table.from_pandas(
            group[[f.name for f in _APIPRCD_VAL_SCHEMA]],
            schema=_APIPRCD_VAL_SCHEMA, preserve_index=False,
        )
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = sub_dir / f"apiprcd_val_{ts}.parquet"
        _pq.write_table(tbl, fp, compression="zstd")
        written += len(group)
    print(f"[silver] stock_valuation: wrote {written:,} rows under {dest_root}")


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
        print(f"[fetch] TWN/AFUTR {start}..{end} (chunked)", flush=True)
        afutr = _tej_get_chunked("TWN/AFUTR", start, end, chunk_days=10)
        print(f"  -> {len(afutr):,} rows total", flush=True)
        out = adapt_afutr_to_bars_1d(afutr)
        print(f"  -> {len(out):,} rows after filtering individual-stock-futures", flush=True)
        write_silver_futures_daily(out, mode=mode)

    if "futures_large_trader" in tables:
        print(f"[fetch] TWN/AFUTRHU {start}..{end} (chunked)", flush=True)
        afutrhu = _tej_get_chunked("TWN/AFUTRHU", start, end, chunk_days=30)
        print(f"  -> {len(afutrhu):,} rows total", flush=True)
        out = adapt_afutrhu_to_silver(afutrhu)
        write_silver_large_trader(out, mode=mode)

    if "revenue_monthly" in tables:
        print(f"[fetch] TWN/APISALE {start}..{end}", flush=True)
        apisale = _tej_get_resilient("TWN/APISALE", mdate={"gte": start, "lte": end})
        print(f"  -> {len(apisale):,} rows", flush=True)
        out = adapt_apisale_to_silver(apisale)
        write_silver_revenue(out, mode=mode)

    # --- P1 ---

    if "chip_dist" in tables:
        print(f"[fetch] TWN/APISHRACTW {start}..{end} (chunked, weekly per stock)", flush=True)
        # 2K stocks × 52 weeks/year ≈ 100K rows/year — chunk by 90 days
        df = _tej_get_chunked("TWN/APISHRACTW", start, end, chunk_days=60)
        print(f"  -> {len(df):,} rows total", flush=True)
        out = adapt_apishractw_to_silver(df)
        write_silver_chip_dist(out, mode=mode)

    if "cash_dividend" in tables:
        print(f"[fetch] TWN/ADIV {start}..{end} (event-based, chunked yearly)", flush=True)
        df = _tej_get_chunked("TWN/ADIV", start, end, chunk_days=365)
        print(f"  -> {len(df):,} rows total", flush=True)
        out = adapt_adiv_to_silver(df)
        write_silver_cash_dividend(out, mode=mode)

    if "stock_futures_corp_actions" in tables:
        print(f"[fetch] TWN/AFUTRSTK {start}..{end} (event-based, small)", flush=True)
        df = _tej_get_resilient("TWN/AFUTRSTK", mdate={"gte": start, "lte": end})
        print(f"  -> {len(df):,} rows", flush=True)
        out = adapt_afutrstk_to_silver(df)
        write_silver_sfut_corp_actions(out, mode=mode)

    if "inst_futures_full" in tables:
        print(f"[fetch] TWN/AFINST {start}..{end} (chunked)", flush=True)
        # ~114 rows/day × 250 days/year × 18 years ≈ 510K rows; chunk 60 days
        df = _tej_get_chunked("TWN/AFINST", start, end, chunk_days=60)
        print(f"  -> {len(df):,} rows total", flush=True)
        out = adapt_afinst_to_silver(df)
        write_silver_inst_futures_full(out, mode=mode)

    # --- P2 ---

    if "security_attrs" in tables:
        print(f"[fetch] TWN/APISTOCK (1 row per stock, single shot)", flush=True)
        # APISTOCK is per-coid metadata; no date range, just fetch the full table
        df = _tej_get_resilient("TWN/APISTOCK")
        print(f"  -> {len(df):,} rows", flush=True)
        out = adapt_apistock_to_silver(df)
        write_silver_security_attrs(out, mode=mode)

    if "stock_trading_attrs" in tables:
        print(f"[fetch] TWN/APISTKATTR {start}..{end} (chunked, daily per stock)", flush=True)
        # ~2K stocks × ~250 days/year → 500K rows/year — chunk by 30 days
        df = _tej_get_chunked("TWN/APISTKATTR", start, end, chunk_days=30)
        print(f"  -> {len(df):,} rows total", flush=True)
        out = adapt_apistkattr_to_silver(df)
        write_silver_stock_trading_attrs(out, mode=mode)

    if "accounting_raw" in tables:
        print(f"[fetch] TWN/AINVFINB {start}..{end} (chunked yearly, wide 118-col)", flush=True)
        # ~2K stocks × 4 quarters × wide = ~10K rows/year, but each row has 118
        # cols so per-call size is large; chunk yearly to be safe
        df = _tej_get_chunked("TWN/AINVFINB", start, end, chunk_days=365)
        print(f"  -> {len(df):,} rows total", flush=True)
        out = adapt_ainvfinb_to_silver(df)
        write_silver_accounting_raw(out, mode=mode)

    if "capital_changes" in tables:
        print(f"[fetch] TWN/APISTK1 {start}..{end} (event-based, chunked yearly)", flush=True)
        # ~3K events/year across all listed stocks — yearly chunk is well under limit
        df = _tej_get_chunked("TWN/APISTK1", start, end, chunk_days=365)
        print(f"  -> {len(df):,} rows total", flush=True)
        out = adapt_apistk1_to_silver(df)
        write_silver_capital_changes(out, mode=mode)

    if "stock_valuation" in tables:
        print(f"[fetch] TWN/APIPRCD {start}..{end} (valuation cols, chunked 30d)", flush=True)
        # ~1.8K rows/day full market; 30-day chunk ≈ 54K rows (tested under limit)
        df = _tej_get_chunked("TWN/APIPRCD", start, end, chunk_days=30)
        print(f"  -> {len(df):,} rows total", flush=True)
        out = adapt_apiprcd_to_valuation_silver(df)
        write_silver_stock_valuation(out, mode=mode)


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
