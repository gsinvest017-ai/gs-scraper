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
| **M2** | extend `Dataset` dataclass + 填 raw/bronze/silver/gold tuple + `_measure_layer()` helper | ✅ |
| **M3** | render 新欄位（5 個 layer cell）+ regen 兩份 HTML + JSON | ✅ |
| **M4** | push live | ✅ |

## 進度日誌

### M2 — Dataset extended + measure_layer

加入 `raw_paths / bronze_paths / silver_paths / gold_paths` 為 `tuple[str, ...]` 預設空。`field(default_factory=tuple)` 給 dataclass 默認。25 個 dataset 全部填好 path glob patterns（含 brace-expansion 的 `{NQ,ES,GC}_1min_*.zip` 跟 `**/*.parquet` 遞迴）。

`_measure_layer(patterns)` helper：手寫 brace expansion（單一 `{a,b,c}`）+ `glob.iglob(recursive=True)` + `os.walk` 累計 file size。dedupe 用 `seen` set 避免單一 layer 內重複。回傳 `{file_count, size_bytes, examples (up to 3)}`。

`_fmt_bytes(n)` 把 bytes → `2.5 GB / 766.1 MB / 443.0 KB` 樣式。

`probe()` 每筆 dataset 都計 4 個 layer，塞進 `row["layers"]`。JSON 自動 include。

### M3 — HTML 渲染新欄位

HTML_TEMPLATE：

- 加 4 個 layer total pill（📦 Raw 2.5 GB / 🥉 Bronze 2.3 GB / 🥈 Silver 698 MB / 🥇 Gold 294 MB）在 severity summary 下方
- 表格 header 多 5 欄：`📦 Raw / 🥉 Bronze / 🥈 Silver / 🥇 Gold / 📊 Catalog rows`
- CSS 給 `.layer` class 小字 + tabular-nums + tooltip 顯示 examples

`render_html`：

- 新 `layer_cell(info)` 印 `{size}<span class="files">·{n}</span>`；title attr = sample paths
- 新 `catalog_rows_cell(r)` 印 `6.6M / 12.5K / —` style；title = exact count
- **總計 dedupe**：用 `layer_pattern_union` 把所有 dataset 的 patterns 合進 set 再 `_measure_layer` 一次，避免共用 FinMind sqlite 被算兩次（修正後 Bronze 從 4.7 GB → 2.3 GB）

`mkdocs build --strict` PASS。

### M4 — push live

`git push origin main` (7c037af) → docs.yml workflow ~30s 完成。CDN cache 25s 後 cache-bust 抓到新版：「🥉 Bronze 2.3 GB / 🥈 Silver 698.4 MB / 🥇 Gold 294.4 MB」與五個 `<th>` 都正確顯示。Live URL: https://gsinvest017-ai.github.io/gs-scraper/gap_dashboard.html

## Fallback

- 改壞 column 排版：`git revert <M3-commit>`
- 路徑映射打錯：直接修 DATASETS registry 內的 patterns
- 計 size 太慢：可以 cache 結果到 `meta/audit/storage_inventory.json`
