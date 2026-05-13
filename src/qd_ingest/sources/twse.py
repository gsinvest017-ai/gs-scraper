"""TWSE source ingester.

Input:
- 三大法人買賣超/twse_bfi82u/twse_bfi82u_combined_long_utf8.csv   (TWSE 三大法人台股 net 買賣超)

Output:
- silver/flows/tw_inst_market_daily/year=*/...parquet
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
from rich.console import Console

from ..common.audit import IngestRecord, sha256_file, write_audit
from ..common.io import write_silver_partitioned
from ..common.paths import silver_flows

console = Console()

# Map TWSE identity_en values to canonical.
# Source values: 'foreign_ex_dealer','foreign_dealer','sitc','dealer_self','dealer_hedge'
# Canonical keep the same English (already lower_snake).
IDENTITY_OK = {"foreign_ex_dealer", "foreign_dealer", "sitc", "dealer_self", "dealer_hedge"}


def ingest_market_inst_daily(csv_path: str | Path, *, dry_run: bool = False) -> dict:
    """TWSE bfi82u long CSV -> silver/flows/tw_inst_market_daily."""
    fp = Path(csv_path).resolve()
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    sha = sha256_file(fp)
    console.log(f"[TWSE inst_market_daily] reading {fp.name} sha256={sha[:12]}...")

    df = pd.read_csv(fp)
    rows_in = len(df)
    # The CSV ships both `identity` (Chinese) and `identity_en` (English snake_case).
    # Drop the Chinese one, then rename identity_en -> identity.
    if "identity" in df.columns and "identity_en" in df.columns:
        df = df.drop(columns=["identity"])
    df = df.rename(columns={"identity_en": "identity"})
    df = df[df["identity"].isin(IDENTITY_OK)].copy()

    df["trading_date"] = pd.to_datetime(df["date"]).dt.date
    df["source"] = "twse"
    df["ingestion_ts"] = pd.Timestamp.now(tz="UTC")
    df["year"] = pd.to_datetime(df["date"]).dt.year.astype("int32")
    df["buy_twd"] = pd.to_numeric(df.get("buy_twd"), errors="coerce")
    df["sell_twd"] = pd.to_numeric(df.get("sell_twd"), errors="coerce")
    df["net_twd"] = pd.to_numeric(df.get("net_twd"), errors="coerce")

    schema = pa.schema([
        ("trading_date", pa.date32()),
        ("identity",     pa.string()),
        ("buy_twd",      pa.float64()),
        ("sell_twd",     pa.float64()),
        ("net_twd",      pa.float64()),
        ("source",       pa.string()),
        ("ingestion_ts", pa.timestamp("ns", tz="UTC")),
        ("year",         pa.int32()),
    ])
    out = df[[f.name for f in schema]]
    tbl = pa.Table.from_pandas(out, schema=schema, preserve_index=False)
    dest = silver_flows("tw_inst_market_daily")
    if not dry_run:
        write_silver_partitioned(tbl, dest, ["year"], existing_data_behavior="delete_matching")

    rows_out = len(out)
    summary = {
        "rows_in": rows_in, "rows_out": rows_out,
        "identities": sorted(df["identity"].unique().tolist()),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    if not dry_run:
        write_audit(IngestRecord(
            source="twse", table="tw_inst_market_daily", bronze_file=str(fp),
            rows_in=rows_in, rows_out=rows_out, sha256=sha, status="ok",
            started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            extra=summary,
        ))
    console.log(f"[TWSE inst_market_daily] [green]done[/green]: {summary}")
    return summary
