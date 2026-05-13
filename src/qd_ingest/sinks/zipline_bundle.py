"""silver -> zipline-tej bundle adapter.

Provides `silver_tquant_bundle` function that reads silver/bars/bars_1d (tw_stock + tw_etf)
and writes a zipline daily bundle compatible with zipline-tej's `TEJ_XTAI` calendar.

Usage (after installing zipline-tej):

  # In ~/.zipline/extension.py
  from qd_ingest.sinks.zipline_bundle import register_silver_bundle
  register_silver_bundle()

  # Then ingest with:
  $ zipline ingest -b silver_tquant
  $ zipline run -f my_algo.py -b silver_tquant --start 2020-01-01 --end 2025-12-31

This adapter only depends on zipline at registration time (lazy import).
The implementation does NOT call zipline directly from QUANTDATA's CLI — keeps
qd_ingest installable without the zipline-tej dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ..common.paths import CATALOG_DB, ROOT, SILVER


def _load_silver_universe(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return zipline-compatible asset metadata for tw_stock + tw_etf."""
    df = con.sql(f"""
        WITH bars AS (
            SELECT symbol,
                   MIN(trading_date) AS start_date,
                   MAX(trading_date) AS end_date
            FROM read_parquet(
                '{SILVER}/bars/bars_1d/asset_class=tw_stock/**/*.parquet',
                hive_partitioning=TRUE
            )
            GROUP BY symbol
        )
        SELECT
            row_number() OVER (ORDER BY symbol) - 1     AS sid,
            symbol                                     AS symbol,
            symbol                                     AS asset_name,
            start_date,
            end_date,
            end_date + INTERVAL 1 DAY                  AS auto_close_date,
            'TEJ_XTAI'                                 AS exchange
        FROM bars
        ORDER BY symbol
    """).df()
    return df


def _load_bars_for_sid(con: duckdb.DuckDBPyConnection, symbol: str) -> pd.DataFrame:
    df = con.sql(f"""
        SELECT
            trading_date  AS date,
            COALESCE(adj_open, open)    AS open,
            COALESCE(adj_high, high)    AS high,
            COALESCE(adj_low, low)      AS low,
            COALESCE(adj_close, close)  AS close,
            volume
        FROM read_parquet(
            '{SILVER}/bars/bars_1d/asset_class=tw_stock/**/*.parquet',
            hive_partitioning=TRUE
        )
        WHERE symbol = ?
        ORDER BY trading_date
    """, [symbol]).df()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def silver_tquant_bundle(
    environ: dict[str, Any],
    asset_db_writer,
    minute_bar_writer,
    daily_bar_writer,
    adjustment_writer,
    calendar,
    start_session,
    end_session,
    cache,
    show_progress,
    output_dir,
):
    """Zipline ingest function. Registered via register_silver_bundle()."""
    con = duckdb.connect(str(CATALOG_DB), read_only=True)
    universe = _load_silver_universe(con)

    # Write asset metadata
    universe_idx = universe.set_index("sid")
    asset_db_writer.write(equities=universe_idx)

    def _iter():
        for _, row in universe.iterrows():
            sid = int(row["sid"])
            df = _load_bars_for_sid(con, row["symbol"])
            # Reindex to calendar sessions
            sessions = calendar.sessions_in_range(start_session, end_session)
            df = df.reindex(sessions)
            yield sid, df

    daily_bar_writer.write(_iter(), show_progress=show_progress)
    # Adjustments + dividends placeholder: TEJ EWPRCD already adj-applied via adj_close
    adjustment_writer.write()


def register_silver_bundle(
    name: str = "silver_tquant",
    start: str = "2010-01-04",
    end: str | None = None,
) -> None:
    """Register the bundle with zipline. Call this from ~/.zipline/extension.py."""
    from zipline.data.bundles import register
    from zipline.utils.calendar_utils import get_calendar

    cal = get_calendar("TEJ_XTAI")
    register(
        name,
        silver_tquant_bundle,
        calendar_name="TEJ_XTAI",
        start_session=pd.Timestamp(start),
        end_session=pd.Timestamp(end) if end else cal.last_session,
    )
