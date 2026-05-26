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


def build_margin_factors() -> dict:
    """Margin / short-sale time-series factors from tw_margin_daily.

    Output: gold/features/margin_factors.parquet  (keyed by trading_date, stock_id)
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "flows" / "tw_margin_daily" / "year=*" / "*.parquet")
    df = pl.scan_parquet(glob).select([
        "trading_date", "stock_id",
        "margin_balance_lot", "short_balance_lot",
        "margin_util_pct", "short_to_margin_pct",
    ]).collect()
    if df.schema["trading_date"] != pl.Date:
        df = df.with_columns(pl.col("trading_date").cast(pl.Date))
    df = df.sort(["stock_id", "trading_date"])

    # rolling mean + std for z-score
    mean60 = pl.col("margin_util_pct").rolling_mean(window_size=60).over("stock_id")
    std60  = pl.col("margin_util_pct").rolling_std(window_size=60).over("stock_id")

    out = df.with_columns([
        (pl.col("margin_balance_lot")
            - pl.col("margin_balance_lot").shift(5).over("stock_id")).alias("margin_balance_chg_5d"),
        (pl.col("margin_balance_lot")
            - pl.col("margin_balance_lot").shift(20).over("stock_id")).alias("margin_balance_chg_20d"),
        (pl.col("short_balance_lot")
            - pl.col("short_balance_lot").shift(5).over("stock_id")).alias("short_balance_chg_5d"),
        (pl.col("short_balance_lot")
            - pl.col("short_balance_lot").shift(20).over("stock_id")).alias("short_balance_chg_20d"),
        ((pl.col("margin_util_pct") - mean60) / std60).alias("margin_util_zscore_60d"),
        (pl.col("short_to_margin_pct")
            - pl.col("short_to_margin_pct").shift(20).over("stock_id")).alias("short_to_margin_chg_20d"),
    ]).select([
        "trading_date", "stock_id",
        "margin_balance_chg_5d", "margin_balance_chg_20d",
        "short_balance_chg_5d", "short_balance_chg_20d",
        "margin_util_zscore_60d", "short_to_margin_chg_20d",
    ]).with_columns([
        pl.lit("qd_gold_margin_factors_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "margin_factors.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height, "stocks": out["stock_id"].n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/margin_factors",
        bronze_file="silver/flows/tw_margin_daily",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[margin_factors] {info}")
    return info


def build_fundamentals_pit() -> dict:
    """Point-in-time fundamentals panel from fundamentals_q.

    Filter to consolidated quarterly reports; key by publish_date (the actually
    knowable-from date). Add TTM EPS / revenue, YoY net income / revenue,
    rolling-4 ROE.

    Output: gold/features/fundamentals_pit.parquet
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "fundamentals" / "fin_q" / "**" / "*.parquet")
    df = pl.scan_parquet(glob).filter(
        (pl.col("period_type") == "Q") & (pl.col("consolidated") == True)
    ).select([
        "stock_id", "fiscal_period", "publish_date",
        "eps", "roe_post", "revenue", "net_income", "ni_to_parent",
    ]).collect()
    if df.schema["publish_date"] != pl.Date:
        df = df.with_columns(pl.col("publish_date").cast(pl.Date))
    df = df.sort(["stock_id", "publish_date"])

    out = df.with_columns([
        pl.col("eps").rolling_sum(window_size=4).over("stock_id").alias("eps_ttm"),
        pl.col("revenue").rolling_sum(window_size=4).over("stock_id").alias("revenue_ttm"),
        pl.col("roe_post").rolling_mean(window_size=4).over("stock_id").alias("roe_ttm_avg"),
        ((pl.col("net_income") / pl.col("net_income").shift(4).over("stock_id") - 1) * 100)
            .alias("ni_yoy_chg_pct"),
        ((pl.col("revenue") / pl.col("revenue").shift(4).over("stock_id") - 1) * 100)
            .alias("revenue_yoy_chg_pct"),
    ]).select([
        "publish_date", "stock_id", "fiscal_period",
        "eps", "roe_post", "revenue", "net_income",
        "eps_ttm", "revenue_ttm", "roe_ttm_avg",
        "ni_yoy_chg_pct", "revenue_yoy_chg_pct",
    ]).rename({"publish_date": "trading_date"}).with_columns([
        pl.lit("qd_gold_fundamentals_pit_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "fundamentals_pit.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height, "stocks": out["stock_id"].n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/fundamentals_pit",
        bronze_file="silver/fundamentals/fin_q",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[fundamentals_pit] {info}")
    return info


def build_futures_large_trader_factors() -> dict:
    """Large-trader concentration factors from tw_futures_large_trader_daily.

    Output: gold/features/futures_large_trader_factors.parquet
              (keyed by trading_date, product, expiry_month)
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "flows" / "tw_futures_large_trader_daily" / "year=*" / "*.parquet")
    df = pl.scan_parquet(glob).select([
        "trading_date", "product", "expiry_month",
        "total_oi",
        "top5_buy_traders_pct", "top5_sell_traders_pct",
        "top10_buy_traders_pct", "top10_sell_traders_pct",
        "top10_buy_institutional_pct", "top10_sell_institutional_pct",
    ]).collect()
    if df.schema["trading_date"] != pl.Date:
        df = df.with_columns(pl.col("trading_date").cast(pl.Date))
    df = df.sort(["product", "expiry_month", "trading_date"])

    out = df.with_columns([
        (pl.col("top10_buy_traders_pct") - pl.col("top10_sell_traders_pct"))
            .alias("top10_net_pct"),
        (pl.col("top10_buy_institutional_pct") - pl.col("top10_sell_institutional_pct"))
            .alias("top10_institutional_net_pct"),
        ((pl.col("top5_buy_traders_pct") + pl.col("top5_sell_traders_pct")) / 2.0)
            .alias("top5_concentration_avg"),
        (pl.col("total_oi") - pl.col("total_oi").shift(5).over(["product", "expiry_month"]))
            .alias("oi_chg_5d"),
        (pl.col("total_oi") - pl.col("total_oi").shift(20).over(["product", "expiry_month"]))
            .alias("oi_chg_20d"),
    ]).select([
        "trading_date", "product", "expiry_month",
        "total_oi",
        "top10_net_pct", "top10_institutional_net_pct", "top5_concentration_avg",
        "oi_chg_5d", "oi_chg_20d",
    ]).with_columns([
        pl.lit("qd_gold_futures_large_trader_factors_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "futures_large_trader_factors.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height,
            "contracts": out.select(["product", "expiry_month"]).n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/futures_large_trader_factors",
        bronze_file="silver/flows/tw_futures_large_trader_daily",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[futures_large_trader_factors] {info}")
    return info


def build_futures_inst_factors() -> dict:
    """Per-identity institutional positioning factors from tw_inst_futures_full_daily.

    The silver view carries 162 identity_codes (3 institutions × {TX/MTX/TE/TXO/...}).
    Output is a per (trading_date, identity_code) panel useful for cross-identity
    regime comparisons (e.g. FINI net_oi vs Dealer net_oi spread).

    Output: gold/features/futures_inst_factors.parquet
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "flows" / "tw_inst_futures_full_daily" / "year=*" / "*.parquet")
    df = pl.scan_parquet(glob).select([
        "trading_date", "identity_code", "identity_zh",
        "long_volume", "short_volume", "net_volume",
        "long_volume_pct", "short_volume_pct",
        "long_oi", "short_oi", "net_oi",
        "ingestion_ts",
    ]).collect()
    if df.schema["trading_date"] != pl.Date:
        df = df.with_columns(pl.col("trading_date").cast(pl.Date))
    # dedup silver multi-ingest by keeping latest per (trading_date, identity_code)
    df = df.sort(["identity_code", "trading_date", "ingestion_ts"]).unique(
        subset=["identity_code", "trading_date"], keep="last"
    ).drop("ingestion_ts").sort(["identity_code", "trading_date"])

    mean60 = pl.col("net_volume").rolling_mean(window_size=60).over("identity_code")
    std60  = pl.col("net_volume").rolling_std(window_size=60).over("identity_code")

    out = df.with_columns([
        (pl.col("net_oi")
            - pl.col("net_oi").shift(5).over("identity_code")).alias("net_oi_chg_5d"),
        (pl.col("net_oi")
            - pl.col("net_oi").shift(20).over("identity_code")).alias("net_oi_chg_20d"),
        ((pl.col("net_volume") - mean60) / std60).alias("net_volume_zscore_60d"),
        (pl.col("long_oi").cast(pl.Float64)
            / pl.when(pl.col("short_oi") == 0).then(None).otherwise(pl.col("short_oi"))
        ).alias("long_short_oi_ratio"),
        ((pl.col("long_volume") + pl.col("short_volume")).cast(pl.Float64)
            / pl.when((pl.col("long_oi") + pl.col("short_oi")) == 0).then(None)
              .otherwise(pl.col("long_oi") + pl.col("short_oi"))
        ).alias("volume_to_oi_ratio"),
    ]).select([
        "trading_date", "identity_code", "identity_zh",
        "long_oi", "short_oi", "net_oi",
        "long_volume_pct", "short_volume_pct",
        "net_oi_chg_5d", "net_oi_chg_20d",
        "net_volume_zscore_60d",
        "long_short_oi_ratio", "volume_to_oi_ratio",
    ]).with_columns([
        pl.lit("qd_gold_futures_inst_factors_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "futures_inst_factors.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height, "identities": out["identity_code"].n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/futures_inst_factors",
        bronze_file="silver/flows/tw_inst_futures_full_daily",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[futures_inst_factors] {info}")
    return info


def build_stock_attrs_status() -> dict:
    """Binary attribute panel from tw_stock_trading_attrs_daily.

    Converts the 11 'Y'/'' varchar flags into clean booleans and adds 30-day
    rolling counts for transient flags (attention / disposition). Keeps static
    classifier columns (industry / board / market) at the daily grain so
    downstream filters can use latest.

    Output: gold/features/stock_attrs_status.parquet
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "flows" / "tw_stock_trading_attrs_daily" / "year=*" / "*.parquet")
    df = pl.scan_parquet(glob).select([
        "trading_date", "stock_id",
        "market", "board_zh", "main_industry_zh", "sub_industry_zh",
        "is_attention", "is_disposition", "is_suspended", "is_full_settle",
        "no_daytrade_buy_first", "no_daytrade_sell_first",
        "is_twn50", "is_msci", "is_otc50", "is_otc200", "is_hdiv", "is_mcap",
        "ingestion_ts",
    ]).collect()
    if df.schema["trading_date"] != pl.Date:
        df = df.with_columns(pl.col("trading_date").cast(pl.Date))
    df = df.sort(["stock_id", "trading_date", "ingestion_ts"]).unique(
        subset=["stock_id", "trading_date"], keep="last"
    ).drop("ingestion_ts").sort(["stock_id", "trading_date"])

    def yflag(col: str) -> pl.Expr:
        return (pl.col(col).fill_null("") == "Y")

    out = df.with_columns([
        yflag("is_attention").alias("is_attention_bool"),
        yflag("is_disposition").alias("is_disposition_bool"),
        yflag("is_suspended").alias("is_suspended_bool"),
        yflag("is_full_settle").alias("is_full_settle_bool"),
        (yflag("no_daytrade_buy_first") | yflag("no_daytrade_sell_first"))
            .alias("is_no_daytrade_bool"),
        yflag("is_twn50").alias("is_twn50_bool"),
        yflag("is_msci").alias("is_msci_bool"),
        yflag("is_otc50").alias("is_otc50_bool"),
        yflag("is_otc200").alias("is_otc200_bool"),
        yflag("is_hdiv").alias("is_hdiv_bool"),
        yflag("is_mcap").alias("is_mcap_bool"),
    ]).with_columns([
        pl.col("is_attention_bool").cast(pl.Int8)
            .rolling_sum(window_size=30).over("stock_id").alias("attention_count_30d"),
        pl.col("is_disposition_bool").cast(pl.Int8)
            .rolling_sum(window_size=30).over("stock_id").alias("disposition_count_30d"),
    ]).select([
        "trading_date", "stock_id",
        "market", "board_zh", "main_industry_zh", "sub_industry_zh",
        "is_attention_bool", "is_disposition_bool", "is_suspended_bool",
        "is_full_settle_bool", "is_no_daytrade_bool",
        "is_twn50_bool", "is_msci_bool", "is_otc50_bool", "is_otc200_bool",
        "is_hdiv_bool", "is_mcap_bool",
        "attention_count_30d", "disposition_count_30d",
    ]).with_columns([
        pl.lit("qd_gold_stock_attrs_status_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "stock_attrs_status.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height, "stocks": out["stock_id"].n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/stock_attrs_status",
        bronze_file="silver/flows/tw_stock_trading_attrs_daily",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[stock_attrs_status] {info}")
    return info


def build_dividend_calendar() -> dict:
    """Forward-looking dividend event panel from cash_dividend_events.

    Output keyed by (ex_date, stock_id). Adds per-share dividend, naive yield
    vs prev_close, TTM dividend (365-day rolling sum), and YoY growth.

    Output: gold/features/dividend_calendar.parquet
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "fundamentals" / "cash_dividend_events" / "year=*" / "*.parquet")
    df = pl.scan_parquet(glob).select([
        "stock_id", "ex_date", "period_end", "period_start", "dividend_type",
        "cash_div_earnings", "cash_div_reserve", "special_dividend",
        "prev_close", "ref_price", "pay_date", "announce_date",
        "total_cash_div_ktwd", "ingestion_ts",
    ]).collect()
    if df.schema["ex_date"] != pl.Date:
        df = df.with_columns(pl.col("ex_date").cast(pl.Date))
    # silver carries multiple ingest snapshots per (stock_id, ex_date); keep latest.
    df = df.sort(["stock_id", "ex_date", "ingestion_ts"]).unique(
        subset=["stock_id", "ex_date", "dividend_type"], keep="last"
    ).drop("ingestion_ts").sort(["stock_id", "ex_date"])

    df = df.with_columns([
        (pl.col("cash_div_earnings").fill_null(0.0)
         + pl.col("cash_div_reserve").fill_null(0.0)
         + pl.col("special_dividend").fill_null(0.0)).alias("cash_div_per_share"),
    ])

    # TTM: rolling sum over previous 365 days. Polars does this via rolling_*_by.
    out = df.with_columns([
        pl.col("cash_div_per_share").rolling_sum_by(
            by="ex_date", window_size="365d"
        ).over("stock_id").alias("ttm_cash_div_per_share"),
        (pl.col("cash_div_per_share") /
            pl.when(pl.col("prev_close") == 0).then(None).otherwise(pl.col("prev_close"))
            * 100.0).alias("div_yield_pct"),
        (pl.col("ex_date") - pl.col("announce_date")).dt.total_days()
            .alias("days_announce_to_ex"),
        ((pl.col("cash_div_per_share")
            / pl.col("cash_div_per_share").shift(1).over("stock_id")) - 1.0
            ).alias("yoy_growth_ratio"),
    ]).with_columns([
        (pl.col("yoy_growth_ratio") * 100.0).alias("yoy_growth_pct"),
    ]).select([
        "ex_date", "stock_id", "period_end", "period_start", "dividend_type",
        "cash_div_per_share", "div_yield_pct",
        "prev_close", "ref_price", "pay_date", "announce_date",
        "ttm_cash_div_per_share", "yoy_growth_pct",
        "days_announce_to_ex", "total_cash_div_ktwd",
    ]).with_columns([
        pl.lit("qd_gold_dividend_calendar_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "dividend_calendar.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height, "stocks": out["stock_id"].n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/dividend_calendar",
        bronze_file="silver/fundamentals/cash_dividend_events",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[dividend_calendar] {info}")
    return info


def build_stock_futures_adjustments() -> dict:
    """Per-futures_code adjustment panel from tw_stock_futures_corp_actions.

    Adds running totals (cum_cash_div_per_share, cum_stock_div_per_share,
    cum_equity_value_per_lot), prev adjust date and gap (days_since_prev_adj),
    and adj_seq_no per futures_code.

    Output: gold/features/stock_futures_adjustments.parquet
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "flows" / "tw_stock_futures_corp_actions" / "year=*" / "*.parquet")
    df = pl.scan_parquet(glob).select([
        "futures_code", "adjust_date", "adjust_reason", "contract_type",
        "stock_div_per_share", "cash_div_per_share", "cash_adjusted_yn",
        "shares_per_lot", "cash_div_per_lot", "equity_value_per_lot", "ref_price",
        "ingestion_ts",
    ]).collect()
    if df.schema["adjust_date"] != pl.Date:
        df = df.with_columns(pl.col("adjust_date").cast(pl.Date))
    df = df.sort(["futures_code", "adjust_date", "ingestion_ts"]).unique(
        subset=["futures_code", "adjust_date"], keep="last"
    ).drop("ingestion_ts").sort(["futures_code", "adjust_date"])

    out = df.with_columns([
        pl.col("cash_div_per_share").fill_null(0.0).cum_sum().over("futures_code")
            .alias("cum_cash_div_per_share"),
        pl.col("stock_div_per_share").fill_null(0.0).cum_sum().over("futures_code")
            .alias("cum_stock_div_per_share"),
        pl.col("equity_value_per_lot").fill_null(0.0).cum_sum().over("futures_code")
            .alias("cum_equity_value_per_lot"),
        pl.col("adjust_date").shift(1).over("futures_code").alias("prev_adjust_date"),
        pl.col("adjust_date").cum_count().over("futures_code").alias("adj_seq_no"),
    ]).with_columns([
        (pl.col("adjust_date") - pl.col("prev_adjust_date")).dt.total_days()
            .alias("days_since_prev_adj"),
    ]).select([
        "adjust_date", "futures_code", "adjust_reason", "contract_type",
        "cash_div_per_share", "stock_div_per_share", "cash_adjusted_yn",
        "shares_per_lot", "cash_div_per_lot", "equity_value_per_lot", "ref_price",
        "cum_cash_div_per_share", "cum_stock_div_per_share",
        "cum_equity_value_per_lot",
        "prev_adjust_date", "days_since_prev_adj", "adj_seq_no",
    ]).with_columns([
        pl.lit("qd_gold_stock_futures_adjustments_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "stock_futures_adjustments.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height, "futures_codes": out["futures_code"].n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/stock_futures_adjustments",
        bronze_file="silver/flows/tw_stock_futures_corp_actions",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[stock_futures_adjustments] {info}")
    return info


def build_futures_bar_factors() -> dict:
    """Day-bar factor panel for tw_futures + tw_stock_futures from silver/bars/bars_1d.

    Mirrors stock_factor_daily but covers futures (TXF/MXF/individual stock futures).
    Includes open-interest deltas, which stocks lack.

    Output: gold/features/futures_bar_factors.parquet
            keyed by (trading_date, asset_class, symbol)
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "bars" / "bars_1d" / "asset_class=*" / "**" / "*.parquet")
    df = pl.scan_parquet(glob, hive_partitioning=False).filter(
        pl.col("asset_class").is_in(["tw_futures", "tw_stock_futures"])
        & (pl.col("session") == "day")
        & pl.col("close").is_not_null()
        & (pl.col("symbol").is_not_null())
        & (pl.col("symbol") != "")
    ).select([
        "trading_date", "asset_class", "symbol",
        "high", "low", "close", "volume", "open_interest",
    ]).collect()
    if df.schema["trading_date"] != pl.Date:
        df = df.with_columns(pl.col("trading_date").cast(pl.Date))
    df = df.sort(["asset_class", "symbol", "trading_date"])

    df = df.with_columns([
        pl.col("close").log().diff().over(["asset_class", "symbol"]).alias("log_ret"),
    ])

    out = df.with_columns([
        (pl.col("close") / pl.col("close").shift(5).over(["asset_class", "symbol"]) - 1).alias("ret_5d"),
        (pl.col("close") / pl.col("close").shift(20).over(["asset_class", "symbol"]) - 1).alias("ret_20d"),
        (pl.col("close") / pl.col("close").shift(60).over(["asset_class", "symbol"]) - 1).alias("ret_60d"),
        pl.col("log_ret").rolling_std(window_size=20).over(["asset_class", "symbol"]).alias("vol_20d"),
        pl.col("log_ret").rolling_std(window_size=60).over(["asset_class", "symbol"]).alias("vol_60d"),
        (pl.col("high") - pl.col("low")).rolling_mean(window_size=14)
            .over(["asset_class", "symbol"]).alias("atr_14"),
        (pl.col("close").cast(pl.Float64) * pl.col("volume").cast(pl.Float64))
            .rolling_mean(window_size=20).over(["asset_class", "symbol"]).alias("turnover_20d"),
        (pl.col("open_interest")
            - pl.col("open_interest").shift(5).over(["asset_class", "symbol"])).alias("oi_chg_5d"),
        (pl.col("open_interest")
            - pl.col("open_interest").shift(20).over(["asset_class", "symbol"])).alias("oi_chg_20d"),
    ]).select([
        "trading_date", "asset_class", "symbol",
        "close", "volume", "open_interest",
        "ret_5d", "ret_20d", "ret_60d",
        "vol_20d", "vol_60d", "atr_14", "turnover_20d",
        "oi_chg_5d", "oi_chg_20d",
    ]).with_columns([
        pl.lit("qd_gold_futures_bar_factors_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "futures_bar_factors.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height,
            "symbols": out.select(["asset_class", "symbol"]).n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/futures_bar_factors",
        bronze_file="silver/bars/bars_1d/tw_futures+tw_stock_futures",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[futures_bar_factors] {info}")
    return info


def materialize_qc_snapshot() -> dict:
    """Materialize qc_stock_price_diff view as a gold parquet for portability.

    The view is a TEJ vs FinMind close/volume reconciliation. Persisting it
    means tools without access to both source views can still consume QC.

    Outputs:
      gold/features/qc_stock_price_diff_snapshot.parquet
      gold/features/qc_stock_price_diff_yearly.parquet
    """
    import duckdb
    from ..common.paths import CATALOG_DB
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    con = duckdb.connect(str(CATALOG_DB), read_only=True)

    dest = GOLD / "features" / "qc_stock_price_diff_snapshot.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM qc_stock_price_diff) TO '{dest}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{dest}')").fetchone()[0]

    dest_y = GOLD / "features" / "qc_stock_price_diff_yearly.parquet"
    con.execute(f"""
        COPY (
            SELECT
                EXTRACT(year FROM trading_date)::INT AS year,
                count(*) AS rows,
                count(DISTINCT stock_id) AS stocks,
                avg(abs(pct_diff)) AS mean_abs_pct_diff,
                max(abs(pct_diff)) AS max_abs_pct_diff,
                sum(CASE WHEN abs(pct_diff) > 1.0 THEN 1 ELSE 0 END) AS rows_diff_gt_1pct,
                avg(CASE WHEN tej_volume IS NULL OR finmind_volume IS NULL THEN NULL
                         ELSE 1.0 * (tej_volume - finmind_volume)
                              / NULLIF(GREATEST(tej_volume, finmind_volume), 0) END)
                    AS mean_vol_rel_diff
            FROM qc_stock_price_diff
            GROUP BY 1
            ORDER BY 1
        ) TO '{dest_y}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    rows_y = con.execute(f"SELECT count(*) FROM read_parquet('{dest_y}')").fetchone()[0]
    con.close()

    info = {"snapshot_rows": rows, "yearly_rows": rows_y,
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="catalog_derived", table="gold/features/qc_stock_price_diff_snapshot",
        bronze_file="catalog:qc_stock_price_diff",
        rows_in=rows, rows_out=rows, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[qc_stock_price_diff_snapshot] {info}")
    return info


def materialize_finmind_canonical() -> dict:
    """Materialize finmind_stock_price_norm LEFT JOIN finmind_stock_price_adj_norm
    as a single gold parquet — gives downstream a parquet-only path to FinMind data.

    Output: gold/features/finmind_price_canonical.parquet
    """
    import duckdb
    from ..common.paths import CATALOG_DB
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    con = duckdb.connect(str(CATALOG_DB), read_only=True)
    dest = GOLD / "features" / "finmind_price_canonical.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)

    con.execute(f"""
        COPY (
            SELECT
                r.trading_date,
                r.stock_id,
                r.open, r.high, r.low, r.close,
                r.volume, r.amount_twd, r.spread, r.turnover,
                a.open  AS adj_open,
                a.high  AS adj_high,
                a.low   AS adj_low,
                a.close AS adj_close,
                'qd_gold_finmind_price_canonical_v1' AS source
            FROM finmind_stock_price_norm r
            LEFT JOIN finmind_stock_price_adj_norm a
              ON r.trading_date = a.trading_date AND r.stock_id = a.stock_id
        ) TO '{dest}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{dest}')").fetchone()[0]
    stocks = con.execute(
        f"SELECT count(DISTINCT stock_id) FROM read_parquet('{dest}')"
    ).fetchone()[0]
    con.close()

    info = {"rows": rows, "stocks": stocks,
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="catalog_derived", table="gold/features/finmind_price_canonical",
        bronze_file="catalog:finmind_stock_price_norm+adj_norm",
        rows_in=rows, rows_out=rows, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[finmind_price_canonical] {info}")
    return info


def build_chip_dist_factors() -> dict:
    """Weekly holder-distribution factors from tw_chip_dist_daily.

    Silver rows are weekly snapshots (typically Fri). 4w-window = shift(4).
    Captures the 大戶/散戶 dynamic + pledged ratio.

    Output: gold/features/chip_dist_factors.parquet
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "flows" / "tw_chip_dist_daily" / "year=*" / "*.parquet")
    df = pl.scan_parquet(glob).select([
        "trading_date", "stock_id",
        "holdings_total_kshare", "pledged_kshare",
        "pct_under_400", "pct_over_1000",
        "holders_under_400", "holders_over_1000",
        "ingestion_ts",
    ]).collect()
    if df.schema["trading_date"] != pl.Date:
        df = df.with_columns(pl.col("trading_date").cast(pl.Date))
    df = df.sort(["stock_id", "trading_date", "ingestion_ts"]).unique(
        subset=["stock_id", "trading_date"], keep="last"
    ).drop("ingestion_ts").sort(["stock_id", "trading_date"])

    out = df.with_columns([
        (pl.col("pct_over_1000")
            - pl.col("pct_over_1000").shift(4).over("stock_id")).alias("large_holder_pct_chg_4w"),
        (pl.col("pct_under_400")
            - pl.col("pct_under_400").shift(4).over("stock_id")).alias("retail_pct_chg_4w"),
        (pl.col("pct_over_1000")
            / pl.when(pl.col("pct_under_400") == 0).then(None).otherwise(pl.col("pct_under_400"))
        ).alias("concentration_ratio"),
        (pl.col("pledged_kshare").cast(pl.Float64)
            / pl.when(pl.col("holdings_total_kshare") == 0).then(None)
              .otherwise(pl.col("holdings_total_kshare").cast(pl.Float64))
            * 100.0
        ).alias("pledged_pct"),
        (pl.col("holders_over_1000")
            - pl.col("holders_over_1000").shift(4).over("stock_id")).alias("large_holder_count_chg_4w"),
    ]).select([
        "trading_date", "stock_id",
        "pct_under_400", "pct_over_1000",
        "large_holder_pct_chg_4w", "retail_pct_chg_4w",
        "concentration_ratio", "pledged_pct",
        "large_holder_count_chg_4w",
    ]).with_columns([
        pl.lit("qd_gold_chip_dist_factors_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "chip_dist_factors.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height, "stocks": out["stock_id"].n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/chip_dist_factors",
        bronze_file="silver/flows/tw_chip_dist_daily",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[chip_dist_factors] {info}")
    return info


def build_revenue_factors() -> dict:
    """Monthly revenue factors from revenue_monthly silver.

    Silver already has yoy/mom/ttm/3m growth; gold adds acceleration + 24m
    z-score + persistence.

    Output: gold/features/revenue_factors.parquet
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    glob = str(SILVER / "fundamentals" / "revenue_monthly" / "year=*" / "*.parquet")
    df = pl.scan_parquet(glob).select([
        "stock_id", "fiscal_month", "publish_date",
        "revenue_monthly_ktwd", "revenue_ttm_ktwd",
        "revenue_yoy_growth_pct", "revenue_mom_growth_pct",
        "revenue_3m_growth_pct", "revenue_ttm_growth_pct",
        "ingestion_ts",
    ]).collect()
    if df.schema["fiscal_month"] != pl.Date:
        df = df.with_columns(pl.col("fiscal_month").cast(pl.Date))
    df = df.sort(["stock_id", "fiscal_month", "ingestion_ts"]).unique(
        subset=["stock_id", "fiscal_month"], keep="last"
    ).drop("ingestion_ts").sort(["stock_id", "fiscal_month"])

    mean24 = pl.col("revenue_3m_growth_pct").rolling_mean(window_size=24).over("stock_id")
    std24  = pl.col("revenue_3m_growth_pct").rolling_std(window_size=24).over("stock_id")
    mean24_ttm = pl.col("revenue_ttm_growth_pct").rolling_mean(window_size=24).over("stock_id")
    std24_ttm  = pl.col("revenue_ttm_growth_pct").rolling_std(window_size=24).over("stock_id")

    out = df.with_columns([
        (pl.col("revenue_yoy_growth_pct")
            - pl.col("revenue_yoy_growth_pct").shift(1).over("stock_id")
        ).alias("revenue_yoy_acceleration"),
        ((pl.col("revenue_3m_growth_pct") - mean24) / std24).alias("revenue_3m_zscore_24m"),
        ((pl.col("revenue_ttm_growth_pct") - mean24_ttm) / std24_ttm).alias("revenue_ttm_zscore_24m"),
        (pl.col("revenue_mom_growth_pct") > 0).cast(pl.Float64)
            .rolling_mean(window_size=6).over("stock_id").alias("revenue_mom_persistence_6m"),
    ]).select([
        "fiscal_month", "stock_id", "publish_date",
        "revenue_monthly_ktwd", "revenue_ttm_ktwd",
        "revenue_yoy_growth_pct", "revenue_mom_growth_pct",
        "revenue_yoy_acceleration",
        "revenue_3m_zscore_24m", "revenue_ttm_zscore_24m",
        "revenue_mom_persistence_6m",
    ]).with_columns([
        pl.lit("qd_gold_revenue_factors_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "revenue_factors.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height, "stocks": out["stock_id"].n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/revenue_factors",
        bronze_file="silver/fundamentals/revenue_monthly",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[revenue_factors] {info}")
    return info


def materialize_accounting_snapshot() -> dict:
    """Materialize accounting_raw view as a gold parquet for portability.

    The silver has 121 columns (mostly Chinese names). This builder is a
    direct COPY via DuckDB so downstream tools don't need to glob the
    hive-partitioned silver.

    Outputs:
      gold/features/accounting_raw_snapshot.parquet
      gold/features/accounting_raw_yearly.parquet
    """
    import duckdb
    from ..common.paths import CATALOG_DB
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    con = duckdb.connect(str(CATALOG_DB), read_only=True)

    dest = GOLD / "features" / "accounting_raw_snapshot.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM accounting_raw) TO '{dest}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{dest}')").fetchone()[0]

    dest_y = GOLD / "features" / "accounting_raw_yearly.parquet"
    con.execute(f"""
        COPY (
            SELECT
                EXTRACT(year FROM fiscal_month)::INT AS year,
                count(*) AS rows,
                count(DISTINCT stock_id) AS stocks,
                avg(資產總額) AS mean_total_assets,
                avg(負債總額) AS mean_total_liabilities,
                avg(現金及約當現金) AS mean_cash
            FROM accounting_raw
            GROUP BY 1
            ORDER BY 1
        ) TO '{dest_y}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    rows_y = con.execute(f"SELECT count(*) FROM read_parquet('{dest_y}')").fetchone()[0]
    con.close()

    info = {"snapshot_rows": rows, "yearly_rows": rows_y,
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="catalog_derived", table="gold/features/accounting_raw_snapshot",
        bronze_file="catalog:accounting_raw",
        rows_in=rows, rows_out=rows, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[accounting_raw_snapshot] {info}")
    return info


def _materialize_view_snapshot(view: str, dest_name: str, source_tag: str) -> dict:
    """Generic helper: COPY a catalog view to gold/features/<dest_name>.parquet."""
    import duckdb
    from ..common.paths import CATALOG_DB
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    con = duckdb.connect(str(CATALOG_DB), read_only=True)
    dest = GOLD / "features" / f"{dest_name}.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM {view}) TO '{dest}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{dest}')").fetchone()[0]
    con.close()
    info = {"rows": rows, "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source=source_tag, table=f"gold/features/{dest_name}",
        bronze_file=f"catalog:{view}",
        rows_in=rows, rows_out=rows, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[{dest_name}] {info}")
    return info


def materialize_tw_inst_futures_daily_snapshot() -> dict:
    """TAIFEX 三大法人期貨 daily snapshot (6.5K rows, scraper-direct silver)."""
    return _materialize_view_snapshot(
        "tw_inst_futures_daily", "tw_inst_futures_daily_snapshot",
        "catalog_derived",
    )


def materialize_txo_daily_features_snapshot() -> dict:
    """TXO 14-col daily features snapshot (1.5K rows)."""
    return _materialize_view_snapshot(
        "txo_daily_features", "txo_daily_features_snapshot",
        "catalog_derived",
    )


def materialize_tw_inst_market_daily_snapshot() -> dict:
    """市場層級三大法人 aggregate snapshot (15 rows)."""
    return _materialize_view_snapshot(
        "tw_inst_market_daily", "tw_inst_market_daily_snapshot",
        "catalog_derived",
    )


def build_bars_1m_daily_summary() -> dict:
    """Per-day OHLCV aggregation from bars_1m. 1m → 1d collapse cuts 15.6M rows to ~50K daily summaries.

    Output: gold/features/bars_1m_daily_summary.parquet
            keyed by (trading_date, asset_class, symbol)
    """
    import duckdb
    from ..common.paths import CATALOG_DB
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    con = duckdb.connect(str(CATALOG_DB), read_only=True)
    dest = GOLD / "features" / "bars_1m_daily_summary.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"""
        COPY (
            SELECT
                trading_date,
                asset_class,
                symbol,
                COUNT(*) AS bars_count,
                MIN(ts_utc) AS first_ts,
                MAX(ts_utc) AS last_ts,
                FIRST(open ORDER BY ts_utc) AS day_open,
                MAX(high) AS day_high,
                MIN(low) AS day_low,
                LAST(close ORDER BY ts_utc) AS day_close,
                SUM(volume) AS day_volume,
                AVG(close) AS day_avg_close,
                'qd_gold_bars_1m_daily_summary_v1' AS source
            FROM bars_1m
            WHERE close IS NOT NULL
            GROUP BY 1, 2, 3
            ORDER BY 2, 3, 1
        ) TO '{dest}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{dest}')").fetchone()[0]
    con.close()
    info = {"rows": rows, "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="catalog_derived", table="gold/features/bars_1m_daily_summary",
        bronze_file="catalog:bars_1m",
        rows_in=rows, rows_out=rows, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[bars_1m_daily_summary] {info}")
    return info


def build_macro_factors() -> dict:
    """Macro time-series factors from silver/macro/macro_daily.parquet.

    Per-symbol mom + vol + atr14, similar to stock_factor_daily but for
    VIX / USDTWD / WTI / 10Y etc.

    Output: gold/features/macro_factors.parquet  (keyed by trading_date, symbol)
    """
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    src = SILVER / "macro" / "macro_daily.parquet"
    if not src.exists():
        console.log(f"[red]missing {src}[/red]")
        return {}
    df = pl.read_parquet(src).select([
        "trading_date", "symbol", "open", "high", "low", "close", "adj_close", "volume",
    ])
    if df.schema["trading_date"] != pl.Date:
        df = df.with_columns(pl.col("trading_date").cast(pl.Date))
    df = df.filter(pl.col("close").is_not_null()).sort(["symbol", "trading_date"])

    df = df.with_columns([
        pl.col("close").log().diff().over("symbol").alias("log_ret"),
    ])

    out = df.with_columns([
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).alias("ret_1d"),
        (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1).alias("ret_5d"),
        (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1).alias("ret_20d"),
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1).alias("ret_60d"),
        pl.col("log_ret").rolling_std(window_size=20).over("symbol").alias("vol_20d"),
        pl.col("log_ret").rolling_std(window_size=60).over("symbol").alias("vol_60d"),
        (pl.col("high") - pl.col("low")).rolling_mean(window_size=14).over("symbol").alias("atr_14"),
    ]).select([
        "trading_date", "symbol", "close", "adj_close",
        "ret_1d", "ret_5d", "ret_20d", "ret_60d",
        "vol_20d", "vol_60d", "atr_14",
    ]).with_columns([
        pl.lit("qd_gold_macro_factors_v1").alias("source"),
        pl.lit(dt.datetime.now(dt.timezone.utc)).alias("ingestion_ts"),
    ])

    dest = GOLD / "features" / "macro_factors.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(dest, compression="zstd", compression_level=3)
    info = {"rows": out.height, "symbols": out["symbol"].n_unique(),
            "elapsed_sec": round(time.time() - t0, 1)}
    write_audit(IngestRecord(
        source="silver_derived", table="gold/features/macro_factors",
        bronze_file="silver/macro/macro_daily.parquet",
        rows_in=out.height, rows_out=out.height, sha256="", status="ok",
        started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        extra=info,
    ))
    console.log(f"[macro_factors] {info}")
    return info


def build_all() -> dict:
    summary = {
        "txo": copy_txo_daily_features(),
        "cross_market": copy_cross_market_features(),
        "stock_factor": build_stock_factor_daily(),
        "inst_flow": build_inst_flow_factors(),
        "margin": build_margin_factors(),
        "fundamentals_pit": build_fundamentals_pit(),
        "futures_large_trader": build_futures_large_trader_factors(),
        "futures_inst": build_futures_inst_factors(),
        "stock_attrs": build_stock_attrs_status(),
        "dividend_calendar": build_dividend_calendar(),
        "stock_futures_adjustments": build_stock_futures_adjustments(),
        "futures_bar_factors": build_futures_bar_factors(),
        "qc_snapshot": materialize_qc_snapshot(),
        "finmind_canonical": materialize_finmind_canonical(),
        "chip_dist_factors": build_chip_dist_factors(),
        "revenue_factors": build_revenue_factors(),
        "accounting_snapshot": materialize_accounting_snapshot(),
        "tw_inst_futures_daily_snapshot": materialize_tw_inst_futures_daily_snapshot(),
        "txo_daily_features_snapshot": materialize_txo_daily_features_snapshot(),
        "tw_inst_market_daily_snapshot": materialize_tw_inst_market_daily_snapshot(),
        "bars_1m_daily_summary": build_bars_1m_daily_summary(),
        "macro_factors": build_macro_factors(),
    }
    return summary


if __name__ == "__main__":
    build_all()
