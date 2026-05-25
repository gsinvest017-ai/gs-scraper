#!/usr/bin/env python3
"""restore_finmind_views.py — re-create FinMind + qc views in catalog/quant.duckdb.

`qd-ingest build-catalog` rebuilds the catalog from a fixed view DDL set; it
does NOT know about the FinMind bronze sqlite views. Each daily_refresh
therefore drops them. This script restores them idempotently after every
build-catalog run.

The views point at the latest bronze/finmind/finmind_*.sqlite by glob;
if multiple snapshots exist, the lexicographically largest filename wins
(i.e. snapshot dates sort correctly).

Usage:
    python scripts/restore_finmind_views.py
    python scripts/restore_finmind_views.py --catalog catalog/quant_public.duckdb
    python scripts/restore_finmind_views.py --sqlite bronze/finmind/finmind_2026-05-18.sqlite

Exit codes:
    0  views restored (or nothing to restore — no sqlite found)
    1  catalog DB not found / unreadable
    2  sqlite file specified/found but unreadable
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]


def find_latest_sqlite() -> Path | None:
    cands = sorted(glob.glob(str(REPO / "bronze" / "finmind" / "finmind_*.sqlite")))
    if not cands:
        return None
    return Path(cands[-1])


def restore(catalog_path: Path, sqlite_path: Path) -> int:
    abs_sqlite = str(sqlite_path.resolve())
    con = duckdb.connect(str(catalog_path))
    con.execute("INSTALL sqlite; LOAD sqlite;")

    # Raw 1:1 views
    raw_views = [
        ("taiwan_stock_price",              "finmind_stock_price"),
        ("taiwan_stock_price_adj",          "finmind_stock_price_adj"),
        ("taiwan_stock_info",               "finmind_stock_info"),
        ("taiwan_stock_info_with_warrant",  "finmind_stock_info_with_warrant"),
        ("taiwan_stock_trading_date",       "finmind_trading_date"),
        ("taiwan_stock_week_price",         "finmind_stock_week_price"),
    ]
    for src, name in raw_views:
        con.execute(f"DROP VIEW IF EXISTS {name}")
        con.execute(
            f"CREATE VIEW {name} AS SELECT * FROM sqlite_scan('{abs_sqlite}', '{src}')"
        )

    # Canonical normalised views (max/min -> high/low, Trading_Volume -> volume, +source)
    for src, name in [
        ("taiwan_stock_price",     "finmind_stock_price_norm"),
        ("taiwan_stock_price_adj", "finmind_stock_price_adj_norm"),
    ]:
        con.execute(f"DROP VIEW IF EXISTS {name}")
        con.execute(f"""
            CREATE VIEW {name} AS
            SELECT CAST(date AS DATE)                  AS trading_date,
                   stock_id,
                   open,
                   "max"                               AS high,
                   "min"                               AS low,
                   close,
                   CAST(Trading_Volume AS BIGINT)      AS volume,
                   CAST(Trading_money  AS BIGINT)      AS amount_twd,
                   spread,
                   CAST(Trading_turnover AS BIGINT)    AS turnover,
                   'finmind'                           AS "source"
            FROM sqlite_scan('{abs_sqlite}', '{src}')
        """)

    # QC: TEJ vs FinMind 2010+ overlap (best-effort — tolerates missing tw_stock_bars)
    try:
        con.execute("DROP VIEW IF EXISTS qc_stock_price_diff")
        con.execute("""
            CREATE VIEW qc_stock_price_diff AS
            SELECT t.trading_date,
                   t.symbol AS stock_id,
                   t.close  AS tej_close,
                   f.close  AS finmind_close,
                   (t.close - f.close) / f.close AS pct_diff,
                   t.volume AS tej_volume,
                   f.volume AS finmind_volume
            FROM tw_stock_bars t
            JOIN finmind_stock_price_norm f
              ON t.symbol = f.stock_id AND t.trading_date = f.trading_date
            WHERE t.asset_class='tw_stock' AND t.close IS NOT NULL
              AND f.close IS NOT NULL AND f.close > 0
        """)
    except Exception as e:
        print(f"[warn] qc_stock_price_diff: skipped — {e}", file=sys.stderr)

    restored = [
        r[0] for r in con.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='main'
              AND (table_name LIKE 'finmind%' OR table_name='qc_stock_price_diff')
            ORDER BY 1
        """).fetchall()
    ]
    con.close()
    print(f"[restore_finmind_views] {len(restored)} views restored in {catalog_path}:")
    for v in restored:
        print(f"  - {v}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--catalog", default=str(REPO / "catalog" / "quant.duckdb"),
                   help="Path to catalog .duckdb (default: catalog/quant.duckdb)")
    p.add_argument("--sqlite", default=None,
                   help="Path to FinMind sqlite (default: latest bronze/finmind/finmind_*.sqlite)")
    args = p.parse_args()

    catalog_path = Path(args.catalog)
    if not catalog_path.exists():
        print(f"[restore_finmind_views] ERROR: catalog not found at {catalog_path}",
              file=sys.stderr)
        return 1

    sqlite_path = Path(args.sqlite) if args.sqlite else find_latest_sqlite()
    if sqlite_path is None:
        print("[restore_finmind_views] no bronze/finmind/finmind_*.sqlite found; nothing to restore",
              file=sys.stderr)
        return 0
    if not sqlite_path.exists():
        print(f"[restore_finmind_views] ERROR: sqlite not found at {sqlite_path}",
              file=sys.stderr)
        return 2

    return restore(catalog_path, sqlite_path)


if __name__ == "__main__":
    raise SystemExit(main())
