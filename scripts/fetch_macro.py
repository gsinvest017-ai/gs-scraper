"""fetch_macro.py — yfinance daily refresh for SUPPLEMENT macro parquets.

Refreshes each RAW_SOURCES/SUPPLEMENT/<category>/<stem>_daily.parquet by
downloading the incremental window (since the file's max date) from Yahoo
Finance and appending it, preserving the file's `Date`-indexed OHLCV schema.

After this runs, `qd-ingest macro-daily` (or the derived rebuild) picks up the
fresh rows into silver/macro/macro_daily.parquet → macro_factors gold.

Usage:
  .venv/bin/python scripts/fetch_macro.py                # refresh all
  .venv/bin/python scripts/fetch_macro.py --only VIX,SPY # subset by stem
  .venv/bin/python scripts/fetch_macro.py --full         # re-fetch full history
  .venv/bin/python scripts/fetch_macro.py --dry-run      # print plan only
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SUPP = (REPO / ".." / "RAW_SOURCES" / "SUPPLEMENT").resolve()

# (category dir, stem) -> yfinance ticker. Explicit map (safer than rule-derivation).
TICKERS: dict[tuple[str, str], str] = {
    # US_INDEX (^-prefixed)
    ("US_INDEX", "DJI"): "^DJI",
    ("US_INDEX", "GSPC"): "^GSPC",
    ("US_INDEX", "IRX"): "^IRX",
    ("US_INDEX", "NDX"): "^NDX",
    ("US_INDEX", "RUT"): "^RUT",
    ("US_INDEX", "SOX"): "^SOX",
    ("US_INDEX", "TNX"): "^TNX",
    ("US_INDEX", "VIX"): "^VIX",
    # US_FUTURES (=F)
    ("US_FUTURES", "ES_F"): "ES=F",
    ("US_FUTURES", "NQ_F"): "NQ=F",
    ("US_FUTURES", "RTY_F"): "RTY=F",
    ("US_FUTURES", "YM_F"): "YM=F",
    # US_SECTOR_ETF (plain)
    ("US_SECTOR_ETF", "GLD"): "GLD",
    ("US_SECTOR_ETF", "IWM"): "IWM",
    ("US_SECTOR_ETF", "QQQ"): "QQQ",
    ("US_SECTOR_ETF", "SPY"): "SPY",
    ("US_SECTOR_ETF", "TLT"): "TLT",
    ("US_SECTOR_ETF", "XLE"): "XLE",
    ("US_SECTOR_ETF", "XLF"): "XLF",
    ("US_SECTOR_ETF", "XLI"): "XLI",
    ("US_SECTOR_ETF", "XLK"): "XLK",
    ("US_SECTOR_ETF", "XLV"): "XLV",
    # COMMODITY (=F)
    ("COMMODITY", "CL_F"): "CL=F",
    ("COMMODITY", "GC_F"): "GC=F",
    ("COMMODITY", "HG_F"): "HG=F",
    ("COMMODITY", "NG_F"): "NG=F",
    ("COMMODITY", "SI_F"): "SI=F",
    # FX
    ("FX", "CNY_X"): "CNY=X",
    ("FX", "DX-Y_NYB"): "DX-Y.NYB",
    ("FX", "EURUSD_X"): "EURUSD=X",
    ("FX", "JPY_X"): "JPY=X",
    ("FX", "USDTWD"): "TWD=X",
    # TW_INDEX
    ("TW_INDEX", "0050_TW"): "0050.TW",
    ("TW_INDEX", "0056_TW"): "0056.TW",
    ("TW_INDEX", "TWII"): "^TWII",
    # ASIA
    ("ASIA", "000001_SS"): "000001.SS",
    ("ASIA", "HSI"): "^HSI",
    ("ASIA", "KS11"): "^KS11",
    ("ASIA", "N225"): "^N225",
    ("ASIA", "STI"): "^STI",
    # CREDIT (plain)
    ("CREDIT", "HYG"): "HYG",
    ("CREDIT", "IEF"): "IEF",
    ("CREDIT", "LQD"): "LQD",
    ("CREDIT", "SHY"): "SHY",
    ("CREDIT", "TIP"): "TIP",
}

STD_COLS = ["open", "high", "low", "close", "adj_close", "volume"]


def _existing_frame(fp: Path) -> pd.DataFrame | None:
    """Read existing parquet, return a Date-indexed standard-schema frame (or None)."""
    if not fp.exists():
        return None
    df = pd.read_parquet(fp)
    # USDTWD prefixed schema → standard
    if "usdtwd_close" in df.columns:
        ren = {f"usdtwd_{c}": c for c in ("open", "high", "low", "close", "adj_close", "volume")}
        df = df.rename(columns=ren)
        if "date" in df.columns:
            df = df.set_index("date")
    # Ensure a DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        # maybe a 'Date'/'date' column
        for c in ("Date", "date", "trading_date"):
            if c in df.columns:
                df = df.set_index(c)
                break
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    df.index.name = "Date"
    keep = [c for c in STD_COLS if c in df.columns]
    return df[keep].sort_index()


def _normalize_yf(raw: pd.DataFrame) -> pd.DataFrame:
    """yfinance download df → standard Date-indexed OHLCV frame."""
    df = raw.copy()
    # yfinance may return MultiIndex columns for single ticker; flatten
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    ren = {
        "Open": "open", "High": "high", "Low": "low", "Close": "close",
        "Adj Close": "adj_close", "Volume": "volume",
    }
    df = df.rename(columns=ren)
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    df.index.name = "Date"
    keep = [c for c in STD_COLS if c in df.columns]
    return df[keep].sort_index()


def refresh_one(cat: str, stem: str, ticker: str, *, full: bool, dry_run: bool) -> dict:
    fp = SUPP / cat / f"{stem}_daily.parquet"
    existing = _existing_frame(fp)
    if existing is not None and len(existing) and not full:
        last = existing.index.max().date()
        start = (last + dt.timedelta(days=1)).isoformat()
    else:
        start = "2010-01-01"
        last = None

    today = dt.date.today()
    if last is not None and last >= today:
        return {"stem": stem, "ticker": ticker, "added": 0, "note": "already current"}

    if dry_run:
        return {"stem": stem, "ticker": ticker, "plan_start": start, "max_before": str(last)}

    import yfinance as yf
    try:
        raw = yf.download(ticker, start=start, auto_adjust=False, progress=False,
                          threads=False)
    except Exception as e:
        return {"stem": stem, "ticker": ticker, "error": f"download failed: {e}"}
    if raw is None or raw.empty:
        return {"stem": stem, "ticker": ticker, "added": 0, "note": "no new rows"}

    new = _normalize_yf(raw)
    if existing is not None and len(existing):
        combined = pd.concat([existing, new])
    else:
        combined = new
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()

    fp.parent.mkdir(parents=True, exist_ok=True)
    if fp.exists():
        shutil.copy(fp, fp.with_suffix(".parquet.bak"))
    combined.to_parquet(fp)
    return {
        "stem": stem, "ticker": ticker,
        "added": int(len(new)),
        "max_after": str(combined.index.max().date()),
        "total": int(len(combined)),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="yfinance refresh of SUPPLEMENT macro parquets")
    p.add_argument("--only", help="comma-separated stems (e.g. VIX,SPY,USDTWD)")
    p.add_argument("--full", action="store_true", help="re-fetch full history (from 2010)")
    p.add_argument("--dry-run", action="store_true", help="print plan only")
    args = p.parse_args(argv)

    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    targets = [(c, s, t) for (c, s), t in TICKERS.items() if not only or s in only]
    if not targets:
        print(f"no matching stems for --only {args.only}", file=sys.stderr)
        return 1

    print(f"fetch_macro: {len(targets)} symbols (full={args.full}, dry_run={args.dry_run})")
    ok = added = failed = 0
    for cat, stem, ticker in targets:
        info = refresh_one(cat, stem, ticker, full=args.full, dry_run=args.dry_run)
        if info.get("error"):
            failed += 1
            print(f"  ✗ {stem:12s} ({ticker}): {info['error']}")
        else:
            ok += 1
            added += info.get("added", 0)
            tail = info.get("note") or f"+{info.get('added',0)} → {info.get('max_after','?')}"
            print(f"  ✓ {stem:12s} ({ticker}): {tail}")
    print(f"fetch_macro done: {ok} ok / {failed} failed / {added} rows added")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
