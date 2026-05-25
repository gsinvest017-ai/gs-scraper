# Catalog views

`catalog/quant.duckdb` 在 2026-05-25 快照含 **49 個 view / macro**（含 9 個 FinMind / QC 還原 view 與 9 個新 gold derived view）。本頁逐一列出 row count、date range、底層儲存。

> 自動更新：要重新生這張清單，跑：
>
> ```bash
> .venv/bin/python scripts/gap_report.py --format json
> # → meta/audit/gap_report.json
> ```

## 1. Bars — 日 K / 分 K

| view | rows | date range | 底層 |
|---|---:|---|---|
| `bars_1d` | 10,419,601 | 2010-01-04 ~ 2026-05-20 | `silver/bars/asset_class=*/year=*/...parquet`（含台股 + 期 + 美股 + ETF） |
| `bars_1m` | 15,603,868 | 2010-01-03 ~ 2026-03-12 | `silver/bars/asset_class=tw_future/symbol=MXF/...` + histdata 1min |
| `tw_stock_bars` | 6,587,436 | 2010-01-04 ~ 2026-05-18 | `bars_1d` 過濾 `asset_class='tw_stock'` |

### 標準欄位

```
ts_utc          TIMESTAMP WITH TIME ZONE
trading_date    DATE
asset_class     VARCHAR  -- tw_stock / tw_future / tw_option / us_future / us_etf
exchange        VARCHAR  -- TWSE / TPEX / TAIFEX / CME / NYSE
symbol          VARCHAR
contract_id     VARCHAR  -- futures only
session         VARCHAR  -- day / night / combined
open/high/low/close DOUBLE
volume          BIGINT
open_interest   BIGINT   -- futures only
vwap            DOUBLE
settlement      DOUBLE   -- futures only
adj_open/high/low/close DOUBLE
adj_factor      DOUBLE
source          VARCHAR  -- tej / finmind / histdata / yahoo
ingestion_ts    TIMESTAMP WITH TIME ZONE
quality_flag    VARCHAR
year            INTEGER  -- partition key
```

## 2. Flows — 三大法人 / 融資券 / 集保

| view | rows | date range | 描述 |
|---|---:|---|---|
| `tw_inst_stock_daily` | 6,554,948 | 2010-01-04 ~ 2026-05-15 | 個股三大法人買賣超 |
| `tw_margin_daily` | 3,701,367 | 2010-01-04 ~ 2026-05-15 | 融資融券餘額 + 維持率 |
| `tw_chip_dist_daily` | 479,101 | 2022-01-07 ~ 2026-05-15 | 集保戶股權分散表 |
| `tw_inst_futures_daily` | 6,561 | 2023-05-08 ~ 2026-05-08 | 期貨三大法人（TAIFEX 直抓） |
| `tw_inst_futures_full_daily` | 465,819 | 2008-01-02 ~ 2026-05-20 | 期貨三大法人完整版（含選擇權） |
| `tw_futures_large_trader_daily` | 32,016 | 2026-04-07 ~ 2026-05-20 | 期貨大額交易人未沖銷部位 |

## 3. Fundamentals — 季報 / 月營收 / 股利

| view | rows | date range | 描述 |
|---|---:|---|---|
| `fundamentals_q` | 202,568 | 2010-03-03 ~ 2026-03-31 | 季報 + 累季財報 + ROA/ROE/EPS |
| `revenue_monthly` | 95,061 | 2022-02-05 ~ 2026-05-15 | 月營收（每月 10 日前後公告） |
| `accounting_raw` | 177,549 | 2022-04-19 ~ 2026-05-20 | 原始會計簽證科目（AINVFINB 118 欄） |
| `cash_dividend_events` | 10,458 | 2022-01-03 ~ 2026-10-15（含未來） | 現金股利除息事件 |

## 4. Futures continuous

| view | rows | date range | 描述 |
|---|---:|---|---|
| `tx_continuous_d` | 2,528 | 2016-01-04 ~ 2026-05-22 | TX 連續期（**2026-05-09 後**從 bars_1d 衍生） |
| `mtx_continuous_d` | 2,528 | 2016-01-04 ~ 2026-05-22 | MTX 連續期（同上） |
| `stock_futures_continuous_d` | 539,992 | 2015-01-05 ~ 2026-04-10 | 個股期連續近月（仍卡 underlying 4/13） |

!!! note "TX / MTX 連續期 — 兩種來源"

    歷史段（≤ 2026-05-08）來自 `RAW_SOURCES/日k 期貨tquant lab/{TX,MTX}_continuous_*.parquet` 手動 drop，含完整 back-adjusted `*_adj` 序列與 `adj_factor`。

    新尾段（2026-05-09 ~）由 `bars_1d.tw_futures` 衍生 — 每日選 max(volume) 的月份合約為 front。`source='qd_{tx|mtx}_continuous_extended_from_bars1d'` 標記，**`adj_factor=NULL` + `*_adj = raw`**（back-adjust chain 不延續）。要做嚴格 back-adj 回測時要過濾掉這段。

## 5. Options

| view | rows | date range | 描述 |
|---|---:|---|---|
| `txo_daily_features` | 1,481 | 2020-03-02 ~ 2026-04-01 | 選擇權 TXO 日特徵（PCR / IV percentile 等） |

## 6. Macro / cross-market

| view | rows | date range | 描述 |
|---|---:|---|---|
| `macro_daily` | 91,048 | 2017-12-31 ~ 2026-05-07 | VIX / USDTWD / WTI / 美 10Y 等 |
| `cross_market_features` | 2,080 | (date 欄為 NULL) | 跨市場 derived（vol-corr 等） |

## 7. Events / attrs

| view | rows | date range | 描述 |
|---|---:|---|---|
| `tw_stock_trading_attrs_daily` | 3,154,720 | 2021-01-04 ~ 2026-05-21 | 個股交易屬性（注意 / 處置 / 全額交割） |
| `tw_stock_futures_corp_actions` | 55,741 | 2013-01-17 ~ 2026-09-16 | 個股期調整事件（除權息 / 公司行動） |
| `security_attrs` | 3,405 | — | 標的屬性快照 |
| `tw_inst_market_daily` | 15 | 2026-04-14 ~ 2026-04-16 | 市場層級三大法人彙總（aggregated） |

## 8. Derived gold

| view | rows | date range | 描述 |
|---|---:|---|---|
| `stock_factor_daily` | 6,597,986 | 2010-01-04 ~ 2026-05-22 | 個股技術因子：`ret_1d/5d/20d/60d/120d`, `mom_12_1`, `vol_20d/60d`, `turnover_20d` |
| `inst_flow_factors` | 6,567,005 | 2010-01-04 ~ 2026-05-22 | 個股法人流量因子（9 個）：`foreign_net_5d/20d/60d`, `sitc_net_5d/20d`, `dealer_net_5d/20d`, `foreign_hold_pct_chg_20d`, `inst_net_persistence_20d` |
| `margin_factors` | 3,713,424 | 2010-01-04 ~ 2026-05-22 | 融資融券時序因子（6 個）：`margin_balance_chg_5d/20d`, `short_balance_chg_5d/20d`, `margin_util_zscore_60d`, `short_to_margin_chg_20d` |
| `fundamentals_pit` | 93,525 | 2010-05-15 ~ 2026-03-31 | PIT 財務 panel（依 publish_date 對齊）：`eps`, `roe_post`, `eps_ttm`, `revenue_ttm`, `roe_ttm_avg`, `ni_yoy_chg_pct`, `revenue_yoy_chg_pct` |
| `futures_large_trader_factors` | 99,162 | 2017-08-21 ~ 2026-05-22 | 期貨大額交易人因子：`top10_net_pct`, `top10_institutional_net_pct`, `top5_concentration_avg`, `oi_chg_5d/20d` |
| `futures_inst_factors` | 466,047 | 2008-01-02 ~ 2026-05-22 | 期貨三大法人因子（162 identities × 5 factors）：`net_oi_chg_5d/20d`, `net_volume_zscore_60d`, `long_short_oi_ratio`, `volume_to_oi_ratio` |
| `stock_attrs_status` | 3,160,185 | 2021-01-04 ~ 2026-05-25 | 個股交易屬性 boolean panel：`is_attention_bool`, `is_disposition_bool`, ..., `is_twn50_bool`, `is_hdiv_bool` 等 + `attention_count_30d` / `disposition_count_30d` |
| `dividend_calendar` | 10,458 | 2022-01-03 ~ 2026-10-15 | 除權息 event panel：`cash_div_per_share`, `div_yield_pct`, `ttm_cash_div_per_share`, `yoy_growth_pct`, `days_announce_to_ex` |
| `stock_futures_adjustments` | 56,049 | 2013-01-17 ~ 2026-09-16 | 個股期累計調整：`cum_cash_div_per_share`, `cum_stock_div_per_share`, `cum_equity_value_per_lot`, `prev_adjust_date`, `days_since_prev_adj`, `adj_seq_no` |
| `futures_bar_factors` | 1,741,051 | 2010-01-04 ~ 2026-05-22 | 期貨日 K 衍生（tw_futures + tw_stock_futures, 378 symbols）：`ret_5d/20d/60d`, `vol_20d/60d`, `atr_14`, `turnover_20d`, `oi_chg_5d/20d` |
| `cross_market_features` | 2,080 | (date 欄為 NULL) | 跨市場 derived（vol-corr 等） |

Builders 都在 `src/qd_ingest/sources/derived.py`。重生整批：

```bash
PYTHONPATH=src .venv/bin/python -m qd_ingest.sources.derived
```

執行 `build_all()`：txo_daily_features (copy)、cross_market_features (copy + 修 index)、stock_factor_daily / inst_flow_factors / margin_factors / fundamentals_pit / futures_large_trader_factors / futures_inst_factors / stock_attrs_status / dividend_calendar / stock_futures_adjustments / futures_bar_factors (polars 衍生)、qc_snapshot / finmind_canonical (DuckDB SQL 物化)。整批 < 60s。

!!! note "Silver dedup 副產品"

    `tw_stock_trading_attrs_daily` / `tw_inst_futures_full_daily` / `cash_dividend_events` / `tw_stock_futures_corp_actions` silver 因多次 ingest 而有 6–19% 重覆列。本批 builder 統一以 `unique(keep='last' by ingestion_ts)` 在 gold 層去重，所以 gold rows 通常 < silver rows，這是 feature 不是 bug。

## 9. FinMind bronze（snapshot 2026-05-18）

詳見 [FinMind 整合頁](finmind.md)。

| view | rows | date range | 描述 |
|---|---:|---|---|
| `finmind_stock_price` | 10,578,728 | 2000-01-04 ~ 2026-05-15 | raw 日 K（原始欄名 max/min/Trading_Volume） |
| `finmind_stock_price_norm` | 10,578,728 | 2000-01-04 ~ 2026-05-15 | canonical 命名（high/low/volume）|
| `finmind_stock_price_adj` | 10,571,636 | 2000-01-04 ~ 2026-05-13 | 還原權息 raw |
| `finmind_stock_price_adj_norm` | 10,571,636 | 2000-01-04 ~ 2026-05-13 | 還原權息 canonical |
| `finmind_stock_info` | 3,088 | 2020-06-01 ~ — | 全市場（含興櫃）清單 |
| `finmind_stock_info_with_warrant` | 126,311 | 2021-01-04 ~ 2026-05-14 | 含權證清單 |
| `finmind_trading_date` | 6,512 | 2000-01-04 ~ 2026-05-14 | 2000-2026 交易日曆 |
| `finmind_stock_week_price` | 2,225,018 | 2000-01-03 ~ 2026-05-11 | 週 K |

## 9b. FinMind canonical gold

| view | rows | date range | 描述 |
|---|---:|---|---|
| `finmind_price_canonical` | 10,592,556 | 2000-01-04 ~ 2026-05-22 | FinMind `raw OHLCV + adj OHLC` LEFT JOIN，parquet 持久化（取代 sqlite 查詢） |

> Source: `finmind_stock_price_norm`（10.6M）LEFT JOIN `finmind_stock_price_adj_norm`（10.6M）on (trading_date, stock_id)。輸出含 `open/high/low/close + adj_open/adj_high/adj_low/adj_close + volume/amount_twd/spread/turnover`。

## 10. QC

| view | rows | date range | 描述 |
|---|---:|---|---|
| `qc_stock_price_diff` | 6,386,130 | 2010-01-04 ~ 2026-05-22 | TEJ vs FinMind 2010+ 重疊段（純 view，computed only） |
| `qc_stock_price_diff_snapshot` | 6,386,130 | 2010-01-04 ~ 2026-05-22 | 同上但物化為 parquet（gold/features/qc_stock_price_diff_snapshot.parquet） |
| `qc_stock_price_diff_yearly` | 17 | 2010 ~ 2026 | 逐年彙總：`mean_abs_pct_diff`, `max_abs_pct_diff`, `rows_diff_gt_1pct`, `mean_vol_rel_diff`，2011+ TEJ/FinMind close 完全一致 |

## 11. Reference

| view | rows | 描述 |
|---|---:|---|
| `symbol_map` | 30 | 期貨代碼對應表（TXF/MXF/...） |
| `contract_specs` | 12 | tick size / multiplier / settlement |
| `calendar_xtai` | 3,924 | XTAI 交易日曆 2010-2025 |

> 規模統計：所有 view 加總 ≈ 8,700 萬列，物理 parquet < 4 GB（zstd L3）。
