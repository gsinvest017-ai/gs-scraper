"""Canonical paths in the QUANTDATA repo. Single source of truth for layout."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

BRONZE = ROOT / "bronze"
SILVER = ROOT / "silver"
GOLD = ROOT / "gold"
REFERENCE = ROOT / "reference"
CATALOG = ROOT / "catalog"
META = ROOT / "meta"
STAGING = ROOT / "_staging"
QUARANTINE = ROOT / "_quarantine"

META_AUDIT = META / "audit"
META_SCHEMA = META / "schema"
META_LINEAGE = META / "lineage"

CATALOG_DB = CATALOG / "quant.duckdb"


def silver_bars(freq: str) -> Path:
    """silver/bars/bars_{freq}/  where freq in {1d,1m,5m,1h}."""
    return SILVER / "bars" / f"bars_{freq}"


def silver_options(freq: str) -> Path:
    return SILVER / "options" / f"txo_chain_{freq}"


def silver_flows(table: str) -> Path:
    return SILVER / "flows" / table


def silver_fundamentals(table: str) -> Path:
    return SILVER / "fundamentals" / table


def silver_macro() -> Path:
    return SILVER / "macro"
