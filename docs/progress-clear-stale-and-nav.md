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

## 進度日誌

### M1 — gap_dashboard 加 nav 返回 Search UI  `(M1 commit)`

`scripts/gap_report.py` HTML template 加 `<nav class="topnav">`，含「← Search UI」
按鈕（金色 hover）+ Downloads 連結 + Gap dashboard 當前位置 highlight。
重生 `docs/gap_dashboard.html` + `docs-site/gap_dashboard.html`。

### M2 — `scripts/refresh_continuous_from_raw.py`  `(M2 commit)`

把 RAW_SOURCES 內手動 dump 的連續期 parquet 標準化進 gold/continuous/：

| view | rows | max_date | source |
|---|---|---|---|
| `tx_continuous_d` | 2,518 | 2026-05-08 | `日k 期貨tquant lab/TX_continuous_raw.parquet` |
| `mtx_continuous_d` | 2,518 | 2026-05-08 | 同上 MTX |
| `stock_futures_continuous_d` | 539,992 / 314 contracts | 2026-04-10 | `股票期貨/continuous_near_month.parquet` |

腳本可被 cron 呼叫；目前 RAW 本身停在 5/8、4/10，所以 dashboard 還顯示 STALE
（不是腳本 bug）。

### M3 — cross_market 修 + 退役死的 inst_market 兩條  `da31ba4`

- **真因**：`copy_cross_market_features` 對 named index `Date` 的處理錯了
  （只認 unnamed non-RangeIndex），所以 date 全 NaT，dashboard 看到 EMPTY。
  改成「任何非 RangeIndex 都 reset 拉回 column，再依名字 rename → 'date'」。
  date range 2018-04-30 → 2026-04-28 ✓
- **退役**：silver `tw_inst_market_daily` 是 15 row / 2026-04-16 的死 view，
  由 `market_inst_aggregated`（OK）取代；gap_report.DATASETS 拿掉這兩條，
  build_all() 同步註解 materialize_tw_inst_market_daily_snapshot。
- **效果**：STALE 7→5、EMPTY 1→0。

### M4 — `scripts/ingest_bars_1m.py` + 重生 dashboard  `(M4 commit)`

寫 `ingest_mxf_1m()`：把 1.67M rows MXF 1 分鐘 K 寫進
`silver/bars/bars_1m/asset_class=tw_futures/symbol=MXF/year=YYYY/`，
hive-partitioned 7 個年份（2020-2026），dedup by datetime。

跑完後 `bars_1m` view 共 **15.6M rows**（4 個 symbol：us_futures GC/ES/NQ +
tw_futures MXF）。`build_bars_1m_daily_summary` 也跑了，14,859 rows。

dashboard 最終狀態：

| 狀態 | 起 | 終 | delta |
|---|---|---|---|
| ✅ OK | 31 | **31** | 0 |
| ℹ️ INFO | 3 | 4 | +1（cross_market 修好但 max_date 仍 35d） |
| ⚠️ WARN | 4 | 4 | 0 |
| 🔴 STALE | 7 | **5** | **-2**（移除 inst_market 兩條） |
| ❓ EMPTY | 1 | **0** | **-1**（cross_market 修好） |

剩 5 條 STALE 全是 RAW 自身過時（24/24/52/81/81 天），cron 跑我新加的腳本沒辦法
讓資料變新，**等使用者手動更新 RAW 就會自動 cover**。

## 還沒解的（下一輪 todo）

1. **接 cron**：把 `refresh_continuous_from_raw.py` + `ingest_bars_1m.py` 串
   進 `daily_refresh.sh`（在 step 3.7 derived rebuild 之前），確保使用者
   `RAW_SOURCES` 更新後當天 cron 就自動 propagate。
2. **bars_1m hive partition 顯示 None None**：DuckDB GROUP BY 時 MXF 的
   asset_class/symbol 顯示 None（雖然 parquet 內欄位正常）。要追是
   `hive_partitioning=TRUE` 跟 in-file column conflict 造成；不影響 max_date，
   先 mark 為已知。
3. **RAW_SOURCES 自動下載**：tx_continuous / mtx_continuous / 個股期 / MXF
   1m 都是手動 dump，有沒有 TEJ 等自動 source 是另一個工程。

## Fallback

```bash
git revert HEAD~4..HEAD
rm -f scripts/refresh_continuous_from_raw.py scripts/ingest_bars_1m.py
git checkout HEAD~4 -- scripts/gap_report.py src/qd_ingest/sources/derived.py
```
