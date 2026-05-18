"""Fetch the latest TEJ data and write it to RAW_SOURCES/TEJ資料/*.csv.

Why a custom script vs `zipline ingest -b tquant`:
  zipline-tej writes data to ~/.zipline (bcolz + sqlite), which is NOT
  compatible with the QUANTDATA Parquet lakehouse. We use the same tejapi
  SDK that zipline-tej uses internally, but write CSVs in the exact column
  order that qd_ingest.sources.tej.* expects (see EWPRCD_RENAME / etc.).

Required env vars:
  TEJAPI_KEY      your TEJ API token
  TEJAPI_BASE     https://api.tej.com.tw

Usage:
  .venv/bin/python scripts/fetch_tej.py --table all --start 20100101 --end today
  .venv/bin/python scripts/fetch_tej.py --table stock_daily --start 20260101
  .venv/bin/python scripts/fetch_tej.py --table all --append-since-silver
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAW = Path(os.environ.get("QUANTDATA_RAW", REPO.parent / "RAW_SOURCES")) / "TEJ資料"


# Maps logical table -> (TEJ dataset code, raw csv filename, columns in TEJ order)
# Column order MUST match what qd_ingest.sources.tej.*_RENAME expects.
TEJ_TABLES = {
    "stock_daily": {
        "dataset": "TWN/EWPRCD",
        "csv": "TWN_EWPRCD_股價.csv",
        "columns": [
            "coid", "mdate",
            "open_d", "high_d", "low_d", "close_d",
            "vol", "per", "outstanding_shares", "pbr_tej", "cash_div_yield",
            "open_adj", "high_adj", "low_adj", "close_adj",
        ],
        # Map TEJ field name -> Chinese header the ingester expects
        "header_zh": [
            "證券碼", "日期",
            "開盤價", "最高價", "最低價", "收盤價",
            "成交量(千股)", "交易所本益比", "流通股數(千股)", "交易所股價淨值比", "現金股利率",
            "開盤價-除權息", "最高價-除權息", "最低價-除權息", "收盤價-除權息",
        ],
        "date_col": "mdate",
    },
    "inst_stock": {
        "dataset": "TWN/EWTINST1",
        "csv": "TWN_EWTINST1_三大法人.csv",
        "columns": [
            "coid", "mdate",
            "qfii_net_b", "fund_net_b", "dealer_net_b", "ttl_net_b",
            "qfii_buy_v", "fund_buy_v", "qfii_sell_v", "fund_sell_v",
            "dealer_buy_v", "dealer_sell_v",
            "qfii_hold_v", "fund_hold_v", "dealer_hold_v",
            "qfii_hold_pct", "fund_hold_pct", "dealer_hold_pct",
        ],
        "header_zh": [
            "證券碼", "日期",
            "外資買賣超(千股)", "投信買賣超(千股)", "自營買賣超(千股)", "合計買賣超(千股)",
            "外資買進張數", "投信買進張數", "外資賣出張數", "投信賣出張數",
            "自營買進張數", "自營賣出張數",
            "外資總持股數(千股)", "投信總持股數(千股)", "自營總持股數(千股)",
            "外資總持股率(%)", "投信總持股率(%)", "自營總持股率(%)",
        ],
        "date_col": "mdate",
    },
    "margin": {
        "dataset": "TWN/EWGIN",
        "csv": "TWN_EWGIN_融資融券.csv",
        "columns": [
            "coid", "mdate",
            "mt_buy", "mt_sell", "sbl_buy", "sbl_sell",
            "mt_balance", "sbl_balance", "mt_balance_amt", "sbl_balance_amt",
            "mt_util", "sbl_util", "sbl_to_mt",
            "mt_maintenance", "sbl_maintenance", "account_maintenance",
        ],
        "header_zh": [
            "證券碼", "日期",
            "融資買進(張)", "融資賣出(張)", "融券買入(張)", "融券賣出(張)",
            "融資餘額(張)", "融券餘額(張)", "融資餘額(千元)", "融券餘額(千元)",
            "融資使用率", "融券使用率", "券資比",
            "融資維持率", "融券維持率", "整戶維持率",
        ],
        "date_col": "mdate",
    },
    # Fundamentals: ingest takes both 單季 (Q) and 累季 (YTD) versions.
    # TEJ EWIFINQ dataset switches on the `period_type` request parameter.
    "fundamentals_q": {
        "dataset": "TWN/EWIFINQ",
        "csv": "TWN_EWIFINQ_單季財報.csv",
        # Fundamentals have ~60 cols; we let TEJ return all and just rename the
        # date column. The ingester accepts the wide format with only the known
        # subset and ignores the rest.
        "columns": None,
        "header_zh": None,
        "date_col": "annd_s",     # 財報發布日 in TEJ
        "tej_kwargs": {"period_type": "Q"},
    },
    "fundamentals_ytd": {
        "dataset": "TWN/EWIFINQ",
        "csv": "TWN_EWIFINQ_累季財報.csv",
        "columns": None,
        "header_zh": None,
        "date_col": "annd_s",
        "tej_kwargs": {"period_type": "A"},  # accumulated
    },
}


def _check_env() -> None:
    if not os.environ.get("TEJAPI_KEY"):
        sys.exit(
            "ERROR: TEJAPI_KEY env var is required.\n"
            "  Get a key at https://api.tej.com.tw/trial.html\n"
            "  export TEJAPI_KEY=<your_key>\n"
            "  export TEJAPI_BASE=https://api.tej.com.tw"
        )
    os.environ.setdefault("TEJAPI_BASE", "https://api.tej.com.tw")


def _silver_max_date(table: str) -> dt.date | None:
    """Read the current silver max date for the given logical table (best-effort).

    Returns None if silver is empty or can't be read (no DuckDB lock contention here:
    we connect read-only to the catalog snapshot).
    """
    import shutil
    import tempfile

    catalog_src = REPO / "catalog" / "quant.duckdb"
    if not catalog_src.exists():
        return None
    # Copy to avoid lock contention with any open UI session
    tmp = Path(tempfile.mkdtemp()) / "snap.duckdb"
    try:
        shutil.copy(catalog_src, tmp)
        import duckdb
        con = duckdb.connect(str(tmp), read_only=True)
        con.execute(f"SET file_search_path='{REPO}'")
        view_map = {
            "stock_daily": ("tw_stock_bars", "trading_date"),
            "inst_stock":  ("tw_inst_stock_daily", "trading_date"),
            "margin":      ("tw_margin_daily", "trading_date"),
            "fundamentals_q":   ("fundamentals_q", "publish_date"),
            "fundamentals_ytd": ("fundamentals_q", "publish_date"),
        }
        if table not in view_map:
            return None
        view, col = view_map[table]
        row = con.execute(f"SELECT MAX({col}) FROM {view}").fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()
        except Exception:
            pass


def _fetch_table(table: str, start: str, end: str, *, paginate_chunk: int = 10000) -> "pd.DataFrame":  # noqa: F821
    """Pull one logical table from TEJ. Returns a DataFrame in the column order/headers
    that qd_ingest.sources.tej.* expects (so the CSV is a drop-in replacement)."""
    import pandas as pd
    import tejapi

    tejapi.ApiConfig.api_key = os.environ["TEJAPI_KEY"]
    tejapi.ApiConfig.api_base = os.environ["TEJAPI_BASE"]

    cfg = TEJ_TABLES[table]
    kwargs = dict(cfg.get("tej_kwargs") or {})
    print(f"[fetch] {table}: dataset={cfg['dataset']} {start}..{end} kwargs={kwargs}")

    df = tejapi.get(
        cfg["dataset"],
        mdate={"gte": start, "lte": end},
        paginate=True,
        chinese_column_name=True,
        **kwargs,
    )
    print(f"[fetch] {table}: got {len(df):,} rows")
    # If a strict column set is required, reorder + drop extras and apply Chinese headers
    if cfg["header_zh"]:
        missing = [c for c in cfg["header_zh"] if c not in df.columns]
        if missing:
            raise RuntimeError(f"TEJ response missing expected columns for {table}: {missing}")
        df = df[cfg["header_zh"]]
    return df


def _merge_and_write(table: str, new_df, *, mode: str = "merge") -> Path:
    """Write fetched CSV. mode='merge' (default) deduplicates with existing RAW CSV
    on (證券碼/coid, date)."""
    import pandas as pd

    cfg = TEJ_TABLES[table]
    out_fp = RAW / cfg["csv"]
    out_fp.parent.mkdir(parents=True, exist_ok=True)

    if mode == "overwrite" or not out_fp.exists():
        new_df.to_csv(out_fp, index=False, encoding="utf-8-sig")
        print(f"[write] {out_fp.name}: wrote {len(new_df):,} rows (overwrite)")
        return out_fp

    # merge: read existing, drop overlapping date+id rows, append
    existing = pd.read_csv(out_fp, dtype=str)
    key_cols = [c for c in existing.columns if c in ("證券碼", "日期", "財報發布日")]
    if len(key_cols) < 2:
        # Fallback: just append; ingester is idempotent per-year anyway
        merged = pd.concat([existing, new_df.astype(str)], ignore_index=True)
        merged = merged.drop_duplicates()
    else:
        new_str = new_df.astype(str)
        # Remove existing rows that the new fetch will replace
        mask = ~existing.set_index(key_cols).index.isin(new_str.set_index(key_cols).index)
        kept = existing[mask]
        merged = pd.concat([kept, new_str], ignore_index=True)
        merged = merged.drop_duplicates(subset=key_cols, keep="last")

    merged.to_csv(out_fp, index=False, encoding="utf-8-sig")
    print(f"[write] {out_fp.name}: merged -> {len(merged):,} rows "
          f"(was {len(existing):,}, added {len(merged) - len(existing):,})")
    return out_fp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table",
        choices=list(TEJ_TABLES) + ["all"],
        default="all",
        help="Which logical table to fetch (default: all)",
    )
    parser.add_argument("--start", default="20100101", help="YYYYMMDD inclusive (default 20100101)")
    parser.add_argument(
        "--end",
        default=dt.date.today().strftime("%Y%m%d"),
        help="YYYYMMDD inclusive (default today)",
    )
    parser.add_argument(
        "--append-since-silver",
        action="store_true",
        help="Override --start to (silver max date + 1) per table. Useful for daily refresh.",
    )
    parser.add_argument(
        "--mode",
        choices=["merge", "overwrite"],
        default="merge",
        help="merge (default): dedupe + append to existing RAW CSV; overwrite: replace.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be fetched without calling TEJ.",
    )
    args = parser.parse_args()

    tables = list(TEJ_TABLES) if args.table == "all" else [args.table]

    if args.dry_run:
        print(f"[dry-run] would fetch {tables} from {args.start} to {args.end}, mode={args.mode}")
        return

    _check_env()

    for t in tables:
        start = args.start
        if args.append_since_silver:
            mx = _silver_max_date(t)
            if mx is not None:
                start = (mx + dt.timedelta(days=1)).strftime("%Y%m%d")
                print(f"[plan] {t}: silver max={mx}, starting fetch at {start}")
            else:
                print(f"[plan] {t}: silver max unknown, using --start={start}")
        if start > args.end:
            print(f"[skip] {t}: start={start} > end={args.end}, already up to date")
            continue
        df = _fetch_table(t, start, args.end)
        if len(df) == 0:
            print(f"[skip] {t}: empty TEJ response for {start}..{args.end}")
            continue
        _merge_and_write(t, df, mode=args.mode)

    print("\n[done] next steps:")
    print("  .venv/bin/python -m qd_ingest.cli tej-stock        --csv ../RAW_SOURCES/TEJ資料/TWN_EWPRCD_股價.csv")
    print("  .venv/bin/python -m qd_ingest.cli tej-inst-stock   --csv ../RAW_SOURCES/TEJ資料/TWN_EWTINST1_三大法人.csv")
    print("  .venv/bin/python -m qd_ingest.cli tej-margin       --csv ../RAW_SOURCES/TEJ資料/TWN_EWGIN_融資融券.csv")
    print("  .venv/bin/python -m qd_ingest.cli tej-fundamentals \\")
    print("      --quarterly ../RAW_SOURCES/TEJ資料/TWN_EWIFINQ_單季財報.csv \\")
    print("      --ytd       ../RAW_SOURCES/TEJ資料/TWN_EWIFINQ_累季財報.csv")
    print("  .venv/bin/python -m qd_ingest.cli build-catalog")


if __name__ == "__main__":
    main()
