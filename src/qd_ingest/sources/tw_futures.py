"""TW futures + stock futures source ingester.

Inputs:
- MXF_1m_clean_all.parquet                                  -> silver/bars/bars_1m (MXF, tw_futures)
- MXF_1d_clean_all.parquet/MXF_1d_clean_all.parquet         -> silver/bars/bars_1d (MXF)
- 日k 期貨tquant lab/{TX,MTX}_continuous_{raw,adj_back}.parquet -> gold/continuous/{tx,mtx}_continuous_d.parquet
- 股票期貨/stock_futures_daily.parquet                       -> silver/bars/bars_1d (asset_class=tw_stock_futures)
- 股票期貨/clean_all_sessions.parquet                        -> silver/bars/bars_1d (with session=both day+ah)
- 股票期貨/continuous_near_month.parquet                     -> gold/continuous/stock_futures_continuous_d.parquet
"""

from __future__ import annotations

import datetime as dt
import shutil
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console

from ..common.audit import IngestRecord, sha256_file, write_audit
from ..common.io import write_silver_partitioned
from ..common.paths import GOLD, ROOT, silver_bars

console = Console()


# ---------------------------------------------------------------------------
# MXF 1m / 1d (cleaned)  ->  silver/bars/bars_{1m,1d}
# ---------------------------------------------------------------------------

# Shared canonical bars schema (for both 1d and 1m)
_BARS_SCHEMA = pa.schema([
    ("ts_utc",        pa.timestamp("ns", tz="UTC")),
    ("trading_date",  pa.date32()),
    ("asset_class",   pa.string()),
    ("exchange",      pa.string()),
    ("symbol",        pa.string()),
    ("contract_id",   pa.string()),
    ("session",       pa.string()),
    ("open",          pa.float64()),
    ("high",          pa.float64()),
    ("low",           pa.float64()),
    ("close",         pa.float64()),
    ("volume",        pa.int64()),
    ("open_interest", pa.int64()),
    ("vwap",          pa.float64()),
    ("settlement",    pa.float64()),
    ("adj_open",      pa.float64()),
    ("adj_high",      pa.float64()),
    ("adj_low",       pa.float64()),
    ("adj_close",     pa.float64()),
    ("adj_factor",    pa.float64()),
    ("source",        pa.string()),
    ("ingestion_ts",  pa.timestamp("ns", tz="UTC")),
    ("quality_flag",  pa.string()),
    ("year",          pa.int32()),
])


def _to_bars_table(df: pd.DataFrame) -> pa.Table:
    df = df[[f.name for f in _BARS_SCHEMA]].copy()
    return pa.Table.from_pandas(df, schema=_BARS_SCHEMA, preserve_index=False)


def ingest_mxf(*, dry_run: bool = False) -> dict:
    """MXF 1m and 1d cleaned parquets -> silver/bars/bars_{1m,1d}."""
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    summary: dict = {"mxf_1m": None, "mxf_1d": None, "elapsed_sec": 0.0}

    # --- 1m ---
    fp = ROOT / "MXF_1m_clean_all.parquet"
    if fp.exists():
        sha = sha256_file(fp)
        console.log(f"[MXF 1m] reading {fp.name} sha256={sha[:12]}...")
        df = pq.read_table(fp).to_pandas()
        n = len(df)
        # source 'datetime' is naive Asia/Taipei (per inspection earlier)
        ts_local = pd.to_datetime(df["datetime"]).dt.tz_localize("Asia/Taipei")
        out = pd.DataFrame({
            "ts_utc":       ts_local.dt.tz_convert("UTC"),
            "trading_date": pd.to_datetime(df["trading_date"]).dt.date,
            "asset_class":  "tw_futures",
            "exchange":     "TAIFEX",
            "symbol":       "MXF",
            "contract_id":  pd.Series([None] * n, dtype="object"),
            # session is 'day'/'ah'; both map to canonical {day, ah}
            "session":      df["session"].astype(str),
            "open":         pd.to_numeric(df["open"], errors="coerce"),
            "high":         pd.to_numeric(df["high"], errors="coerce"),
            "low":          pd.to_numeric(df["low"], errors="coerce"),
            "close":        pd.to_numeric(df["close"], errors="coerce"),
            "volume":       pd.array(pd.to_numeric(df["volume"], errors="coerce"), dtype="Int64"),
            "open_interest": pd.array([pd.NA] * n, dtype="Int64"),
            "vwap":         pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "settlement":   pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "adj_open":     pd.to_numeric(df["adj_open"], errors="coerce"),
            "adj_high":     pd.to_numeric(df["adj_high"], errors="coerce"),
            "adj_low":      pd.to_numeric(df["adj_low"], errors="coerce"),
            "adj_close":    pd.to_numeric(df["adj_close"], errors="coerce"),
            "adj_factor":   pd.Series(dtype=float),
            "source":       "mxf_clean",
            "ingestion_ts": pd.Timestamp.now(tz="UTC"),
            "quality_flag": df["is_settlement_day"].map(lambda x: "settlement" if x else "ok").astype(str),
        })
        mask = out["adj_close"].notna() & out["close"].notna() & (out["close"] != 0)
        out.loc[mask, "adj_factor"] = (out.loc[mask, "adj_close"] / out.loc[mask, "close"]).astype(float)
        out["year"] = pd.to_datetime(out["ts_utc"]).dt.year.astype("int32")

        dest = silver_bars("1m") / "asset_class=tw_futures" / "symbol=MXF"
        if not dry_run:
            if dest.exists():
                shutil.rmtree(dest)
            tbl = _to_bars_table(out)
            write_silver_partitioned(tbl, dest, ["year"], existing_data_behavior="overwrite_or_ignore")
        summary["mxf_1m"] = {"rows": n, "years": sorted(out["year"].unique().tolist())}
        console.log(f"[MXF 1m] -> {len(out):,} rows across {len(summary['mxf_1m']['years'])} years")
        if not dry_run:
            write_audit(IngestRecord(
                source="mxf_clean", table="bars_1m", bronze_file=str(fp),
                rows_in=n, rows_out=len(out), sha256=sha, status="ok",
                started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                extra=summary["mxf_1m"],
            ))

    # --- 1d ---
    fp1d = ROOT / "MXF_1d_clean_all.parquet" / "MXF_1d_clean_all.parquet"
    if fp1d.exists():
        sha = sha256_file(fp1d)
        console.log(f"[MXF 1d] reading {fp1d.name} sha256={sha[:12]}...")
        df = pq.read_table(fp1d).to_pandas()
        n = len(df)
        td = pd.to_datetime(df["trading_date"])
        # anchor at 13:45 Asia/Taipei close = 05:45 UTC
        ts_utc = (td + pd.Timedelta(hours=13, minutes=45)).dt.tz_localize("Asia/Taipei").dt.tz_convert("UTC")
        out = pd.DataFrame({
            "ts_utc":       ts_utc,
            "trading_date": td.dt.date,
            "asset_class":  "tw_futures",
            "exchange":     "TAIFEX",
            "symbol":       "MXF",
            "contract_id":  pd.Series([None] * n, dtype="object"),
            "session":      "day",
            "open":         pd.to_numeric(df["open"], errors="coerce"),
            "high":         pd.to_numeric(df["high"], errors="coerce"),
            "low":          pd.to_numeric(df["low"], errors="coerce"),
            "close":        pd.to_numeric(df["close"], errors="coerce"),
            "volume":       pd.array(pd.to_numeric(df["volume"], errors="coerce"), dtype="Int64"),
            "open_interest": pd.array([pd.NA] * n, dtype="Int64"),
            "vwap":         pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "settlement":   pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "adj_open":     pd.to_numeric(df["adj_open"], errors="coerce"),
            "adj_high":     pd.to_numeric(df["adj_high"], errors="coerce"),
            "adj_low":      pd.to_numeric(df["adj_low"], errors="coerce"),
            "adj_close":    pd.to_numeric(df["adj_close"], errors="coerce"),
            "adj_factor":   pd.Series(dtype=float),
            "source":       "mxf_clean",
            "ingestion_ts": pd.Timestamp.now(tz="UTC"),
            "quality_flag": df["is_settlement_day"].map(lambda x: "settlement" if x else "ok").astype(str),
        })
        mask = out["adj_close"].notna() & out["close"].notna() & (out["close"] != 0)
        out.loc[mask, "adj_factor"] = (out.loc[mask, "adj_close"] / out.loc[mask, "close"]).astype(float)
        out["year"] = pd.to_datetime(out["ts_utc"]).dt.year.astype("int32")

        dest = silver_bars("1d") / "asset_class=tw_futures" / "symbol=MXF"
        if not dry_run:
            if dest.exists():
                shutil.rmtree(dest)
            tbl = _to_bars_table(out)
            write_silver_partitioned(tbl, dest, ["year"], existing_data_behavior="overwrite_or_ignore")
        summary["mxf_1d"] = {"rows": n, "years": sorted(out["year"].unique().tolist())}
        console.log(f"[MXF 1d] -> {len(out):,} rows")
        if not dry_run:
            write_audit(IngestRecord(
                source="mxf_clean", table="bars_1d", bronze_file=str(fp1d),
                rows_in=n, rows_out=len(out), sha256=sha, status="ok",
                started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                extra=summary["mxf_1d"],
            ))

    summary["elapsed_sec"] = round(time.time() - t0, 1)
    console.log(f"[MXF ingest] [green]done[/green]: {summary}")
    return summary


# ---------------------------------------------------------------------------
# TX / MTX continuous (TEJ tquant lab)  ->  gold/continuous
# ---------------------------------------------------------------------------

def ingest_tw_futures_continuous(*, dry_run: bool = False) -> dict:
    """日k 期貨tquant lab/{TX,MTX}_continuous_adj_back.parquet -> gold/continuous/{tx,mtx}_continuous_d.parquet."""
    src_dir = ROOT / "日k 期貨tquant lab"
    dest_dir = GOLD / "continuous"
    dest_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {}
    started = dt.datetime.now(dt.timezone.utc).isoformat()

    for product, src_name in [("tx", "TX_continuous_adj_back.parquet"),
                              ("mtx", "MTX_continuous_adj_back.parquet")]:
        src_fp = src_dir / src_name
        if not src_fp.exists():
            console.log(f"[red][cont] {src_name} missing[/red]")
            continue
        sha = sha256_file(src_fp)
        df = pq.read_table(src_fp).to_pandas()
        n = len(df)
        # Normalize column names: TEJ uses _d suffix; we strip + canonicalize
        out = pd.DataFrame({
            "trading_date":   pd.to_datetime(df["mdate"]).dt.date,
            "product":        product.upper(),
            "front_contract": df["coid"].astype(str),
            "expiry":         pd.to_datetime(df["due_m"]).dt.date,
            "front_expiry_first_trade": pd.to_datetime(df["front_due_m"]).dt.date,
            "last_trade_date":         pd.to_datetime(df["last_tradedate"]).dt.date,
            "days_to_expiry":          pd.array(pd.to_numeric(df["remain"], errors="coerce"), dtype="Int64"),
            "open":     pd.to_numeric(df["open_d"], errors="coerce"),
            "high":     pd.to_numeric(df["high_d"], errors="coerce"),
            "low":      pd.to_numeric(df["low_d"], errors="coerce"),
            "close":    pd.to_numeric(df["close_d"], errors="coerce"),
            "settle":   pd.to_numeric(df["settle"], errors="coerce"),
            "volume":   pd.array(pd.to_numeric(df["vol_d"], errors="coerce"), dtype="Int64"),
            "open_interest": pd.array(pd.to_numeric(df["oi_2"], errors="coerce"), dtype="Int64"),
            "roi_pct":  pd.to_numeric(df["roi"], errors="coerce"),
            "basis":    pd.to_numeric(df["basis"], errors="coerce"),
            "adj_factor": pd.to_numeric(df["adj_factor"], errors="coerce"),
            "open_adj":   pd.to_numeric(df["open_d_adj"], errors="coerce"),
            "high_adj":   pd.to_numeric(df["high_d_adj"], errors="coerce"),
            "low_adj":    pd.to_numeric(df["low_d_adj"], errors="coerce"),
            "close_adj":  pd.to_numeric(df["close_d_adj"], errors="coerce"),
            "settle_adj": pd.to_numeric(df["settle_adj"], errors="coerce"),
            "source":     "tej_tquant_lab",
            "ingestion_ts": pd.Timestamp.now(tz="UTC"),
        })
        out = out.sort_values("trading_date").reset_index(drop=True)
        dest_fp = dest_dir / f"{product}_continuous_d.parquet"
        if not dry_run:
            pq.write_table(pa.Table.from_pandas(out, preserve_index=False), dest_fp, compression="zstd")
            write_audit(IngestRecord(
                source="tej_tquant_lab", table=f"gold/continuous/{product}_continuous_d",
                bronze_file=str(src_fp), rows_in=n, rows_out=n, sha256=sha,
                status="ok", started_at=started,
                ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                extra={"product": product.upper()},
            ))
        summary[product] = {"rows": n, "range": [str(out["trading_date"].min()), str(out["trading_date"].max())]}
        console.log(f"[cont] {product.upper()}: {n} rows {summary[product]['range']}")

    console.log(f"[tw_futures_continuous] [green]done[/green]: {summary}")
    return summary


# ---------------------------------------------------------------------------
# Stock futures daily  ->  silver/bars/bars_1d (asset_class=tw_stock_futures)
# Stock futures continuous -> gold/continuous/stock_futures_continuous_d.parquet
# ---------------------------------------------------------------------------

def ingest_stock_futures(*, dry_run: bool = False) -> dict:
    """股票期貨/{stock_futures_daily,continuous_near_month}.parquet."""
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    src_dir = ROOT / "股票期貨"
    summary: dict = {}

    # --- daily (per-contract, all delivery months) ---
    fp = src_dir / "stock_futures_daily.parquet"
    if fp.exists():
        sha = sha256_file(fp)
        df = pq.read_table(fp).to_pandas()
        n = len(df)
        td = pd.to_datetime(df["date"])
        ts_utc = (td + pd.Timedelta(hours=13, minutes=45)).dt.tz_localize("Asia/Taipei").dt.tz_convert("UTC")
        # session: '一般' -> 'day', '盤後' -> 'ah'
        session_map = {"一般": "day", "盤後": "ah"}
        sessions = df["session"].astype(str).map(session_map).fillna("day")
        out = pd.DataFrame({
            "ts_utc":       ts_utc,
            "trading_date": td.dt.date,
            "asset_class":  "tw_stock_futures",
            "exchange":     "TAIFEX",
            # canonical symbol = underlying code (e.g. 2330 for TSMC stock future "CDF202403")
            "symbol":       df["underlying_code"].astype(str),
            "contract_id":  df["futures_code"].astype(str) + df["delivery_month"].astype(str),
            "session":      sessions,
            "open":         pd.to_numeric(df["open"], errors="coerce"),
            "high":         pd.to_numeric(df["high"], errors="coerce"),
            "low":          pd.to_numeric(df["low"], errors="coerce"),
            "close":        pd.to_numeric(df["close"], errors="coerce"),
            "volume":       pd.array(pd.to_numeric(df["volume"], errors="coerce"), dtype="Int64"),
            "open_interest": pd.array(pd.to_numeric(df["open_interest"], errors="coerce"), dtype="Int64"),
            "vwap":         pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "settlement":   pd.to_numeric(df["settlement_price"], errors="coerce"),
            "adj_open":     pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "adj_high":     pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "adj_low":      pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "adj_close":    pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "adj_factor":   pd.Series([pd.NA] * n, dtype="Float64").astype(float),
            "source":       "taifex_stkfut",
            "ingestion_ts": pd.Timestamp.now(tz="UTC"),
            "quality_flag": "ok",
        })
        out["year"] = pd.to_datetime(out["ts_utc"]).dt.year.astype("int32")
        dest = silver_bars("1d") / "asset_class=tw_stock_futures"
        if not dry_run:
            if dest.exists():
                shutil.rmtree(dest)
            tbl = _to_bars_table(out)
            write_silver_partitioned(tbl, dest, ["year"], existing_data_behavior="overwrite_or_ignore")
            write_audit(IngestRecord(
                source="taifex_stkfut", table="bars_1d", bronze_file=str(fp),
                rows_in=n, rows_out=len(out), sha256=sha, status="ok",
                started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                extra={"underlying_count": int(df["underlying_code"].nunique())},
            ))
        summary["daily"] = {
            "rows": n,
            "underlyings": int(df["underlying_code"].nunique()),
            "years": sorted(out["year"].unique().tolist()),
        }
        console.log(f"[stockfut daily] -> {n:,} rows, {summary['daily']['underlyings']} underlyings")

    # --- continuous near-month ---
    fp = src_dir / "continuous_near_month.parquet"
    if fp.exists():
        sha = sha256_file(fp)
        df = pq.read_table(fp).to_pandas()
        n = len(df)
        out = df.copy()
        out["trading_date"] = pd.to_datetime(out["date"]).dt.date
        out["underlying_code"] = out["underlying_code"].astype(str)
        out = out.drop(columns=["date"])
        dest = GOLD / "continuous" / "stock_futures_continuous_d.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dry_run:
            pq.write_table(pa.Table.from_pandas(out, preserve_index=False), dest, compression="zstd")
            write_audit(IngestRecord(
                source="taifex_stkfut", table="gold/continuous/stock_futures_continuous_d",
                bronze_file=str(fp), rows_in=n, rows_out=n, sha256=sha,
                status="ok", started_at=started,
                ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                extra={"underlying_count": int(df["underlying_code"].nunique())},
            ))
        summary["continuous"] = {
            "rows": n, "underlyings": int(df["underlying_code"].nunique()),
        }
        console.log(f"[stockfut continuous] -> {n:,} rows, {summary['continuous']['underlyings']} underlyings")

    summary["elapsed_sec"] = round(time.time() - t0, 1)
    console.log(f"[stock_futures] [green]done[/green]: {summary}")
    return summary
