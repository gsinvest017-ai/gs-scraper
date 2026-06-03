"""共用 dataset metadata：data_source enum + per-view 中文長描述。

被 `scripts/gap_report.py`（gap dashboard）與 `ui/search/catalog_inspector.py`
（views dashboard）共同使用，避免兩處維護同一份對照表。

`DATA_SOURCES` — 10 種資料源 enum，dashboard 顯示用 pill 區隔。
`DATASET_META` — view name → (data_source, long_description) 對照。

View 不在 `DATASET_META` → fallback `("other", "")`。
"""
from __future__ import annotations

DATA_SOURCES = {
    "TEJ-API",         # scripts/fetch_tej.py → TWN/A* tables
    "TEJ-訂閱包",        # 手動匯出 CSV (fundamentals_q / accounting_raw_extended)
    "FinMind",         # scripts/fetch_finmind.py → bronze/finmind/*.sqlite
    "TQuant-Lab",      # RAW_SOURCES/{日k 期貨tquant lab,股票期貨,MXF_*}/
    "yfinance",        # scripts/fetch_macro.py → macro_daily
    "TAIFEX",          # 期交所 OpenAPI / web
    "TWSE",            # 證交所 web scraping
    "Yahoo-extracted", # RAW/三大法人買賣超/institutional_yahoo_value_clean.csv
    "derived",         # 純 silver→gold transformation
    "manual-RAW",      # 其他手動 dump (rf_daily, cross_market_features)
    "other",
}


# view name → (data_source, long_description)
DATASET_META: dict[str, tuple[str, str]] = {
    # === TEJ-API（fetch_tej.py） ===
    "tw_stock_bars":                 ("TEJ-API", "台股日 K：OHLCV + 還原權息漲跌幅；2010 起逐日 ~6.6M rows / ~1500 stocks。"),
    "tw_inst_stock_daily":           ("TEJ-API", "每股每日三大法人（外資、投信、自營）買賣超 lot 數 + 持股比；2010~。"),
    "tw_margin_daily":               ("TEJ-API", "每股每日融資、融券、券資比、餘額;2010~。"),
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
    "capital_changes":               ("TEJ-API", "資本形成 / 股本變動事件（TWN/APISTK1）：現金增資 / 盈餘配股 / 減資 / CB轉換 / 庫藏股註銷 / 合併 / IPO，event-based by 除權日。"),
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
    # === TQuant-Lab dump ===
    "tx_continuous_d":               ("TQuant-Lab", "TX 連續期日 K（tquant lab 整理過的 continuous contract，含 adj_back 版本）。"),
    "mtx_continuous_d":              ("TQuant-Lab", "MTX 連續期日 K（同上）。"),
    "stock_futures_continuous_d":    ("TQuant-Lab", "個股期連續日 K，314 contracts。"),
    "bars_1m":                       ("TQuant-Lab", "1 分鐘 K：tw_futures (MXF) + us_futures (GC/ES/NQ)；15.6M rows。"),
    "txo_1min":                      ("TQuant-Lab", "TXO 1 分鐘 K（strike / option_type / minute / OHLCV）2.19M rows。"),
    # === manual-RAW ===
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
    # === derived snapshots（純 COPY） ===
    "accounting_raw_snapshot":       ("derived", "accounting_raw 的 gold 單檔副本（純 COPY，方便外部工具讀）。"),
    "accounting_raw_yearly":         ("derived", "accounting_raw 的 yearly summary（rows / stocks / mean assets / liabilities / cash）。"),
    "tw_inst_futures_daily_snapshot": ("derived", "tw_inst_futures_daily 的 gold 單檔副本。"),
    "txo_daily_features_snapshot":   ("derived", "txo_daily_features 的 gold 單檔副本。"),
    "qc_stock_price_diff_snapshot":  ("derived", "qc_stock_price_diff 的 gold 單檔副本。"),
    "bars_1m_daily_summary":         ("derived", "從 bars_1m 1分鐘 K aggregate 到 daily OHLCV summary。"),
    # === reference / calendar / non-monitored catalog views ===
    "calendar_xtai":                 ("derived", "TEJ_XTAI 交易日 calendar（trading_date / is_trading / session）。"),
    "symbol_map":                    ("derived", "(source, symbol) → canonical name 對照。"),
    "contract_specs":                ("derived", "期貨契約規格（tick_size / multiplier / sessions）。"),
    "security_attrs":                ("TEJ-API", "證券屬性（產業類別、上市狀態、IPO 日）。"),
}


def get_meta(view: str) -> tuple[str, str]:
    """Return (data_source, long_description) for a view; fallback to ('other', '')."""
    return DATASET_META.get(view, ("other", ""))
