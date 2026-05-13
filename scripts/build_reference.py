"""Build reference/*.parquet from seeds.

- reference/symbol_map.parquet      <- reference/seeds/symbol_map.yaml
- reference/contract_specs.parquet  <- reference/seeds/contract_specs.yaml
- reference/calendar_xtai.parquet   <- derived from existing TEJ stock CSV (or fallback)

Idempotent: rerun overwrites.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "reference" / "seeds"
OUT = ROOT / "reference"

CONTRACT_SPECS_COLS = [
    "product_id", "exchange", "multiplier", "tick_size", "tick_value", "currency",
    "session_open_local", "session_close_local", "has_afterhours",
    "ah_open_local", "ah_close_local", "settle_method", "notes",
]

SYMBOL_MAP_COLS = [
    "canonical_symbol", "name_zh", "name_en", "asset_class", "exchange", "currency",
    "tej_id", "taifex_code", "yahoo_ticker", "histdata_ticker", "bloomberg_ticker",
    "underlying_symbol", "contract_spec_id", "active_from", "active_to",
]


def build_contract_specs() -> None:
    rows = yaml.safe_load((SEEDS / "contract_specs.yaml").read_text(encoding="utf-8"))
    df = pd.DataFrame(rows)
    for c in CONTRACT_SPECS_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[CONTRACT_SPECS_COLS]
    out = OUT / "contract_specs.parquet"
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), out, compression="zstd")
    print(f"  contract_specs.parquet: {len(df)} rows -> {out}")


def build_symbol_map() -> None:
    rows = yaml.safe_load((SEEDS / "symbol_map.yaml").read_text(encoding="utf-8"))
    df = pd.DataFrame(rows)
    # canonical_symbol must be VARCHAR even when YAML emits '0050' as string already
    df["canonical_symbol"] = df["canonical_symbol"].astype(str)
    for c in SYMBOL_MAP_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[SYMBOL_MAP_COLS]
    out = OUT / "symbol_map.parquet"
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), out, compression="zstd")
    print(f"  symbol_map.parquet: {len(df)} rows -> {out}")


def build_calendar_xtai() -> None:
    """Derive XTAI trading dates from existing TEJ stock CSV if present;
    otherwise fall back to MXF cleaned daily parquet."""
    dates: pd.Series | None = None
    tej_csv = ROOT / "TEJ資料" / "TWN_EWPRCD_股價.csv"
    mxf_pq = ROOT / "MXF_1d_clean_all.parquet" / "MXF_1d_clean_all.parquet"

    if tej_csv.exists():
        # ~6M rows; read date col only
        d = pd.read_csv(tej_csv, usecols=["日期"], dtype={"日期": "int64"})
        dates = pd.to_datetime(d["日期"].astype(str), format="%Y%m%d").drop_duplicates().sort_values()
        src = "tej_ewprcd"
    elif mxf_pq.exists():
        d = pq.read_table(mxf_pq, columns=["trading_date"]).to_pandas()
        dates = pd.to_datetime(d["trading_date"]).drop_duplicates().sort_values()
        src = "mxf_1d"
    else:
        print("  WARN: no source found for calendar_xtai; skipping")
        return

    cal = pd.DataFrame({
        "trading_date": dates.dt.date,
        "is_trading": True,
        "year": dates.dt.year,
        "month": dates.dt.month,
        "weekday": dates.dt.weekday,
        "source": src,
    })
    out = OUT / "calendar_xtai.parquet"
    pq.write_table(pa.Table.from_pandas(cal, preserve_index=False), out, compression="zstd")
    print(f"  calendar_xtai.parquet: {len(cal)} rows ({cal['trading_date'].min()}..{cal['trading_date'].max()}) -> {out}")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    print("Building reference parquets:")
    build_contract_specs()
    build_symbol_map()
    build_calendar_xtai()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
