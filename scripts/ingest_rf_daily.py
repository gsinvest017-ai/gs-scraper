#!/usr/bin/env python3
"""把 RAW_SOURCES/無風險利率日資料_2019-2026.csv 寫進 silver/macro/rf_daily.parquet。

source schema (CSV)：
    date,rf

silver layout（非 hive-partitioned，單檔即可——一條 daily 序列）：
    silver/macro/rf_daily.parquet

加上 source / ingestion_ts；dedup (date) keep last by ingestion_ts。
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from qd_ingest.common.paths import RAW_ROOT, SILVER

NOW_ISO = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def ingest_rf(*, dry_run: bool = False) -> dict:
    src = RAW_ROOT / "無風險利率日資料_2019-2026.csv"
    dest = SILVER / "macro" / "rf_daily.parquet"
    if not src.exists():
        return {"ok": False, "error": f"missing source: {src}"}

    df = pd.read_csv(src)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["rf"] = pd.to_numeric(df["rf"], errors="coerce")
    df["source"] = "tquant_lab_rf_csv"
    df["ingestion_ts"] = NOW_ISO

    df = df.sort_values("ingestion_ts").drop_duplicates(subset=["date"], keep="last")
    df = df.dropna(subset=["rf"]).sort_values("date").reset_index(drop=True)

    info = {
        "rows": len(df),
        "date_min": str(df["date"].min()),
        "date_max": str(df["date"].max()),
        "rf_mean": round(df["rf"].mean(), 6),
        "rf_max": round(df["rf"].max(), 6),
        "rf_min": round(df["rf"].min(), 6),
    }
    if dry_run:
        info["dry_run"] = True
        return info

    dest.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), dest, compression="zstd")
    info["dest"] = str(dest)
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    r = ingest_rf(dry_run=args.dry_run)
    print(r)
    return 0 if r.get("ok") is not False else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
