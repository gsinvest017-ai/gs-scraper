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
import glob
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog" / "quant.duckdb"

# --- Trading-day-aware lag config -----------------------------------------
TPE_TZ = ZoneInfo("Asia/Taipei")
# Cutoff = "today's data is expected to be in silver". TW spot closes 13:30,
# TEJ EOD lands ~14:00, but our cron runs 17:30 — so today's silver isn't
# expected until ~18:00 TPE. Before that, expected_latest = previous trading
# day so no false lag during the 13:30-17:30 window.
EOD_CUTOFF_HOUR_TPE = 18
# Categories whose lag is measured in trading days (not calendar days).
# These all carry a trading-date semantic (TW market days) so weekends and
# pre-EOD intraday today should not be charged as lag.
#   - daily-trading: TEJ OHLCV / flows / etc.
#   - snapshot:      bronze sqlite views (FinMind) — underlying is trading-day
#   - derived:       gold features / cross-market — input is trading-day
# Other categories (monthly/quarterly/event) follow publication-date semantics
# and keep raw calendar lag.
TRADING_DAY_CATEGORIES = frozenset({"daily-trading", "snapshot", "derived"})


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

# --- Data source enum (中文使用者識別資料源用) -----------------------------
# 沒有實際做型別驗證，只是 dashboard 顯示 + 篩用；想加就加
DATA_SOURCES = {
    "TEJ-API",        # scripts/fetch_tej.py → TWN/A* tables
    "TEJ-訂閱包",      # 手動匯出 CSV (fundamentals_q / accounting_raw_extended)
    "FinMind",        # scripts/fetch_finmind.py → bronze/finmind/*.sqlite
    "TQuant-Lab",     # RAW_SOURCES/{日k 期貨tquant lab,股票期貨,MXF_*}/
    "yfinance",       # scripts/fetch_macro.py → macro_daily
    "TAIFEX",         # 期交所 OpenAPI / web
    "TWSE",           # 證交所 web scraping
    "Yahoo-extracted",# RAW/三大法人買賣超/institutional_yahoo_value_clean.csv
    "derived",        # 純 silver→gold transformation
    "manual-RAW",     # 其他手動 dump (rf_daily, cross_market_features)
    "other",
}


# view name → (data_source, long_description)。__post_init__ 自動套；
# 沒列出的 view 走原本 description + data_source='other'。
_DATASET_META: dict[str, tuple[str, str]] = {
    # === TEJ-API（fetch_tej.py） ===
    "tw_stock_bars":                 ("TEJ-API", "台股日 K：OHLCV + 還原權息漲跌幅；2010 起逐日 ~6.6M rows / ~1500 stocks。"),
    "tw_inst_stock_daily":           ("TEJ-API", "每股每日三大法人（外資、投信、自營）買賣超 lot 數 + 持股比；2010~。"),
    "tw_margin_daily":               ("TEJ-API", "每股每日融資、融券、券資比、餘額；2010~。"),
    "tw_inst_futures_daily":         ("TEJ-API", "期貨三大法人 daily（自營/投信/外資 × 多空 OI）。"),
    "tw_inst_futures_full_daily":    ("TEJ-API", "tw_inst_futures_daily 加 hedge / proprietary 細項與 net OI。"),
    "tw_futures_large_trader_daily": ("TEJ-API", "期貨大額交易人未沖銷部位。"),
    "bars_1d":                       ("TEJ-API", "日 K bars，含 tw_stock / tw_futures / tw_stock_futures / us_futures 多 asset class。"),
    "revenue_monthly":               ("TEJ-API", "每月營收（合併 / 單體），TEJ 約每月 21 日更新上月。"),
    "tw_chip_dist_daily":            ("TEJ-API", "個股每日 chip 分佈（大戶持股集中度等）。"),
    "cash_dividend_events":          ("TEJ-API", "現金股利發放事件（除息日、配息額）。"),
    "tw_stock_trading_attrs_daily":  ("TEJ-API", "個股交易屬性日資料（漲跌停、處置注意、警示）。"),
    "tw_stock_futures_corp_actions": ("TEJ-API", "個股期 corp action（分割、合併、股利等調整事件）。"),
    "stock_futures_adjustments":     ("TEJ-API", "個股期日資料調整參數（除息調整、保證金倍數）。"),
    "accounting_raw":                ("TEJ-API", "TWN/AINVFINB API 抓的單季財報（121 cols, 2022~2026）。"),
    # === TEJ-訂閱包 ===
    "fundamentals_q":                ("TEJ-訂閱包", "TWN/EWIFINQ CSV 季財報精簡版；訂閱包手動下載，無 API 自動 refresh。"),
    "fundamentals_pit":              ("derived",   "fundamentals_q 的 PIT (Point-In-Time) 對齊 — 用 publish_date 而非 fiscal_month。"),
    "accounting_raw_extended":       ("TEJ-訂閱包", "TEJ 訂閱包 CSV 單季財報（796 cols, 2005~2025, 1045 stocks）；IFRS9 細項展開。"),
    # === FinMind ===
    "finmind_stock_price":           ("FinMind", "FinMind TaiwanStockPrice raw（無還原）。"),
    "finmind_stock_price_adj":       ("FinMind", "FinMind TaiwanStockPriceAdj（還原權息）。"),
    "finmind_stock_price_norm":      ("FinMind", "finmind_stock_price 經 canonical schema 標準化（trading_date / open / high / low / close / volume）。"),
    "finmind_stock_price_adj_norm":  ("FinMind", "finmind_stock_price_adj 標準化版。"),
    "finmind_stock_week_price":      ("FinMind", "FinMind TaiwanStockWeekPrice（週 K）。"),
    "finmind_stock_info":            ("FinMind", "FinMind TaiwanStockInfo（代碼、產業、IPO 日期）。"),
    "finmind_stock_info_with_warrant": ("FinMind", "FinMind 含權證版的 stock info。"),
    "finmind_trading_date":          ("FinMind", "FinMind 提供的交易日 calendar。"),
    "finmind_txo_option_daily":      ("FinMind", "FinMind TaiwanOptionDaily（TXO 日資料：strike / type / OHLC / volume / OI / settle）。"),
    "finmind_price_canonical":       ("derived", "finmind_stock_price_norm + adj_norm 合一的 canonical view。"),
    # === yfinance ===
    "macro_daily":                   ("yfinance", "yfinance 抓 45 個 macro symbol（VIX / SPX / SOX / USDTWD / GC / IRX / TNX / ... ）daily。"),
    # === TQuant-Lab dump (RAW_SOURCES manual) ===
    "tx_continuous_d":               ("TQuant-Lab", "TX 連續期日 K（tquant lab 整理過的 continuous contract，含 adj_back 版本）。"),
    "mtx_continuous_d":              ("TQuant-Lab", "MTX 連續期日 K（同上）。"),
    "stock_futures_continuous_d":    ("TQuant-Lab", "個股期連續日 K，314 contracts。"),
    "bars_1m":                       ("TQuant-Lab", "1 分鐘 K：tw_futures (MXF) + us_futures (GC/ES/NQ)；15.6M rows。"),
    "txo_1min":                      ("TQuant-Lab", "TXO 1 分鐘 K（strike / option_type / minute / OHLCV）2.19M rows。"),
    # === manual RAW ===
    "rf_daily":                      ("manual-RAW", "TWD 無風險利率日資料 (2019~2026)；手動匯出 CSV。"),
    "cross_market_features":         ("manual-RAW", "跨市場特徵（VIX / SPY / DXY / Gold / Oil 等的 ret / vol / ma）；RAW SUPPLEMENT/DERIVED 手動 dump。"),
    # === Yahoo-extracted ===
    "tw_inst_market_daily":          ("Yahoo-extracted", "市場層級三大法人 TWD 買賣超（從 Yahoo 整理過的 CSV，2024-05~2026-04）。"),
    # === derived gold ===
    "stock_factor_daily":            ("derived", "從 tw_stock_bars 衍生：momentum (1m/3m/6m/12m) + vol (20d/60d) + RSI factor。"),
    "inst_flow_factors":             ("derived", "從 tw_inst_stock_daily 衍生：foreign / sitc / dealer 5d/20d/60d 滾動 sum + z-score。"),
    "margin_factors":                ("derived", "從 tw_margin_daily 衍生：融資 / 融券餘額 z-score, 券資比變化。"),
    "futures_inst_factors":          ("derived", "從 tw_inst_futures_full_daily 衍生：per-identity 期貨持倉因子。"),
    "futures_large_trader_factors":  ("derived", "從 tw_futures_large_trader_daily 衍生 large trader 持倉因子。"),
    "futures_bar_factors":           ("derived", "從 bars_1d futures 衍生 momentum / vol / basis factor。"),
    "chip_dist_factors":             ("derived", "從 tw_chip_dist_daily 衍生大戶持股 z-score。"),
    "dividend_calendar":             ("derived", "從 cash_dividend_events + future scheduled 整理出的除息日曆。"),
    "stock_attrs_status":            ("derived", "tw_stock_trading_attrs_daily 的 boolean panel 化（is_attention / is_disposal）。"),
    "qc_stock_price_diff":           ("derived", "QC：tw_stock_bars vs finmind_price_canonical 對表差異。"),
    "revenue_factors":               ("derived", "從 revenue_monthly 衍生 YoY / MoM 成長率 + z-score。"),
    "macro_factors":                 ("derived", "從 macro_daily 衍生 ret / vol / ATR factor。"),
    "market_inst_aggregated":        ("derived", "從 tw_inst_stock_daily 個股級 aggregate 到市場級 (lots-based)。"),
    "txo_daily_features":            ("derived", "FinMind TXO 衍生 12 個 daily features（pcr_vol/oi、max_pain、atm_iv、iv_skew）。"),
    "txo_1min_intraday":             ("derived", "從 txo_1min 衍生：per-day total_volume、peak_minute、PCP-implied spot realized vol。"),
    "inst_market_factors":           ("derived", "從 tw_inst_market_daily 衍生 5/20/60d 滾動 sum + 60d z-score。"),
    # === derived snapshots（純 COPY，方便 portability） ===
    "accounting_raw_snapshot":       ("derived", "accounting_raw 的 gold 單檔副本（純 COPY，方便外部工具讀）。"),
    "accounting_raw_yearly":         ("derived", "accounting_raw 的 yearly summary（rows / stocks / mean assets / liabilities / cash）。"),
    "tw_inst_futures_daily_snapshot": ("derived", "tw_inst_futures_daily 的 gold 單檔副本。"),
    "txo_daily_features_snapshot":   ("derived", "txo_daily_features 的 gold 單檔副本。"),
    "qc_stock_price_diff_snapshot":  ("derived", "qc_stock_price_diff 的 gold 單檔副本。"),
    "bars_1m_daily_summary":         ("derived", "從 bars_1m 1分鐘 K aggregate 到 daily OHLCV summary。"),
}


@dataclass
class Dataset:
    view: str
    date_col: str
    category: str
    fetch_cmd: str
    description: str
    tier: str = "P1"
    # Storage-layer glob patterns (relative to REPO). Used by the dashboard to
    # show per-layer paths/size for migration checklists. Empty tuple = "no
    # data in this layer for this dataset" (e.g. TEJ ingest skips bronze).
    raw_paths: tuple[str, ...] = field(default_factory=tuple)
    bronze_paths: tuple[str, ...] = field(default_factory=tuple)
    silver_paths: tuple[str, ...] = field(default_factory=tuple)
    gold_paths: tuple[str, ...] = field(default_factory=tuple)
    # 新增（中文使用者導向）
    data_source: str = "other"        # 從 DATA_SOURCES 取
    long_description: str = ""        # 1-3 行中文「裡面放了什麼資料」

    def __post_init__(self):
        # 從 _DATASET_META 自動填（如果定義裡沒明確給）
        if self.view in _DATASET_META:
            ds, ld = _DATASET_META[self.view]
            if self.data_source == "other":
                self.data_source = ds
            if not self.long_description:
                self.long_description = ld


DATASETS = [
    # --- TEJ stock daily price + flows (P0, CSV-backed via fetch_tej.py) ---
    Dataset("tw_stock_bars",           "trading_date", "daily-trading",
            "fetch_tej.py --table stock_daily --append-since-silver",
            "TEJ 個股日 K (OHLCV + 除權息調整)", "P0",
            raw_paths=("../RAW_SOURCES/TEJ資料/TWN_EWPRCD_股價.csv",),
            silver_paths=("silver/bars/bars_1d/asset_class=tw_stock/**/*.parquet",),
            gold_paths=("gold/features/stock_factor_daily.parquet",)),
    Dataset("tw_inst_stock_daily",     "trading_date", "daily-trading",
            "fetch_tej.py --table inst_stock --append-since-silver",
            "個股三大法人買賣超", "P0",
            raw_paths=("../RAW_SOURCES/TEJ資料/TWN_EWTINST1_三大法人.csv",),
            silver_paths=("silver/flows/tw_inst_stock_daily/**/*.parquet",),
            gold_paths=(
                "gold/features/inst_flow_factors.parquet",
                "gold/features/market_inst_aggregated.parquet",
            )),
    Dataset("tw_margin_daily",         "trading_date", "daily-trading",
            "fetch_tej.py --table margin --append-since-silver",
            "融資融券餘額", "P0",
            raw_paths=("../RAW_SOURCES/TEJ資料/TWN_EWGIN_融資融券.csv",),
            silver_paths=("silver/flows/tw_margin_daily/**/*.parquet",),
            gold_paths=("gold/features/margin_factors.parquet",)),

    # --- TAIFEX / TEJ 期貨 (P0, direct silver-parquet) ---
    Dataset("tw_inst_futures_daily",   "trading_date", "daily-trading",
            "(TAIFEX scraper — currently no auto-refresh)",
            "期貨三大法人（TAIFEX 直抓）", "P0",
            raw_paths=("../RAW_SOURCES/三大法人買賣超/**/*",),
            silver_paths=("silver/flows/tw_inst_futures_daily/**/*.parquet",),
            gold_paths=("gold/features/tw_inst_futures_daily_snapshot.parquet",)),
    Dataset("tw_inst_futures_full_daily", "trading_date", "daily-trading",
            "fetch_tej.py --table inst_futures_full --append-since-silver",
            "期貨三大法人完整版（含選擇權）", "P1",
            silver_paths=("silver/flows/tw_inst_futures_full_daily/**/*.parquet",),
            gold_paths=("gold/features/futures_inst_factors.parquet",)),
    Dataset("tw_futures_large_trader_daily", "trading_date", "daily-trading",
            "fetch_tej.py --table futures_large_trader --append-since-silver",
            "期貨大額交易人未沖銷部位", "P0",
            silver_paths=("silver/flows/tw_futures_large_trader_daily/**/*.parquet",),
            gold_paths=("gold/features/futures_large_trader_factors.parquet",)),
    Dataset("bars_1d",                 "trading_date", "daily-trading",
            "fetch_tej.py --table futures_daily --append-since-silver",
            "所有期貨日 K（含 MXF/TXF/個股期）", "P0",
            silver_paths=("silver/bars/bars_1d/**/*.parquet",),
            gold_paths=(
                "gold/features/stock_factor_daily.parquet",
                "gold/features/futures_bar_factors.parquet",
                "gold/continuous/tx_continuous_d.parquet",
                "gold/continuous/mtx_continuous_d.parquet",
                "gold/continuous/stock_futures_continuous_d.parquet",
            )),
    Dataset("tx_continuous_d",         "trading_date", "daily-trading",
            "(TX 連續期 — 來自 RAW_SOURCES/日k 期貨tquant lab/，無自動 refresh)",
            "TX 連續期", "P1",
            raw_paths=("../RAW_SOURCES/日k 期貨tquant lab/TX_continuous_*.parquet",),
            gold_paths=("gold/continuous/tx_continuous_d.parquet",)),
    Dataset("mtx_continuous_d",        "trading_date", "daily-trading",
            "(MTX 連續期 — 來自 RAW_SOURCES/日k 期貨tquant lab/，無自動 refresh)",
            "MTX 連續期", "P1",
            raw_paths=("../RAW_SOURCES/日k 期貨tquant lab/MTX_continuous_*.parquet",),
            gold_paths=("gold/continuous/mtx_continuous_d.parquet",)),
    Dataset("stock_futures_continuous_d", "trading_date", "daily-trading",
            "(個股期連續 — 來自 RAW_SOURCES/股票期貨/，無自動 refresh)",
            "個股期連續近月", "P2",
            raw_paths=("../RAW_SOURCES/股票期貨/continuous_near_month.parquet",
                       "../RAW_SOURCES/股票期貨/stock_futures_daily.parquet"),
            gold_paths=("gold/continuous/stock_futures_continuous_d.parquet",)),

    # --- 1-minute bars (manual MXF parquet ingest) ---
    Dataset("bars_1m",                 "trading_date", "daily-trading",
            "(MXF 1m — 來自 RAW_SOURCES/MXF_1m_clean_all/，需手動更新)",
            "1 分鐘 K 線 (MXF)", "P2",
            raw_paths=("../RAW_SOURCES/MXF_1m_clean_all.parquet",
                       "../RAW_SOURCES/{NQ,ES,GC}_1min_*.zip",
                       "../RAW_SOURCES/{NQ,ES,GC}/**/*.parquet"),
            silver_paths=("silver/bars/bars_1m/**/*.parquet",),
            gold_paths=("gold/features/bars_1m_daily_summary.parquet",)),

    # --- TEJ chip / attrs / dividends (P1/P2) ---
    Dataset("tw_chip_dist_daily",      "trading_date", "weekly",
            "fetch_tej.py --table chip_dist --append-since-silver",
            "TEJ 集保戶股權分散表（週公告）", "P1",
            silver_paths=("silver/flows/tw_chip_dist_daily/**/*.parquet",),
            gold_paths=("gold/features/chip_dist_factors.parquet",)),
    Dataset("tw_stock_trading_attrs_daily", "trading_date", "daily-trading",
            "fetch_tej.py --table stock_trading_attrs --append-since-silver",
            "個股交易屬性（注意/處置/全額交割）", "P2",
            silver_paths=("silver/flows/tw_stock_trading_attrs_daily/**/*.parquet",),
            gold_paths=("gold/features/stock_attrs_status.parquet",)),

    # --- 月營收 / 季報 / 會計 ---
    Dataset("revenue_monthly",         "fiscal_month", "monthly",
            "fetch_tej.py --table revenue_monthly --append-since-silver",
            "月營收（每月 10 日前後公告）", "P0",
            silver_paths=("silver/fundamentals/revenue_monthly/**/*.parquet",),
            gold_paths=("gold/features/revenue_factors.parquet",)),
    Dataset("fundamentals_q",          "publish_date", "quarterly",
            "(TWN_EWIFINQ CSV — 來自 TEJ 訂閱包，無 API auto-refresh)",
            "季報（單季 + 累季財報）", "P1",
            raw_paths=("../RAW_SOURCES/TEJ資料/TWN_EWIFINQ_單季財報.csv",
                       "../RAW_SOURCES/TEJ資料/TWN_EWIFINA_累季財報.csv"),
            silver_paths=("silver/fundamentals/fin_q/**/*.parquet",),
            gold_paths=("gold/features/fundamentals_pit.parquet",)),
    Dataset("accounting_raw",          "fiscal_month", "quarterly",
            "fetch_tej.py --table accounting_raw --append-since-silver",
            "原始會計簽證科目（AINVFINB 118 欄）", "P2",
            silver_paths=("silver/fundamentals/accounting_raw/**/*.parquet",),
            gold_paths=(
                "gold/features/accounting_raw_snapshot.parquet",
                "gold/features/accounting_raw_yearly.parquet",
            )),

    # --- 衍生 (downstream of stock_bars etc.) ---
    Dataset("stock_factor_daily",      "trading_date", "derived",
            "qd-ingest rebuild-stock-factors  (依 tw_stock_bars 衍生)",
            "個股技術因子（漲跌幅、RSI、ADX 等）", "P1",
            gold_paths=("gold/features/stock_factor_daily.parquet",)),
    Dataset("inst_flow_factors",       "trading_date", "derived",
            "python -m qd_ingest.sources.derived  (or rebuild via inst_flow_factors block)",
            "個股法人流量因子（5/20/60d 累積 + 持股率變化 + persistence）", "P1",
            gold_paths=("gold/features/inst_flow_factors.parquet",)),
    Dataset("margin_factors",          "trading_date", "derived",
            "python -m qd_ingest.sources.derived  (build_margin_factors)",
            "融資融券時序因子（balance chg 5/20d, util z-score 60d）", "P1",
            gold_paths=("gold/features/margin_factors.parquet",)),
    Dataset("fundamentals_pit",        "trading_date", "quarterly",
            "python -m qd_ingest.sources.derived  (build_fundamentals_pit)",
            "PIT 財務 panel（依 publish_date 對齊，含 TTM 與 YoY 因子）", "P1",
            gold_paths=("gold/features/fundamentals_pit.parquet",)),
    Dataset("futures_large_trader_factors", "trading_date", "derived",
            "python -m qd_ingest.sources.derived  (build_futures_large_trader_factors)",
            "期貨大額交易人因子（top10 net, concentration, OI 變化）", "P1",
            gold_paths=("gold/features/futures_large_trader_factors.parquet",)),
    Dataset("macro_daily",             "trading_date", "daily-trading",
            "yfinance scraper for VIX/USDTWD/...  (no auto job yet)",
            "總體變數日資料 (VIX, USDTWD, ...)", "P1",
            silver_paths=("silver/macro/**/*.parquet",),
            gold_paths=("gold/features/macro_factors.parquet",)),
    Dataset("rf_daily",                "date",         "daily-trading",
            "scripts/ingest_rf_daily.py  (one-shot ingest of RAW CSV)",
            "無風險利率日資料 (TWD rf)", "P1",
            raw_paths=("../RAW_SOURCES/無風險利率日資料_*.csv",),
            silver_paths=("silver/macro/rf_daily.parquet",),
            gold_paths=("silver/macro/rf_daily.parquet",)),  # 純 silver；無需再衍生 gold
    Dataset("txo_1min",                "trade_date",   "daily-trading",
            "scripts/ingest_txo_1min.py  (one-shot ingest of RAW parquet)",
            "選擇權 TXO 1 分鐘 K", "P2",
            raw_paths=("../RAW_SOURCES/TXO_1min_merged_*.parquet",),
            silver_paths=("silver/options/txo_1min/**/*.parquet",),
            gold_paths=("silver/options/txo_1min/**/*.parquet",)),  # 純 silver；下一輪可衍生 gold
    Dataset("txo_daily_features",      "date",         "daily-trading",
            "python -m qd_ingest.sources.derived  (build_txo_daily_features ← FinMind option daily)",
            "選擇權 TXO 日特徵", "P2",
            bronze_paths=("bronze/finmind/finmind_*.sqlite",),
            gold_paths=("gold/features/txo_daily_features_snapshot.parquet",)),
    Dataset("cross_market_features",   "date",         "derived",
            "(cross-market features — derived from VIX/SPY/macro; rebuild after macro_daily refresh)",
            "跨市場特徵（VIX-vol、SPY-corr 等）", "P2",
            gold_paths=("gold/features/cross_market_features.parquet",)),
    # tw_inst_market_daily 復活：source 改為 RAW_SOURCES/三大法人買賣超/
    # institutional_yahoo_value_clean.csv（474 rows, 2024-05-02 ~ 2026-04-16,
    # 市場層級三大法人 TWD 資料）；上面市場層級彙總（market_inst_aggregated）
    # 是 lot-based stock-level aggregation，這條才是真正的 TWD 市場 flow。
    Dataset("tw_inst_market_daily",    "trading_date", "daily-trading",
            "scripts/ingest_inst_market_daily.py  (one-shot from yahoo cleaned CSV)",
            "市場層級三大法人買賣超 (TWD)", "P1",
            raw_paths=("../RAW_SOURCES/三大法人買賣超/institutional_yahoo_value_clean.csv",),
            silver_paths=("silver/flows/tw_inst_market_daily/**/*.parquet",),
            gold_paths=("silver/flows/tw_inst_market_daily/**/*.parquet",)),  # 純 silver；下輪可衍生 gold

    # --- FinMind bronze snapshot (auto-refreshed daily via fetch_finmind.py / daily_refresh step 1.7) ---
    Dataset("finmind_stock_price_norm",     "trading_date", "daily-trading",
            "scripts/fetch_finmind.py  (daily_refresh step 1.7; ~1d publish lag)",
            "FinMind 個股日 K (canonical 命名 + 2000-2026 完整)", "P1",
            raw_paths=("../RAW_SOURCES/FINMIND資料集.zip",),
            bronze_paths=("bronze/finmind/finmind_*.sqlite",),
            gold_paths=("gold/features/finmind_price_canonical.parquet",)),
    Dataset("finmind_stock_price_adj_norm", "trading_date", "daily-trading",
            "scripts/fetch_finmind.py  (daily_refresh step 1.7; ~1d publish lag)",
            "FinMind 還原權息日 K (TEJ adj_close 對帳用)", "P2",
            raw_paths=("../RAW_SOURCES/FINMIND資料集.zip",),
            bronze_paths=("bronze/finmind/finmind_*.sqlite",),
            gold_paths=("gold/features/finmind_price_canonical.parquet",)),
    Dataset("qc_stock_price_diff",          "trading_date", "derived",
            "(rebuild after either tw_stock_bars or finmind_stock_price_norm updates)",
            "TEJ vs FinMind 對帳 view（2010+ 重疊段）", "P2",
            gold_paths=(
                "gold/features/qc_stock_price_diff_snapshot.parquet",
                "gold/features/qc_stock_price_diff_yearly.parquet",
            )),

    # --- Event-driven (future-dated rows; check MAX vs today) ---
    Dataset("cash_dividend_events",    "ex_date",      "event",
            "fetch_tej.py --table cash_dividend --append-since-silver",
            "現金股利除息事件（forward-looking）", "P1",
            silver_paths=("silver/fundamentals/cash_dividend_events/**/*.parquet",),
            gold_paths=("gold/features/dividend_calendar.parquet",)),
    Dataset("tw_stock_futures_corp_actions", "adjust_date", "event",
            "fetch_tej.py --table stock_futures_corp_actions --append-since-silver",
            "個股期調整事件", "P2",
            silver_paths=("silver/flows/tw_stock_futures_corp_actions/**/*.parquet",),
            gold_paths=("gold/features/stock_futures_adjustments.parquet",)),

    # --- New derived (M2 of this round): one per goldified silver-only view ---
    Dataset("futures_inst_factors",    "trading_date", "derived",
            "python -m qd_ingest.sources.derived  (build_futures_inst_factors)",
            "期貨三大法人因子（net_oi 變化、long_short OI ratio、vol/oi）", "P1",
            gold_paths=("gold/features/futures_inst_factors.parquet",)),
    Dataset("stock_attrs_status",      "trading_date", "derived",
            "python -m qd_ingest.sources.derived  (build_stock_attrs_status)",
            "個股交易屬性 bool panel（attention/disposition + 30d 計數 + index 成員）", "P2",
            gold_paths=("gold/features/stock_attrs_status.parquet",)),
    Dataset("dividend_calendar",       "ex_date",      "event",
            "python -m qd_ingest.sources.derived  (build_dividend_calendar)",
            "除權息事件 panel（per-share + yield + TTM + YoY）", "P1",
            gold_paths=("gold/features/dividend_calendar.parquet",)),
    Dataset("stock_futures_adjustments", "adjust_date", "event",
            "python -m qd_ingest.sources.derived  (build_stock_futures_adjustments)",
            "個股期調整累計表（cum cash/stock div、間隔、序號）", "P2",
            gold_paths=("gold/features/stock_futures_adjustments.parquet",)),

    # --- New derived (this round): goldify last 100% complete views ---
    Dataset("futures_bar_factors",     "trading_date", "derived",
            "python -m qd_ingest.sources.derived  (build_futures_bar_factors)",
            "期貨日 K 衍生因子（mom 5/20/60d, vol 20/60d, ATR14, turnover, OI Δ）", "P1",
            gold_paths=("gold/features/futures_bar_factors.parquet",)),
    Dataset("qc_stock_price_diff_snapshot", "trading_date", "derived",
            "python -m qd_ingest.sources.derived  (materialize_qc_snapshot)",
            "TEJ vs FinMind 對帳 parquet 持久化（含 yearly aggregate 副產物）", "P2",
            gold_paths=(
                "gold/features/qc_stock_price_diff_snapshot.parquet",
                "gold/features/qc_stock_price_diff_yearly.parquet",
            )),
    Dataset("finmind_price_canonical", "trading_date", "derived",
            "python -m qd_ingest.sources.derived  (materialize_finmind_canonical)",
            "FinMind raw + adj OHLC 合併（parquet 取代 sqlite 查詢）", "P1",
            gold_paths=("gold/features/finmind_price_canonical.parquet",)),

    # --- /goldify-100 iter1 (2026-05-26) ---
    Dataset("chip_dist_factors",       "trading_date", "weekly",
            "python -m qd_ingest.sources.derived  (build_chip_dist_factors)",
            "集保戶股權分散因子（大戶 / 散戶 4w 變化、集中度、質押率）", "P1",
            gold_paths=("gold/features/chip_dist_factors.parquet",)),
    Dataset("revenue_factors",         "fiscal_month", "monthly",
            "python -m qd_ingest.sources.derived  (build_revenue_factors)",
            "月營收衍生因子（YoY 加速度、24m z-score、6m persistence）", "P0",
            gold_paths=("gold/features/revenue_factors.parquet",)),
    Dataset("accounting_raw_snapshot", "fiscal_month", "quarterly",
            "python -m qd_ingest.sources.derived  (materialize_accounting_snapshot)",
            "會計簽證科目 parquet 持久化（含 yearly aggregate 副產物）", "P2",
            gold_paths=(
                "gold/features/accounting_raw_snapshot.parquet",
                "gold/features/accounting_raw_yearly.parquet",
            )),

    # --- /goldify-100 scope-fix (2026-05-26) — STALE silvers materialized as gold ---
    Dataset("tw_inst_futures_daily_snapshot", "trading_date", "daily-trading",
            "python -m qd_ingest.sources.derived  (materialize_tw_inst_futures_daily_snapshot)",
            "TAIFEX 三大法人期貨 snapshot（截至 silver max_date）", "P0",
            gold_paths=("gold/features/tw_inst_futures_daily_snapshot.parquet",)),
    Dataset("txo_daily_features_snapshot", "date", "daily-trading",
            "python -m qd_ingest.sources.derived  (materialize_txo_daily_features_snapshot)",
            "TXO 日特徵 snapshot（PCR / max_pain / IV proxy）", "P2",
            gold_paths=("gold/features/txo_daily_features_snapshot.parquet",)),
    # tw_inst_market_daily_snapshot 已隨上游 view 退役，從 registry 移除。
    Dataset("bars_1m_daily_summary",   "trading_date", "daily-trading",
            "python -m qd_ingest.sources.derived  (build_bars_1m_daily_summary)",
            "1m bars 日彙總（per asset_class × symbol：OHLCV + bars_count）", "P2",
            gold_paths=("gold/features/bars_1m_daily_summary.parquet",)),
    Dataset("macro_factors",           "trading_date", "daily-trading",
            "python -m qd_ingest.sources.derived  (build_macro_factors)",
            "Macro time-series factors（VIX/USDTWD/... 之 ret/vol/atr）", "P1",
            gold_paths=("gold/features/macro_factors.parquet",)),
    Dataset("market_inst_aggregated",  "trading_date", "daily-trading",
            "python -m qd_ingest.sources.derived  (build_market_inst_aggregated)",
            "市場層級三大法人彙總（從 tw_inst_stock_daily 衍生，含 lots + hold_pct mean）", "P1",
            gold_paths=("gold/features/market_inst_aggregated.parquet",)),
    Dataset("txo_1min_intraday",       "trading_date", "daily-trading",
            "python -m qd_ingest.sources.derived  (build_txo_1min_intraday_features)",
            "TXO 1分鐘 intraday：volume/peak-minute/atm-realized-vol", "P2",
            gold_paths=("gold/features/txo_1min_intraday.parquet",)),
    Dataset("inst_market_factors",     "trading_date", "daily-trading",
            "python -m qd_ingest.sources.derived  (build_inst_market_factors)",
            "市場層級三大法人 5/20/60d 滾動 + 60d z-score", "P1",
            gold_paths=("gold/features/inst_market_factors.parquet",)),
    Dataset("accounting_raw_extended", "fiscal_month", "quarterly",
            "scripts/ingest_accounting_raw_extended.py  (one-shot from TEJ subscription CSV)",
            "TEJ 訂閱包單季財報 (796 cols, 2005~2025)", "P2",
            raw_paths=("../../Users/User/Downloads/台灣上市公司單季財報資料*.csv",),
            silver_paths=("silver/fundamentals/accounting_raw_extended/**/*.parquet",),
            gold_paths=("silver/fundamentals/accounting_raw_extended/**/*.parquet",)),  # 純 silver；下輪可衍生 gold
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
    if category == "weekly":
        # TEJ APISHRACTW etc publish weekly (typically Fri close + 1-2 day lag).
        # 0-7d = fresh, 8-14d = WARN, >14d = STALE.
        if lag_days <= 7: return "OK"
        if lag_days <= 14: return "WARN"
        return "STALE"
    if category == "monthly":
        # date_col is typically fiscal_month (= 1st of month X for data of month X);
        # publication usually happens day 10 of month X+1, so the *fresh* state
        # already shows lag ~30-45d. Next month's data won't arrive until ~day 10
        # of month X+2, so the natural max lag is ~70d before the next batch
        # publishes. 0-60d = OK, 60-90d = WARN, >90d = STALE.
        if lag_days <= 60: return "OK"
        if lag_days <= 90: return "WARN"
        return "STALE"
    if category == "quarterly":
        # Quarterly statements: Q1 ends 3/31, publishes by 5/15 (45d lag);
        # next batch (Q2) lands by 8/15 → max fresh lag ~135d before stale.
        if lag_days <= 100: return "OK"
        if lag_days <= 180: return "WARN"
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


# --- Trading-day-aware lag -------------------------------------------------

def _load_trading_days(con: duckdb.DuckDBPyConnection) -> set[dt.date]:
    """Load the set of TW trading days from calendar_xtai. Empty set if view
    missing or any error — caller should then fall back to weekday heuristic."""
    try:
        rows = con.execute(
            "SELECT trading_date FROM main.calendar_xtai WHERE is_trading = TRUE"
        ).fetchall()
        return {r[0] for r in rows if r[0] is not None}
    except Exception:
        return set()


def _is_trading_day(d: dt.date, calendar_days: set[dt.date]) -> bool:
    """True if d is a TW trading day.

    Authoritative source = `calendar_xtai`. For dates past the calendar's
    coverage (currently 2025-12-31), fall back to weekday Mon-Fri. This will
    over-count holidays like 春節 / 端午 / 國慶 — accepted trade-off until
    calendar gets refreshed."""
    if calendar_days:
        cal_max = max(calendar_days)
        cal_min = min(calendar_days)
        if cal_min <= d <= cal_max:
            return d in calendar_days
    # Fallback for dates outside calendar coverage
    return d.weekday() < 5  # Mon-Fri


def expected_latest_trading_day(
    now_tpe: dt.datetime,
    calendar_days: set[dt.date],
    eod_cutoff_hour: int = EOD_CUTOFF_HOUR_TPE,
) -> dt.date:
    """Latest TW trading day for which EOD data is expected to have landed.

    - If today is a trading day AND now_tpe.hour >= eod_cutoff_hour → today
    - Else step back day-by-day until the next trading day is found
      (≤ 30 days backstop)"""
    today = now_tpe.date()
    if _is_trading_day(today, calendar_days) and now_tpe.hour >= eod_cutoff_hour:
        return today
    candidate = today - dt.timedelta(days=1)
    for _ in range(30):
        if _is_trading_day(candidate, calendar_days):
            return candidate
        candidate -= dt.timedelta(days=1)
    return candidate  # safety net (shouldn't hit)


# --- Storage layer measurement ---------------------------------------------

def _measure_layer(patterns: tuple[str, ...]) -> dict:
    """Resolve glob patterns under REPO and return total size + file count.

    Patterns are repo-relative (we glob from REPO as cwd). Supports recursive
    `**` and brace expansion via two-pass (we expand `{a,b}` manually since
    `glob` lacks built-in brace expansion).
    """
    if not patterns:
        return {"file_count": 0, "size_bytes": 0, "examples": []}

    def expand_braces(p: str) -> list[str]:
        # very small brace-expander; only one {a,b,c} per pattern.
        if "{" not in p:
            return [p]
        lhs, rest = p.split("{", 1)
        if "}" not in rest:
            return [p]
        body, rhs = rest.split("}", 1)
        return [f"{lhs}{tok}{rhs}" for tok in body.split(",")]

    cwd = str(REPO)
    total_bytes = 0
    total_files = 0
    examples: list[str] = []
    seen = set()
    for raw_pat in patterns:
        for pat in expand_braces(raw_pat):
            # Make absolute for glob; resolve symlinks lazily
            abs_pat = os.path.join(cwd, pat)
            for path in glob.iglob(abs_pat, recursive=True):
                if path in seen:
                    continue
                seen.add(path)
                if os.path.isfile(path):
                    total_bytes += os.path.getsize(path)
                    total_files += 1
                    if len(examples) < 3:
                        examples.append(os.path.relpath(path, cwd))
                elif os.path.isdir(path):
                    for root, _, files in os.walk(path):
                        for f in files:
                            fp = os.path.join(root, f)
                            if fp in seen:
                                continue
                            seen.add(fp)
                            try:
                                total_bytes += os.path.getsize(fp)
                                total_files += 1
                            except OSError:
                                pass
                    if len(examples) < 3:
                        examples.append(os.path.relpath(path, cwd))
    return {"file_count": total_files, "size_bytes": total_bytes, "examples": examples}


def _fmt_bytes(n: int) -> str:
    if n <= 0:
        return "—"
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= threshold:
            return f"{n / threshold:.1f} {unit}"
    return f"{n} B"


# --- Probe -----------------------------------------------------------------

def probe(catalog_path: Path) -> list[dict]:
    """Snapshot catalog, then query MAX(date_col) for each dataset."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="gap_report_"))
    snap = tmp_dir / "snap.duckdb"
    shutil.copy(catalog_path, snap)
    today = dt.date.today()
    now_tpe = dt.datetime.now(TPE_TZ)
    try:
        con = duckdb.connect(str(snap), read_only=True)
        con.execute(f"SET file_search_path='{REPO}'")
        trading_days = _load_trading_days(con)
        expected_td = expected_latest_trading_day(now_tpe, trading_days)
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
                    # For daily-trading: lag measured against expected_latest_trading_day
                    # so weekends + pre-EOD today never inflate the lag count.
                    if d.category in TRADING_DAY_CATEGORIES:
                        lag = max(0, (expected_td - max_date).days)
                    else:
                        lag = (today - max_date).days
                    severity = classify(lag, d.category)
                layers = {
                    "raw":    _measure_layer(d.raw_paths),
                    "bronze": _measure_layer(d.bronze_paths),
                    "silver": _measure_layer(d.silver_paths),
                    "gold":   _measure_layer(d.gold_paths),
                }
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
                    "layers": layers,
                })
            except Exception as e:
                rows.append({
                    "view": d.view, "description": d.description,
                    "category": d.category, "tier": d.tier,
                    "date_col": d.date_col, "max_date": None,
                    "row_count": None, "lag_days": None,
                    "layers": {
                        "raw":    _measure_layer(d.raw_paths),
                        "bronze": _measure_layer(d.bronze_paths),
                        "silver": _measure_layer(d.silver_paths),
                        "gold":   _measure_layer(d.gold_paths),
                    },
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
<meta name="color-scheme" content="dark">
<title>QUANTDATA Gap Dashboard</title>
<style>
  /* GitHub-dark palette — matches ui/search + gs-zipline-tej dashboard */
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", "PingFang TC", sans-serif;
          margin: 24px; color: #e6edf3; background: #0f1419; }}
  h1 {{ margin: 0 0 6px 0; font-size: 22px; }}
  .subtitle {{ color: #7d8590; margin-bottom: 18px; font-size: 13px; }}
  .topnav {{ display:flex; align-items:center; gap:8px; padding:8px 0 14px 0; font-size:13px; color:#7d8590; border-bottom:1px solid #2a323e; margin-bottom:18px; }}
  .topnav a {{ color:#58a6ff; text-decoration:none; }}
  .topnav a:hover {{ color:#79c0ff; text-decoration:underline; }}
  .topnav .back {{ font-weight:600; padding:4px 10px; border:1px solid #2a323e; border-radius:6px; background:#161c24; }}
  .topnav .back:hover {{ border-color:#58a6ff; }}
  .topnav .here {{ color:#e6edf3; font-weight:600; }}
  .topnav .navsep {{ color:#3b434e; }}
  td.note {{ font-size:11px; color:#c9b88a; max-width:280px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  td.note:hover {{ white-space:normal; word-break:break-word; }}
  .notes-toolbar {{ display:flex; gap:10px; align-items:center; margin:12px 0 8px 0; font-size:13px; }}
  .notes-toolbar .meta {{ color:#7d8590; }}
  .notes-toolbar button {{ background:#161210; color:#d4af37; border:1px solid #2b2218; padding:6px 14px; border-radius:6px; cursor:pointer; font-size:13px; }}
  .notes-toolbar button:hover {{ border-color:#d4af37; }}
  /* Modal */
  .notes-modal-backdrop {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:50; }}
  .notes-modal-backdrop.show {{ display:flex; align-items:center; justify-content:center; }}
  .notes-modal {{ background:#161c24; border:1px solid #2a323e; border-radius:8px; padding:18px; width:min(820px, 92vw); max-height:84vh; overflow:auto; }}
  .notes-modal h2 {{ margin:0 0 6px 0; font-size:16px; color:#e6edf3; }}
  .notes-modal .updated {{ font-size:11px; color:#7d8590; margin-bottom:14px; }}
  .notes-modal .row {{ display:grid; grid-template-columns:200px 1fr; gap:10px; align-items:flex-start; margin-bottom:8px; }}
  .notes-modal .row code {{ font-size:12px; color:#d4af37; }}
  .notes-modal textarea {{ width:100%; min-height:40px; resize:vertical; background:#0c0a0d; color:#f0e8d6; border:1px solid #2b2218; border-radius:4px; padding:6px 8px; font:13px/1.4 var(--font-sans, sans-serif); }}
  .notes-modal textarea:focus {{ outline:1px solid #d4af37; }}
  .notes-modal .actions {{ display:flex; gap:10px; justify-content:flex-end; margin-top:14px; padding-top:12px; border-top:1px solid #2a323e; }}
  .notes-modal .actions button.primary {{ background:#d4af37; color:#0d0a05; border:none; padding:8px 18px; border-radius:6px; font-weight:600; cursor:pointer; }}
  .notes-modal .actions button.cancel {{ background:transparent; color:#7d8590; border:1px solid #2a323e; padding:8px 18px; border-radius:6px; cursor:pointer; }}
  .notes-modal .saving {{ font-size:11px; color:#56d364; margin-right:auto; align-self:center; opacity:0; transition:opacity .2s; }}
  .notes-modal .saving.show {{ opacity:1; }}
  .summary {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 22px; }}
  .pill {{ padding: 10px 18px; border-radius: 10px; font-weight: 600; min-width: 90px; text-align: center; }}
  .pill.OK    {{ background: rgba(86,211,100,0.15);  color: #56d364; }}
  .pill.WARN  {{ background: rgba(240,136,62,0.15);  color: #f0883e; }}
  .pill.STALE {{ background: rgba(248,81,73,0.15);   color: #f85149; }}
  .pill.EMPTY {{ background: rgba(166,143,255,0.15); color: #b39dff; }}
  .pill.INFO  {{ background: rgba(88,166,255,0.15);  color: #58a6ff; }}
  table {{ width: 100%; border-collapse: collapse; background: #161c24;
           box-shadow: 0 1px 2px rgba(0,0,0,0.3); border-radius: 8px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #2a323e; font-size: 13px; }}
  th {{ background: #1f2731; font-weight: 600; color: #e6edf3; }}
  tr:last-child td {{ border-bottom: none; }}
  tr.OK    {{ background: #161c24; }}
  tr.WARN  {{ background: rgba(240,136,62,0.06); }}
  tr.STALE {{ background: rgba(248,81,73,0.07); }}
  tr.EMPTY {{ background: rgba(166,143,255,0.06); }}
  tr.INFO  {{ background: rgba(88,166,255,0.06); }}
  .lag {{ font-variant-numeric: tabular-nums; text-align: right; }}
  .pct {{ font-variant-numeric: tabular-nums; text-align: right; font-weight: 600; }}
  .layer {{ font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap;
            font-size: 11.5px; color: #7d8590; }}
  .layer.has-data {{ color: #e6edf3; font-weight: 500; }}
  .layer.empty {{ color: #4d5560; }}
  .layer .files {{ color: #7d8590; font-size: 10.5px; margin-left: 4px; }}
  .layer-totals {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 8px 0 22px 0; font-size: 12px; }}
  .layer-totals .ltot {{ background: #1f2731; padding: 6px 12px; border-radius: 6px; }}
  .layer-totals .ltot b {{ color: #e6edf3; }}
  .bar {{ position: relative; display: inline-block; height: 10px; background: #1f2731; border-radius: 4px; vertical-align: middle; border: 1px solid #2a323e; }}
  .bar > span {{ position: absolute; left: 0; top: 0; height: 100%; border-radius: 4px; }}
  .bar > span.OK    {{ background: #56d364; }}
  .bar > span.WARN  {{ background: #f0883e; }}
  .bar > span.STALE {{ background: #f85149; }}
  .bar > span.INFO  {{ background: #58a6ff; }}
  .bar > span.EMPTY {{ background: #6e7681; }}
  code {{ background: #1f2731; padding: 2px 6px; border-radius: 4px; font-size: 12px;
          font-family: "JetBrains Mono", "Menlo", monospace; }}
  .tier-P0 {{ font-weight: 700; }}
  .tier-P2 {{ opacity: 0.7; }}
  .legend {{ font-size: 12px; color: #7d8590; margin-top: 16px; }}
</style>
</head>
<body>
<nav class="topnav">
  <a href="/" class="back">← Search UI</a>
  <span class="navsep">·</span>
  <a href="/downloads">Downloads</a>
  <span class="navsep">·</span>
  <span class="here">Gap dashboard</span>
</nav>
<h1>📊 QUANTDATA Gap Dashboard</h1>
<div class="subtitle">Generated {generated_at} · catalog: <code>catalog/quant.duckdb</code> · {total} datasets monitored</div>

<div class="summary">
  <div class="pill STALE">🔴 STALE<br><b>{count_STALE}</b></div>
  <div class="pill WARN">⚠️ WARN<br><b>{count_WARN}</b></div>
  <div class="pill EMPTY">❓ EMPTY<br><b>{count_EMPTY}</b></div>
  <div class="pill INFO">ℹ️ INFO<br><b>{count_INFO}</b></div>
  <div class="pill OK">✅ OK<br><b>{count_OK}</b></div>
</div>

<div class="layer-totals">
  <div class="ltot">📦 Raw 總大小 <b>{layer_total_raw}</b></div>
  <div class="ltot">🥉 Bronze <b>{layer_total_bronze}</b></div>
  <div class="ltot">🥈 Silver <b>{layer_total_silver}</b></div>
  <div class="ltot">🥇 Gold <b>{layer_total_gold}</b></div>
</div>

<div class="notes-toolbar">
  <button id="edit-notes-btn" type="button">📝 編輯註解</button>
  <span class="meta" id="notes-meta">— 註解由 <code>meta/gap_comments.json</code> 載入；點擊編輯後存回 Flask <code>POST /api/gap_comments</code></span>
</div>
<div id="notes-modal" class="notes-modal-backdrop" role="dialog" aria-modal="true">
  <div class="notes-modal">
    <h2>📝 編輯 view 註解</h2>
    <div class="updated" id="notes-updated"></div>
    <div id="notes-list"></div>
    <div class="actions">
      <span class="saving" id="notes-saving">儲存中…</span>
      <button type="button" class="cancel" id="notes-cancel">取消</button>
      <button type="button" class="primary" id="notes-save">儲存全部</button>
    </div>
  </div>
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
  <th class="layer">📦 Raw</th>
  <th class="layer">🥉 Bronze</th>
  <th class="layer">🥈 Silver</th>
  <th class="layer">🥇 Gold</th>
  <th class="layer">📊 Catalog rows</th>
  <th>Suggested action</th>
  <th>📝 Note</th>
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
  Daily-trading 類別的 lag 為 <b>trading-day-aware</b>：對齊到「上一個應已有 EOD 的台股交易日」(weekend 與每日 15:00 TPE EOD 前的 today 不計入 lag)。
  Severity rules — daily-trading: 0-1d=OK / 2-5d=WARN / &gt;5d=STALE.
  Monthly: 0-15d=OK / 15-45d=WARN / &gt;45d=STALE.
  Quarterly: 0-60d=OK / 60-120d=WARN / &gt;120d=STALE.
  Event (forward-looking): MAX&lt;today = WARN/STALE depending on age.
  Derived tables (e.g. stock_factor_daily) inherit from upstream — flagged INFO only.
</div>

<script>
(function() {{
  // List of views shown in dashboard — derived from rendered table rows.
  const views = Array.from(document.querySelectorAll('td.note')).map(t => t.dataset.view);
  const modal   = document.getElementById('notes-modal');
  const listEl  = document.getElementById('notes-list');
  const updEl   = document.getElementById('notes-updated');
  const metaEl  = document.getElementById('notes-meta');
  const saveBtn = document.getElementById('notes-save');
  const saving  = document.getElementById('notes-saving');

  async function loadComments() {{
    try {{
      const r = await fetch('/api/gap_comments', {{ cache: 'no-store' }});
      if (!r.ok) throw new Error('http ' + r.status);
      return await r.json();
    }} catch (e) {{
      metaEl.textContent = '⚠ 載入失敗（' + e + '）— Flask 端尚未串接？仍可看 server-rendered 註解，但無法編輯。';
      return null;
    }}
  }}

  function openModal(data) {{
    listEl.innerHTML = '';
    updEl.textContent = data && data.updated_at ? ('上次更新：' + data.updated_at) : '';
    const map = (data && data.comments) || {{}};
    views.forEach(v => {{
      const row = document.createElement('div');
      row.className = 'row';
      const code = document.createElement('code'); code.textContent = v;
      const ta = document.createElement('textarea');
      ta.dataset.view = v;
      ta.value = map[v] || '';
      ta.placeholder = '（無註解）';
      row.appendChild(code); row.appendChild(ta);
      listEl.appendChild(row);
    }});
    modal.classList.add('show');
  }}

  async function saveAll() {{
    const tas = Array.from(listEl.querySelectorAll('textarea'));
    saving.classList.add('show');
    let ok = 0, fail = 0;
    for (const ta of tas) {{
      try {{
        const r = await fetch('/api/gap_comments', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ view: ta.dataset.view, comment: ta.value }}),
        }});
        if (r.ok) ok++; else fail++;
      }} catch (e) {{ fail++; }}
    }}
    saving.textContent = '已儲存 ' + ok + ' / ' + (ok + fail) + ' 條';
    setTimeout(() => {{ saving.classList.remove('show'); modal.classList.remove('show'); location.reload(); }}, 800);
  }}

  document.getElementById('edit-notes-btn').addEventListener('click', async () => {{
    const data = await loadComments();
    if (data === null) return;  // already messaged
    openModal(data);
  }});
  document.getElementById('notes-cancel').addEventListener('click', () => modal.classList.remove('show'));
  modal.addEventListener('click', (e) => {{ if (e.target === modal) modal.classList.remove('show'); }});
  saveBtn.addEventListener('click', saveAll);
}})();
</script>
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

    # Aggregate per-layer totals for the migration sizing block.
    # Re-run _measure_layer on the union of all patterns so shared paths
    # (e.g. one FinMind sqlite covering 2 datasets) are counted ONCE.
    layer_pattern_union: dict[str, set[str]] = {"raw": set(), "bronze": set(), "silver": set(), "gold": set()}
    for d in DATASETS:
        layer_pattern_union["raw"].update(d.raw_paths)
        layer_pattern_union["bronze"].update(d.bronze_paths)
        layer_pattern_union["silver"].update(d.silver_paths)
        layer_pattern_union["gold"].update(d.gold_paths)
    layer_totals_bytes = {
        lname: _measure_layer(tuple(sorted(pats)))["size_bytes"]
        for lname, pats in layer_pattern_union.items()
    }

    def layer_cell(layer_info: dict) -> str:
        n = layer_info.get("file_count", 0) if layer_info else 0
        b = layer_info.get("size_bytes", 0) if layer_info else 0
        if n == 0:
            return '<td class="layer empty">—</td>'
        examples = (layer_info or {}).get("examples") or []
        title = " · ".join(examples).replace('"', "&quot;")
        return (
            f'<td class="layer has-data" title="{title}">'
            f'{_fmt_bytes(b)}<span class="files">·{n}</span>'
            f'</td>'
        )

    def catalog_rows_cell(r: dict) -> str:
        n = r.get("row_count")
        if n is None:
            return '<td class="layer empty">—</td>'
        # human-readable: 6,587,436 → 6.6M, 1,234 → 1.2K
        if n >= 1_000_000:
            label = f"{n/1_000_000:.1f}M"
        elif n >= 1000:
            label = f"{n/1000:.1f}K"
        else:
            label = str(n)
        return f'<td class="layer has-data" title="exact: {n:,d} rows">{label}</td>'

    # Load manual gap-comments (per-view notes for low completeness reasons etc.)
    comments_path = REPO / "meta" / "gap_comments.json"
    comments_map: dict[str, str] = {}
    if comments_path.exists():
        try:
            comments_map = json.loads(comments_path.read_text(encoding="utf-8")).get("comments", {}) or {}
        except Exception:
            comments_map = {}

    def _html_escape(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                  .replace('"', "&quot;").replace("'", "&#39;"))

    rows_html = []
    for r in rows_sorted:
        meta = SEVERITY[r["severity"]]
        lag_str = f"{r['lag_days']}d" if r["lag_days"] is not None else "—"
        c = _completeness(r["lag_days"])
        pct_str = f"{c * 100:.0f}%"
        action_html = f'<code>{r["fetch_cmd"]}</code>' if r["severity"] != "OK" else ""
        layers = r.get("layers", {}) or {}
        note = comments_map.get(r["view"], "")
        # Truncate to ~80 chars for cell display; full text in title tooltip.
        note_disp = (note[:78] + "…") if len(note) > 80 else note
        note_html = (f'<td class="note" data-view="{r["view"]}" '
                     f'title="{_html_escape(note)}">{_html_escape(note_disp)}</td>')
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
            f'{layer_cell(layers.get("raw"))}'
            f'{layer_cell(layers.get("bronze"))}'
            f'{layer_cell(layers.get("silver"))}'
            f'{layer_cell(layers.get("gold"))}'
            f'{catalog_rows_cell(r)}'
            f'<td>{action_html}</td>'
            f'{note_html}'
            f'</tr>'
        )

    return HTML_TEMPLATE.format(
        generated_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total=len(rows),
        rows_html="\n".join(rows_html),
        layer_total_raw=_fmt_bytes(layer_totals_bytes["raw"]),
        layer_total_bronze=_fmt_bytes(layer_totals_bytes["bronze"]),
        layer_total_silver=_fmt_bytes(layer_totals_bytes["silver"]),
        layer_total_gold=_fmt_bytes(layer_totals_bytes["gold"]),
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
