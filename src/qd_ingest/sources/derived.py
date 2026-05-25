"""Derived gold layer.

Inputs:
- SUPPLEMENT/DERIVED/txo_daily_features.parquet   -> gold/features/txo_daily_features.parquet (copy + normalize)
- SUPPLEMENT/DERIVED/cross_market_features.parquet -> gold/features/cross_market_features.parquet (copy + repair index)
- silver/bars/bars_1d (asset_class=tw_stock) -> gold/features/stock_factor_daily.parquet  (compute mom + vol factors)
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console

from ..common.audit import IngestRecord, sha256_file, write_audit
from ..common.paths import GOLD, RAW_ROOT, SILVER

console = Console()


def copy_txo_daily_features() -> dict:
    src = RAW_ROOT / "SUPPLEMENT" / "DERIVED" / "txo_daily_features.parquet"
    dest = GOLD / "features" / "txo_daily_features.parquet"
    if not src.exists():
        console.log(f"[red]missing {src}[/red]")
        return {}
    dest.parent.mkdir(parents=True, exist_ok=True)
    df = pq.read_table(src).to_pandas()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["source"] = "tw_derived"
    df["ingestion_ts"] = pd.Timestamp.now(tz="UTC")
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), dest, compression="zstd")
    info = {"rows": len(df), "range": [str(df["date"].min()), str(df["date"].max())]}
    write_audit(IngestRecord(
        source="tw_derived", table="gold/features/txo_daily_features",
        bronze_file=str(src), rows_in=len(df), rows_out=len(df),
        sha256=sha256_file(src), status="ok",
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[txo_daily_features] {info}")
    return info


def copy_cross_market_features() -> dict:
    src = RAW_ROOT / "SUPPLEMENT" / "DERIVED" / "cross_market_features.parquet"
    dest = GOLD / "features" / "cross_market_features.parquet"
    if not src.exists():
        console.log(f"[red]missing {src}[/red]")
        return {}
    dest.parent.mkdir(parents=True, exist_ok=True)
    df = pq.read_table(src).to_pandas()
    # The bronze file has no 'date' column — the index is the date. Recover it.
    if df.index.name is None and not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index().rename(columns={"index": "date"})
    elif "date" not in df.columns:
        # Sometimes pyarrow strips index; if so date isn't recoverable — use US_FUTURES NQ_F_daily as alignment
        # Cheap fallback: count rows == nq_f_daily length; reattach
        nq_fp = RAW_ROOT / "SUPPLEMENT" / "US_FUTURES" / "NQ_F_daily.parquet"
        nq = pq.read_table(nq_fp).to_pandas()
        if len(df) == len(nq):
            df.insert(0, "date", nq.index)
    df["date"] = pd.to_datetime(df["date"]).dt.date if "date" in df.columns else pd.NaT
    df["source"] = "tw_derived"
    df["ingestion_ts"] = pd.Timestamp.now(tz="UTC")
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), dest, compression="zstd")
    info = {"rows": len(df), "cols": len(df.columns)}
    write_audit(IngestRecord(
        source="tw_derived", table="gold/features/cross_market_features",
        bronze_file=str(src), rows_in=len(df), rows_out=len(df),
        sha256=sha256_file(src), status="ok",
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[cross_market_features] {info}")
    return info


def build_stock_factor_daily() -> dict:
    """Compute basic factor panel for tw_stock from silver/bars/bars_1d.

    Factors:
      - ret_1d, ret_5d, ret_20d, ret_60d, ret_120d  (close-to-close)
      - mom_12_1     = 12M momentum excl. most recent month  (Jegadeesh-Titman style)
      - vol_20d      = trailing 20-day std of log returns
      - vol_60d      = trailing 60-day std of log returns
      - turnover_20d = trailing 20-day mean of volume*close (proxy)

    Output: gold/features/stock_factor_daily.parquet  (keyed by (trading_date, symbol))
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    bars_glob = str((SILVER / "bars" / "bars_1d" / "asset_class=tw_stock" / "year=*" / "*.parquet"))

    df = pl.scan_parquet(bars_glob).select([
        "trading_date", "symbol", "close", "volume",
    ]).collect()

    # Cast trading_date to date if not already
    if df.schema["trading_date"] != pl.Date:
        df = df.with_columns(pl.col("trading_date").cast(pl.Date))

    df = df.filter(pl.col("close").is_not_null()).sort(["symbol", "trading_date"])

    # log-return for vol calc
    df = df.with_columns([
        pl.col("close").log().diff().over("symbol").alias("log_ret"),
        pl.col("close").pct_change().over("symbol").alias("ret_1d"),
    ])

    df = df.with_columns([
        (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1).alias("ret_5d"),
        (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1).alias("ret_20d"),
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1).alias("ret_60d"),
        (pl.col("close") / pl.col("close").shift(120).over("symbol") - 1).alias("ret_120d"),
        # 12-1 momentum: t-21 -> t-252, skipping most recent 21 trading days
        (pl.col("close").shift(21).over("symbol")
         / pl.col("close").shift(252).over("symbol") - 1).alias("mom_12_1"),
        pl.col("log_ret").rolling_std(window_size=20).over("symbol").alias("vol_20d"),
        pl.col("log_ret").rolling_std(window_size=60).over("symbol").alias("vol_60d"),
        (pl.col("volume").cast(pl.Float64) * pl.col("close")).rolling_mean(window_size=20).over("symbol").alias("turnover_20d"),
    ])

    out = df.select([
        "trading_date", "symbol",
        "ret_1d", "ret_5d", "ret_20d", "ret_60d", "ret_120d",
        "mom_12_1", "vol_20d", "vol_60d", "turnover_20d",
    ])

    dest = GOLD / "features" / "stock_factor_daily.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)

    info = {
        "rows": out.height,
        "symbols": out["symbol"].n_unique(),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/stock_factor_daily",
        bronze_file="silver/bars/bars_1d/tw_stock",
        rows_in=out.height, rows_out=out.height, sha256="",
        status="ok",
        started_at=started,
        ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[stock_factor_daily] {info}")
    return info


def build_inst_flow_factors() -> dict:
    """Institutional flow factors derived from silver/flows/tw_inst_stock_daily.

    Factors (keyed by trading_date, stock_id):
      - foreign_net_5d / _20d / _60d : rolling sum of foreign net lots
      - sitc_net_5d / _20d           : rolling sum of investment-trust net lots
      - dealer_net_5d / _20d         : rolling sum of dealer net lots
      - foreign_hold_pct_chg_20d     : 20d change in foreign ownership %
      - inst_net_persistence_20d     : fraction of 20-day window with positive total inst net

    Output: gold/features/inst_flow_factors.parquet
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "flows" / "tw_inst_stock_daily" / "year=*" / "*.parquet")

    df = pl.scan_parquet(glob).select([
        "trading_date", "stock_id",
        "foreign_net_lot", "sitc_net_lot", "dealer_net_lot", "total_net_lot",
        "foreign_hold_pct",
    ]).collect()

    if df.schema["trading_date"] != pl.Date:
        df = df.with_columns(pl.col("trading_date").cast(pl.Date))
    df = df.sort(["stock_id", "trading_date"])

    df = df.with_columns([
        pl.col("foreign_net_lot").rolling_sum(window_size=5).over("stock_id").alias("foreign_net_5d"),
        pl.col("foreign_net_lot").rolling_sum(window_size=20).over("stock_id").alias("foreign_net_20d"),
        pl.col("foreign_net_lot").rolling_sum(window_size=60).over("stock_id").alias("foreign_net_60d"),
        pl.col("sitc_net_lot").rolling_sum(window_size=5).over("stock_id").alias("sitc_net_5d"),
        pl.col("sitc_net_lot").rolling_sum(window_size=20).over("stock_id").alias("sitc_net_20d"),
        pl.col("dealer_net_lot").rolling_sum(window_size=5).over("stock_id").alias("dealer_net_5d"),
        pl.col("dealer_net_lot").rolling_sum(window_size=20).over("stock_id").alias("dealer_net_20d"),
        (pl.col("foreign_hold_pct")
           - pl.col("foreign_hold_pct").shift(20).over("stock_id")).alias("foreign_hold_pct_chg_20d"),
        (pl.col("total_net_lot") > 0).cast(pl.Float64)
            .rolling_mean(window_size=20).over("stock_id").alias("inst_net_persistence_20d"),
    ])

    out = df.select([
        "trading_date", "stock_id",
        "foreign_net_5d", "foreign_net_20d", "foreign_net_60d",
        "sitc_net_5d", "sitc_net_20d",
        "dealer_net_5d", "dealer_net_20d",
        "foreign_hold_pct_chg_20d",
        "inst_net_persistence_20d",
    ]).with_columns([
        pl.lit("qd_gold_inst_flow_factors_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "inst_flow_factors.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)

    info = {
        "rows": out.height,
        "stocks": out["stock_id"].n_unique(),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/inst_flow_factors",
        bronze_file="silver/flows/tw_inst_stock_daily",
        rows_in=out.height, rows_out=out.height, sha256="",
        status="ok",
        started_at=started,
        ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[inst_flow_factors] {info}")
    return info


def build_all() -> dict:
    summary = {
        "txo": copy_txo_daily_features(),
        "cross_market": copy_cross_market_features(),
        "stock_factor": build_stock_factor_daily(),
        "inst_flow": build_inst_flow_factors(),
    }
    return summary


if __name__ == "__main__":
    build_all()
