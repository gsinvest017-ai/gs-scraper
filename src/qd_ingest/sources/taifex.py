"""TAIFEX source ingester.

Input (current):
- SUPPLEMENT/TAIFEX/foreign_oi_daily.parquet  (already aggregated wide: product × identity columns)

Output:
- silver/flows/tw_inst_futures_daily/year=*/...parquet  (long: one row per product+identity+date)

TODO (W2 follow-up): switch to bronze CSVs in SUPPLEMENT/TAIFEX/_bronze/ for full historical coverage.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console

from ..common.audit import IngestRecord, sha256_file, write_audit
from ..common.io import write_silver_partitioned
from ..common.paths import silver_flows

console = Console()

# Wide TAIFEX columns -> (identity, side, metric)
# identity: 'dealer','sitc','fii'  (inv -> sitc in SUPPLEMENT parquet naming)
# side: 'long','short','net'
# metric: 'trade_contracts','oi_contracts','trade_million','oi_million'
# Note: the SUPPLEMENT parquet only has *_contracts; *_million not provided -> emit as NaN.

IDENTITY_MAP = {"dealer": "dealer", "inv": "sitc", "fii": "fii"}


def _melt_wide(df: pd.DataFrame) -> pd.DataFrame:
    """Convert wide (one row per product+date with dealer/inv/fii × long/short/net columns)
    into long (one row per product+date+identity)."""
    rows = []
    ts_local = "Asia/Taipei"
    for _, r in df.iterrows():
        trading_date = pd.to_datetime(r.get("trade_date_local") or r.get("date")).normalize()
        ts_utc = (
            trading_date + pd.Timedelta(hours=13, minutes=30)  # close 13:30 local
        ).tz_localize(ts_local).tz_convert("UTC")
        product = str(r["product"])
        for raw_id, canon_id in IDENTITY_MAP.items():
            base = {
                "trading_date": trading_date.date(),
                "ts_utc":       ts_utc,
                "product":      product,
                "identity":     canon_id,
                "long_trade_contracts":  _to_int(r.get(f"{raw_id}_long_trade")),
                "short_trade_contracts": _to_int(r.get(f"{raw_id}_short_trade")),
                "net_trade_contracts":   _to_int(r.get(f"{raw_id}_net_trade")),
                "long_oi_contracts":     _to_int(r.get(f"{raw_id}_long_oi")),
                "short_oi_contracts":    _to_int(r.get(f"{raw_id}_short_oi")),
                "net_oi_contracts":      _to_int(r.get(f"{raw_id}_net_oi")),
                "long_trade_million":    None,
                "short_trade_million":   None,
                "net_trade_million":     None,
                "long_oi_million":       None,
                "short_oi_million":      None,
                "net_oi_million":        None,
                "net_oi_z60":            _to_float(r.get(f"{raw_id}_z60")),
                "source":       "taifex",
                "ingestion_ts": pd.Timestamp.now(tz="UTC"),
            }
            rows.append(base)
    long_df = pd.DataFrame(rows)
    long_df["year"] = pd.to_datetime(long_df["trading_date"]).dt.year.astype("int32")
    return long_df


def _to_int(v) -> int | None:
    if pd.isna(v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_float(v) -> float | None:
    if pd.isna(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ingest_inst_futures(parquet_path: str | Path, *, dry_run: bool = False) -> dict:
    """SUPPLEMENT/TAIFEX/foreign_oi_daily.parquet -> silver/flows/tw_inst_futures_daily."""
    fp = Path(parquet_path).resolve()
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    sha = sha256_file(fp)
    console.log(f"[TAIFEX inst_futures] reading {fp.name} sha256={sha[:12]}...")
    df = pq.read_table(fp).to_pandas()
    rows_in = len(df)

    long_df = _melt_wide(df)
    rows_out = len(long_df)

    schema = pa.schema([
        ("trading_date",          pa.date32()),
        ("ts_utc",                pa.timestamp("ns", tz="UTC")),
        ("product",               pa.string()),
        ("identity",              pa.string()),
        ("long_trade_contracts",  pa.int64()),
        ("short_trade_contracts", pa.int64()),
        ("net_trade_contracts",   pa.int64()),
        ("long_trade_million",    pa.float64()),
        ("short_trade_million",   pa.float64()),
        ("net_trade_million",     pa.float64()),
        ("long_oi_contracts",     pa.int64()),
        ("short_oi_contracts",    pa.int64()),
        ("net_oi_contracts",      pa.int64()),
        ("long_oi_million",       pa.float64()),
        ("short_oi_million",      pa.float64()),
        ("net_oi_million",        pa.float64()),
        ("net_oi_z60",            pa.float64()),
        ("source",                pa.string()),
        ("ingestion_ts",          pa.timestamp("ns", tz="UTC")),
        ("year",                  pa.int32()),
    ])
    tbl = pa.Table.from_pandas(long_df[[f.name for f in schema]], schema=schema, preserve_index=False)
    dest = silver_flows("tw_inst_futures_daily")
    if not dry_run:
        write_silver_partitioned(tbl, dest, ["year"], existing_data_behavior="delete_matching")

    summary = {
        "rows_in": rows_in,
        "rows_out": rows_out,
        "products": sorted(long_df["product"].unique().tolist()),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    if not dry_run:
        write_audit(IngestRecord(
            source="taifex", table="tw_inst_futures_daily", bronze_file=str(fp),
            rows_in=rows_in, rows_out=rows_out, sha256=sha, status="ok",
            started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            extra=summary,
        ))
    console.log(f"[TAIFEX inst_futures] [green]done[/green]: {summary}")
    return summary
