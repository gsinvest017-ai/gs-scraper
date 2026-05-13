"""Final smoke test for QUANTDATA architecture.

Demonstrates the medallion architecture end-to-end through the DuckDB catalog:
  bars_1d × tw_inst_stock_daily × tw_margin_daily × fundamentals_q (ASOF) × macro_daily

Run from repo root:  python scripts/smoke_query.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "catalog" / "quant.duckdb"


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    con.execute(f"SET file_search_path='{ROOT}'")

    print("=" * 72)
    print("1) Catalog views")
    print("=" * 72)
    print(con.sql("SHOW TABLES").df())
    print()

    print("=" * 72)
    print("2) symbol_map sample (5 rows)")
    print("=" * 72)
    print(con.sql("""
        SELECT canonical_symbol, name_zh, asset_class, exchange, yahoo_ticker
        FROM symbol_map
        WHERE asset_class IN ('tw_futures','us_futures','us_index','fx')
        ORDER BY asset_class, canonical_symbol
        LIMIT 8
    """).df())
    print()

    print("=" * 72)
    print("3) TSMC (2330) end-to-end join (bars × inst × margin) 2024 sample")
    print("=" * 72)
    print(con.sql("""
        SELECT b.trading_date, b.close, b.volume,
               i.foreign_net_lot, i.total_net_lot,
               m.margin_balance_lot, m.short_to_margin_pct
        FROM bars_1d b
        LEFT JOIN tw_inst_stock_daily i ON b.trading_date = i.trading_date AND b.symbol = i.stock_id
        LEFT JOIN tw_margin_daily      m ON b.trading_date = m.trading_date AND b.symbol = m.stock_id
        WHERE b.asset_class = 'tw_stock' AND b.symbol = '2330'
          AND b.trading_date BETWEEN DATE '2024-01-02' AND DATE '2024-01-10'
        ORDER BY b.trading_date
    """).df())
    print()

    print("=" * 72)
    print("4) Point-in-time ASOF join (bars × fundamentals_q) — 2330 sample")
    print("=" * 72)
    # ASOF needs period_type filter BEFORE the join (otherwise the matched row may be YTD)
    print(con.sql("""
        WITH fq AS (
            SELECT stock_id, fiscal_period, publish_date, eps, roe_post
            FROM fundamentals_q WHERE period_type = 'Q'
        )
        SELECT b.trading_date, b.close, f.fiscal_period, f.publish_date, f.eps, f.roe_post
        FROM tw_stock_bars b
        ASOF LEFT JOIN fq f
          ON b.symbol = f.stock_id AND b.trading_date >= f.publish_date
        WHERE b.symbol = '2330'
          AND b.trading_date IN (DATE '2024-05-14', DATE '2024-05-15', DATE '2024-08-09', DATE '2024-08-12')
        ORDER BY b.trading_date
    """).df())
    print()

    print("=" * 72)
    print("5) TAIFEX MXF foreign OI 趨勢")
    print("=" * 72)
    print(con.sql("""
        SELECT trading_date, identity, net_oi_contracts, net_oi_z60
        FROM tw_inst_futures_daily
        WHERE product = 'MXF' AND identity = 'fii'
        ORDER BY trading_date DESC
        LIMIT 5
    """).df())
    print()

    print("=" * 72)
    print("6) 美股 macro daily 抽樣")
    print("=" * 72)
    print(con.sql("""
        SELECT trading_date, symbol, close
        FROM macro_daily
        WHERE symbol IN ('VIX','SPX','TAIEX','USDTWD') AND trading_date >= DATE '2026-04-01'
        ORDER BY trading_date DESC, symbol
        LIMIT 8
    """).df())
    print()

    print("=" * 72)
    print("7) bars_1m sample — MXF 2024 first 3 day-session bars")
    print("=" * 72)
    print(con.sql("""
        SELECT ts_utc, open, high, low, close, volume
        FROM bars_1m
        WHERE asset_class = 'tw_futures' AND symbol = 'MXF' AND session = 'day'
          AND ts_utc >= TIMESTAMPTZ '2024-01-02 00:00:00 UTC'
        ORDER BY ts_utc LIMIT 3
    """).df())
    print()

    print("=" * 72)
    print("8) Gold continuous: TX vs MTX recent close")
    print("=" * 72)
    print(con.sql("""
        SELECT tx.trading_date, tx.close AS tx_close, mtx.close AS mtx_close, tx.basis
        FROM tx_continuous_d tx
        JOIN mtx_continuous_d mtx USING (trading_date)
        WHERE tx.trading_date >= DATE '2026-05-01'
        ORDER BY tx.trading_date DESC
    """).df())
    print()

    print("=" * 72)
    print("9) Gold stock_factor_daily: top 5 12-1 momentum on 2024-12-31")
    print("=" * 72)
    print(con.sql("""
        SELECT symbol, mom_12_1, vol_60d, ret_120d
        FROM stock_factor_daily
        WHERE trading_date = DATE '2024-12-31' AND mom_12_1 IS NOT NULL
        ORDER BY mom_12_1 DESC LIMIT 5
    """).df())
    print()

    print("=" * 72)
    print("10) Stock futures bars — TSMC (2330) Q4 2024")
    print("=" * 72)
    print(con.sql("""
        SELECT trading_date, contract_id, close, volume, open_interest
        FROM bars_1d
        WHERE asset_class = 'tw_stock_futures' AND symbol = '2330' AND session = 'day'
          AND trading_date BETWEEN DATE '2024-12-27' AND DATE '2024-12-31'
        ORDER BY trading_date DESC, contract_id LIMIT 5
    """).df())
    print()

    print("=" * 72)
    print("11) Row counts (all tables)")
    print("=" * 72)
    print(con.sql("""
        SELECT 'bars_1d'                    AS tbl, COUNT(*) AS rows FROM bars_1d UNION ALL
        SELECT 'bars_1m',                   COUNT(*) FROM bars_1m UNION ALL
        SELECT 'tw_inst_futures_daily',     COUNT(*) FROM tw_inst_futures_daily UNION ALL
        SELECT 'tw_inst_stock_daily',       COUNT(*) FROM tw_inst_stock_daily UNION ALL
        SELECT 'tw_margin_daily',           COUNT(*) FROM tw_margin_daily UNION ALL
        SELECT 'fundamentals_q',            COUNT(*) FROM fundamentals_q UNION ALL
        SELECT 'macro_daily',               COUNT(*) FROM macro_daily UNION ALL
        SELECT 'stock_factor_daily',        COUNT(*) FROM stock_factor_daily UNION ALL
        SELECT 'stock_futures_continuous_d',COUNT(*) FROM stock_futures_continuous_d UNION ALL
        SELECT 'tx_continuous_d',           COUNT(*) FROM tx_continuous_d UNION ALL
        SELECT 'mtx_continuous_d',          COUNT(*) FROM mtx_continuous_d UNION ALL
        SELECT 'txo_daily_features',        COUNT(*) FROM txo_daily_features UNION ALL
        SELECT 'cross_market_features',     COUNT(*) FROM cross_market_features UNION ALL
        SELECT 'calendar_xtai',             COUNT(*) FROM calendar_xtai UNION ALL
        SELECT 'symbol_map',                COUNT(*) FROM symbol_map UNION ALL
        SELECT 'contract_specs',            COUNT(*) FROM contract_specs UNION ALL
        SELECT 'tw_inst_market_daily',      COUNT(*) FROM tw_inst_market_daily
        ORDER BY rows DESC
    """).df())
    print()

    print("Smoke test [PASS]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
