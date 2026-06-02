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

## Fallback

```bash
git revert HEAD~3..HEAD
# 或單獨還原 derived.py：
git checkout HEAD~3 -- src/qd_ingest/sources/derived.py
```
