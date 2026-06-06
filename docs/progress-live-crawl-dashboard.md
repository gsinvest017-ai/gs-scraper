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

---

# 第二階段 — 最新交易日標的時間序列視圖

## 目標

/live 頁面加「投資標的時間序列」panel：以**最新交易日**為錨點，顯示選定標的
（台股 / 台期 / 股期 / 總經指數）近 N 個交易日的價量時間序列 + 最新交易日
OHLCV 與漲跌幅，作為實盤監控的行情側視圖。

## 資料源盤點（2026-06-06）

| view | 內容 | 最新日期 | 採用 |
|------|------|---------|------|
| `bars_1d` | tw_stock 2918 檔 / tw_futures 120 / tw_stock_futures 261 | 2026-06-05 | ✅ |
| `macro_daily` | 45 個總經標的（TAIEX/SPX/VIX/USDTWD…） | 2026-06-05 | ✅ |
| `bars_1m` | 只有 GC/NQ/ES（美期） | 2026-03-12（stale） | ❌ |
| `tx_continuous_d` / `mtx_continuous_d` | 台指連續 | 2026-05-08（stale） | ❌ |

## 計畫 milestone

| # | 內容 | 預期產出 |
|---|------|---------|
| M4 | 後端 timeseries API + unit tests | `ui/search/live_timeseries.py`（symbol 清單 + 近 N 日序列查詢）、`/api/live/symbols`、`/api/live/timeseries` |
| M5 | 前端 panel | live.html 加 watchlist chips（最新日報價+漲跌%）+ Plotly 價量圖 + 標的搜尋 autocomplete |
| M6 | e2e 測試 + 文件收尾 | e2e route 測試、README 補充、進度檔完結 |

## M4 — timeseries 後端（commit `2cf9a53`）

- `ui/search/live_timeseries.py`：
  - `list_symbols()` — bars_1d + macro_daily 聯集（3343 標的），重名（如 0050）
    bars_1d 優先、macro 版掛 `macro:` 前綴；process 內 cache。
  - `get_timeseries(symbol, days)` — 近 N 個交易日 OHLCV（days clamp 5..365）+
    最新交易日統計（prev_close / change / change_pct）；先試 bars_1d 再 fallback
    macro_daily，`macro:` 前綴強制 macro 源；參數全走 binding 防注入。
- routes：`/api/live/symbols`、`/api/live/timeseries?symbol&days`（400/404）。
- 13 個 unit + e2e 測試（獨立 mini duckdb fixture + monkeypatch get_connection）。

## M5 — 前端時間序列 panel（commit `9217c37`）

- watchlist chips：最新日收盤 + 漲跌%（**台股慣例紅漲綠跌**），預設
  TAIEX / 2330 / 0050 / USDTWD / VIX，localStorage 持久化，可增刪。
- Plotly candlestick + volume 雙 y 軸；最新交易日金色虛線；20/60/120/240 日切換。
- datalist autocomplete（上限 2000 筆）；per-`symbol@days` 前端 cache。
- 已實測：/live 200、symbols 3343、TAIEX 20 日序列 + 漲跌% 正確。

## M6 — 文件收尾

- README /live 章節補「標的時間序列」描述；進度檔完結。
- 全套 `pytest tests/` 204 passed。

---

# 第三階段 — 當日逐 tick 實盤監控

## 目標

/live 頁面主視圖改為**當日逐 tick 交易資料**：內建 tick collector 在盤中輪詢
TWSE MIS 即時行情 API（`mis.twse.com.tw/stock/api/getStockInfo.jsp`，免費、
無需 key，約 5 秒快照），對 watchlist 標的持續收 tick（成交價/單量/累積量/
五檔），寫入 `meta/realtime/ticks_<date>.jsonl`（已被 `meta/**` gitignore），
前端即時畫當日 tick 走勢 + 逐筆明細表，作為實盤監控主模組。

## 資料源驗證（2026-06-06）

- `GET mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw|tse_t00.tw&json=1`
  ✅ 可達，回 `z`(最後成交價) `tv`(單量) `v`(累積量) `a/b`(五檔) `tlong`(ms epoch)
- 指數：`tse_t00.tw`（加權指數）、`otc_o00.tw`（櫃買指數）
- 上市/上櫃自動偵測：probe `tse_<sym>.tw` 失敗 fallback `otc_<sym>.tw`，結果 cache
- 期貨（mis.taifex.com.tw）本階段不做 — 列後續方向

## 計畫 milestone

| # | 內容 | 預期產出 |
|---|------|---------|
| M7 | tick collector 後端 + unit tests | `ui/search/tick_collector.py`：MIS client、背景 thread 輪詢、dedup、ring buffer、JSONL 持久化 |
| M8 | Flask API + e2e tests | `/api/live/ticks/{status,start,stop}`、`/api/live/ticks?symbol&since_seq`（增量） |
| M9 | 前端 tick 主視圖 | live.html：tick 走勢圖（價格+累積量）、逐筆明細表、collector 控制、3s 自動更新 |
| M10 | 文件收尾 | README、進度檔完結 |

## 已知限制 / 後續方向

- SSE 每個連線一個 server thread（Flask dev server）；多人同看建議改 gunicorn
  gevent worker，或直接全走輪詢。
- audit JSONL 只記 ingest 完成事件，「進行中」的爬蟲看不到 — 若要 in-flight
  進度，得在 fetch 腳本加 start 事件或 heartbeat。
- 跨日不會自動切檔：頁面開過夜要手動切日期（或 F5）。
- 時間序列是**日線**：台股無分鐘級資料（bars_1m 只有 GC/NQ/ES 且 stale 至
  2026-03-12）；要盤中 tick/分 K 得先建 intraday ingest。
- symbol cache 在 server process 生命週期內不失效；catalog 快照 refresh 後
  可打 `/api/live/symbols?refresh=1` 強制重抓。

## Fallback 指引

- 回滾：`git log --oneline | grep 'M[0-9]:'` 找對應 commit，`git revert <hash>`。
- 核心檔案：`ui/search/live_monitor.py`、`ui/search/app.py`（routes）、
  `ui/search/templates/live.html`、`tests/test_live_monitor.py`。
- 啟動驗證：`scripts/run_search_ui.sh` → http://127.0.0.1:5050/live
