#!/usr/bin/env python3
"""把 RAW_SOURCES/三大法人買賣超/institutional_yahoo_value_clean.csv 寫進
silver/flows/tw_inst_market_daily/ (hive partition by year)。

source schema (16 cols 已 cleaned)：
    date, foreign_ex_dealer_billion, foreign_dealer_billion, foreign_total_billion,
    sitc_billion, dealer_self_billion, dealer_hedge_billion, dealer_total_billion,
    three_inst_total_billion, foreign_sitc_billion, extra_1..4, source, source_line

silver layout (hive-partitioned by year)：
    silver/flows/tw_inst_market_daily/year=YYYY/*.parquet

dedup: trading_date keep last by ingestion_ts。
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd
import pyarrow as pa

from qd_ingest.common.io import write_silver_partitioned
from qd_ingest.common.paths import RAW_ROOT, SILVER

NOW_ISO = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


_RENAME = {
    "foreign_ex_dealer_billion": "foreign_ex_dealer_twd_bn",
    "foreign_dealer_billion": "foreign_dealer_twd_bn",
    "foreign_total_billion": "foreign_total_twd_bn",
    "sitc_billion": "sitc_twd_bn",
    "dealer_self_billion": "dealer_self_twd_bn",
    "dealer_hedge_billion": "dealer_hedge_twd_bn",
    "dealer_total_billion": "dealer_total_twd_bn",
    "three_inst_total_billion": "three_inst_total_twd_bn",
    "foreign_sitc_billion": "foreign_sitc_twd_bn",
}


def ingest_inst_market(*, dry_run: bool = False) -> dict:
    src = RAW_ROOT / "三大法人買賣超" / "institutional_yahoo_value_clean.csv"
    dest_root = SILVER / "flows" / "tw_inst_market_daily"

    if not src.exists():
        return {"ok": False, "error": f"missing source: {src}"}

    df = pd.read_csv(src)
    df = df.rename(columns=_RENAME).rename(columns={"date": "trading_date"})

    df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
    df["year"] = pd.to_datetime(df["trading_date"]).dt.year
    df["source"] = "yahoo_inst_value_cleaned"
    df["ingestion_ts"] = NOW_ISO

    df = df.sort_values("ingestion_ts").drop_duplicates(subset=["trading_date"], keep="last")
    df = df.sort_values(["year", "trading_date"]).reset_index(drop=True)

    info = {
        "rows": len(df),
        "trading_date_min": str(df["trading_date"].min()),
        "trading_date_max": str(df["trading_date"].max()),
        "years": sorted(df["year"].unique().tolist()),
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
    r = ingest_inst_market(dry_run=args.dry_run)
    print(r)
    return 0 if r.get("ok") is not False else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
