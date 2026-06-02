# 2026-06-02 — 加速 RAW → silver/gold 清洗

## 目標

`goldify_audit` 顯示 catalog 內 view 全已 goldified（0 candidates），但
`RAW_SOURCES/` 還有沒被 ingest 的檔。本輪盤點所有 RAW 檔、實作可立刻清洗的、
列出無法簡單處理的（含理由）。

## RAW_SOURCES 盤點

| 檔/目錄 | 狀態 | 處置 |
|---|---|---|
| `加權指數日線_2020-2026.csv` | ✅ 已涵蓋 | `macro_daily` 已有 TAIEX 1966 rows 從 2018 起 |
| `無風險利率日資料_2019-2026.csv` | ❌ **missing** | **M2 ingest** → `silver/macro/rf_daily.parquet` |
| `TXO_1min_merged_*.parquet` (2.19M rows) | ❌ **missing** | **M3 ingest** → `silver/options/txo_1min/` |
| `選擇權日盤逐筆原始資料_TXO.parquet` (2.68M rows) | ⚠️ 重疊 finmind_txo_option_daily（中文 header） | **skip** — 與既有 view 同範圍且 header 需翻譯 |
| `MXF_1d_clean_all.parquet` | ✅ 已涵蓋 | bars_1d 內 `tw_futures/MXF` 1523 rows 與此檔同 |
| `MXF_1m_clean_all.parquet` | ✅ 已涵蓋 | silver/bars/bars_1m/tw_futures/MXF 已 ingest |
| `日k 期貨tquant lab/` | ✅ 已涵蓋 | gold/continuous + daily_refresh step 3.55 |
| `股票期貨/` | ✅ 已涵蓋 | gold/continuous/stock_futures_continuous_d.parquet |
| `外資投信買賣超資料/institutional_clean.parquet` (3.8M rows, 2012-2026) | ⚠️ 重疊 tw_inst_stock_daily（2010-2026, 6.6M rows） | **skip** — 既有 silver 涵蓋更廣更新 |
| `三大法人買賣超/` | ⚠️ 只有 .md/.png 筆記 | **skip** — 無實質資料 |
| `台指期一分鐘/` | ⚠️ 內含巢狀 `三大法人買賣超/`（似建構錯誤目錄） | **skip** — 結構不清，需使用者整理 |
| `TAIFEX_BACKFILL_INBOX/` | ⚠️ 空目錄 | **skip** — 預留 inbox 沒檔 |
| `DATA_BY_SYMBOL/{DIA,ES,GC,NQ,QQQ}` | ✅ 已涵蓋 | bars_1m 已有 us_futures GC/ES/NQ |
| `SUPPLEMENT/` | ✅ 已涵蓋 | DERIVED/cross_market + US_FUTURES/NQ 已用 |
| `FINMIND資料集.zip` | ✅ 已涵蓋 | 已解壓進 bronze/finmind/ |
| `archives/` / `_backup_tej_*/` | — | backup，跳過 |
| `RS_Rating.7z` | ⚠️ 壓縮檔 | **skip** — 需 7z 解壓 + 內部結構未知 |

## 計畫 milestone

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔（盤點） |
| **M2** | `scripts/ingest_rf_daily.py`：rf CSV → `silver/macro/rf_daily.parquet`；`catalog.py` 加 view |
| **M3** | `scripts/ingest_txo_1min.py`：parquet → `silver/options/txo_1min/`；`catalog.py` 加 view；`gap_report.py` Dataset registry |
| **M4** | 重建 catalog（lock-immune 走 tmp）+ 重生 dashboard + 進度檔收尾 |

## 為何其餘檔「無法簡單處理」

1. **`選擇權日盤逐筆 TXO`** — Chinese-header schema + 與 `finmind_txo_option_daily` 重疊；解 = 寫 chinese→english column mapper + 重複資料合併策略。**省略**：對價值低。
2. **institutional_clean**（外資投信）— 已被 `tw_inst_stock_daily` 完整涵蓋（min date 早 2 年、rows 多 2.7M）。**沒新資訊**。
3. **三大法人買賣超 .md/.png** — Notion notes 與螢幕截圖，不是結構化資料。
4. **`台指期一分鐘/`** — 內部結構畸形（含巢狀 `三大法人買賣超/` 子目錄）— 像建構錯誤；需使用者澄清。
5. **`RS_Rating.7z`** — 7z 壓縮，內容未知；解壓後再評估。本輪不開。

## 進度日誌

### M1 — 盤點  `(M1 commit)`

RAW_SOURCES 內 13 個 entry。掃完發現：
- 6 個已涵蓋（macro_daily / bars_1m / bars_1d / gold/continuous / bronze/finmind 等）
- 2 個 missing 可立刻 ingest（**rf** + **TXO 1min**）
- 5 個 deferred（重疊、結構不清、需解壓）

### M2 — `ingest_rf_daily.py` + catalog/gap_report 註冊  `(M2 commit)`

`scripts/ingest_rf_daily.py`：讀 CSV → dedup → 寫 `silver/macro/rf_daily.parquet`。
catalog.py 加 `rf_daily` view。gap_report.DATASETS 加一條 P1 entry。

跑出：rows 2922、date 2019-01-01 → 2026-12-31、rf 均 0.845%（max 1.225% / min 0.350%）。

### M3 — `ingest_txo_1min.py` + view + 重建 catalog  `5ac8531`

`scripts/ingest_txo_1min.py`：2.19M rows TXO 1分鐘 K → `silver/options/txo_1min/year=YYYY/`
hive partition；dedup by (trade_date, expiry_month, strike, option_type, minute)。

catalog.py 加 `txo_1min` view (`hive_partitioning=TRUE` + `union_by_name=TRUE`)。
重建 catalog：59 → **61 views**。swap 進 live catalog（duckdb -ui 用既有 fd
讀舊 inode，不會炸）。

dashboard 變化：
- OK 31 → **32**（rf_daily OK）
- STALE 5 → **6**（txo_1min STALE，因 RAW 自身過時，max 4/22）
- 其餘不變

`meta/gap_comments.json` 加 txo_1min 註解。

### M4 — daily_refresh.sh 串接新 ingest + 收尾  `(M4 commit)`

`daily_refresh.sh` 在 step 3.55/3.56 之後加 step 3.57 (rf) + 3.58 (txo_1min)，
都 non-fatal。**未來 RAW 更新 → 隔日 cron 自動 propagate**。

整段 propagate chain：

```
RAW_SOURCES update                              ← 使用者手動
        ↓ (cron 17:30 CST)
step 3.55  refresh_continuous_from_raw.py       → gold/continuous/{tx,mtx,個股期}
step 3.56  ingest_bars_1m.py                    → silver/bars/bars_1m/MXF
step 3.57  ingest_rf_daily.py                   → silver/macro/rf_daily
step 3.58  ingest_txo_1min.py                   → silver/options/txo_1min
step 3.7   build_all                            → 衍生 gold
step 4     gap_dashboard 重生
```

## 為何其餘檔「無法簡單處理」（最終版）

1. **`選擇權日盤逐筆 TXO`** — Chinese-header schema + 與 `finmind_txo_option_daily` 完全重疊；無新資訊。
2. **institutional_clean.parquet** — 已被 `tw_inst_stock_daily` 完整涵蓋（2010-2026, 6.6M vs 3.8M rows）。
3. **三大法人買賣超 .md/.png** — Notion 筆記 + 截圖，非結構化。
4. **`台指期一分鐘/`** — 目錄結構畸形（內含巢狀 `三大法人買賣超`），需使用者整理。
5. **`RS_Rating.7z`** — 壓縮檔，內容未知；下一輪可解壓後評估。

## 下一輪建議

- **txo_1min 衍生 gold**：用 1min OHLC 算 intraday vol cone / IV time-of-day pattern；補進 `derived.py`
- **rf_daily 串進 BS-IV**：目前 `_TXO_RF` 寫死 0.015，可改為 `rf_daily` 對應日期動態取
- 整理使用者的 `台指期一分鐘/` 目錄結構後重新評估

## Fallback

```bash
git revert HEAD~4..HEAD
rm -f scripts/ingest_rf_daily.py scripts/ingest_txo_1min.py
```
