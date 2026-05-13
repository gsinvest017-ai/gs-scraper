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
    con.execute(f"""
        CREATE OR REPLACE VIEW bars_1d AS
        SELECT * FROM read_parquet(
            '{_rel(SILVER / "bars" / "bars_1d")}/**/*.parquet',
            hive_partitioning=TRUE
        );
    """)
    # bars_1m has no data yet but pre-register the view target (will return empty for now)
    # Actually skip until data exists, or guard with try/except in queries.

    # === Silver flows ===
    for tbl in ("tw_inst_futures_daily", "tw_inst_stock_daily", "tw_margin_daily", "tw_inst_market_daily"):
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

    # === Silver macro ===
    macro_fp = SILVER / "macro" / "macro_daily.parquet"
    if macro_fp.exists():
        con.execute(f"""
            CREATE OR REPLACE VIEW macro_daily AS
            SELECT * FROM read_parquet('{_rel(macro_fp)}');
        """)

    # === Gold (placeholders for future) ===
    # gold/features/, gold/continuous/ -- not yet populated

    # === Convenience macros ===
    con.execute("""
        CREATE OR REPLACE MACRO tw_stock_with_inst(stock_id_, start_, end_) AS TABLE
            SELECT b.trading_date, b.symbol, b.close, b.volume,
                   i.foreign_net_lot, i.sitc_net_lot, i.dealer_net_lot, i.total_net_lot,
                   m.margin_balance_lot, m.short_balance_lot, m.short_to_margin_pct
            FROM bars_1d b
            LEFT JOIN tw_inst_stock_daily i USING (trading_date)
            LEFT JOIN tw_margin_daily m USING (trading_date, stock_id)
            WHERE b.asset_class = 'tw_stock'
              AND b.symbol = stock_id_
              AND i.stock_id = stock_id_
              AND b.trading_date BETWEEN start_ AND end_;
    """)

    con.execute("""
        CREATE OR REPLACE MACRO tw_stock_asof_fundamentals(stock_id_, start_, end_) AS TABLE
            SELECT b.trading_date, b.close, f.fiscal_period, f.publish_date, f.eps, f.roe_post
            FROM bars_1d b
            ASOF LEFT JOIN fundamentals_q f
              ON b.symbol = f.stock_id
              AND b.trading_date >= f.publish_date
            WHERE b.asset_class = 'tw_stock'
              AND b.symbol = stock_id_
              AND f.period_type = 'Q'
              AND b.trading_date BETWEEN start_ AND end_;
    """)

    views = con.sql("SHOW TABLES").df()
    console.log(f"[catalog] built {len(views)} views/macros at {db}")
    print(views)
    con.close()


if __name__ == "__main__":
    build()
