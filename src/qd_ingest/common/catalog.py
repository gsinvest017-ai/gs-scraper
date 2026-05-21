"""Build catalog/quant.duckdb: views + macros over silver/gold/reference parquet.

Idempotent: drops and re-creates all views. Safe to re-run after any silver update.
"""

from __future__ import annotations

import duckdb
from rich.console import Console

from .paths import CATALOG, CATALOG_DB, GOLD, REFERENCE, ROOT, SILVER

console = Console()

# Use relative POSIX paths so the catalog is portable (catalog/quant.duckdb assumes cwd=ROOT).
def _rel(p) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def build(*, db_path=None) -> None:
    db = db_path or CATALOG_DB
    CATALOG.mkdir(exist_ok=True)
    con = duckdb.connect(str(db))
    # Always run with cwd=ROOT so relative globs resolve
    con.execute(f"SET file_search_path='{ROOT}'")

    # === Reference ===
    con.execute(f"""
        CREATE OR REPLACE VIEW symbol_map AS
        SELECT * FROM read_parquet('{_rel(REFERENCE / "symbol_map.parquet")}');
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW contract_specs AS
        SELECT * FROM read_parquet('{_rel(REFERENCE / "contract_specs.parquet")}');
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW calendar_xtai AS
        SELECT * FROM read_parquet('{_rel(REFERENCE / "calendar_xtai.parquet")}');
    """)

    # === Silver bars ===
    # NOTE: hive_partitioning=FALSE because different asset classes use different partition
    # keys (tw_stock: year only; tw_futures: symbol+year). The asset_class/symbol/year
    # columns are already present *inside* each parquet, so no info is lost.
    con.execute(f"""
        CREATE OR REPLACE VIEW bars_1d AS
        SELECT * FROM read_parquet(
            '{_rel(SILVER / "bars" / "bars_1d")}/**/*.parquet',
            hive_partitioning=FALSE,
            union_by_name=TRUE
        );
    """)
    bars_1m_root = SILVER / "bars" / "bars_1m"
    if bars_1m_root.exists() and any(bars_1m_root.rglob("*.parquet")):
        con.execute(f"""
            CREATE OR REPLACE VIEW bars_1m AS
            SELECT * FROM read_parquet(
                '{_rel(bars_1m_root)}/**/*.parquet',
                hive_partitioning=FALSE,
                union_by_name=TRUE
            );
        """)

    # === Silver flows ===
    for tbl in ("tw_inst_futures_daily", "tw_inst_stock_daily", "tw_margin_daily",
                "tw_inst_market_daily", "tw_futures_large_trader_daily",
                "tw_chip_dist_daily", "tw_stock_futures_corp_actions",
                "tw_inst_futures_full_daily"):
        path = SILVER / "flows" / tbl
        if (path).exists():
            con.execute(f"""
                CREATE OR REPLACE VIEW {tbl} AS
                SELECT * FROM read_parquet(
                    '{_rel(path)}/**/*.parquet',
                    hive_partitioning=TRUE
                );
            """)

    # === Silver fundamentals ===
    fin_q_path = SILVER / "fundamentals" / "fin_q"
    if fin_q_path.exists():
        con.execute(f"""
            CREATE OR REPLACE VIEW fundamentals_q AS
            SELECT * FROM read_parquet(
                '{_rel(fin_q_path)}/**/*.parquet',
                hive_partitioning=TRUE
            );
        """)
    rev_path = SILVER / "fundamentals" / "revenue_monthly"
    if rev_path.exists():
        con.execute(f"""
            CREATE OR REPLACE VIEW revenue_monthly AS
            SELECT * FROM read_parquet(
                '{_rel(rev_path)}/**/*.parquet',
                hive_partitioning=TRUE
            );
        """)
    div_path = SILVER / "fundamentals" / "cash_dividend_events"
    if div_path.exists():
        con.execute(f"""
            CREATE OR REPLACE VIEW cash_dividend_events AS
            SELECT * FROM read_parquet(
                '{_rel(div_path)}/**/*.parquet',
                hive_partitioning=TRUE
            );
        """)

    # === Silver macro ===
    macro_fp = SILVER / "macro" / "macro_daily.parquet"
    if macro_fp.exists():
        con.execute(f"""
            CREATE OR REPLACE VIEW macro_daily AS
            SELECT * FROM read_parquet('{_rel(macro_fp)}');
        """)

    # === Gold ===
    for name, fp in [
        ("tx_continuous_d",  GOLD / "continuous" / "tx_continuous_d.parquet"),
        ("mtx_continuous_d", GOLD / "continuous" / "mtx_continuous_d.parquet"),
        ("stock_futures_continuous_d", GOLD / "continuous" / "stock_futures_continuous_d.parquet"),
        ("txo_daily_features",         GOLD / "features" / "txo_daily_features.parquet"),
        ("cross_market_features",      GOLD / "features" / "cross_market_features.parquet"),
        ("stock_factor_daily",         GOLD / "features" / "stock_factor_daily.parquet"),
    ]:
        if fp.exists():
            con.execute(f"""
                CREATE OR REPLACE VIEW {name} AS
                SELECT * FROM read_parquet('{_rel(fp)}');
            """)

    # === Convenience macros ===
    # Filter b.asset_class = 'tw_stock' to avoid Cartesian product with tw_stock_futures
    # (the stock-futures underlying_code shares the same '2330' symbol).
    con.execute("""
        CREATE OR REPLACE VIEW tw_stock_bars AS
            SELECT * FROM bars_1d
            WHERE asset_class = 'tw_stock' AND session = 'day';
    """)
    con.execute("""
        CREATE OR REPLACE MACRO tw_stock_with_inst(stock_id_, start_, end_) AS TABLE
            SELECT b.trading_date, b.symbol, b.close, b.volume,
                   i.foreign_net_lot, i.sitc_net_lot, i.dealer_net_lot, i.total_net_lot,
                   m.margin_balance_lot, m.short_balance_lot, m.short_to_margin_pct
            FROM tw_stock_bars b
            LEFT JOIN tw_inst_stock_daily i
              ON b.trading_date = i.trading_date AND b.symbol = i.stock_id
            LEFT JOIN tw_margin_daily m
              ON b.trading_date = m.trading_date AND b.symbol = m.stock_id
            WHERE b.symbol = stock_id_
              AND b.trading_date BETWEEN start_ AND end_;
    """)

    con.execute("""
        CREATE OR REPLACE MACRO tw_stock_asof_fundamentals(stock_id_, start_, end_) AS TABLE
            SELECT b.trading_date, b.close, f.fiscal_period, f.publish_date, f.eps, f.roe_post
            FROM tw_stock_bars b
            ASOF LEFT JOIN (SELECT * FROM fundamentals_q WHERE period_type = 'Q') f
              ON b.symbol = f.stock_id
              AND b.trading_date >= f.publish_date
            WHERE b.symbol = stock_id_
              AND b.trading_date BETWEEN start_ AND end_;
    """)

    # bars_1m intraday by symbol
    if bars_1m_root.exists() and any(bars_1m_root.rglob("*.parquet")):
        con.execute("""
            CREATE OR REPLACE MACRO bars_1m_for(asset_class_, symbol_, start_ts_, end_ts_) AS TABLE
                SELECT * FROM bars_1m
                WHERE asset_class = asset_class_
                  AND symbol = symbol_
                  AND ts_utc BETWEEN start_ts_ AND end_ts_
                ORDER BY ts_utc;
        """)

    views = con.sql("SHOW TABLES").df()
    console.log(f"[catalog] built {len(views)} views/macros at {db}")
    print(views)
    con.close()


if __name__ == "__main__":
    build()
