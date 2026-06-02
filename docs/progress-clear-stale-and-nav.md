# 2026-06-02 — 清剩餘 STALE + gap dashboard 加返回 nav

## 目標

兩件事：

1. gap_dashboard.html 沒有 nav 連回 Search UI views，使用者只能瀏覽器自己改 URL；
   在 dashboard 加一條返回按鈕。
2. 把上一輪未清的 7 條 STALE/EMPTY 清掉：
   - 3 條 continuous（tx/mtx/個股期）— 從 RAW_SOURCES parquet refresh 到
     `gold/continuous/`
   - 1 條 tw_inst_market_daily + 1 條 snapshot — 寫 `build_tw_inst_market_daily()`
     從 `tw_inst_stock_daily` aggregate
   - 1 條 EMPTY `cross_market_features` — 補進 `build_all()`
   - 1 條 bars_1m + 1 條 bars_1m_daily_summary — 從 `RAW_SOURCES/MXF_1m_clean_all.parquet`
     寫進 `silver/bars/bars_1m/` hive partition

## 計畫 milestone

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + gap_report.py HTML template 加「← Search UI」返回按鈕 |
| **M2** | `scripts/refresh_continuous_from_raw.py` + 跑一次 → 3 條 STALE 解 |
| **M3** | `build_tw_inst_market_daily()` aggregator + `cross_market_features` 接 build_all → 解 2 + 1 條 |
| **M4** | `scripts/ingest_bars_1m.py` 從 MXF parquet → silver hive partition；跑 build_bars_1m_daily_summary；重生 dashboard；收尾 |

## RAW_SOURCES schema 摘要

| RAW file | 來源欄位（節錄） | 目的地 |
|---|---|---|
| `日k 期貨tquant lab/TX_continuous_raw.parquet` | mdate / coid / open_d/high_d/low_d/close_d / vol_d / underlying_id … 26 cols | `gold/continuous/tx_continuous_d.parquet` |
| 同上 MTX | 同上 | `gold/continuous/mtx_continuous_d.parquet` |
| `股票期貨/continuous_near_month.parquet` | date / futures_code / open/high/low/close / volume … 24 cols | `gold/continuous/stock_futures_continuous_d.parquet` |
| `MXF_1m_clean_all.parquet` | datetime / trading_date / session / open/high/low/close / adj_* … 15 cols, 1.67M rows, 2020-03-02 → 2026-03-11 | `silver/bars/bars_1m/source=raw/...` |

## Fallback

```bash
git revert HEAD~3..HEAD
rm -f scripts/refresh_continuous_from_raw.py scripts/ingest_bars_1m.py
git checkout HEAD~3 -- scripts/gap_report.py src/qd_ingest/sources/derived.py
```
