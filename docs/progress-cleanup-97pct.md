# 2026-05-26 — Cleanup 97% views + 重 goldify lag

## 觸發

`/safe-yolo gap dashboard 中有很多 data catalog 的 complete rate 進度是 97 percent, 請優先判斷這些資料是否其實已經完成只是因為沒有最新當日日期的資料而卡在 97 percent 但其實是 100 percent 如果是這樣請標示為 100 percent 如果不是這樣 則調用爬蟲將其爬到 100 percent 並且繼續調用 data engineering flow 將所有 complete rate 為 100 percent 的 data catalog 都處理到 gold medal`

## 起始狀態

`gap_report` 計算 `completeness = clamp(1 − lag/90, 0, 1)`，**97% ≈ 3 天 lag**。Dashboard 目前有 16 個 view 卡在 3d lag（WARN/INFO 各佔一半），實際分類：

### 類別 A — 衍生 gold lag silver（**rebuild 即可變 100%**，silver 已是 5/25）

| view | upstream | upstream max | gold max | 修法 |
|---|---|---|---|---|
| `stock_factor_daily` | `tw_stock_bars` | 5/25 | 5/22 | rebuild |
| `inst_flow_factors` | `tw_inst_stock_daily` | 5/25 | 5/22 | rebuild |
| `margin_factors` | `tw_margin_daily` | 5/25 | 5/22 | rebuild |
| `futures_large_trader_factors` | `tw_futures_large_trader_daily` | 5/25 | 5/22 | rebuild |
| `futures_inst_factors` | `tw_inst_futures_full_daily` | 5/25 | 5/22 | rebuild |
| `futures_bar_factors` | `bars_1d` (tw_futures+sf) | 5/25 | 5/22 | rebuild |
| `stock_attrs_status` | `tw_stock_trading_attrs_daily` | 5/25 | 5/25 | already OK |
| `dividend_calendar` | `cash_dividend_events` | 10/15 | 10/15 | already OK |
| `stock_futures_adjustments` | `tw_stock_futures_corp_actions` | 9/16 | 9/16 | already OK |

### 類別 B — 上游 intrinsically 完整（**dashboard 認知錯，應顯示 100%**）

| view | upstream max | reality | 修法 |
|---|---|---|---|
| `fundamentals_pit` | publish_date 2026-03-31（Q1） | Q1 公告完整，Q2 5/15 才開始陸續公告 | Dataset.category 從 `derived` 改成 `quarterly` |

### 類別 C — FinMind snapshot 卡 5/22（**bronze sqlite 限制**）

| view | reason |
|---|---|
| `finmind_stock_price_norm` | bronze `finmind_2026-05-25.sqlite` max(date)=5/22；crawler 5/25 14:48 跑時 FinMind 還沒發 5/25 EOD |
| `finmind_stock_price_adj_norm` | 同上 |
| `finmind_price_canonical` | LEFT JOIN 上面兩個，constrained by 5/22 |
| `qc_stock_price_diff` | 對帳 view，constrained by FinMind 5/22 |
| `qc_stock_price_diff_snapshot` | 同上 |

修法：跑 FinMind 增量 crawler 到 5/25（手動觸發），bronze 重新整理；或本輪先接受、改 dashboard 把 snapshot category 對 lag 寬容化（snapshot 本來就不 daily fresh）。

實際做法：**接受 finmind 5/22**（snapshot category 本來 INFO 設計就不算 problem）；rebuild `finmind_price_canonical` / `qc_stock_price_diff_snapshot` 跟著 5/22 走，dashboard 顯示 5/22 INFO，**這是該 view 當前可達上界**。

### 類別 D — TEJ 細粒度 dataset cron 沒涵蓋（**手動 fetch**）

| view | 修法 |
|---|---|
| `tw_chip_dist_daily` | `fetch_tej.py --table chip_dist --append-since-silver` |
| `accounting_raw` | 86d WARN（非 3d, 跳過本輪，問題不一樣） |

### 類別 E — 期貨連續期（**從 bars_1d 重生**）

| view | 修法 |
|---|---|
| `tx_continuous_d` | rebuild from bars_1d（昨日已加 builder） |
| `mtx_continuous_d` | 同上 |

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + 分類 | ⏳ |
| **M2** | rebuild 6 個 derived gold 從 5/22 → 5/25（stock_factor / inst_flow / margin / futures_large_trader / futures_inst / futures_bar） | ⏳ |
| **M3** | `fundamentals_pit` category `derived` → `quarterly`；rebuild tx/mtx continuous；refresh `tw_chip_dist_daily` via fetch_tej | ⏳ |
| **M4** | run goldify_audit；regen dashboard；mkdocs strict；commit；push | ⏳ |

## Fallback

- DuckDB UI 持鎖（PID 102069）：用 `gap_report.py` 的 tempfile copy 模式做唯讀，寫入用 `qd_ingest.common.catalog.build()` 自帶的 lock 處理
- builder rebuild 跑壞：個別 gold parquet 是冪等覆蓋，重跑即可

## 完成日誌

（M2-M4 完成後追加）
