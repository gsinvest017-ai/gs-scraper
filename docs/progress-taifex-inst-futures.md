# 2026-05-27 — TAIFEX 期貨三大法人 接 cron（bottleneck #4）

## 觸發

`/safe-yolo 陸續按照推薦排序解決還未解決的問題`（#1/#2/#3 已完成；本輪做 #4）

## 目標

2 個 P0 view 卡在 2026-05-08（18d STALE，無 auto-refresh）：

| view | 來源（舊） |
|---|---|
| `tw_inst_futures_daily` | 手動 dump `SUPPLEMENT/TAIFEX/foreign_oi_daily.parquet`（wide）→ `taifex.py::ingest_inst_futures` melt 成 long silver |
| `tw_inst_futures_daily_snapshot` | 上面的 gold（`materialize_tw_inst_futures_daily_snapshot`） |

## 關鍵決策：**derive，不寫新 web scraper**

優先序清單寫的是「TAIFEX 期貨三大法人 **fetcher**」，但勘查後發現**不需要**寫脆弱的 TAIFEX 官網爬蟲：

- **`tw_inst_futures_full_daily`（TEJ `TWN/AFINST`）已經每天自動刷新**（`fetch_tej.py --table inst_futures_full`，daily_refresh step 1，fresh 到 2026-05-26）。
- 它是 `tw_inst_futures_daily` 的**超集**：162 個 identity_code × 各商品；而 stale view 只要其中 9 個 code 聚合成 3 商品 × 3 法人。
- 因此把 stale view **從已經 fresh 的 full view 衍生**即可，零新增外部依賴、零新 failure mode、且資料已驗證逐筆相同。

### 已驗證的 9-code 對應

| product | dealer(自營11/21) | sitc(投信12/22) | fii(外資13/23) |
|---|---|---|---|
| **TXF**（臺指期） | `11TX` | `12TX` | `13TX` |
| **MXF**（小型臺指期） | `11MTX` | `12MTX` | `13MTX` |
| **TXO**（臺指選，期權 prefix 2x） | `21TXO` | `22TXO` | `23TXO` |

機構前綴：`11/21`→dealer、`12/22`→sitc、`13/23`→fii。

**逐筆驗證**（2026-05-08, fii/TXO）：舊 stale view = (net_trade 52, long_oi 27805, short_oi 29586, net_oi -1781)，full view `23TXO` = (52, 27805, 29586, -1781) **完全相同**。MXF/TXF 同理（直接對應 `*TX`/`*MTX`）。

### 涵蓋範圍更廣

full view 9-code 從 **2008-01-02 到 2026-05-26**（4,516 交易日），比舊 view（2023-05-08 起）多了 15 年歷史。衍生時用全史覆寫所有 year partition，provenance 統一為 `tej_afinst_derived`。

### 多筆 ingest 去重（HARD INVARIANT）

full view 每個 (identity_code, trading_date) 觀察到 **6 筆重複**（6 個不同 ingestion_ts）。builder 必須 `keep last by ingestion_ts`。

## 欄位對應 full → daily

| daily 欄位 | full 來源 |
|---|---|
| long_trade_contracts | long_volume |
| short_trade_contracts | short_volume |
| net_trade_contracts | net_volume |
| long_oi_contracts | long_oi |
| short_oi_contracts | short_oi |
| net_oi_contracts | net_oi |
| *_million (6 欄) | NULL（與舊一致） |
| net_oi_z60 | 每 (product,identity) 依 trading_date 的 60-row rolling zscore of net_oi_contracts |
| ts_utc | trading_date + 13:30 Asia/Taipei（沿用 taifex.py 慣例） |
| source | `tej_afinst_derived` |

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + 驗證對應 | ✅ |
| **M2** | `derive_inst_futures_daily()` in taifex.py + CLI + run（驗 max date 2026-05-26、值對齊 full） | ✅ |
| **M3** | 接進 daily_refresh.sh（step 2.6）+ docs | ✅ |
| **M4** | materialize snapshot + dashboard 驗 2 P0 STALE→OK + commit | ✅ |

## Fallback

- **full view 也壞掉**（TEJ AFINST down）：derive 會跳過（找不到 silver 或空），non-fatal；舊 silver partition 保留。可改回手動 `qd-ingest taifex-inst --parquet`。
- **z60 NaN**（歷史不足 60 日）：前 60 日為 NaN，與舊行為一致。
- **rollback**：`git revert` builder + daily_refresh step；silver 用 `delete_matching` 覆寫，重跑冪等。

## 完成日誌

### M2 — `derive_inst_futures_daily()`（commit `d10c5bd`）

- `SILVER_SCHEMA` 抽成 module 常數，`ingest_inst_futures` 與新 fn 共用。
- polars scan full view silver → filter 9 code → `sort(ingestion_ts).unique(keep="last")` 去重 → pandas → map product/identity → rename → 60d rolling z-score（pandas groupby transform）→ ts_utc 13:30 Asia/Taipei → UTC → pyarrow → `write_silver_partitioned(["year"], delete_matching)`。
- run：**40,644 rows**（4,516 交易日 × 9），2008-01-02 ~ 2026-05-26；fii/TXO 2026-05-08 = (52, 27805, 29586, -1781) 與舊 view 完全相同。

### M3 — daily_refresh step 2.6 + docs（commit `23e8547`）

- step 2.6 在 macro silver (2.5) 後、build-catalog 前，`$VENV_PY -m qd_ingest.sources.taifex`，non-fatal。
- header step 列表 + changelog + ops/daily-refresh.md（mermaid L46 + info box）。`bash -n` OK；`mkdocs --strict` PASS。

### M4 — materialize + dashboard

- `materialize_tw_inst_futures_daily_snapshot()` → gold 40,644 rows。
- dashboard：**OK 37 → 39，STALE 8 → 6**。`tw_inst_futures_daily` + `tw_inst_futures_daily_snapshot` 兩個 P0 全 OK（0d）。

## 後續

- 剩 STALE=6：`bars_1m`/`bars_1m_daily_summary`（#6，需付費源）、`txo_daily_features`/`_snapshot`（#5）、`tw_inst_market_daily`/`_snapshot`（市場層級三大法人，可由 tw_inst_stock_daily 聚合）。
- backlog：獨立 TAIFEX 官網爬蟲作為 TEJ AFINST 斷線時的備援。
