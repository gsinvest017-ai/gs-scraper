"""Pandera schemas for silver.options_chain_{1d,1m}."""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema

options_chain_1d_schema = DataFrameSchema(
    {
        "ts_utc":       Column("datetime64[ns, UTC]", nullable=False),
        "trading_date": Column("datetime64[ns]", nullable=False, coerce=True),
        "exchange":     Column(str, nullable=False),
        "underlying":   Column(str, nullable=False),
        "symbol":       Column(str, nullable=False),
        "expiry":       Column("datetime64[ns]", nullable=False, coerce=True),
        "expiry_code":  Column(str, nullable=True),
        "strike":       Column(float, pa.Check.gt(0), nullable=False),
        "option_type":  Column(str, pa.Check.isin({"C", "P"}), nullable=False),
        "session":      Column(str, nullable=False),
        "open":         Column(float, nullable=True),
        "high":         Column(float, nullable=True),
        "low":          Column(float, nullable=True),
        "close":        Column(float, nullable=True),
        "volume":       Column("Int64", pa.Check.ge(0), nullable=True),
        "open_interest": Column("Int64", nullable=True),
        "settlement":   Column(float, nullable=True),
        "best_bid":     Column(float, nullable=True),
        "best_ask":     Column(float, nullable=True),
        "moneyness":    Column(float, nullable=True),
        "dte":          Column("Int64", nullable=True),
        "iv":           Column(float, nullable=True),
        "source":       Column(str, nullable=False),
        "ingestion_ts": Column("datetime64[ns, UTC]", nullable=False),
    },
    strict="filter",
    coerce=True,
    unique=["symbol", "expiry", "strike", "option_type", "session", "ts_utc"],
)
