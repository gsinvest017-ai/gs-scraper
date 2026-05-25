"""gap_report.py — survey every monitored view in the DuckDB catalog,
compute lag vs expected freshness, and emit a dashboard.

Usage:
  python scripts/gap_report.py                     # terminal text (default)
  python scripts/gap_report.py --format json       # machine-readable
  python scripts/gap_report.py --format html       # writes docs/gap_dashboard.html (+ mirror docs-site/)
  python scripts/gap_report.py --format all        # text + json + html (+ mirror docs-site/)

Lag severity by category:
  daily-trading: 0-1 trading days = OK, 2-5 = WARN, >5 = STALE
  monthly:       0-15d = OK, 15-45 = WARN, >45 = STALE   (revenue lags ~10d)
  quarterly:     0-60d = OK, 60-120 = WARN, >120 = STALE
  event:         look at MIN(ex_date) >= today instead — empty future = STALE
  derived:       inherits from upstream (we only flag; lag is informational)

The script uses a temp-copy of the catalog so it can run while DuckDB UI
holds a lock on catalog/quant.duckdb.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog" / "quant.duckdb"


# --- Dataset registry ------------------------------------------------------
#
# Each row describes ONE monitored view. Adding a new dataset = adding a row.
#
# fields:
#   view       — DuckDB view name (under main schema)
#   date_col   — column carrying the freshness signal
#   category   — daily-trading / monthly / quarterly / event / derived
#   fetch_cmd  — suggested action when stale (shown in report)
#   description— short human label
#   tier       — P0 (must-have) / P1 (nice-to-have) / P2 (low priority)

@dataclass
class Dataset:
    view: str
    date_col: str
    category: str
    fetch_cmd: str
    description: str
    tier: str = "P1"


DATASETS = [
    # --- TEJ stock daily price + flows (P0, CSV-backed via fetch_tej.py) ---
    Dataset("tw_stock_bars",           "trading_date", "daily-trading",
            "fetch_tej.py --table stock_daily --append-since-silver",
            "TEJ 個股日 K (OHLCV + 除權息調整)", "P0"),
    Dataset("tw_inst_stock_daily",     "trading_date", "daily-trading",
            "fetch_tej.py --table inst_stock --append-since-silver",
            "個股三大法人買賣超", "P0"),
    Dataset("tw_margin_daily",         "trading_date", "daily-trading",
            "fetch_tej.py --table margin --append-since-silver",
            "融資融券餘額", "P0"),

    # --- TAIFEX / TEJ 期貨 (P0, direct silver-parquet) ---
    Dataset("tw_inst_futures_daily",   "trading_date", "daily-trading",
            "(TAIFEX scraper — currently no auto-refresh)",
            "期貨三大法人（TAIFEX 直抓）", "P0"),
    Dataset("tw_inst_futures_full_daily", "trading_date", "daily-trading",
            "fetch_tej.py --table inst_futures_full --append-since-silver",
            "期貨三大法人完整版（含選擇權）", "P1"),
    Dataset("tw_futures_large_trader_daily", "trading_date", "daily-trading",
            "fetch_tej.py --table futures_large_trader --append-since-silver",
            "期貨大額交易人未沖銷部位", "P0"),
    Dataset("bars_1d",                 "trading_date", "daily-trading",
            "fetch_tej.py --table futures_daily --append-since-silver",
            "所有期貨日 K（含 MXF/TXF/個股期）", "P0"),
    Dataset("tx_continuous_d",         "trading_date", "daily-trading",
            "(TX 連續期 — 來自 RAW_SOURCES/日k 期貨tquant lab/，無自動 refresh)",
            "TX 連續期", "P1"),
    Dataset("mtx_continuous_d",        "trading_date", "daily-trading",
            "(MTX 連續期 — 來自 RAW_SOURCES/日k 期貨tquant lab/，無自動 refresh)",
            "MTX 連續期", "P1"),
    Dataset("stock_futures_continuous_d", "trading_date", "daily-trading",
            "(個股期連續 — 來自 RAW_SOURCES/股票期貨/，無自動 refresh)",
            "個股期連續近月", "P2"),

    # --- 1-minute bars (manual MXF parquet ingest) ---
    Dataset("bars_1m",                 "trading_date", "daily-trading",
            "(MXF 1m — 來自 RAW_SOURCES/MXF_1m_clean_all/，需手動更新)",
            "1 分鐘 K 線 (MXF)", "P2"),

    # --- TEJ chip / attrs / dividends (P1/P2) ---
    Dataset("tw_chip_dist_daily",      "trading_date", "daily-trading",
            "fetch_tej.py --table chip_dist --append-since-silver",
            "TEJ 集保戶股權分散表", "P1"),
    Dataset("tw_stock_trading_attrs_daily", "trading_date", "daily-trading",
            "fetch_tej.py --table stock_trading_attrs --append-since-silver",
            "個股交易屬性（注意/處置/全額交割）", "P2"),

    # --- 月營收 / 季報 / 會計 ---
    Dataset("revenue_monthly",         "fiscal_month", "monthly",
            "fetch_tej.py --table revenue_monthly --append-since-silver",
            "月營收（每月 10 日前後公告）", "P0"),
    Dataset("fundamentals_q",          "publish_date", "quarterly",
            "(TWN_EWIFINQ CSV — 來自 TEJ 訂閱包，無 API auto-refresh)",
            "季報（單季 + 累季財報）", "P1"),
    Dataset("accounting_raw",          "fiscal_month", "quarterly",
            "fetch_tej.py --table accounting_raw --append-since-silver",
            "原始會計簽證科目（AINVFINB 118 欄）", "P2"),

    # --- 衍生 (downstream of stock_bars etc.) ---
    Dataset("stock_factor_daily",      "trading_date", "derived",
            "qd-ingest rebuild-stock-factors  (依 tw_stock_bars 衍生)",
            "個股技術因子（漲跌幅、RSI、ADX 等）", "P1"),
    Dataset("macro_daily",             "trading_date", "daily-trading",
            "yfinance scraper for VIX/USDTWD/...  (no auto job yet)",
            "總體變數日資料 (VIX, USDTWD, ...)", "P1"),
    Dataset("txo_daily_features",      "date",         "daily-trading",
            "(TXO daily features — 來自選擇權日盤逐筆，無 auto-refresh)",
            "選擇權 TXO 日特徵", "P2"),
    Dataset("cross_market_features",   "date",         "derived",
            "(cross-market features — derived from VIX/SPY/macro; rebuild after macro_daily refresh)",
            "跨市場特徵（VIX-vol、SPY-corr 等）", "P2"),
    Dataset("tw_inst_market_daily",    "trading_date", "daily-trading",
            "(市場層級三大法人 — 上游應由 tw_inst_stock_daily aggregated 而來)",
            "市場層級三大法人彙總", "P2"),

    # --- FinMind bronze snapshot (one-shot dump 2026-05-18, not auto-refreshed) ---
    Dataset("finmind_stock_price_norm",     "trading_date", "snapshot",
            "(re-sync bronze/finmind/finmind_*.sqlite when FinMind crawler produces a new snapshot)",
            "FinMind 個股日 K (canonical 命名 + 2000-2026 完整)", "P1"),
    Dataset("finmind_stock_price_adj_norm", "trading_date", "snapshot",
            "(re-sync bronze/finmind/finmind_*.sqlite for fresh adj series)",
            "FinMind 還原權息日 K (TEJ adj_close 對帳用)", "P2"),
    Dataset("qc_stock_price_diff",          "trading_date", "derived",
            "(rebuild after either tw_stock_bars or finmind_stock_price_norm updates)",
            "TEJ vs FinMind 對帳 view（2010+ 重疊段）", "P2"),

    # --- Event-driven (future-dated rows; check MAX vs today) ---
    Dataset("cash_dividend_events",    "ex_date",      "event",
            "fetch_tej.py --table cash_dividend --append-since-silver",
            "現金股利除息事件（forward-looking）", "P1"),
    Dataset("tw_stock_futures_corp_actions", "adjust_date", "event",
            "fetch_tej.py --table stock_futures_corp_actions --append-since-silver",
            "個股期調整事件", "P2"),
]


# --- Severity tiers --------------------------------------------------------

SEVERITY = {
    "OK":    {"emoji": "✅", "ansi": "\033[32m", "weight": 0},
    "WARN":  {"emoji": "⚠️ ", "ansi": "\033[33m", "weight": 1},
    "STALE": {"emoji": "🔴", "ansi": "\033[31m", "weight": 2},
    "EMPTY": {"emoji": "❓", "ansi": "\033[35m", "weight": 3},
    "INFO":  {"emoji": "ℹ️ ", "ansi": "\033[36m", "weight": 0},
}
ANSI_RESET = "\033[0m"


def classify(lag_days: int | None, category: str) -> str:
    if lag_days is None:
        return "EMPTY"
    if category == "event":
        # Events look forward. If MAX(date) >= today, we have upcoming
        # events — OK. If MAX(date) < today, we've consumed all known
        # events and the table is stale.
        if lag_days <= 0:
            return "OK"
        return "WARN" if lag_days < 30 else "STALE"
    if category == "monthly":
        if lag_days <= 15: return "OK"
        if lag_days <= 45: return "WARN"
        return "STALE"
    if category == "quarterly":
        if lag_days <= 60:  return "OK"
        if lag_days <= 120: return "WARN"
        return "STALE"
    if category == "derived":
        # Downstream tables — we just flag, the action is "rebuild".
        if lag_days is None or lag_days <= 1: return "OK"
        return "INFO"
    if category == "snapshot":
        # Bronze snapshot — never expected to be "fresh". If it has data,
        # surface as INFO (weight 0, won't trigger alerts); empty → EMPTY.
        return "INFO" if lag_days is not None else "EMPTY"
    # daily-trading
    if lag_days <= 1: return "OK"
    if lag_days <= 5: return "WARN"
    return "STALE"


# --- Probe -----------------------------------------------------------------

def probe(catalog_path: Path) -> list[dict]:
    """Snapshot catalog, then query MAX(date_col) for each dataset."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="gap_report_"))
    snap = tmp_dir / "snap.duckdb"
    shutil.copy(catalog_path, snap)
    today = dt.date.today()
    try:
        con = duckdb.connect(str(snap), read_only=True)
        con.execute(f"SET file_search_path='{REPO}'")
        rows = []
        for d in DATASETS:
            try:
                r = con.execute(
                    f"SELECT MAX({d.date_col}), COUNT(*) FROM main.{d.view}"
                ).fetchone()
                max_date, n_rows = r
                if max_date is None:
                    severity = "EMPTY"
                    lag = None
                else:
                    if hasattr(max_date, "date"):
                        max_date = max_date.date()
                    lag = (today - max_date).days
                    severity = classify(lag, d.category)
                rows.append({
                    "view": d.view,
                    "description": d.description,
                    "category": d.category,
                    "tier": d.tier,
                    "date_col": d.date_col,
                    "max_date": str(max_date) if max_date else None,
                    "row_count": n_rows,
                    "lag_days": lag,
                    "severity": severity,
                    "fetch_cmd": d.fetch_cmd,
                })
            except Exception as e:
                rows.append({
                    "view": d.view, "description": d.description,
                    "category": d.category, "tier": d.tier,
                    "date_col": d.date_col, "max_date": None,
                    "row_count": None, "lag_days": None,
                    "severity": "EMPTY", "fetch_cmd": d.fetch_cmd,
                    "error": str(e),
                })
        return rows
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --- Text renderer ---------------------------------------------------------

def render_text(rows: list[dict], today: dt.date, use_color: bool = True) -> str:
    out = []
    out.append(f"QUANTDATA gap report — generated {today.isoformat()}")
    out.append("=" * 100)
    out.append(f"{'view':<32} {'tier':<4} {'max_date':<11} {'lag':>5}  status  action")
    out.append("-" * 100)

    # Sort: severity desc, then tier (P0 first), then lag desc
    sev_order = {"STALE": 0, "WARN": 1, "EMPTY": 2, "INFO": 3, "OK": 4}
    rows_sorted = sorted(rows, key=lambda r: (
        sev_order.get(r["severity"], 99),
        r["tier"],
        -(r["lag_days"] or 0),
    ))

    for r in rows_sorted:
        sev = r["severity"]
        meta = SEVERITY[sev]
        lag_str = f"{r['lag_days']}d" if r["lag_days"] is not None else "—"
        prefix = meta["ansi"] if use_color else ""
        suffix = ANSI_RESET if use_color else ""
        status = f"{meta['emoji']} {sev}"
        action = r["fetch_cmd"] if sev not in ("OK",) else "—"
        out.append(
            f"{prefix}{r['view']:<32} {r['tier']:<4} {r['max_date'] or 'EMPTY':<11} "
            f"{lag_str:>5}  {status:<10}  {action}{suffix}"
        )

    # Summary
    out.append("-" * 100)
    counts = {sev: 0 for sev in SEVERITY}
    for r in rows: counts[r["severity"]] += 1
    out.append(
        f"summary: ✅ OK={counts['OK']}  ⚠️  WARN={counts['WARN']}  "
        f"🔴 STALE={counts['STALE']}  ❓ EMPTY={counts['EMPTY']}  ℹ️  INFO={counts['INFO']}"
    )
    return "\n".join(out)


# --- HTML renderer ---------------------------------------------------------

HTML_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>QUANTDATA Gap Dashboard</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", "PingFang TC", sans-serif;
          margin: 24px; color: #1f2937; background: #f9fafb; }}
  h1 {{ margin: 0 0 6px 0; font-size: 22px; }}
  .subtitle {{ color: #6b7280; margin-bottom: 18px; font-size: 13px; }}
  .summary {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 22px; }}
  .pill {{ padding: 10px 18px; border-radius: 10px; font-weight: 600; min-width: 90px; text-align: center; }}
  .pill.OK    {{ background: #d1fae5; color: #065f46; }}
  .pill.WARN  {{ background: #fef3c7; color: #92400e; }}
  .pill.STALE {{ background: #fee2e2; color: #991b1b; }}
  .pill.EMPTY {{ background: #ede9fe; color: #5b21b6; }}
  .pill.INFO  {{ background: #dbeafe; color: #1e40af; }}
  table {{ width: 100%; border-collapse: collapse; background: white;
           box-shadow: 0 1px 2px rgba(0,0,0,0.05); border-radius: 8px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e5e7eb; font-size: 13px; }}
  th {{ background: #f3f4f6; font-weight: 600; color: #374151; }}
  tr:last-child td {{ border-bottom: none; }}
  tr.OK    {{ background: #ffffff; }}
  tr.WARN  {{ background: #fffbeb; }}
  tr.STALE {{ background: #fef2f2; }}
  tr.EMPTY {{ background: #faf5ff; }}
  tr.INFO  {{ background: #eff6ff; }}
  .lag {{ font-variant-numeric: tabular-nums; text-align: right; }}
  .pct {{ font-variant-numeric: tabular-nums; text-align: right; font-weight: 600; }}
  .bar {{ position: relative; display: inline-block; height: 10px; background: #f3f4f6; border-radius: 4px; vertical-align: middle; border: 1px solid #e5e7eb; }}
  .bar > span {{ position: absolute; left: 0; top: 0; height: 100%; border-radius: 4px; }}
  .bar > span.OK    {{ background: #10b981; }}
  .bar > span.WARN  {{ background: #f59e0b; }}
  .bar > span.STALE {{ background: #ef4444; }}
  .bar > span.INFO  {{ background: #3b82f6; }}
  .bar > span.EMPTY {{ background: #9ca3af; }}
  code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 12px;
          font-family: "JetBrains Mono", "Menlo", monospace; }}
  .tier-P0 {{ font-weight: 700; }}
  .tier-P2 {{ opacity: 0.7; }}
  .legend {{ font-size: 12px; color: #6b7280; margin-top: 16px; }}
</style>
</head>
<body>
<h1>📊 QUANTDATA Gap Dashboard</h1>
<div class="subtitle">Generated {generated_at} · catalog: <code>catalog/quant.duckdb</code> · {total} datasets monitored</div>

<div class="summary">
  <div class="pill STALE">🔴 STALE<br><b>{count_STALE}</b></div>
  <div class="pill WARN">⚠️ WARN<br><b>{count_WARN}</b></div>
  <div class="pill EMPTY">❓ EMPTY<br><b>{count_EMPTY}</b></div>
  <div class="pill INFO">ℹ️ INFO<br><b>{count_INFO}</b></div>
  <div class="pill OK">✅ OK<br><b>{count_OK}</b></div>
</div>

<table>
<thead>
<tr>
  <th>Status</th>
  <th>Tier</th>
  <th>View</th>
  <th>Description</th>
  <th>Category</th>
  <th>Max date</th>
  <th class="lag">Lag</th>
  <th class="pct">完整度</th>
  <th>Completeness</th>
  <th>Suggested action</th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>

<div class="legend">
  預設排序：完整度 (Completeness) 從高到低；同分時 tier P0 在上。
  Completeness = clamp(1 − lag_days / 90, 0, 1) × 100% — 涵蓋未來日期 (negative lag) 視為 100%，無資料 (EMPTY) 視為 0%。
  Bar 視覺：填滿 = 完整。
  Severity rules — daily-trading: 0-1d=OK / 2-5d=WARN / &gt;5d=STALE.
  Monthly: 0-15d=OK / 15-45d=WARN / &gt;45d=STALE.
  Quarterly: 0-60d=OK / 60-120d=WARN / &gt;120d=STALE.
  Event (forward-looking): MAX&lt;today = WARN/STALE depending on age.
  Derived tables (e.g. stock_factor_daily) inherit from upstream — flagged INFO only.
</div>
</body>
</html>
"""


_COMPLETENESS_CAP_DAYS = 90  # lag >= cap → 0% complete; lag <= 0 → 100%


def _completeness(lag_days: int | None) -> float | None:
    """Map lag_days to a 0.0–1.0 completeness score.

    Conventions:
      lag is None (EMPTY view, no rows)  → 0.0  (sorts to bottom)
      lag <= 0                            → 1.0  (data covers up to / past today)
      0 < lag < cap                       → linear interpolation
      lag >= cap                          → 0.0
    """
    if lag_days is None:
        return 0.0
    if lag_days <= 0:
        return 1.0
    if lag_days >= _COMPLETENESS_CAP_DAYS:
        return 0.0
    return 1.0 - (lag_days / _COMPLETENESS_CAP_DAYS)


def render_html(rows: list[dict], today: dt.date) -> str:
    # Default sort: completeness DESC (most complete on top), then tier asc
    # (P0 before P1/P2 at same completeness), then view name asc for stable order.
    rows_sorted = sorted(rows, key=lambda r: (
        -_completeness(r["lag_days"]),       # highest completeness first
        r["tier"],                           # P0 < P1 < P2 lexicographically
        r["view"],
    ))

    # Bar visual: 'completeness fill' — bar fully filled = 100% complete,
    # empty bar = 0% complete (severely stale or no data).
    BAR_WIDTH = 180
    def bar_html(r):
        c = _completeness(r["lag_days"])
        # Choose fill color: prefer severity, but EMPTY needs its own grey shade.
        fill_class = r["severity"] if r["severity"] in ("OK", "WARN", "STALE", "INFO") else "EMPTY"
        fill_px = int(round(c * BAR_WIDTH))
        return (
            f'<span class="bar" style="width:{BAR_WIDTH}px">'
            f'<span class="{fill_class}" style="width:{fill_px}px"></span>'
            f'</span>'
        )

    counts = {sev: 0 for sev in SEVERITY}
    for r in rows: counts[r["severity"]] += 1

    rows_html = []
    for r in rows_sorted:
        meta = SEVERITY[r["severity"]]
        lag_str = f"{r['lag_days']}d" if r["lag_days"] is not None else "—"
        c = _completeness(r["lag_days"])
        pct_str = f"{c * 100:.0f}%"
        action_html = f'<code>{r["fetch_cmd"]}</code>' if r["severity"] != "OK" else ""
        rows_html.append(
            f'<tr class="{r["severity"]}">'
            f'<td>{meta["emoji"]} {r["severity"]}</td>'
            f'<td class="tier-{r["tier"]}">{r["tier"]}</td>'
            f'<td><code>{r["view"]}</code></td>'
            f'<td>{r["description"]}</td>'
            f'<td>{r["category"]}</td>'
            f'<td>{r["max_date"] or "—"}</td>'
            f'<td class="lag">{lag_str}</td>'
            f'<td class="pct">{pct_str}</td>'
            f'<td>{bar_html(r)}</td>'
            f'<td>{action_html}</td>'
            f'</tr>'
        )

    return HTML_TEMPLATE.format(
        generated_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total=len(rows),
        rows_html="\n".join(rows_html),
        **{f"count_{k}": v for k, v in counts.items()},
    )


# --- Main ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--format", choices=["text", "json", "html", "all"],
                        default="text")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI color in text output")
    parser.add_argument("--out-html", default=str(REPO / "docs" / "gap_dashboard.html"),
                        help="HTML output path (used by --format html / all)")
    parser.add_argument("--out-html-mirror", default=str(REPO / "docs-site" / "gap_dashboard.html"),
                        help="Secondary HTML mirror — copied into docs-site so MkDocs publishes it"
                             " at https://<pages-url>/gap_dashboard.html. Set to empty string to skip.")
    parser.add_argument("--out-json", default=str(REPO / "meta" / "audit" / "gap_report.json"),
                        help="JSON output path (used by --format json / all)")
    parser.add_argument("--catalog", default=str(CATALOG),
                        help="Override catalog path")
    args = parser.parse_args()

    catalog_path = Path(args.catalog)
    if not catalog_path.exists():
        print(f"ERROR: catalog not found at {catalog_path}", file=sys.stderr)
        return 1

    rows = probe(catalog_path)
    today = dt.date.today()

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "today": today.isoformat(),
        "catalog": str(catalog_path),
        "datasets": rows,
    }

    if args.format in ("text", "all"):
        print(render_text(rows, today, use_color=not args.no_color))

    if args.format in ("json", "all"):
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(payload, indent=2, default=str))
        print(f"\n[json] wrote {args.out_json}", file=sys.stderr)

    if args.format in ("html", "all"):
        html_body = render_html(rows, today)
        Path(args.out_html).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_html).write_text(html_body)
        print(f"[html] wrote {args.out_html}", file=sys.stderr)
        if args.out_html_mirror:
            Path(args.out_html_mirror).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out_html_mirror).write_text(html_body)
            print(f"[html] mirrored to {args.out_html_mirror}", file=sys.stderr)

    # Exit code reflects severity for use in cron / CI
    sev_counts = {sev: 0 for sev in SEVERITY}
    for r in rows: sev_counts[r["severity"]] += 1
    if sev_counts["STALE"] > 0:
        return 2
    if sev_counts["WARN"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
