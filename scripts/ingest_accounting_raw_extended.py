#!/usr/bin/env python3
"""把 TEJ 訂閱包匯出 CSV 寫進 silver/fundamentals/accounting_raw_extended/。

source: 使用者手動匯出 `台灣上市公司單季財報資料YYYY_YYYY.csv`
schema: 796 cols（IFRS9 細項展開），66,181 rows，2005-Q2 ~ 2025-Q4

silver layout: hive-partitioned by year
    silver/fundamentals/accounting_raw_extended/year=YYYY/*.parquet

dedup: (stock_id, fiscal_month, period_type) keep last by ingestion_ts。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
from pathlib import Path

import pandas as pd
import pyarrow as pa

from qd_ingest.common.io import write_silver_partitioned
from qd_ingest.common.paths import SILVER

NOW_ISO = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def ingest_extended(*, src_path: str | None = None, dry_run: bool = False) -> dict:
    # 預設搜 /mnt/c/Users/User/Downloads/台灣上市公司單季財報資料*.csv
    if src_path is None:
        matches = sorted(glob.glob(
            "/mnt/c/Users/User/Downloads/台灣上市公司單季財報資料*.csv"
        ))
        if not matches:
            return {"ok": False, "error": "no source CSV found under /mnt/c/Users/User/Downloads/"}
        src_path = matches[-1]  # 取最新
    src = Path(src_path)
    if not src.exists():
        return {"ok": False, "error": f"missing: {src}"}

    dest_root = SILVER / "fundamentals" / "accounting_raw_extended"

    df = pd.read_csv(src, encoding="utf-8")
    # Strip leading/trailing whitespace from column names (358/796 affected)
    df.columns = [c.strip() for c in df.columns]

    # 標準化欄位
    df["stock_id"] = df["代號"].astype(str).str.strip()
    df["fiscal_month"] = pd.to_datetime(df["年/月"]).dt.date
    df["fiscal_quarter"] = df["季別"].astype(int)
    df["period_type"] = df["單季(Q)/單半年(H)"].astype(str).str.strip()
    df["year"] = pd.to_datetime(df["年/月"]).dt.year
    df["source"] = f"tej_subscription_csv_{src.stem}"
    df["ingestion_ts"] = NOW_ISO

    # dedup
    key_cols = ["stock_id", "fiscal_month", "period_type"]
    n_before = len(df)
    df = df.sort_values("ingestion_ts").drop_duplicates(subset=key_cols, keep="last")
    df = df.sort_values(["year", "fiscal_month", "stock_id"]).reset_index(drop=True)
    n_after = len(df)

    info = {
        "src": str(src),
        "rows_in": n_before,
        "rows_out": n_after,
        "rows_dedup": n_before - n_after,
        "cols": len(df.columns),
        "fiscal_month_min": str(df["fiscal_month"].min()),
        "fiscal_month_max": str(df["fiscal_month"].max()),
        "years": sorted(df["year"].unique().tolist()),
        "stocks": int(df["stock_id"].nunique()),
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
    ap.add_argument("--src", default=None, help="path to source CSV (default: auto-detect)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    r = ingest_extended(src_path=args.src, dry_run=args.dry_run)
    print(r)
    return 0 if r.get("ok") is not False else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
