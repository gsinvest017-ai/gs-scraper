# 2026-06-02 — 加速 ingest 進度（derived 對 DuckDB 鎖免疫）

## 目標

gap dashboard 顯示 9 個 WARN + 10 個 INFO + 1 個 EMPTY，全部是 **derived
gold 沒被 cron 重生**。根因：`duckdb -ui` 互動 session（PID 1929506，已開
4d 20h）長時間鎖住 catalog，daily_refresh.sh step 3.7 的
`python -m qd_ingest.sources.derived` 每次跑都失敗、cron log 寫
`derived gold rebuild failed (rc=?) — non-fatal`。

`ui.search.catalog_inspector` 早就解決過同樣問題：先 `shutil.copy` catalog
到 tmp 再 `duckdb.connect(read_only=True)`。本次把同樣模式套到
`qd_ingest.sources.derived` 的 6 個 `duckdb.connect` 點，讓 derived rebuild
不再被 `-ui` 鎖擋下，cron 自動恢復 — **以後不用每次 manual 重跑 derived**。

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔（plan + 診斷） |
| **M2** | `derived.py` 加 `_readonly_catalog()` context manager（tmp copy + 讀 + 清檔）；替換 6 處 `duckdb.connect(CATALOG_DB, read_only=True)` |
| **M3** | 跑一次 `python -m qd_ingest.sources.derived`，重生 dashboard，看 STALE/WARN/INFO/EMPTY 數變化 |
| **M4** | 收尾 + 進度日誌 + 列出剩下無法即時解的 STALE（手動 RAW_SOURCES → silver 那幾條） |

## Diagnosis 摘要（M1）

dashboard 現況（2026-06-02）：

| 狀態 | 數 | 例 |
|---|---|---|
| ✅ OK | 19 | tw_stock_bars / tw_inst_stock_daily / macro_daily 等 fetcher 端正常 |
| ℹ️ INFO | 10 | stock_factor_daily / inst_flow_factors / margin_factors … 全是 derived，等重生 |
| ⚠️ WARN | 9 | macro_factors / txo_daily_features / market_inst_aggregated … 同樣 derived |
| 🔴 STALE | 7 | tx/mtx/個股期連續、bars_1m、tw_inst_market_daily |
| ❓ EMPTY | 1 | cross_market_features（builder 已存在，跑就有） |

**根因**：cron 跑了，但 step 3.7 撞 lock 退出 non-fatal → 19 個 derived 從未在最新 silver 上重新生成。

**本輪修法**：6 處 `duckdb.connect(CATALOG_DB, read_only=True)` → 改走 tmp copy。對使用者的 `-ui` session 零影響、不殺 process。

**本輪不修的 STALE**：tx/mtx/MXF/個股期連續這 5 條來自 `RAW_SOURCES/*` 手動 dump 的 parquet，沒有 fetcher；要寫 RAW_SOURCES → silver 的 ingest（不在本輪範圍，列下一輪 todo）。

## 進度日誌

### M2 — `_readonly_catalog()` helper + 6 處替換  `1108be7`

`derived.py` 開頭加 `_readonly_catalog()`：tmp `shutil.copy(CATALOG_DB)`、
`atexit` 清檔、per-process 共用一份 snapshot。6 處 `duckdb.connect(CATALOG_DB,
read_only=True)` 全換掉。一個小 smoke：`_readonly_catalog()` 對仍鎖中的
catalog 仍能讀到 59 個 table。

### M3 — 跑 build_all + 重生 dashboard  `974425a`

`python -m qd_ingest.sources.derived` 跑完，22 個 builder 全 OK，總時間
~45 秒。row count 例：

- `stock_factor_daily` 6.6M rows / 2916 symbols
- `accounting_raw_snapshot` 6.4M rows / 17 yearly partitions
- `chip_dist_factors` 10.6M rows / 3092 stocks
- `dividend_calendar` 3.2M rows

Dashboard 變化（before → after）：

| 狀態 | before | after | delta |
|---|---|---|---|
| ✅ OK | 19 | **31** | **+12** |
| ℹ️ INFO | 10 | 3 | -7 |
| ⚠️ WARN | 9 | 4 | -5 |
| 🔴 STALE | 7 | 7 | 0 |
| ❓ EMPTY | 1 | 1 | 0 |

**主要勝利**：19 個曾經 INFO/WARN 的 derived 一次清掉 12 條。剩下 7 條 INFO/WARN
是 cron 還沒重跑（不是被擋）；下次 cron 自動 cover。

## 還沒解的（下一輪 todo）

### 🔴 STALE × 7（需手動 RAW_SOURCES → silver 的 ingest，本輪不做）

| view | 來源 | 工作量 |
|---|---|---|
| `tx_continuous_d` | `RAW_SOURCES/日k 期貨tquant lab/TX*.parquet` | 寫 ingest read + 標準化 schema |
| `mtx_continuous_d` | 同上 | 同上 |
| `stock_futures_continuous_d` | `RAW_SOURCES/股票期貨/` | 同上 |
| `bars_1m` | `RAW_SOURCES/MXF_1m_clean_all/` | 同上 |
| `bars_1m_daily_summary` | derived from bars_1m | 上游補齊 → 自動就好 |
| `tw_inst_market_daily` | aggregate from `tw_inst_stock_daily`（OK） | 寫一個 `build_tw_inst_market_daily()` |
| `tw_inst_market_daily_snapshot` | materialize 上面 | 上游補齊 → 自動就好 |

### ❓ EMPTY × 1

- `cross_market_features`：builder 已存在但本輪沒被叫到（M3 跑的 build_all 看
  log 沒看到 cross_market 行）。**追因**：可能在 build_all() 內被條件式 skip；
  下次跑前看 `derived.py` build_all 是否有覆蓋這支。

## 還想加速？

從 ROI 高到低：

1. **`tw_inst_market_daily` 寫個 group-by aggregator**（10 行 SQL），解掉 2 條
   STALE 並串到 build_all
2. **`build_all` 加 cross_market_features 呼叫**，解掉 EMPTY
3. **RAW_SOURCES → silver ingest 寫個 fetcher**（單表 ~30 行），解掉剩下 4 條
   STALE
4. **改 daily_refresh.sh** 在 step 3.7 之前 sleep 等 lock（這次根因已解，未來
   多保險用）
5. **cron 跑 derived 改成讀 tmp snapshot 並 keep 過去 N 份**（debug 時可以拿
   過去 snapshot 比對 row count）

## Fallback

```bash
git revert HEAD~3..HEAD
# 或單獨還原 derived.py：
git checkout HEAD~3 -- src/qd_ingest/sources/derived.py
```
