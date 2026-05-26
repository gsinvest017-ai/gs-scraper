"""extend_continuous.py — extend gold/continuous/{tx,mtx}_continuous_d.parquet
from silver bars_1d.

The historical part (≤ 2026-05-08) comes from TEJ tquant_lab manual drop with
full back-adjustment. The newer tail is appended on the fly:
  source = 'qd_{tx|mtx}_continuous_extended_from_bars1d'
  adj_factor = NULL, *_adj columns = raw (back-adjustment chain not extended)

Usage:
  .venv/bin/python scripts/extend_continuous.py
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog" / "quant.duckdb"


def extend(product: str) -> dict:
    """product: 'TX' or 'MTX'."""
    fp = REPO / "gold" / "continuous" / f"{product.lower()}_continuous_d.parquet"
    if not fp.exists():
        return {"product": product, "skipped": True, "reason": "parquet missing"}

    # Use a temp copy of the catalog to avoid the write-lock when DuckDB UI is open.
    tmp_cat = REPO / "tmp" / "extend_continuous.duckdb"
    tmp_cat.parent.mkdir(exist_ok=True)
    if tmp_cat.exists():
        tmp_cat.unlink()
    shutil.copy(CATALOG, tmp_cat)
    con = duckdb.connect(str(tmp_cat))

    max_date = con.execute(
        f"SELECT max(trading_date) FROM '{fp}'"
    ).fetchone()[0]

    # Front contract per day = max(volume) among non-weekly contracts (no 'W' suffix)
    new_rows = con.execute(f"""
        WITH ranked AS (
            SELECT
                trading_date,
                symbol AS product,
                contract_id AS front_contract,
                open, high, low, close, settlement AS settle,
                volume, open_interest,
                ROW_NUMBER() OVER (PARTITION BY trading_date ORDER BY volume DESC) AS rn
            FROM bars_1d
            WHERE asset_class = 'tw_futures'
              AND symbol = '{product}'
              AND contract_id NOT LIKE '%W%'  -- exclude weekly variants
              AND trading_date > DATE '{max_date}'
              AND close IS NOT NULL
        )
        SELECT
            trading_date,
            product,
            front_contract,
            -- Parse expiry from contract_id like TX202606 → last day of 2026-06
            (
                STRPTIME(SUBSTR(front_contract, length(product) + 1, 6) || '01', '%Y%m%d')::DATE
                + INTERVAL 1 MONTH - INTERVAL 1 DAY
            )::DATE AS expiry,
            NULL::DATE AS front_expiry_first_trade,
            NULL::DATE AS last_trade_date,
            (
                (STRPTIME(SUBSTR(front_contract, length(product) + 1, 6) || '01', '%Y%m%d')::DATE
                 + INTERVAL 1 MONTH - INTERVAL 1 DAY)::DATE
                - trading_date
            ) AS days_to_expiry,
            open, high, low, close, settle, volume, open_interest,
            ((close - open) / NULLIF(open, 0) * 100.0) AS roi_pct,
            NULL::DOUBLE AS basis,
            NULL::DOUBLE AS adj_factor,
            open  AS open_adj,
            high  AS high_adj,
            low   AS low_adj,
            close AS close_adj,
            settle AS settle_adj,
            'qd_{product.lower()}_continuous_extended_from_bars1d' AS source,
            (now() AT TIME ZONE 'UTC') AS ingestion_ts,
        FROM ranked
        WHERE rn = 1
        ORDER BY trading_date
    """).fetchdf()

    if new_rows.empty:
        con.close()
        tmp_cat.unlink()
        return {"product": product, "max_before": str(max_date), "added": 0}

    # Append + write back. Read existing, concat, write parquet.
    existing = con.execute(f"SELECT * FROM '{fp}'").fetchdf()
    import pandas as pd
    combined = pd.concat([existing, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(subset=["trading_date"], keep="last").sort_values("trading_date")

    # backup
    bak = fp.with_suffix(".parquet.bak")
    shutil.copy(fp, bak)

    # write
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.Table.from_pandas(combined, preserve_index=False), fp, compression="zstd")

    new_max = combined["trading_date"].max()
    info = {
        "product": product,
        "max_before": str(max_date),
        "max_after": str(new_max),
        "added": len(new_rows),
        "total": len(combined),
    }
    con.close()
    tmp_cat.unlink()
    return info


def main():
    for product in ("TX", "MTX"):
        info = extend(product)
        print(info)


if __name__ == "__main__":
    main()
