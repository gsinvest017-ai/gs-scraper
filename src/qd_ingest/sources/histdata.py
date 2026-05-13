"""histdata source ingester.

Input layout (current, pre-bronze-rename):
- NQ/{2010..2024}.parquet  (raw_ticker=nsxusd)
- ES/{2010..2024}.parquet  (raw_ticker=spxusd)
- GC/{2010..2024}.parquet  (raw_ticker=xauusd)

Each yearly file: ~300K rows, OHLCV + `timestamp` index UTC.
Note: histdata sells CFD/spot proxies, NOT CME-cleared futures.
Embedded PANDAS_ATTRS warns: volume_not_real, cfd_not_cme_future.
We label these rows with quality_flag='cfd_proxy' to make the limitation explicit.

Output: silver/bars/bars_1m/asset_class=us_futures/symbol=<SYM>/year=<YYYY>/...
"""

from __future__ import annotations

import datetime as dt
import shutil
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console

from ..common.audit import IngestRecord, sha256_file, write_audit
from ..common.io import write_silver_partitioned
from ..common.paths import ROOT, silver_bars

console = Console()

# top-level dir -> canonical symbol
SYMBOLS = {"NQ": "NQ", "ES": "ES", "GC": "GC"}


def _read_year(fp: Path, symbol: str) -> pa.Table:
    df = pq.read_table(fp).to_pandas()
    df = df.reset_index().rename(columns={"timestamp": "ts_utc"})
    n = len(df)
    df["trading_date"] = df["ts_utc"].dt.date  # NB: UTC date, not exchange-local; future enhancement
    df["asset_class"] = "us_futures"
    df["exchange"]    = "CME" if symbol in {"NQ", "ES"} else "COMEX"
    df["symbol"]      = symbol
    df["contract_id"] = pd.Series([None] * n, dtype="object")
    df["session"]     = "eth"
    df["open_interest"] = pd.array([pd.NA] * n, dtype="Int64")
    df["vwap"]        = pd.Series([pd.NA] * n, dtype="Float64").astype(float)
    df["settlement"]  = pd.Series([pd.NA] * n, dtype="Float64").astype(float)
    df["adj_open"]    = pd.Series([pd.NA] * n, dtype="Float64").astype(float)
    df["adj_high"]    = pd.Series([pd.NA] * n, dtype="Float64").astype(float)
    df["adj_low"]     = pd.Series([pd.NA] * n, dtype="Float64").astype(float)
    df["adj_close"]   = pd.Series([pd.NA] * n, dtype="Float64").astype(float)
    df["adj_factor"]  = pd.Series([pd.NA] * n, dtype="Float64").astype(float)
    df["source"]      = "histdata"
    df["ingestion_ts"] = pd.Timestamp.now(tz="UTC")
    df["quality_flag"] = "cfd_proxy"
    df["year"] = df["ts_utc"].dt.year.astype("int32")
    df["volume"] = pd.array(pd.to_numeric(df["volume"], errors="coerce"), dtype="Int64")

    schema = pa.schema([
        ("ts_utc",        pa.timestamp("ns", tz="UTC")),
        ("trading_date",  pa.date32()),
        ("asset_class",   pa.string()),
        ("exchange",      pa.string()),
        ("symbol",        pa.string()),
        ("contract_id",   pa.string()),
        ("session",       pa.string()),
        ("open",          pa.float64()),
        ("high",          pa.float64()),
        ("low",           pa.float64()),
        ("close",         pa.float64()),
        ("volume",        pa.int64()),
        ("open_interest", pa.int64()),
        ("vwap",          pa.float64()),
        ("settlement",    pa.float64()),
        ("adj_open",      pa.float64()),
        ("adj_high",      pa.float64()),
        ("adj_low",       pa.float64()),
        ("adj_close",     pa.float64()),
        ("adj_factor",    pa.float64()),
        ("source",        pa.string()),
        ("ingestion_ts",  pa.timestamp("ns", tz="UTC")),
        ("quality_flag",  pa.string()),
        ("year",          pa.int32()),
    ])
    return pa.Table.from_pandas(df[[f.name for f in schema]], schema=schema, preserve_index=False)


def ingest_us_futures_1m(
    *,
    symbols: tuple[str, ...] | None = None,
    dry_run: bool = False,
) -> dict:
    """Ingest histdata US futures 1m bars from {NQ,ES,GC}/<year>.parquet -> silver/bars/bars_1m."""
    targets = list(symbols or SYMBOLS.keys())
    out_root = silver_bars("1m") / "asset_class=us_futures"
    summary: dict = {"by_symbol": {}, "elapsed_sec": 0.0}
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()

    for sym in targets:
        src_dir = ROOT / sym
        if not src_dir.exists():
            console.log(f"[red][histdata] {sym}: dir {src_dir} missing — skip[/red]")
            continue
        sym_dest = out_root / f"symbol={sym}"
        if not dry_run and sym_dest.exists():
            shutil.rmtree(sym_dest)

        rows_total = 0
        years_written: set[int] = set()
        for yp in sorted(src_dir.glob("*.parquet")):
            sha = sha256_file(yp)
            tbl = _read_year(yp, sym)
            rows_total += tbl.num_rows
            year = int(yp.stem)
            years_written.add(year)
            if not dry_run:
                write_silver_partitioned(
                    tbl, dest_root=sym_dest, partition_cols=["year"],
                    existing_data_behavior="overwrite_or_ignore",
                )
                write_audit(IngestRecord(
                    source="histdata", table="bars_1m", bronze_file=str(yp),
                    rows_in=tbl.num_rows, rows_out=tbl.num_rows, sha256=sha,
                    status="ok",
                    started_at=started,
                    ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    extra={"symbol": sym, "year": year},
                ))
        summary["by_symbol"][sym] = {
            "rows": rows_total,
            "years": sorted(years_written),
        }
        console.log(f"[histdata] {sym}: {rows_total:,} rows across {len(years_written)} years")

    summary["elapsed_sec"] = round(time.time() - t0, 1)
    console.log(f"[histdata us_futures_1m] [green]done[/green]: {summary}")
    return summary
