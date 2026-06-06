# 進度 — 當日增量爬蟲即時監控 dashboard（/live）

## 目標

在現有 Search UI（Flask, port 5050）加一個 `/live` 頁面：即時顯示**當日**增量爬蟲
（`qd-ingest` 各 fetch/build 腳本）寫入 `meta/audit/ingest_<YYYY-MM-DD>.jsonl` 的
審計事件，作為當日實盤前/盤中的資料監控模組 —— 哪些 table 已更新、各 source 進度、
失敗事件即時告警，無需手動 reload。

## 計畫 milestone

| # | 內容 | 預期產出 |
|---|------|---------|
| M1 | 後端 live monitor 模組 + API + unit tests | `ui/search/live_monitor.py`（增量 tail JSONL、彙整 per-table 狀態）、`/api/live/summary`、`/api/live/events`、SSE `/api/live/stream`、`tests/test_live_monitor.py` |
| M2 | 前端 `/live` 頁面 | `templates/live.html` — source 統計列、table 卡片牆（status/rows/max_date/elapsed）、即時事件 feed、SSE 自動更新 + polling fallback、nav 連結 |
| M3 | e2e 測試 + 文件收尾 | e2e route 測試、README/launcher 提示、進度檔完結 |

## 設計重點

- **資料來源單一真相**：`meta/audit/ingest_<date>.jsonl`（`qd_ingest.common.audit.write_audit`
  append-only），dashboard 純讀，不碰 DuckDB catalog（避免鎖衝突）。
- **增量 tail**：client 帶 byte offset 輪詢/SSE，server 只讀新增 bytes，避免每次重 parse 整檔。
- **跨日**：頁面顯示「今天」檔案；日期可用 `?date=YYYY-MM-DD` 回看。

## 進度日誌

（每完成一個 milestone 追加）

## Fallback 指引

- 回滾：`git log --oneline | grep 'M[0-9]:'` 找對應 commit，`git revert <hash>`。
- 核心檔案：`ui/search/live_monitor.py`、`ui/search/app.py`（routes）、
  `ui/search/templates/live.html`、`tests/test_live_monitor.py`。
- 啟動驗證：`scripts/run_search_ui.sh` → http://127.0.0.1:5050/live
