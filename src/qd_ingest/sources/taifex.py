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

SILVER_SCHEMA = pa.schema([
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

    schema = SILVER_SCHEMA
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


# --------------------------------------------------------------------------
# Derive tw_inst_futures_daily from the already-fresh tw_inst_futures_full_daily
# (TEJ TWN/AFINST, auto-refreshed daily). The aggregated 3-product × 3-identity
# view is a strict projection of the 162-code full view, so we avoid a fragile
# TAIFEX-website scraper entirely. See docs/progress-taifex-inst-futures.md.
# --------------------------------------------------------------------------

# full-view identity_code -> (product, identity). 1x/2x prefix = futures/options.
_CODE_MAP: dict[str, tuple[str, str]] = {
    "11TX":  ("TXF", "dealer"), "12TX":  ("TXF", "sitc"), "13TX":  ("TXF", "fii"),
    "11MTX": ("MXF", "dealer"), "12MTX": ("MXF", "sitc"), "13MTX": ("MXF", "fii"),
    "21TXO": ("TXO", "dealer"), "22TXO": ("TXO", "sitc"), "23TXO": ("TXO", "fii"),
}


def derive_inst_futures_daily(*, dry_run: bool = False) -> dict:
    """silver/flows/tw_inst_futures_full_daily -> silver/flows/tw_inst_futures_daily.

    Projects the 9 (institution × TX/MTX/TXO) codes to the canonical
    product×identity long shape, dedups multi-ingest rows (keep last by
    ingestion_ts), and recomputes the 60-day net-OI z-score.
    """
    import polars as pl

    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    src_glob = str(silver_flows("tw_inst_futures_full_daily") / "**" / "*.parquet")

    df = (
        pl.scan_parquet(src_glob)
        .filter(pl.col("identity_code").is_in(list(_CODE_MAP)))
        .select(
            "trading_date", "identity_code", "ingestion_ts",
            "long_volume", "short_volume", "net_volume",
            "long_oi", "short_oi", "net_oi",
        )
        .collect()
        .sort("ingestion_ts")
        .unique(subset=["identity_code", "trading_date"], keep="last")  # multi-ingest dedup
    )
    if df.is_empty():
        console.log("[TAIFEX derive] [yellow]no rows[/yellow] in tw_inst_futures_full_daily — skip")
        return {"rows_out": 0, "note": "empty upstream", "elapsed_sec": round(time.time() - t0, 1)}

    pdf = df.to_pandas()
    pdf["product"] = pdf["identity_code"].map(lambda c: _CODE_MAP[c][0])
    pdf["identity"] = pdf["identity_code"].map(lambda c: _CODE_MAP[c][1])
    pdf = pdf.rename(columns={
        "long_volume": "long_trade_contracts", "short_volume": "short_trade_contracts",
        "net_volume": "net_trade_contracts",
        "long_oi": "long_oi_contracts", "short_oi": "short_oi_contracts", "net_oi": "net_oi_contracts",
    })

    # 60-day net-OI z-score per (product, identity), in trading_date order
    pdf = pdf.sort_values(["product", "identity", "trading_date"]).reset_index(drop=True)
    grp = pdf.groupby(["product", "identity"])["net_oi_contracts"]
    roll_mean = grp.transform(lambda s: s.rolling(60, min_periods=60).mean())
    roll_std = grp.transform(lambda s: s.rolling(60, min_periods=60).std())
    pdf["net_oi_z60"] = (pdf["net_oi_contracts"] - roll_mean) / roll_std

    # ts_utc = trading_date @ 13:30 Asia/Taipei (matches ingest_inst_futures convention)
    td = pd.to_datetime(pdf["trading_date"])
    pdf["ts_utc"] = (td + pd.Timedelta(hours=13, minutes=30)).dt.tz_localize("Asia/Taipei").dt.tz_convert("UTC")
    for col in ("long_trade_million", "short_trade_million", "net_trade_million",
                "long_oi_million", "short_oi_million", "net_oi_million"):
        pdf[col] = None
    pdf["source"] = "tej_afinst_derived"
    pdf["ingestion_ts"] = pd.Timestamp.now(tz="UTC")
    pdf["trading_date"] = td.dt.date
    pdf["year"] = td.dt.year.astype("int32")

    tbl = pa.Table.from_pandas(pdf[[f.name for f in SILVER_SCHEMA]], schema=SILVER_SCHEMA, preserve_index=False)
    dest = silver_flows("tw_inst_futures_daily")
    if not dry_run:
        write_silver_partitioned(tbl, dest, ["year"], existing_data_behavior="delete_matching")

    summary = {
        "rows_out": len(pdf),
        "products": sorted(pdf["product"].unique().tolist()),
        "identities": sorted(pdf["identity"].unique().tolist()),
        "max_date": str(pdf["trading_date"].max()),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    if not dry_run:
        write_audit(IngestRecord(
            source="tej_afinst_derived", table="tw_inst_futures_daily",
            bronze_file="(derived from tw_inst_futures_full_daily)",
            rows_in=len(df), rows_out=len(pdf), sha256="", status="ok",
            started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            extra=summary,
        ))
    console.log(f"[TAIFEX derive] [green]done[/green]: {summary}")
    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Derive tw_inst_futures_daily from tw_inst_futures_full_daily")
    ap.add_argument("--dry-run", action="store_true", help="compute but don't write silver")
    args = ap.parse_args()
    derive_inst_futures_daily(dry_run=args.dry_run)
