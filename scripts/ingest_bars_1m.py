#!/usr/bin/env python3
"""把 RAW_SOURCES/MXF_1m_clean_all.parquet ingest 進 silver/bars/bars_1m/。

source schema：
    datetime / trading_date / session / is_settlement_day / is_holiday_gap /
    minutes_from_open / open / high / low / close / adj_open / adj_high /
    adj_low / adj_close / (15 cols, ~1.67M rows, 2020-03-02 → 2026-03-11)

silver layout（hive-partitioned by year）：
    silver/bars/bars_1m/asset_class=tw_futures/symbol=MXF/year=YYYY/*.parquet

加上 source / ingestion_ts / asset_class / symbol 標準欄；dedup
(datetime) keep last by ingestion_ts。
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from qd_ingest.common.io import write_silver_partitioned
from qd_ingest.common.paths import RAW_ROOT, SILVER

NOW_ISO = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def ingest_mxf_1m(*, dry_run: bool = False) -> dict:
    src = RAW_ROOT / "MXF_1m_clean_all.parquet"
    dest_root = SILVER / "bars" / "bars_1m"
    if not src.exists():
        return {"ok": False, "error": f"missing source: {src}"}

    df = pd.read_parquet(src)
    df["asset_class"] = "tw_futures"
    df["symbol"] = "MXF"
    df["source"] = "tquant_lab_mxf_1m"
    df["ingestion_ts"] = NOW_ISO

    # canonical trading_date column (already exists as object dtype)
    df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
    df["year"] = pd.to_datetime(df["datetime"]).dt.year

    # dedup by (datetime) keep last by ingestion_ts
    df = df.sort_values("ingestion_ts").drop_duplicates(
        subset=["datetime", "symbol"], keep="last"
    )
    df = df.sort_values(["year", "datetime"]).reset_index(drop=True)

    info = {
        "rows": len(df),
        "datetime_min": str(df["datetime"].min()),
        "datetime_max": str(df["datetime"].max()),
        "trading_date_max": str(df["trading_date"].max()),
        "years": sorted(df["year"].unique().tolist()),
    }
    if dry_run:
        info["dry_run"] = True
        return info

    table = pa.Table.from_pandas(df, preserve_index=False)
    write_silver_partitioned(table, dest_root, partition_cols=["asset_class", "symbol", "year"])
    info["dest"] = str(dest_root)
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    r = ingest_mxf_1m(dry_run=args.dry_run)
    print(r)
    return 0 if r.get("ok") is not False else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
