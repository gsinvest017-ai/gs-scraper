"""TEJ source ingester.

CSV files expected under  `TEJ資料/` (will be renamed to bronze/tej/ in W1):
- TWN_EWPRCD_股價.csv         -> silver/bars/bars_1d (asset_class=tw_stock)         [M3]
- TWN_EWTINST1_三大法人.csv    -> silver/flows/tw_inst_stock_daily                  [M4]
- TWN_EWIFINQ_單季財報.csv      -> silver/fundamentals/fin_q  (period_type=Q)        [M4]
- TWN_EWIFINQ_累季財報.csv      -> silver/fundamentals/fin_q  (period_type=YTD)      [M4]
- TWN_EWGIN_融資融券.csv        -> silver/flows/tw_margin_daily                       [M4]
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

    # IMPORTANT: chunks of the same year must accumulate, not delete each other.
    # Strategy: wipe the entire destination once at start (no rewrites mid-ingest),
    # then all chunks append (`overwrite_or_ignore`). If you want partial replace
    # (e.g. just rerun 2025), use `years=(2025,)` and the wipe is restricted below.
    if not dry_run:
        import shutil
        if years:
            for y in years:
                ydir = dest / f"year={y}"
                if ydir.exists():
                    shutil.rmtree(ydir)
        elif dest.exists():
            shutil.rmtree(dest)
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
            chunk_years = set(out["year"].unique().tolist())
            seen_years |= chunk_years
            # Always append; dest was wiped at start.
            write_silver_partitioned(
                tbl,
                dest_root=dest,
                partition_cols=["year"],
                existing_data_behavior="overwrite_or_ignore",
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


# ---------------------------------------------------------------------------
# TWN_EWTINST1_三大法人 -> silver/flows/tw_inst_stock_daily
# ---------------------------------------------------------------------------

EWTINST1_RENAME = {
    "證券碼":                "raw_id",
    "日期":                  "raw_date",
    "外資買賣超(千股)":        "foreign_net_kshare",
    "投信買賣超(千股)":        "sitc_net_kshare",
    "自營買賣超(千股)":        "dealer_net_kshare",
    "合計買賣超(千股)":        "total_net_kshare",
    "外資買進張數":            "foreign_buy_lot",
    "投信買進張數":            "sitc_buy_lot",
    "外資賣出張數":            "foreign_sell_lot",
    "投信賣出張數":            "sitc_sell_lot",
    "自營買進張數":            "dealer_buy_lot",
    "自營賣出張數":            "dealer_sell_lot",
    "外資總持股數(千股)":      "foreign_hold_kshare",
    "投信總持股數(千股)":      "sitc_hold_kshare",
    "自營總持股數(千股)":      "dealer_hold_kshare",
    "外資總持股率(%)":         "foreign_hold_pct",
    "投信總持股率(%)":         "sitc_hold_pct",
    "自營總持股率(%)":         "dealer_hold_pct",
}


def _transform_ewtinst1_chunk(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=EWTINST1_RENAME).reset_index(drop=True)
    stock_id, _ = _normalize_stock_id(df["raw_id"])
    out = pd.DataFrame({
        "trading_date": pd.to_datetime(df["raw_date"].astype(str), format="%Y%m%d"),
        "stock_id":     stock_id,
        "exchange":     "TWSE",   # TEJ EWTINST1 covers both; symbol_map override in M4 post-step
        # buy/sell lots are already in 張 (= 1 lot = 1000 shares). Use as-is.
        "foreign_buy_lot":  pd.array(pd.to_numeric(df["foreign_buy_lot"], errors="coerce"),  dtype="Int64"),
        "foreign_sell_lot": pd.array(pd.to_numeric(df["foreign_sell_lot"], errors="coerce"), dtype="Int64"),
        "sitc_buy_lot":     pd.array(pd.to_numeric(df["sitc_buy_lot"], errors="coerce"),     dtype="Int64"),
        "sitc_sell_lot":    pd.array(pd.to_numeric(df["sitc_sell_lot"], errors="coerce"),    dtype="Int64"),
        "dealer_buy_lot":   pd.array(pd.to_numeric(df["dealer_buy_lot"], errors="coerce"),   dtype="Int64"),
        "dealer_sell_lot":  pd.array(pd.to_numeric(df["dealer_sell_lot"], errors="coerce"),  dtype="Int64"),
        # net 買賣超(千股) ×1000 -> shares; but our schema uses lot. 1000 shares == 1 lot.
        # So net_lot == net_kshare directly (no scale change).
        "foreign_net_lot": pd.array(pd.to_numeric(df["foreign_net_kshare"], errors="coerce"), dtype="Int64"),
        "sitc_net_lot":    pd.array(pd.to_numeric(df["sitc_net_kshare"], errors="coerce"),    dtype="Int64"),
        "dealer_net_lot":  pd.array(pd.to_numeric(df["dealer_net_kshare"], errors="coerce"),  dtype="Int64"),
        "total_net_lot":   pd.array(pd.to_numeric(df["total_net_kshare"], errors="coerce"),   dtype="Int64"),
        # holdings: 千股 = lot (same scale)
        "foreign_hold_lot": pd.array(pd.to_numeric(df["foreign_hold_kshare"], errors="coerce"), dtype="Int64"),
        "sitc_hold_lot":    pd.array(pd.to_numeric(df["sitc_hold_kshare"], errors="coerce"),    dtype="Int64"),
        "dealer_hold_lot":  pd.array(pd.to_numeric(df["dealer_hold_kshare"], errors="coerce"),  dtype="Int64"),
        "foreign_hold_pct": pd.to_numeric(df["foreign_hold_pct"], errors="coerce"),
        "sitc_hold_pct":    pd.to_numeric(df["sitc_hold_pct"], errors="coerce"),
        "dealer_hold_pct":  pd.to_numeric(df["dealer_hold_pct"], errors="coerce"),
        "source":       "tej",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = out["trading_date"].dt.year.astype("int32")
    return out


def ingest_inst_stock_daily(
    csv_path: str | Path,
    *,
    years: tuple[int, ...] | None = None,
    chunksize: int = 300_000,
    dry_run: bool = False,
) -> dict:
    """TEJ TWN_EWTINST1_三大法人.csv -> silver/flows/tw_inst_stock_daily."""
    from ..common.paths import silver_flows

    csv_path = Path(csv_path).resolve()
    t0 = time.time()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    sha = sha256_file(csv_path)
    console.log(f"[TEJ inst_stock_daily] reading {csv_path.name} sha256={sha[:12]}...")

    rows_in = rows_out = 0
    dest = silver_flows("tw_inst_stock_daily")
    seen_years: set[int] = set()
    if not dry_run:
        import shutil
        if years:
            for y in years:
                ydir = dest / f"year={y}"
                if ydir.exists():
                    shutil.rmtree(ydir)
        elif dest.exists():
            shutil.rmtree(dest)

    schema = pa.schema([
        ("trading_date",      pa.date32()),
        ("stock_id",          pa.string()),
        ("exchange",          pa.string()),
        ("foreign_net_lot",   pa.int64()),
        ("sitc_net_lot",      pa.int64()),
        ("dealer_net_lot",    pa.int64()),
        ("total_net_lot",     pa.int64()),
        ("foreign_buy_lot",   pa.int64()),
        ("foreign_sell_lot",  pa.int64()),
        ("sitc_buy_lot",      pa.int64()),
        ("sitc_sell_lot",     pa.int64()),
        ("dealer_buy_lot",    pa.int64()),
        ("dealer_sell_lot",   pa.int64()),
        ("foreign_hold_lot",  pa.int64()),
        ("foreign_hold_pct",  pa.float64()),
        ("sitc_hold_lot",     pa.int64()),
        ("sitc_hold_pct",     pa.float64()),
        ("dealer_hold_lot",   pa.int64()),
        ("dealer_hold_pct",   pa.float64()),
        ("source",            pa.string()),
        ("ingestion_ts",      pa.timestamp("ns", tz="UTC")),
        ("year",              pa.int32()),
    ])
    pq_cols = [f.name for f in schema]

    for chunk_idx, chunk in enumerate(pd.read_csv(csv_path, chunksize=chunksize, dtype={"證券碼": str})):
        rows_in += len(chunk)
        out = _transform_ewtinst1_chunk(chunk)
        if years:
            out = out[out["year"].isin(years)]
        if out.empty:
            continue
        out["trading_date"] = pd.to_datetime(out["trading_date"]).dt.date
        tbl = pa.Table.from_pandas(out[pq_cols], schema=schema, preserve_index=False)

        if not dry_run:
            chunk_years = set(out["year"].unique().tolist())
            seen_years |= chunk_years
            write_silver_partitioned(tbl, dest, ["year"], existing_data_behavior="overwrite_or_ignore")
        rows_out += len(out)
        if chunk_idx % 5 == 0:
            console.log(f"  chunk {chunk_idx}: rows_in={rows_in:,} rows_out={rows_out:,}")

    summary = {
        "rows_in": rows_in,
        "rows_out": rows_out,
        "partitions_written": sorted(seen_years),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    if not dry_run:
        write_audit(IngestRecord(
            source="tej", table="tw_inst_stock_daily", bronze_file=str(csv_path),
            rows_in=rows_in, rows_out=rows_out, sha256=sha,
            status="ok", started_at=started_at,
            ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            extra=summary,
        ))
    console.log(f"[TEJ inst_stock_daily] [green]done[/green]: {summary}")
    return summary


# ---------------------------------------------------------------------------
# TWN_EWGIN_融資融券 -> silver/flows/tw_margin_daily
# ---------------------------------------------------------------------------

EWGIN_RENAME = {
    "證券碼":         "raw_id",
    "日期":           "raw_date",
    "融資買進(張)":    "margin_buy_lot",
    "融資賣出(張)":    "margin_sell_lot",
    "融券買入(張)":    "short_buy_lot",
    "融券賣出(張)":    "short_sell_lot",
    "融資餘額(張)":    "margin_balance_lot",
    "融券餘額(張)":    "short_balance_lot",
    "融資餘額(千元)":  "margin_balance_ktwd",
    "融券餘額(千元)":  "short_balance_ktwd",
    "融資使用率":      "margin_util_pct",
    "融券使用率":      "short_util_pct",
    "券資比":          "short_to_margin_pct",
    "融資維持率":      "margin_maint_pct",
    "融券維持率":      "short_maint_pct",
    "整戶維持率":      "account_maint_pct",
}


def _parse_ewgin_date(s: pd.Series) -> pd.Series:
    """TEJ EWGIN uses '2010-01-04' string. Robust parse."""
    return pd.to_datetime(s.astype(str), errors="coerce")


def _transform_ewgin_chunk(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=EWGIN_RENAME).reset_index(drop=True)
    sid, _ = _normalize_stock_id(df["raw_id"])
    td = _parse_ewgin_date(df["raw_date"])
    out = pd.DataFrame({
        "trading_date": td,
        "stock_id":     sid,
        "margin_buy_lot":      pd.array(pd.to_numeric(df["margin_buy_lot"], errors="coerce"),  dtype="Int64"),
        "margin_sell_lot":     pd.array(pd.to_numeric(df["margin_sell_lot"], errors="coerce"), dtype="Int64"),
        "short_buy_lot":       pd.array(pd.to_numeric(df["short_buy_lot"], errors="coerce"),   dtype="Int64"),
        "short_sell_lot":      pd.array(pd.to_numeric(df["short_sell_lot"], errors="coerce"),  dtype="Int64"),
        "margin_balance_lot":  pd.array(pd.to_numeric(df["margin_balance_lot"], errors="coerce"), dtype="Int64"),
        "short_balance_lot":   pd.array(pd.to_numeric(df["short_balance_lot"], errors="coerce"),  dtype="Int64"),
        "margin_balance_ktwd": pd.to_numeric(df["margin_balance_ktwd"], errors="coerce"),
        "short_balance_ktwd":  pd.to_numeric(df["short_balance_ktwd"], errors="coerce"),
        "margin_util_pct":     pd.to_numeric(df["margin_util_pct"], errors="coerce"),
        "short_util_pct":      pd.to_numeric(df["short_util_pct"], errors="coerce"),
        "short_to_margin_pct": pd.to_numeric(df["short_to_margin_pct"], errors="coerce"),
        "margin_maint_pct":    pd.to_numeric(df["margin_maint_pct"], errors="coerce"),
        "short_maint_pct":     pd.to_numeric(df["short_maint_pct"], errors="coerce"),
        "account_maint_pct":   pd.to_numeric(df["account_maint_pct"], errors="coerce"),
        "source":       "tej",
        "ingestion_ts": pd.Timestamp.now(tz="UTC"),
    })
    out["year"] = out["trading_date"].dt.year.astype("int32")
    return out


def ingest_margin_daily(
    csv_path: str | Path,
    *,
    years: tuple[int, ...] | None = None,
    chunksize: int = 300_000,
    dry_run: bool = False,
) -> dict:
    """TEJ TWN_EWGIN_融資融券.csv -> silver/flows/tw_margin_daily."""
    from ..common.paths import silver_flows

    csv_path = Path(csv_path).resolve()
    t0 = time.time()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    sha = sha256_file(csv_path)
    console.log(f"[TEJ margin_daily] reading {csv_path.name} sha256={sha[:12]}...")

    schema = pa.schema([
        ("trading_date",        pa.date32()),
        ("stock_id",            pa.string()),
        ("margin_buy_lot",      pa.int64()),
        ("margin_sell_lot",     pa.int64()),
        ("short_buy_lot",       pa.int64()),
        ("short_sell_lot",      pa.int64()),
        ("margin_balance_lot",  pa.int64()),
        ("short_balance_lot",   pa.int64()),
        ("margin_balance_ktwd", pa.float64()),
        ("short_balance_ktwd",  pa.float64()),
        ("margin_util_pct",     pa.float64()),
        ("short_util_pct",      pa.float64()),
        ("short_to_margin_pct", pa.float64()),
        ("margin_maint_pct",    pa.float64()),
        ("short_maint_pct",     pa.float64()),
        ("account_maint_pct",   pa.float64()),
        ("source",              pa.string()),
        ("ingestion_ts",        pa.timestamp("ns", tz="UTC")),
        ("year",                pa.int32()),
    ])
    pq_cols = [f.name for f in schema]
    dest = silver_flows("tw_margin_daily")
    seen_years: set[int] = set()
    rows_in = rows_out = 0
    if not dry_run:
        import shutil
        if years:
            for y in years:
                ydir = dest / f"year={y}"
                if ydir.exists():
                    shutil.rmtree(ydir)
        elif dest.exists():
            shutil.rmtree(dest)

    for chunk_idx, chunk in enumerate(pd.read_csv(csv_path, chunksize=chunksize, dtype={"證券碼": str})):
        rows_in += len(chunk)
        out = _transform_ewgin_chunk(chunk)
        out = out.dropna(subset=["trading_date", "stock_id"])
        if years:
            out = out[out["year"].isin(years)]
        if out.empty:
            continue
        out["trading_date"] = pd.to_datetime(out["trading_date"]).dt.date
        tbl = pa.Table.from_pandas(out[pq_cols], schema=schema, preserve_index=False)
        if not dry_run:
            chunk_years = set(out["year"].unique().tolist())
            seen_years |= chunk_years
            write_silver_partitioned(tbl, dest, ["year"], existing_data_behavior="overwrite_or_ignore")
        rows_out += len(out)
        if chunk_idx % 5 == 0:
            console.log(f"  chunk {chunk_idx}: rows_in={rows_in:,} rows_out={rows_out:,}")

    summary = {
        "rows_in": rows_in, "rows_out": rows_out,
        "partitions_written": sorted(seen_years),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    if not dry_run:
        write_audit(IngestRecord(
            source="tej", table="tw_margin_daily", bronze_file=str(csv_path),
            rows_in=rows_in, rows_out=rows_out, sha256=sha, status="ok",
            started_at=started_at, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            extra=summary,
        ))
    console.log(f"[TEJ margin_daily] [green]done[/green]: {summary}")
    return summary


# ---------------------------------------------------------------------------
# TWN_EWIFINQ_單季財報 / 累季財報 -> silver/fundamentals/fin_q
# ---------------------------------------------------------------------------

EWIFINQ_RENAME = {
    "證券碼":             "raw_id",
    "財務資料日":          "fiscal_date",       # YYYYMM int (e.g. 201003 = 2010Q1)
    "季別":               "fiscal_quarter",     # 1..4
    "合併(Y/N)":          "consolidated_yn",
    "幣別":               "currency",
    "財報發布日":          "publish_date_str",
    "每股盈餘":            "eps",
    "ROA(C) 稅前息前折舊前": "roa_pre",
    "ROE(A)-稅後":         "roe_post",
    "營業毛利率":          "gross_margin",
    "營業利益率":          "op_margin",
    "稅後淨利率":          "net_margin",
    "營收成長率":          "rev_growth",
    "營業毛利成長率":       "gross_growth",
    "營業利益成長率":       "op_growth",
    "資產總額":            "total_assets",
    "負債總額":            "total_liab",
    "股東權益總額":         "total_equity",
    "流動資產":            "current_assets",
    "流動負債":            "current_liab",
    "營業收入淨額":         "revenue",
    "營業成本":            "cogs",
    "營業利益":            "op_income",
    "合併總損益":          "net_income",
    "歸屬母公司淨利（損）":   "ni_to_parent",
    "來自營運之現金流量":    "cfo",
    "投資活動之現金流量":    "cfi",
    "籌資活動之現金流量":    "cff",
}


def _transform_ewifinq_chunk(df: pd.DataFrame, period_type: str) -> pd.DataFrame:
    """Transform a TEJ TWN_EWIFINQ chunk. `period_type` in {'Q','YTD'}."""
    df = df.rename(columns=EWIFINQ_RENAME).reset_index(drop=True)
    sid, _ = _normalize_stock_id(df["raw_id"])

    # fiscal_period: '201003' -> '2010Q1'
    fp_int = pd.to_numeric(df["fiscal_date"], errors="coerce").astype("Int64")
    yyyy = (fp_int // 100).astype("Int64")
    mm = (fp_int % 100).astype("Int64")
    # mm in {3,6,9,12} maps to Q1..Q4
    qmap = {3: 1, 6: 2, 9: 3, 12: 4}
    q = mm.map(qmap)
    fiscal_period = yyyy.astype("string") + "Q" + q.astype("string")

    out = pd.DataFrame({
        "stock_id":      sid,
        "fiscal_period": fiscal_period.astype("object"),
        "period_type":   period_type,
        "consolidated":  df["consolidated_yn"].astype(str).str.upper().map({"Y": True, "N": False}).astype("boolean"),
        "currency":      df["currency"].astype(str),
        "publish_date":  pd.to_datetime(df["publish_date_str"], errors="coerce"),
        "eps":           pd.to_numeric(df["eps"], errors="coerce"),
        "roa_pre":       pd.to_numeric(df["roa_pre"], errors="coerce"),
        "roe_post":      pd.to_numeric(df["roe_post"], errors="coerce"),
        "gross_margin":  pd.to_numeric(df["gross_margin"], errors="coerce"),
        "op_margin":     pd.to_numeric(df["op_margin"], errors="coerce"),
        "net_margin":    pd.to_numeric(df["net_margin"], errors="coerce"),
        "rev_growth":    pd.to_numeric(df["rev_growth"], errors="coerce"),
        "gross_growth":  pd.to_numeric(df["gross_growth"], errors="coerce"),
        "op_growth":     pd.to_numeric(df["op_growth"], errors="coerce"),
        "total_assets":  pd.array(pd.to_numeric(df["total_assets"], errors="coerce"),  dtype="Int64"),
        "total_liab":    pd.array(pd.to_numeric(df["total_liab"], errors="coerce"),    dtype="Int64"),
        "total_equity":  pd.array(pd.to_numeric(df["total_equity"], errors="coerce"),  dtype="Int64"),
        "current_assets":pd.array(pd.to_numeric(df["current_assets"], errors="coerce"),dtype="Int64"),
        "current_liab":  pd.array(pd.to_numeric(df["current_liab"], errors="coerce"),  dtype="Int64"),
        "revenue":       pd.array(pd.to_numeric(df["revenue"], errors="coerce"),       dtype="Int64"),
        "cogs":          pd.array(pd.to_numeric(df["cogs"], errors="coerce"),          dtype="Int64"),
        "op_income":     pd.array(pd.to_numeric(df["op_income"], errors="coerce"),     dtype="Int64"),
        "net_income":    pd.array(pd.to_numeric(df["net_income"], errors="coerce"),    dtype="Int64"),
        "ni_to_parent":  pd.array(pd.to_numeric(df["ni_to_parent"], errors="coerce"),  dtype="Int64"),
        "cfo":           pd.array(pd.to_numeric(df["cfo"], errors="coerce"),           dtype="Int64"),
        "cfi":           pd.array(pd.to_numeric(df["cfi"], errors="coerce"),           dtype="Int64"),
        "cff":           pd.array(pd.to_numeric(df["cff"], errors="coerce"),           dtype="Int64"),
        "source":        "tej",
        "ingestion_ts":  pd.Timestamp.now(tz="UTC"),
    })
    # filter rows where fiscal_period failed to parse
    out = out.dropna(subset=["publish_date", "fiscal_period"])
    out["year"] = pd.to_datetime(out["publish_date"]).dt.year.astype("int32")
    return out


def ingest_fundamentals_q(
    quarterly_csv: str | Path,
    ytd_csv: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Load TEJ EWIFINQ 單季 (period_type=Q) and 累季 (period_type=YTD) into silver/fundamentals/fin_q."""
    from ..common.paths import silver_fundamentals

    summary = {"Q": None, "YTD": None}
    dest = silver_fundamentals("fin_q")
    seen_years: set[int] = set()
    # Wipe dest once (idempotent rerun overwrites both Q + YTD)
    if not dry_run:
        import shutil
        if dest.exists():
            shutil.rmtree(dest)

    schema = pa.schema([
        ("stock_id",       pa.string()),
        ("fiscal_period",  pa.string()),
        ("period_type",    pa.string()),
        ("consolidated",   pa.bool_()),
        ("currency",       pa.string()),
        ("publish_date",   pa.date32()),
        ("eps",            pa.float64()),
        ("roa_pre",        pa.float64()),
        ("roe_post",       pa.float64()),
        ("gross_margin",   pa.float64()),
        ("op_margin",      pa.float64()),
        ("net_margin",     pa.float64()),
        ("rev_growth",     pa.float64()),
        ("gross_growth",   pa.float64()),
        ("op_growth",      pa.float64()),
        ("total_assets",   pa.int64()),
        ("total_liab",     pa.int64()),
        ("total_equity",   pa.int64()),
        ("current_assets", pa.int64()),
        ("current_liab",   pa.int64()),
        ("revenue",        pa.int64()),
        ("cogs",           pa.int64()),
        ("op_income",      pa.int64()),
        ("net_income",     pa.int64()),
        ("ni_to_parent",   pa.int64()),
        ("cfo",            pa.int64()),
        ("cfi",            pa.int64()),
        ("cff",            pa.int64()),
        ("source",         pa.string()),
        ("ingestion_ts",   pa.timestamp("ns", tz="UTC")),
        ("year",           pa.int32()),
    ])
    pq_cols = [f.name for f in schema]

    def _load_one(csv_path: Path, period_type: str) -> dict:
        nonlocal seen_years
        t0 = time.time()
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        sha = sha256_file(csv_path)
        console.log(f"[TEJ fundamentals {period_type}] reading {csv_path.name} sha256={sha[:12]}...")
        df = pd.read_csv(csv_path, dtype={"證券碼": str})
        rows_in = len(df)
        out = _transform_ewifinq_chunk(df, period_type=period_type)
        out["publish_date"] = pd.to_datetime(out["publish_date"]).dt.date
        tbl = pa.Table.from_pandas(out[pq_cols], schema=schema, preserve_index=False)
        if not dry_run:
            chunk_years = set(out["year"].unique().tolist())
            seen_years |= chunk_years
            write_silver_partitioned(tbl, dest, ["year"], existing_data_behavior="overwrite_or_ignore")
        elapsed = round(time.time() - t0, 1)
        info = {"rows_in": rows_in, "rows_out": len(out), "elapsed_sec": elapsed}
        if not dry_run:
            write_audit(IngestRecord(
                source="tej", table=f"fundamentals_q/{period_type}", bronze_file=str(csv_path),
                rows_in=rows_in, rows_out=len(out), sha256=sha, status="ok",
                started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                extra=info,
            ))
        console.log(f"  -> {info}")
        return info

    summary["Q"] = _load_one(Path(quarterly_csv).resolve(), "Q")
    if ytd_csv is not None:
        summary["YTD"] = _load_one(Path(ytd_csv).resolve(), "YTD")
    summary["partitions_written"] = sorted(seen_years)
    console.log(f"[TEJ fundamentals_q] [green]done[/green]: {summary}")
    return summary
