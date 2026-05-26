"""extend_stock_futures_continuous.py — append new rows to
gold/continuous/stock_futures_continuous_d.parquet from silver bars_1d.

The historical part (≤ 2026-04-10) comes from RAW_SOURCES/股票期貨 manual drop
with full schema (underlying_code, name, is_rollover, etc). The tail is
extended from bars_1d.tw_futures filtered to individual stock-futures symbols
(3-letter codes excluding well-known index/commodity futures families):
  source = 'qd_stock_futures_continuous_extended_from_bars1d'
  underlying_code / name / is_rollover / hist_high / hist_low = NULL

Usage:
  .venv/bin/python scripts/extend_stock_futures_continuous.py
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog" / "quant.duckdb"

# Symbols in bars_1d.tw_futures that are INDEX / COMMODITY futures, NOT individual
# stock futures. Anything 3-letter NOT in this list is treated as a stock future.
INDEX_AND_COMMODITY_FUTURES = {
    # TAIFEX 指數期 family
    "TX", "MTX", "TE", "TF", "GTF",
    # TAIFEX 商品/小型/特殊
    "MSF", "M1F", "XIF", "TGF", "SXF", "I5F", "TMF", "RHF", "SOF",
    "ZBT", "ZSQ", "ZOK", "ZEF", "ZTE", "ZUR", "ZZE",
    "OAF", "URF", "SIF", "SQF", "ZFF", "RIF", "USF", "F1F", "XAF",
    "BTF", "RTF", "E4F", "SHF", "SMF",
}


def main() -> dict:
    fp = REPO / "gold" / "continuous" / "stock_futures_continuous_d.parquet"
    if not fp.exists():
        return {"skipped": True, "reason": "parquet missing"}

    tmp_cat = REPO / "tmp" / "extend_stock_futures.duckdb"
    tmp_cat.parent.mkdir(exist_ok=True)
    if tmp_cat.exists():
        tmp_cat.unlink()
    shutil.copy(CATALOG, tmp_cat)
    con = duckdb.connect(str(tmp_cat))

    max_date = con.execute(f"SELECT max(trading_date) FROM '{fp}'").fetchone()[0]

    exclusion_csv = ",".join(f"'{s}'" for s in sorted(INDEX_AND_COMMODITY_FUTURES))
    new_rows = con.execute(f"""
        SELECT
            symbol AS futures_code,
            -- delivery_month from contract_id like 'CAF202606' -> '202606'
            CASE
                WHEN contract_id IS NULL THEN NULL
                WHEN length(contract_id) >= length(symbol) + 6
                THEN SUBSTR(contract_id, length(symbol) + 1, 6)
                ELSE NULL
            END AS delivery_month,
            open, high, low, close,
            close - LAG(close) OVER (PARTITION BY symbol ORDER BY trading_date) AS change,
            ((close - LAG(close) OVER (PARTITION BY symbol ORDER BY trading_date))
             / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY trading_date), 0) * 100.0
            ) AS change_pct,
            volume,
            settlement AS settlement_price,
            open_interest::DOUBLE AS open_interest,
            NULL::DOUBLE AS best_bid,
            NULL::DOUBLE AS best_ask,
            NULL::DOUBLE AS hist_high,
            NULL::DOUBLE AS hist_low,
            NULL::VARCHAR AS halted,
            session,
            NULL::DOUBLE AS spread_volume,
            NULL::VARCHAR AS underlying_code,
            NULL::VARCHAR AS name,
            NULL::BOOLEAN AS is_rollover,
            ((close - open) / NULLIF(open, 0)) AS daily_return,
            NULL::BOOLEAN AS is_abnormal_jump,
            trading_date,
        FROM bars_1d
        WHERE asset_class = 'tw_futures'
          AND symbol NOT IN ({exclusion_csv})
          AND length(symbol) = 3
          AND trading_date > DATE '{max_date}'
          AND close IS NOT NULL
          AND contract_id IS NOT NULL
          -- pick the front (highest volume) contract per (symbol, trading_date)
          AND volume = (
                SELECT MAX(b2.volume)
                FROM bars_1d b2
                WHERE b2.asset_class = 'tw_futures'
                  AND b2.symbol = bars_1d.symbol
                  AND b2.trading_date = bars_1d.trading_date
                  AND b2.contract_id IS NOT NULL
                  AND b2.contract_id NOT LIKE '%/%'  -- exclude calendar spreads
          )
          AND contract_id NOT LIKE '%/%'
        ORDER BY symbol, trading_date
    """).fetchdf()

    if new_rows.empty:
        con.close()
        tmp_cat.unlink()
        return {"max_before": str(max_date), "added": 0}

    new_rows["source"] = "qd_stock_futures_continuous_extended_from_bars1d"
    new_rows["ingestion_ts"] = pd.Timestamp.now(tz="UTC")

    existing = con.execute(f"SELECT * FROM '{fp}'").fetchdf()
    # Align column order to existing; fill missing in new_rows with NULLs
    for col in existing.columns:
        if col not in new_rows.columns:
            new_rows[col] = None
    new_rows = new_rows[existing.columns]

    combined = pd.concat([existing, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(subset=["futures_code", "trading_date"], keep="last") \
                       .sort_values(["futures_code", "trading_date"])

    # backup
    bak = fp.with_suffix(".parquet.bak")
    shutil.copy(fp, bak)
    pq.write_table(pa.Table.from_pandas(combined, preserve_index=False), fp, compression="zstd")

    info = {
        "max_before": str(max_date),
        "max_after": str(combined["trading_date"].max()),
        "added": len(new_rows),
        "total": len(combined),
        "futures_codes_added": int(new_rows["futures_code"].nunique()),
    }
    con.close()
    tmp_cat.unlink()
    print(info)
    return info


if __name__ == "__main__":
    main()
