"""TEJ source ingester.

CSV files expected under  `TEJ資料/` (will be renamed to bronze/tej/ in W1):
- TWN_EWPRCD_股價.csv         -> silver/bars/bars_1d (asset_class=tw_stock)
- TWN_EWTINST1_三大法人.csv    -> silver/flows/tw_inst_stock_daily      (M4)
- TWN_EWIFINQ_單季財報.csv      -> silver/fundamentals/fin_q (period_type=Q)   (M4)
- TWN_EWIFINQ_累季財報.csv      -> silver/fundamentals/fin_q (period_type=YTD) (M4)
- TWN_EWGIN_融資融券.csv        -> silver/flows/tw_margin_daily          (M4)

This module starts with **stock_daily** (M3) and is extended in M4.
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
from ..common.paths import silver_bars
from ..common.validators import bars_1d_schema

console = Console()


# ---------------------------------------------------------------------------
# TWN_EWPRCD_股價 -> silver/bars/bars_1d (asset_class=tw_stock)
# ---------------------------------------------------------------------------

EWPRCD_RENAME = {
    "證券碼":         "raw_id",
    "日期":           "raw_date",
    "開盤價":         "open",
    "最高價":         "high",
    "最低價":         "low",
    "收盤價":         "close",
    "成交量(千股)":    "volume_kshare",
    "開盤價-除權息":   "adj_open",
    "最高價-除權息":   "adj_high",
    "最低價-除權息":   "adj_low",
    "收盤價-除權息":   "adj_close",
}

TWSE_OPEN_LOCAL  = dt.time(9, 0)   # 09:00 Asia/Taipei
TWSE_CLOSE_LOCAL = dt.time(13, 30) # 13:30 Asia/Taipei

# stock_id can appear as either '1101' or '1101 台泥' in TEJ.
# Stock identifiers can also be alphanumeric for ETFs (00xx) and KY companies.
_RE_STOCK = r"^[0-9A-Za-z]+"


def _normalize_stock_id(raw: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Split '1101 台泥' into ('1101', '台泥'); '1101' alone -> ('1101', None)."""
    s = raw.astype(str).str.strip()
    parts = s.str.split(n=1, expand=True)
    stock_id = parts[0]
    name = parts[1] if parts.shape[1] > 1 else pd.Series([None] * len(parts), index=parts.index)
    return stock_id, name


def _to_ts_utc(date_int_or_str: pd.Series) -> pd.Series:
    """TEJ date is YYYYMMDD int or string. Anchor at 13:30 Asia/Taipei close, convert to UTC."""
    d = pd.to_datetime(date_int_or_str.astype(str), format="%Y%m%d")
    # close-of-day local timestamp, then to UTC
    return (
        d
        + pd.Timedelta(hours=TWSE_CLOSE_LOCAL.hour, minutes=TWSE_CLOSE_LOCAL.minute)
    ).dt.tz_localize("Asia/Taipei").dt.tz_convert("UTC")


def _transform_ewprcd_chunk(df: pd.DataFrame) -> pd.DataFrame:
    # IMPORTANT: chunked read_csv hands us non-zero-based indexes (chunk N has 200_000*N..),
    # so all helper scalars below would otherwise outer-join and double the row count.
    df = df.rename(columns=EWPRCD_RENAME).reset_index(drop=True)
    stock_id, _name = _normalize_stock_id(df["raw_id"])
    out = pd.DataFrame({
        "ts_utc":       _to_ts_utc(df["raw_date"]),
        "trading_date": pd.to_datetime(df["raw_date"].astype(str), format="%Y%m%d"),
        "asset_class":  "tw_stock",
        "exchange":     "TWSE",      # NB: TEJ TWN_EWPRCD covers both TWSE and TPEX,
                                     # but EWPRCD itself doesn't carry that flag — set TWSE by default;
                                     # downstream can override using symbol_map merge in M4.
        "symbol":       stock_id,
        "contract_id":  pd.Series([None] * len(df), dtype="object"),
        "session":      "day",
        "open":  pd.to_numeric(df["open"], errors="coerce"),
        "high":  pd.to_numeric(df["high"], errors="coerce"),
        "low":   pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        # TEJ 千股 -> shares (×1000). volume column expects shares (BIGINT, nullable).
        "volume": pd.array(
            (pd.to_numeric(df["volume_kshare"], errors="coerce") * 1000).round(),
            dtype="Int64",
        ),
        "open_interest": pd.Series([pd.NA] * len(df), dtype="Int64"),
        "vwap":          pd.Series([pd.NA] * len(df), dtype="Float64").astype(float),
        "settlement":    pd.Series([pd.NA] * len(df), dtype="Float64").astype(float),
        "adj_open":  pd.to_numeric(df["adj_open"], errors="coerce"),
        "adj_high":  pd.to_numeric(df["adj_high"], errors="coerce"),
        "adj_low":   pd.to_numeric(df["adj_low"], errors="coerce"),
        "adj_close": pd.to_numeric(df["adj_close"], errors="coerce"),
        # adj_factor reconstructed as adj_close/close when both present
        "adj_factor": pd.Series(dtype=float),
        "source":       "tej",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
        "quality_flag": "ok",
    })
    # adj_factor: only where both present
    mask = out["adj_close"].notna() & out["close"].notna() & (out["close"] != 0)
    out.loc[mask, "adj_factor"] = (out.loc[mask, "adj_close"] / out.loc[mask, "close"]).astype(float)
    out["year"] = out["ts_utc"].dt.year.astype("int32")
    return out


def ingest_stock_daily(
    csv_path: str | Path,
    *,
    years: tuple[int, ...] | None = None,
    chunksize: int = 200_000,
    dry_run: bool = False,
) -> dict:
    """End-to-end ingester for TEJ TWN_EWPRCD_股價.csv -> silver/bars/bars_1d.

    Returns summary dict (rows_in, rows_out, partitions).
    """
    csv_path = Path(csv_path).resolve()
    t0 = time.time()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    sha = sha256_file(csv_path)
    console.log(f"[TEJ stock_daily] reading {csv_path.name} sha256={sha[:12]}...")

    rows_in = rows_out = 0
    parts_written: set[int] = set()
    dest = silver_bars("1d") / "asset_class=tw_stock"

    # On first chunk we overwrite touched year partitions, then subsequent chunks
    # of the SAME year must "append" without dropping; switch behavior accordingly.
    seen_years: set[int] = set()

    for chunk_idx, chunk in enumerate(pd.read_csv(csv_path, chunksize=chunksize, dtype={"證券碼": str})):
        rows_in += len(chunk)
        out = _transform_ewprcd_chunk(chunk)
        if years:
            out = out[out["year"].isin(years)]
        if out.empty:
            continue

        # Validate first 100 rows of each chunk only (pandera is slow on millions).
        # Full type/coercion still applied via PyArrow on write.
        sample = out.head(100).copy()
        try:
            bars_1d_schema.validate(sample, lazy=True)
        except Exception as e:
            console.log(f"[red]validation failed on chunk {chunk_idx}[/red]: {e}")
            if not dry_run:
                write_audit(IngestRecord(
                    source="tej", table="bars_1d", bronze_file=str(csv_path),
                    rows_in=rows_in, rows_out=rows_out, sha256=sha,
                    status="validation_fail", started_at=started_at,
                    ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    error=str(e)[:500],
                ))
            raise

        # PyArrow schema: ensure ts_utc retains tz-aware UTC
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
        out["trading_date"] = pd.to_datetime(out["trading_date"]).dt.date
        tbl = pa.Table.from_pandas(out[
            [f.name for f in schema]
        ], schema=schema, preserve_index=False)

        if dry_run:
            console.log(f"  [dry-run] chunk {chunk_idx}: {len(out)} rows, years={sorted(out['year'].unique())}")
        else:
            # First touch of each year: delete_matching to refresh
            chunk_years = set(out["year"].unique().tolist())
            new_years = chunk_years - seen_years
            seen_years |= chunk_years
            behavior = "delete_matching" if new_years else "overwrite_or_ignore"
            write_silver_partitioned(
                tbl,
                dest_root=dest,
                partition_cols=["year"],
                existing_data_behavior=behavior,
            )
            parts_written |= chunk_years

        rows_out += len(out)
        if chunk_idx % 5 == 0:
            console.log(f"  chunk {chunk_idx}: rows_in={rows_in:,} rows_out={rows_out:,}  (elapsed {time.time()-t0:.1f}s)")

    ended_at = dt.datetime.now(dt.timezone.utc).isoformat()
    summary = {
        "rows_in": rows_in,
        "rows_out": rows_out,
        "partitions_written": sorted(parts_written),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    if not dry_run:
        write_audit(IngestRecord(
            source="tej", table="bars_1d", bronze_file=str(csv_path),
            rows_in=rows_in, rows_out=rows_out, sha256=sha,
            status="ok", started_at=started_at, ended_at=ended_at,
            extra=summary,
        ))
    console.log(f"[TEJ stock_daily] [green]done[/green]: {summary}")
    return summary
