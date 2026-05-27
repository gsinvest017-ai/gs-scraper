"""fetch_finmind.py — by-date incremental FinMind refresh → bronze snapshot.

Runs under the **FinMind crawler's venv** (it needs that repo's client deps:
httpx / python-dotenv). Instead of the catalog's `per_stock` mode (~3,088 calls
× 2 datasets ≈ 4h), this fetches each missing trading-day window via the FinMind
**by-date bulk** endpoint (no `data_id` → all stocks in one call), so the daily
increment is a handful of calls (seconds). It then snapshots the crawler's live
sqlite to `QUANTDATA/bronze/finmind/finmind_<DATE>.sqlite` (+ sha256) so
`restore_finmind_views.py` (daily_refresh step 3.5) picks it up automatically.

Universe: incremental rows are filtered to the same twse/tpex/emerging stock set
the backfill used (`taiwan_stock_info`), so the by-date bulk (which also returns
tens of thousands of warrants) doesn't pollute the established table.

Usage (from QUANTDATA root, with the FinMind venv python):
  FINMIND_REPO=/path/to/FINMIND資料集 \
    /path/to/FINMIND資料集/.venv/bin/python scripts/fetch_finmind.py
  ... --dry-run            # print plan, no API calls / no writes
  ... --only TaiwanStockPrice
  ... --full               # re-fetch from earliest (slow; prefer the crawler)
  ... --keep 5             # bronze snapshots to retain (default 5)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import os
import shutil
import sqlite3
import sys
from pathlib import Path

QUANT_REPO = Path(__file__).resolve().parents[1]
FINMIND_REPO = Path(os.environ.get("FINMIND_REPO", "/home/kevin/gs-scraper/FINMIND資料集"))
BRONZE = QUANT_REPO / "bronze" / "finmind"

# dataset name -> live-sqlite table (for max-date probing)
PRICE_DATASETS = {
    "TaiwanStockPrice": "taiwan_stock_price",
    "TaiwanStockPriceAdj": "taiwan_stock_price_adj",
}
# global (no date) datasets refreshed each run to keep the universe current
INFO_DATASETS = ["TaiwanStockInfo"]
UNIVERSE_TYPES = ("twse", "tpex", "emerging")


def _today() -> dt.date:
    return dt.date.today()


def _live_db() -> Path:
    return FINMIND_REPO / "data" / "finmind.sqlite"


def _max_date(db: Path, table: str) -> str | None:
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if table not in names:
            return None
        row = con.execute(f'SELECT max(date) FROM "{table}"').fetchone()
        return row[0] if row and row[0] else None
    finally:
        con.close()


def _build_plan(only: set[str] | None, full: bool) -> list[dict]:
    live = _live_db()
    today = _today().isoformat()
    plan: list[dict] = []
    for ds in INFO_DATASETS:
        if only and ds not in only:
            continue
        plan.append({"dataset": ds, "kind": "global", "start": None, "end": None})
    for ds, table in PRICE_DATASETS.items():
        if only and ds not in only:
            continue
        mx = _max_date(live, table)
        start = "2000-01-01" if (full or not mx) else mx  # re-fetch max day (dedup via OR REPLACE)
        plan.append({"dataset": ds, "kind": "by_date", "start": start, "end": today,
                     "max_before": mx})
    return plan


def _day_range(start: str, end: str) -> list[str]:
    """All calendar days [start..end] inclusive (ISO strings). Empty days (weekends/
    holidays / unpublished) simply return 0 rows from the API and are skipped."""
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    out, cur = [], s
    while cur <= e:
        out.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return out


async def _run_fetch(plan: list[dict]) -> dict:
    sys.path.insert(0, str(FINMIND_REPO / "src"))
    from finmind_dump.client import FinMindClient, FinMindConfig
    from finmind_dump.storage import Storage
    from finmind_dump.catalog import by_name

    cfg = FinMindConfig.from_env(FINMIND_REPO / ".env")
    storage = Storage(_live_db())
    results: dict[str, dict] = {}

    async with FinMindClient(cfg) as client:
        # global datasets first (refreshes taiwan_stock_info → universe filter)
        for item in [p for p in plan if p["kind"] == "global"]:
            name = item["dataset"]
            rows = await client.fetch(name)
            n = storage.upsert(by_name(name), rows)
            results[name] = {"fetched": len(rows), "upserted": n}

        universe = set(storage.list_stock_ids(UNIVERSE_TYPES))
        # by-date bulk returns ONE date per call (end_date is ignored when no
        # data_id), so iterate day-by-day across the window.
        for item in [p for p in plan if p["kind"] == "by_date"]:
            name = item["dataset"]
            ds = by_name(name)
            fetched = upserted = 0
            days_landed: list[str] = []
            for day in _day_range(item["start"], item["end"]):
                rows = await client.fetch(name, start_date=day, end_date=day)
                if not rows:
                    continue
                kept = [r for r in rows if r.get("stock_id") in universe] if universe else rows
                if not kept:
                    continue
                fetched += len(rows)
                upserted += storage.upsert(ds, kept)
                days_landed.append(day)
            results[name] = {
                "fetched": fetched, "upserted": upserted, "days_landed": days_landed,
                "start": item["start"], "end": item["end"], "max_before": item.get("max_before"),
            }
    return results


def _checkpoint(db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


def _sha256(fp: Path) -> str:
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_and_gc(keep: int) -> Path:
    live = _live_db()
    _checkpoint(live)  # fold WAL into the .sqlite so the copy is self-contained
    dst = BRONZE / f"finmind_{_today().isoformat()}.sqlite"
    BRONZE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(live, dst)
    dst.with_suffix(".sqlite.sha256").write_text(f"{_sha256(dst)}  {dst.name}\n")
    # GC: keep newest N (lexicographic == chronological for finmind_<ISO-date>)
    snaps = sorted(BRONZE.glob("finmind_*.sqlite"))
    for old in snaps[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
        old.with_suffix(".sqlite.sha256").unlink(missing_ok=True)
        for ext in ("-wal", "-shm"):
            Path(str(old) + ext).unlink(missing_ok=True)
    return dst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="By-date incremental FinMind refresh → bronze snapshot")
    ap.add_argument("--only", help="comma-separated dataset names (e.g. TaiwanStockPrice)")
    ap.add_argument("--full", action="store_true", help="re-fetch from earliest (slow)")
    ap.add_argument("--keep", type=int, default=5, help="bronze snapshots to retain (default 5)")
    ap.add_argument("--dry-run", action="store_true", help="print plan only; no API calls, no writes")
    args = ap.parse_args(argv)

    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    if not _live_db().exists():
        print(f"ERROR: FinMind live db not found: {_live_db()} (set FINMIND_REPO?)", file=sys.stderr)
        return 1

    plan = _build_plan(only, args.full)
    print(f"fetch_finmind: repo={FINMIND_REPO} today={_today().isoformat()} dry_run={args.dry_run}")
    for p in plan:
        if p["kind"] == "by_date":
            print(f"  {p['dataset']:22s} by_date {p['start']}..{p['end']} (max_before={p['max_before']})")
        else:
            print(f"  {p['dataset']:22s} global (full refresh)")
    if args.dry_run:
        print("dry-run: no API calls, no snapshot written.")
        return 0

    results = asyncio.run(_run_fetch(plan))
    for name, r in results.items():
        if "days_landed" in r:
            ld = r["days_landed"]
            span = f"{ld[0]}..{ld[-1]}" if ld else "(no new days)"
            print(f"  ✓ {name:22s} upserted={r['upserted']} days={len(ld)} [{span}]")
        else:
            print(f"  ✓ {name:22s} fetched={r['fetched']} upserted={r['upserted']}")

    dst = _snapshot_and_gc(args.keep)
    print(f"snapshot: {dst}  ({dst.stat().st_size/1e9:.2f} GB)  + .sha256")
    print(f"retained: {sorted(p.name for p in BRONZE.glob('finmind_*.sqlite'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
