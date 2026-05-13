"""Pandera schemas for silver.flows: institutional + margin tables."""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema

INST_FUTURES_IDENTITIES = {
    "dealer", "dealer_self", "dealer_hedge", "sitc", "fii",
}

tw_inst_futures_daily_schema = DataFrameSchema(
    {
        "trading_date":         Column("datetime64[ns]", coerce=True, nullable=False),
        "ts_utc":               Column("datetime64[ns, UTC]", nullable=False),
        "product":              Column(str, nullable=False),
        "identity":             Column(str, pa.Check.isin(INST_FUTURES_IDENTITIES), nullable=False),
        "long_trade_contracts":  Column("Int64", nullable=True),
        "short_trade_contracts": Column("Int64", nullable=True),
        "net_trade_contracts":   Column("Int64", nullable=True),
        "long_trade_million":    Column(float, nullable=True),
        "short_trade_million":   Column(float, nullable=True),
        "net_trade_million":     Column(float, nullable=True),
        "long_oi_contracts":     Column("Int64", nullable=True),
        "short_oi_contracts":    Column("Int64", nullable=True),
        "net_oi_contracts":      Column("Int64", nullable=True),
        "long_oi_million":       Column(float, nullable=True),
        "short_oi_million":      Column(float, nullable=True),
        "net_oi_million":        Column(float, nullable=True),
        "net_oi_z60":            Column(float, nullable=True),
        "source":                Column(str, nullable=False),
        "ingestion_ts":          Column("datetime64[ns, UTC]", nullable=False),
    },
    strict="filter",
    coerce=True,
    unique=["product", "identity", "trading_date"],
)

tw_inst_stock_daily_schema = DataFrameSchema(
    {
        "trading_date":     Column("datetime64[ns]", coerce=True, nullable=False),
        "stock_id":         Column(str, nullable=False),
        "exchange":         Column(str, pa.Check.isin({"TWSE", "TPEX"}), nullable=False),
        "foreign_net_lot":  Column("Int64", nullable=True),
        "sitc_net_lot":     Column("Int64", nullable=True),
        "dealer_net_lot":   Column("Int64", nullable=True),
        "total_net_lot":    Column("Int64", nullable=True),
        "foreign_buy_lot":  Column("Int64", nullable=True),
        "foreign_sell_lot": Column("Int64", nullable=True),
        "sitc_buy_lot":     Column("Int64", nullable=True),
        "sitc_sell_lot":    Column("Int64", nullable=True),
        "dealer_buy_lot":   Column("Int64", nullable=True),
        "dealer_sell_lot":  Column("Int64", nullable=True),
        "foreign_hold_lot": Column("Int64", nullable=True),
        "foreign_hold_pct": Column(float, nullable=True),
        "sitc_hold_lot":    Column("Int64", nullable=True),
        "sitc_hold_pct":    Column(float, nullable=True),
        "dealer_hold_lot":  Column("Int64", nullable=True),
        "dealer_hold_pct":  Column(float, nullable=True),
        "source":           Column(str, nullable=False),
        "ingestion_ts":     Column("datetime64[ns, UTC]", nullable=False),
    },
    strict="filter",
    coerce=True,
    unique=["stock_id", "trading_date"],
)

tw_margin_daily_schema = DataFrameSchema(
    {
        "trading_date":         Column("datetime64[ns]", coerce=True, nullable=False),
        "stock_id":             Column(str, nullable=False),
        "margin_buy_lot":       Column("Int64", nullable=True),
        "margin_sell_lot":      Column("Int64", nullable=True),
        "short_buy_lot":        Column("Int64", nullable=True),
        "short_sell_lot":       Column("Int64", nullable=True),
        "margin_balance_lot":   Column("Int64", nullable=True),
        "short_balance_lot":    Column("Int64", nullable=True),
        "margin_balance_ktwd":  Column(float, nullable=True),
        "short_balance_ktwd":   Column(float, nullable=True),
        "margin_util_pct":      Column(float, nullable=True),
        "short_util_pct":       Column(float, nullable=True),
        "short_to_margin_pct":  Column(float, nullable=True),
        "margin_maint_pct":     Column(float, nullable=True),
        "short_maint_pct":      Column(float, nullable=True),
        "account_maint_pct":    Column(float, nullable=True),
        "source":               Column(str, nullable=False),
        "ingestion_ts":         Column("datetime64[ns, UTC]", nullable=False),
    },
    strict="filter",
    coerce=True,
    unique=["stock_id", "trading_date"],
)

tw_inst_market_daily_schema = DataFrameSchema(
    {
        "trading_date": Column("datetime64[ns]", coerce=True, nullable=False),
        "identity":     Column(str, nullable=False),     # 'foreign_ex_dealer','foreign_dealer','sitc','dealer_self','dealer_hedge'
        "buy_twd":      Column(float, nullable=True),
        "sell_twd":     Column(float, nullable=True),
        "net_twd":      Column(float, nullable=True),
        "source":       Column(str, nullable=False),
        "ingestion_ts": Column("datetime64[ns, UTC]", nullable=False),
    },
    strict="filter",
    coerce=True,
    unique=["identity", "trading_date"],
)
