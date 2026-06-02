#!/usr/bin/env python3
"""把 RAW_SOURCES 內手動 dump 的連續期 parquet 標準化後寫進 gold/continuous/。

對應 gap dashboard 三條 STALE：
- tx_continuous_d  ← RAW_SOURCES/日k 期貨tquant lab/TX_continuous_raw.parquet
- mtx_continuous_d ← 同上 MTX
- stock_futures_continuous_d ← RAW_SOURCES/股票期貨/continuous_near_month.parquet

只做 column rename + ingestion_ts 戳 + dedup-by-trading_date keep last，
不改價量數值；如要再校正、加 adj_back 版本等，請額外加 builder。

用法：
    python scripts/refresh_continuous_from_raw.py
    python scripts/refresh_continuous_from_raw.py --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd

from qd_ingest.common.paths import GOLD, RAW_ROOT

NOW_ISO = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _refresh_tx_mtx(symbol: str, *, dry_run: bool) -> dict:
    """TX / MTX 用同一個來源 schema（tquant lab dump）。"""
    src = RAW_ROOT / "日k 期貨tquant lab" / f"{symbol}_continuous_raw.parquet"
    dest = GOLD / "continuous" / f"{symbol.lower()}_continuous_d.parquet"
    if not src.exists():
        return {"symbol": symbol, "ok": False, "error": f"missing source: {src}"}

    df = pd.read_parquet(src)
    # source schema: mdate / coid / front_due_m / due_m / open_d/high_d/low_d/close_d / vol_d / ...
    df = df.rename(columns={
        "mdate": "trading_date",
        "open_d": "open",
        "high_d": "high",
        "low_d": "low",
        "close_d": "close",
        "vol_d": "volume",
        "coid": "contract_code",
    })
    if "trading_date" not in df.columns:
        return {"symbol": symbol, "ok": False, "error": "no `mdate` column"}

    df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
    df["_symbol"] = symbol  # 標記 underlying
    df["ingestion_ts"] = NOW_ISO

    # dedup by trading_date keep last
    df = df.sort_values("ingestion_ts").drop_duplicates(subset=["trading_date"], keep="last")
    df = df.sort_values("trading_date").reset_index(drop=True)

    if dry_run:
        return {"symbol": symbol, "rows": len(df), "dest": str(dest), "dry_run": True}

    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest, compression="zstd", index=False)
    return {
        "symbol": symbol,
        "rows": len(df),
        "max_date": str(df["trading_date"].max()),
        "min_date": str(df["trading_date"].min()),
        "dest": str(dest.relative_to(Path.cwd()) if dest.is_relative_to(Path.cwd()) else dest),
    }


def _refresh_stock_futures(*, dry_run: bool) -> dict:
    src = RAW_ROOT / "股票期貨" / "continuous_near_month.parquet"
    dest = GOLD / "continuous" / "stock_futures_continuous_d.parquet"
    if not src.exists():
        return {"symbol": "stock_futures", "ok": False, "error": f"missing source: {src}"}

    df = pd.read_parquet(src)
    # source schema: date / futures_code / delivery_month / open/high/low/close / volume / ...
    df = df.rename(columns={"date": "trading_date"})
    df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
    df["ingestion_ts"] = NOW_ISO
    df = df.sort_values("ingestion_ts").drop_duplicates(
        subset=["trading_date", "futures_code"], keep="last"
    )
    df = df.sort_values(["trading_date", "futures_code"]).reset_index(drop=True)

    if dry_run:
        return {"symbol": "stock_futures", "rows": len(df), "dest": str(dest), "dry_run": True}

    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest, compression="zstd", index=False)
    return {
        "symbol": "stock_futures",
        "rows": len(df),
        "futures": df["futures_code"].nunique(),
        "max_date": str(df["trading_date"].max()),
        "min_date": str(df["trading_date"].min()),
        "dest": str(dest.relative_to(Path.cwd()) if dest.is_relative_to(Path.cwd()) else dest),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    results = [
        _refresh_tx_mtx("TX", dry_run=args.dry_run),
        _refresh_tx_mtx("MTX", dry_run=args.dry_run),
        _refresh_stock_futures(dry_run=args.dry_run),
    ]
    for r in results:
        print(r)
    if any(r.get("ok") is False for r in results):
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
