#!/usr/bin/env python3
"""把 RAW_SOURCES/TXO_1min_merged_*.parquet 寫進 silver/options/txo_1min/。

source schema：
    trade_date / product_id / expiry_month / strike_price / option_type /
    minute / open / high / low / close / volume （11 cols, 2.19M rows）

silver layout（hive-partitioned by year）：
    silver/options/txo_1min/year=YYYY/*.parquet

dedup: (trade_date, expiry_month, strike_price, option_type, minute)
keep last by ingestion_ts。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
from pathlib import Path

import pandas as pd
import pyarrow as pa

from qd_ingest.common.io import write_silver_partitioned
from qd_ingest.common.paths import RAW_ROOT, SILVER

NOW_ISO = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def ingest_txo_1min(*, dry_run: bool = False) -> dict:
    pattern = str(RAW_ROOT / "TXO_1min_merged_*.parquet")
    sources = sorted(glob.glob(pattern))
    if not sources:
        return {"ok": False, "error": f"no files matching {pattern}"}

    dest_root = SILVER / "options" / "txo_1min"

    frames = []
    for src in sources:
        df = pd.read_parquet(src)
        df["_source_file"] = Path(src).name
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["year"] = pd.to_datetime(df["trade_date"]).dt.year
    df["source"] = "tquant_lab_txo_1min"
    df["ingestion_ts"] = NOW_ISO

    key_cols = ["trade_date", "expiry_month", "strike_price", "option_type", "minute"]
    df = df.sort_values("ingestion_ts").drop_duplicates(subset=key_cols, keep="last")
    df = df.sort_values(["year", "trade_date", "minute"]).reset_index(drop=True)

    info = {
        "files": [Path(s).name for s in sources],
        "rows": len(df),
        "trade_date_min": str(df["trade_date"].min()),
        "trade_date_max": str(df["trade_date"].max()),
        "years": sorted(df["year"].unique().tolist()),
        "option_types": sorted(df["option_type"].dropna().unique().tolist()),
    }
    if dry_run:
        info["dry_run"] = True
        return info

    table = pa.Table.from_pandas(df, preserve_index=False)
    write_silver_partitioned(table, dest_root, partition_cols=["year"])
    info["dest"] = str(dest_root)
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    r = ingest_txo_1min(dry_run=args.dry_run)
    print(r)
    return 0 if r.get("ok") is not False else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
