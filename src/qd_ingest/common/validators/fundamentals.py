"""Pandera schema for silver.fundamentals.fin_q (TEJ TWN_EWIFINQ 單季/累季)."""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema

fundamentals_q_schema = DataFrameSchema(
    {
        "stock_id":      Column(str, nullable=False),
        "fiscal_period": Column(str, nullable=False),                 # '2024Q1'
        "period_type":   Column(str, pa.Check.isin({"Q", "YTD"}), nullable=False),
        "consolidated":  Column("boolean", nullable=True),
        "currency":      Column(str, nullable=True),
        "publish_date":  Column("datetime64[ns]", coerce=True, nullable=False),
        # profitability
        "eps":          Column(float, nullable=True),
        "roa_pre":      Column(float, nullable=True),
        "roe_post":     Column(float, nullable=True),
        "gross_margin": Column(float, nullable=True),
        "op_margin":    Column(float, nullable=True),
        "net_margin":   Column(float, nullable=True),
        # growth
        "rev_growth":   Column(float, nullable=True),
        "gross_growth": Column(float, nullable=True),
        "op_growth":    Column(float, nullable=True),
        # balance sheet (千元)
        "total_assets":   Column("Int64", nullable=True),
        "total_liab":     Column("Int64", nullable=True),
        "total_equity":   Column("Int64", nullable=True),
        "current_assets": Column("Int64", nullable=True),
        "current_liab":   Column("Int64", nullable=True),
        # income statement
        "revenue":       Column("Int64", nullable=True),
        "cogs":          Column("Int64", nullable=True),
        "op_income":     Column("Int64", nullable=True),
        "net_income":    Column("Int64", nullable=True),
        "ni_to_parent":  Column("Int64", nullable=True),
        # cashflow
        "cfo": Column("Int64", nullable=True),
        "cfi": Column("Int64", nullable=True),
        "cff": Column("Int64", nullable=True),
        "source":       Column(str, nullable=False),
        "ingestion_ts": Column("datetime64[ns, UTC]", nullable=False),
    },
    strict="filter",
    coerce=True,
    unique=["stock_id", "fiscal_period", "period_type"],
)
