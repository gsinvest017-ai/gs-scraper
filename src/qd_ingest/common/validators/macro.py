"""Pandera schemas for silver.macro.* (FX, indices, rates, commodities)."""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema

macro_daily_schema = DataFrameSchema(
    {
        "trading_date": Column("datetime64[ns]", coerce=True, nullable=False),
        "symbol":       Column(str, nullable=False),
        "category":     Column(str, pa.Check.isin(
            {"tw_index", "us_index", "asia_index", "fx", "commodity", "rate", "credit", "etf"}
        ), nullable=False),
        "open":         Column(float, nullable=True),
        "high":         Column(float, nullable=True),
        "low":          Column(float, nullable=True),
        "close":        Column(float, nullable=True),
        "adj_close":    Column(float, nullable=True),
        "volume":       Column("Int64", nullable=True),
        "source":       Column(str, nullable=False),
        "ingestion_ts": Column("datetime64[ns, UTC]", nullable=False),
    },
    strict="filter",
    coerce=True,
    unique=["symbol", "trading_date"],
)
