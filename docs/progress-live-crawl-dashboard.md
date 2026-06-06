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

## M1 — 後端 live monitor（commit `a2d2451`）

- `ui/search/live_monitor.py`：
  - `read_events(date, offset)` — byte-offset 增量 tail：只消費到最後一個 `\n`
    （寫入方 append 半行不吃掉）、offset > size 視為截斷從頭重讀、壞 JSON 行
    跳過但 offset 照推進。
  - `summarize(events)` — per-(source,table) 取最後一筆為當前狀態（含 runs 次數、
    `extra.max_date` / `extra.range[1]` fallback）、per-source ok/fail/rows 統計、
    全日 totals。失敗 table 排最前（兩段 stable sort）。
  - `audit_path()` 用 `^\d{4}-\d{2}-\d{2}$` 驗 date，防 path traversal。
- `app.py` 新 routes：`/live`（頁面）、`/api/live/summary?date&offset`（輪詢）、
  `/api/live/stream`（SSE，2s 檢查 + 20s keepalive comment）。
- 13 個 unit tests。途中修掉兩個 bug：(1) 初版 consumed 計算在 trailing newline
  會多推 1 byte；(2) 排序 key `(-rank, ended_at)` + `reverse=True` 方向錯誤。

## M2 — 前端 /live 頁面（commit `52b0098`）

- `templates/live.html`：統計卡（事件/資料表/成功/失敗/寫入列數）、source pills、
  資料表狀態 table（失敗標紅排最前）、即時事件 feed（200 筆上限 + flash 動畫）、
  日期下拉回看歷史。
- 連線策略：`EventSource` SSE 優先（首包全量），`onerror` 自動降級 5s 輪詢
  （帶 offset 增量）；右上角 ● 狀態燈（綠=SSE / 黃=polling）。
- `base.html` nav 加 `🛰 Live`。
- 已實測：port 5151 起 server → `/live` 200、summary API offset 增量正確
  （append 假事件只回 1 筆新事件）、SSE 首包含全量 summary。

## M3 — e2e 測試 + 文件收尾

- `tests/test_live_monitor.py` 追加 6 個 e2e route 測試（app_client +
  monkeypatch AUDIT_DIR）：頁面 200、非法 date 400（page/summary/stream）、
  summary 全量→增量流程。
- 全套 `pytest tests/` 191 passed。
- `scripts/run_search_ui.sh` 啟動訊息加 `/live` 行；README 新增
  「當日增量爬蟲即時監控（/live）」章節。

## 已知限制 / 後續方向

- SSE 每個連線一個 server thread（Flask dev server）；多人同看建議改 gunicorn
  gevent worker，或直接全走輪詢。
- audit JSONL 只記 ingest 完成事件，「進行中」的爬蟲看不到 — 若要 in-flight
  進度，得在 fetch 腳本加 start 事件或 heartbeat。
- 跨日不會自動切檔：頁面開過夜要手動切日期（或 F5）。

## Fallback 指引

- 回滾：`git log --oneline | grep 'M[0-9]:'` 找對應 commit，`git revert <hash>`。
- 核心檔案：`ui/search/live_monitor.py`、`ui/search/app.py`（routes）、
  `ui/search/templates/live.html`、`tests/test_live_monitor.py`。
- 啟動驗證：`scripts/run_search_ui.sh` → http://127.0.0.1:5050/live
