"""Macro ingester: aggregates SUPPLEMENT/{US_INDEX,US_FUTURES,US_SECTOR_ETF,COMMODITY,FX,TW_INDEX,ASIA,CREDIT}/*.parquet
into silver/macro/macro_daily.parquet (single denormalized table keyed by (symbol, trading_date)).

Each input file is `<SYMBOL>_daily.parquet` with `Date`-indexed `open/high/low/close/volume`.
USDTWD has a different schema (`usdtwd_*` cols + already-derived ret1/ret5/ma20/z20) — handled specially.
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
from ..common.paths import RAW_ROOT, silver_macro

console = Console()

# Map SUPPLEMENT subdir -> canonical category
CATEGORY_BY_DIR = {
    "US_INDEX":      "us_index",
    "US_FUTURES":    "us_futures",
    "US_SECTOR_ETF": "us_etf",
    "COMMODITY":     "commodity",
    "FX":            "fx",
    "TW_INDEX":      "tw_index",
    "ASIA":          "asia_index",
    "CREDIT":        "credit",
}

# Map filename stem -> canonical_symbol (only special cases; default = stem split first '_').
FILENAME_OVERRIDES = {
    "GSPC":      "SPX",
    "DJI":       "DJI",
    "NDX":       "NDX",
    "RUT":       "RUT",
    "SOX":       "SOX",
    "VIX":       "VIX",
    "IRX":       "IRX",
    "TNX":       "TNX",
    "ES_F":      "ES",
    "NQ_F":      "NQ",
    "YM_F":      "YM",
    "RTY_F":     "RTY",
    "CL_F":      "CL",
    "GC_F":      "GC",
    "HG_F":      "HG",
    "NG_F":      "NG",
    "SI_F":      "SI",
    "TWII":      "TAIEX",
    "0050_TW":   "0050",
    "0056_TW":   "0056",
    "USDTWD":    "USDTWD",
    "DX-Y_NYB":  "DXY",
    "EURUSD_X":  "EURUSD",
    "JPY_X":     "USDJPY",
    "CNY_X":     "USDCNY",
    "HSI":       "HSI",
    "N225":      "N225",
    "KS11":      "KS11",
    "STI":       "STI",
    "000001_SS": "SSEC",
    "SPY":       "SPY",
    "QQQ":       "QQQ",
    "TLT":       "TLT",
    "GLD":       "GLD",
    "IWM":       "IWM",
    "XLE":       "XLE",
    "XLF":       "XLF",
    "XLI":       "XLI",
    "XLK":       "XLK",
    "XLV":       "XLV",
    "HYG":       "HYG",
    "LQD":       "LQD",
    "SHY":       "SHY",
    "IEF":       "IEF",
    "TIP":       "TIP",
}


def _stem_to_symbol(stem: str) -> str:
    # stem like 'ES_F_daily' or 'VIX_daily' or 'USDTWD_daily'
    base = stem.replace("_daily", "")
    return FILENAME_OVERRIDES.get(base, base)


def _normalize_one(fp: Path, category: str) -> pd.DataFrame:
    df = pq.read_table(fp).to_pandas()
    sym = _stem_to_symbol(fp.stem)

    # USDTWD: prefixed columns
    if sym == "USDTWD" and "usdtwd_close" in df.columns:
        df = df.rename(columns={
            "usdtwd_open": "open",
            "usdtwd_high": "high",
            "usdtwd_low": "low",
            "usdtwd_close": "close",
            "usdtwd_adj_close": "adj_close",
            "usdtwd_volume": "volume",
        })
        if "date" in df.columns:
            df = df.set_index("date")

    # Normalize index name
    if df.index.name in ("Date", "date", "trading_date", None):
        idx = pd.to_datetime(df.index).normalize()
    else:
        idx = pd.to_datetime(df.index).normalize()

    # Strip tz
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)

    df = df.copy()
    df["trading_date"] = idx.date
    df["symbol"] = sym
    df["category"] = category
    for c in ("open", "high", "low", "close", "adj_close", "volume"):
        if c not in df.columns:
            df[c] = None
    df["source"] = "yahoo"
    df["ingestion_ts"] = pd.Timestamp.now(tz="UTC")
    keep = ["trading_date", "symbol", "category", "open", "high", "low", "close",
            "adj_close", "volume", "source", "ingestion_ts"]
    return df.reset_index(drop=True)[keep]


def ingest_macro_daily(*, dry_run: bool = False) -> dict:
    """Walk SUPPLEMENT/* parquets -> silver/macro/macro_daily.parquet (single file)."""
    t0 = time.time()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    supp = RAW_ROOT / "SUPPLEMENT"
    dest = silver_macro() / "macro_daily.parquet"
    parts: list[pd.DataFrame] = []
    files_seen = 0

    for subdir, category in CATEGORY_BY_DIR.items():
        subp = supp / subdir
        if not subp.exists():
            continue
        for fp in sorted(subp.glob("*_daily.parquet")):
            try:
                norm = _normalize_one(fp, category)
                parts.append(norm)
                files_seen += 1
            except Exception as e:
                console.log(f"[red][macro] skip {fp.relative_to(supp)}: {e}[/red]")

    df = pd.concat(parts, ignore_index=True)
    df = df.dropna(subset=["trading_date", "symbol"])
    df = df.drop_duplicates(subset=["symbol", "trading_date"], keep="last")

    schema = pa.schema([
        ("trading_date", pa.date32()),
        ("symbol",       pa.string()),
        ("category",     pa.string()),
        ("open",         pa.float64()),
        ("high",         pa.float64()),
        ("low",          pa.float64()),
        ("close",        pa.float64()),
        ("adj_close",    pa.float64()),
        ("volume",       pa.int64()),
        ("source",       pa.string()),
        ("ingestion_ts", pa.timestamp("ns", tz="UTC")),
    ])
    for c in ("open", "high", "low", "close", "adj_close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = pd.array(pd.to_numeric(df["volume"], errors="coerce"), dtype="Int64")
    df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
    tbl = pa.Table.from_pandas(df[[f.name for f in schema]], schema=schema, preserve_index=False)

    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(tbl, dest, compression="zstd", compression_level=3)

    rows_out = len(df)
    summary = {
        "files_seen": files_seen,
        "rows_out": rows_out,
        "symbols": sorted(df["symbol"].unique().tolist()),
        "categories": sorted(df["category"].unique().tolist()),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    if not dry_run:
        write_audit(IngestRecord(
            source="yahoo", table="macro_daily", bronze_file=str(supp),
            rows_in=rows_out, rows_out=rows_out, sha256="",  # multi-file source
            status="ok", started_at=started,
            ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            extra=summary,
        ))
    console.log(f"[macro] [green]done[/green]: {files_seen} files, {rows_out} rows, {summary['elapsed_sec']}s")
    return summary
