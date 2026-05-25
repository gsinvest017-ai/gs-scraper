# Gap dashboard — 加 storage layer 欄位

> 啟動：2026-05-25
> 觸發：`/safe-yolo 在gap dashboard裡面新增欄位來標明每個data catalog的raw data/bronze lvl/silver lvl/gold lvl/final db 的資料路徑位置/資料筆數/資料儲存尺寸(GB) 以便於讓人類可以在資料遷移時寫checklist`

## 目標

每個 dataset 在 dashboard 上**同時列出 5 個 storage layer 的概況**（路徑 / 列數 / 大小），讓人類能依此寫資料遷移 checklist：

1. **Raw** — `RAW_SOURCES/` 內的原始 zip/csv/parquet
2. **Bronze** — `bronze/` 不可變層（多數 TEJ ingest 跳過 bronze 直接寫 silver；FinMind 才有 bronze sqlite）
3. **Silver** — `silver/` canonical parquet
4. **Gold** — `gold/` derived parquet（continuous / factors）
5. **Catalog** — `catalog/quant.duckdb` 內的 view（只是 DDL，無資料）

## 設計

每個 layer 一個 cell，內含：
- 大小 (`x MB` / `y GB`)
- 檔案數量
- 路徑作為 tooltip (`title=...`)

Rows：
- Catalog view rows = 既有 `row_count`
- Silver/gold parquet rows = 與 view rows 相同（view 是 SELECT * FROM parquet）；不重複算
- Raw / bronze rows = 通常無法快速 count（csv/sqlite 要打開讀）→ 只顯示 file count + size

## Dataset → layer 路徑映射（25 個）

| view | raw | bronze | silver | gold |
|---|---|---|---|---|
| tw_stock_bars | `RAW_SOURCES/TEJ資料/TWN_EWPRCD_股價.csv` | — | `silver/bars/bars_1d/asset_class=tw_stock/**` | — |
| bars_1d | (composite) | — | `silver/bars/bars_1d/**` | — |
| bars_1m | `RAW_SOURCES/MXF_1m_clean_all.parquet`, `RAW_SOURCES/{NQ,ES,GC}_1min_*.zip` | — | `silver/bars/bars_1m/**` | — |
| tw_inst_stock_daily | `RAW_SOURCES/TEJ資料/TWN_EWTINST1_三大法人.csv` | — | `silver/flows/tw_inst_stock_daily/**` | — |
| tw_margin_daily | `RAW_SOURCES/TEJ資料/TWN_EWGIN_融資融券.csv` | — | `silver/flows/tw_margin_daily/**` | — |
| tw_inst_futures_daily | `RAW_SOURCES/三大法人買賣超/**` | — | `silver/flows/tw_inst_futures_daily/**` | — |
| tw_inst_futures_full_daily | (TEJ API → silver) | — | `silver/flows/tw_inst_futures_full_daily/**` | — |
| tw_futures_large_trader_daily | (TEJ API → silver) | — | `silver/flows/tw_futures_large_trader_daily/**` | — |
| tw_chip_dist_daily | (TEJ API → silver) | — | `silver/flows/tw_chip_dist_daily/**` | — |
| tw_inst_market_daily | (derived) | — | `silver/flows/tw_inst_market_daily/**` | — |
| tw_stock_trading_attrs_daily | (TEJ API → silver) | — | `silver/flows/tw_stock_trading_attrs_daily/**` | — |
| tw_stock_futures_corp_actions | (TEJ API → silver) | — | `silver/flows/tw_stock_futures_corp_actions/**` | — |
| revenue_monthly | (TEJ API → silver) | — | `silver/fundamentals/revenue_monthly/**` | — |
| fundamentals_q | `RAW_SOURCES/TEJ資料/TWN_EWIFINQ_單季財報.csv` | — | `silver/fundamentals/fin_q/**` | — |
| accounting_raw | (TEJ API → silver) | — | `silver/fundamentals/accounting_raw/**` | — |
| cash_dividend_events | (TEJ API → silver) | — | `silver/fundamentals/cash_dividend_events/**` | — |
| security_attrs | (TEJ API → silver) | — | `silver/reference/security_attrs/**` | — |
| macro_daily | (yfinance) | — | `silver/macro/**` | — |
| txo_daily_features | `RAW_SOURCES/選擇權日盤逐筆原始資料_TXO.parquet/**` | — | `silver/options/**` | — |
| tx_continuous_d | `RAW_SOURCES/日k 期貨tquant lab/TX_continuous_*.parquet` | — | — | `gold/continuous/tx_continuous_d.parquet` |
| mtx_continuous_d | `RAW_SOURCES/日k 期貨tquant lab/MTX_continuous_*.parquet` | — | — | `gold/continuous/mtx_continuous_d.parquet` |
| stock_futures_continuous_d | `RAW_SOURCES/股票期貨/continuous_near_month.parquet` | — | — | `gold/continuous/stock_futures_continuous_d.parquet` |
| stock_factor_daily | (derived from silver) | — | — | `gold/features/stock_factor_daily.parquet` |
| cross_market_features | (derived from silver) | — | — | `gold/features/cross_market_features.parquet` |
| finmind_stock_price_norm | `RAW_SOURCES/FINMIND資料集.zip` | `bronze/finmind/finmind_*.sqlite` | — | — |
| finmind_stock_price_adj_norm | 同上 | 同上 | — | — |
| qc_stock_price_diff | (pure view, JOIN TEJ silver × FinMind bronze) | — | — | — |

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔（含映射表） | ✅ |
| **M2** | extend `Dataset` dataclass + 填 raw/bronze/silver/gold tuple + `_measure_layer()` helper | ⏳ |
| **M3** | render 新欄位（5 個 layer cell）+ regen 兩份 HTML + JSON | ⏳ |
| **M4** | push live | ⏳ |

## Fallback

- 改壞 column 排版：`git revert <M3-commit>`
- 路徑映射打錯：直接修 DATASETS registry 內的 patterns
- 計 size 太慢：可以 cache 結果到 `meta/audit/storage_inventory.json`
