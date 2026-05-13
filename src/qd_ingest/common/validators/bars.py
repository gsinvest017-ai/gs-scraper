"""Pandera schemas for silver.bars_{1d,1m,5m,1h}."""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema

ASSET_CLASSES = {
    "tw_futures", "tw_stock_futures", "tw_stock", "tw_etf", "tw_index",
    "us_futures", "us_etf", "us_index", "asia_index", "fx", "commodity", "tw_option",
}
SESSIONS = {"day", "ah", "eth", "rth"}
QUALITY = {"ok", "gap", "holiday", "suspect", "imputed", "settlement"}

bars_1d_schema = DataFrameSchema(
    {
        "ts_utc":       Column("datetime64[ns, UTC]", nullable=False),
        "trading_date": Column("datetime64[ns]", nullable=False, coerce=True),
        "asset_class":  Column(str, pa.Check.isin(ASSET_CLASSES), nullable=False),
        "exchange":     Column(str, nullable=False),
        "symbol":       Column(str, nullable=False),
        "contract_id":  Column(str, nullable=True),
        "session":      Column(str, pa.Check.isin(SESSIONS), nullable=False),
        "open":         Column(float, nullable=True),
        "high":         Column(float, nullable=True),
        "low":          Column(float, nullable=True),
        "close":        Column(float, nullable=True),
        "volume":       Column("Int64", pa.Check.ge(0), nullable=True),
        "open_interest": Column("Int64", nullable=True),
        "vwap":         Column(float, nullable=True),
        "settlement":   Column(float, nullable=True),
        "adj_open":     Column(float, nullable=True),
        "adj_high":     Column(float, nullable=True),
        "adj_low":      Column(float, nullable=True),
        "adj_close":    Column(float, nullable=True),
        "adj_factor":   Column(float, nullable=True),
        "source":       Column(str, nullable=False),
        "ingestion_ts": Column("datetime64[ns, UTC]", nullable=False),
        "quality_flag": Column(str, pa.Check.isin(QUALITY), nullable=False),
    },
    strict="filter",   # allow extras but drop them
    coerce=True,
    unique=["asset_class", "symbol", "contract_id", "session", "ts_utc"],
)

# 1m / 5m / 1h share the schema (same columns). Re-export for clarity.
bars_intraday_schema = bars_1d_schema
