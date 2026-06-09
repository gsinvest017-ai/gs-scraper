# 設計：對外即時行情 API v1（給風控系統等跨機器消費者）

- 日期：2026-06-09
- 範圍：QUANTDATA Search UI（`ui/search/`）新增穩定的 `/api/v1/*` 只讀對外契約
- 狀態：草案待審

## 1. 動機與背景

當日逐 tick 實盤監控與相關即時資訊目前只透過 dashboard 瀏覽器消費
（`/live` + `/api/live/*`）。需要讓**另一台機器上的系統（例如風控系統）**
能直接呼叫，做 mark-to-market、kill-switch、staleness guard 等判斷。

現況（`ui/search/app.py`，Flask，`0.0.0.0:5050`，經 Tailscale `100.x` 對外）已有
一整組 `/api/live/*` JSON 端點，但它們是**為 dashboard 瀏覽器設計**的：

- 無認證、無 CORS
- collector 的 `/start` `/stop` 是無防護的 POST（外部機器可亂關採集器）
- 形狀綁 dashboard 需求（如 `since_seq` 增量游標），缺「每 symbol 當下最新快照」

本設計**不從零造 API**，而是在現有模組之上加一層乾淨、穩定、版本化的對外契約。

### 已確認的設計決定

| 決定點 | 選擇 |
|---|---|
| 風控要拿什麼 | 全讀取面：最新快照（pull）＋逐 tick 增量＋當日/歷史 OHLC＋collector 健康 |
| 認證 | **不加認證**，靠 Tailnet ACL / 內網防火牆做邊界；API 本身不驗 token |
| API 組織 | **新開 `/api/v1/*` 穩定命名空間**（獨立 Blueprint），dashboard `/api/live/*` 不動 |
| collector 啟動 | **snapshot 懶啟動**：未採集的 symbol 自動加入 watchlist 開始採集（`ensure` 預設開） |

## 2. 架構

```
                       ┌──────────────────────────────┐
   風控系統（另一台）   │  ui/search/app.py (Flask)      │
   GET /api/v1/...  ───▶│   register_blueprint(api_v1)  │
                       │                                │
                       │   ui/search/api_v1.py  ◀── 本次新增（Blueprint）
                       │     ├─ /snapshot               │
                       │     ├─ /ticks                  │
                       │     ├─ /ticks/history          │
                       │     ├─ /bars                   │
                       │     └─ /health                 │
                       └───────────┬────────────────────┘
                                   │ 複用（不改其公開介面，僅 collector 補一個方法）
                 ┌─────────────────┼───────────────────────────┐
                 ▼                 ▼                           ▼
        tick_collector.py   tick_history.py            live_timeseries.py
        (ring buffer +      (三層 fallback:            (DuckDB bars_1d
         MIS 輪詢)           自收 JSONL→FinMind)         OHLCV 時序)
```

**隔離原則**：對外契約自成一個可獨立測試的單元（`api_v1.py` Blueprint）。
它只**讀取**現有模組的公開介面，唯一對既有程式碼的修改是在 `TickCollector`
補一個讀取方法 `latest_snapshot()`。dashboard 既有 route 與行為零變動。

## 3. 端點規格

所有回應：
- `Content-Type: application/json`，`JSON_AS_ASCII=False`（已設）
- 頂層一律含 `server_time`：ISO8601 帶 `+08:00`（台北時區），供消費者自算 staleness
- GET 回應加 header `Access-Control-Allow-Origin: *`（只讀、信任內網；方便未來瀏覽器版風控）
- 錯誤格式：`{"error": "<訊息>"}`，搭配 HTTP status：
  - `400` 參數缺失/格式錯（如缺 `symbols`、`since_seq` 非整數）
  - `404` 查無標的（`/bars` 找不到 symbol）
  - `503` collector 未在運行且無法啟動（`/snapshot` 對應情境，見下）

### 3.1 `GET /api/v1/snapshot`

風控主力端點——每個 symbol 當下最新快照（mark-to-market / kill-switch 用）。

**參數**
- `symbols`（必填）：逗號或空白分隔，如 `2330,TAIEX,0050`。大小寫不敏感、去空白。
- `ensure`（選填，預設 `1`）：`1` 時把未在 watchlist 的 symbol 自動加入並啟動採集
  （懶啟動，上限 `MAX_SYMBOLS=20`）；`0` 時純讀，不改 collector 狀態。

**行為**
1. 解析 symbols。
2. 若 `ensure=1`：對不在 collector watchlist 的 symbol 呼叫 `collector.start(現有∪新)`，
   合併後仍受 20 檔上限約束（超過則保留前 20，多出的列入 `dropped`）。
3. 從 ring 取每個 symbol 最新一筆 tick（`collector.latest_snapshot(symbols)`）。
4. 對「剛 ensure、ring 還沒資料」的 symbol → `live=false`、`warming=true`。

**回應**
```json
{
  "server_time": "2026-06-09T13:25:07+08:00",
  "snapshots": {
    "2330": {
      "symbol": "2330", "name": "台積電",
      "price": 1085.0, "bid": 1085.0, "ask": 1090.0,
      "open": 1080.0, "high": 1095.0, "low": 1078.0, "prev_close": 1075.0,
      "cum_vol": 18234.0, "tick_vol": 3.0,
      "change": 10.0, "change_pct": 0.93,
      "time": "13:24:58", "tlong": 1781000698000,
      "age_sec": 9.2, "live": true, "warming": false
    },
    "TAIEX": { "...": "..." }
  },
  "not_collected": [],
  "dropped": []
}
```
- `change` = `price - prev_close`；`change_pct` = `change / prev_close * 100`（`prev_close` 為
  None 時兩者為 `null`）。
- `age_sec` = `server_time - tlong` 秒數（float）。風控以此判斷新鮮度。
- 未被採集且 `ensure=0`：列入 `not_collected`，`snapshots` 內該 symbol 省略。

### 3.2 `GET /api/v1/ticks`

逐 tick 增量流——薄包 `collector.get_ticks()`。

**參數**
- `symbol`（選填）：省略則回所有採集中 symbol 的合併流。
- `since_seq`（選填，預設 0）：上次回傳的 `seq`，只拿之後的新 tick。
- `limit`（選填，預設 5000，上限 20000）。

**回應**
```json
{
  "server_time": "2026-06-09T13:25:07+08:00",
  "symbol": "2330",
  "ticks": [ { "symbol": "2330", "time": "13:24:58", "price": 1085.0,
               "tick_vol": 3.0, "cum_vol": 18234.0, "bid": 1085.0, "ask": 1090.0,
               "tlong": 1781000698000 }, "..." ],
  "seq": 48213
}
```
tick 欄位與 collector 落地的 JSONL schema 一致（見 `tick_collector.parse_tick`）。

### 3.3 `GET /api/v1/ticks/history`

任一日某標的逐 tick（三層 fallback：自收 JSONL → FinMind cache/sqlite → FinMind API）。
薄包 `tick_history.get_history_ticks(date, symbol)`。

**參數**：`date`（必填，`YYYY-MM-DD`）、`symbol`（必填）。

**回應**：`{server_time, date, symbol, source, count, ticks: [...]}`
（`source` 標明命中層級；沿用 `tick_history` 既有回傳，外加 `server_time`）。

### 3.4 `GET /api/v1/bars`

當日 + 歷史日線 OHLCV（算波動率 / ATR / 回撤基準用）。
薄包 `live_timeseries.get_timeseries(symbol, days)`。

**參數**：`symbol`（必填）、`days`（選填，預設 60，上限 365）。

**回應**：`{server_time, symbol, asset_class, days, series:{dates,open,high,low,close,volume},
latest:{trading_date,open,high,low,close,volume,prev_close,change,change_pct}}`
（沿用 `live_timeseries` 既有回傳，外加 `server_time`）。查無標的 → `404`。

### 3.5 `GET /api/v1/health`

collector 健康 + 資料新鮮度。風控在信任 snapshot 前先打這支。

**回應**
```json
{
  "server_time": "2026-06-09T13:25:07+08:00",
  "collector": {
    "running": true,
    "collected_symbols": ["0050", "2330", "TAIEX"],
    "poll_sec": 3.0,
    "started_at": "2026-06-09T09:00:01",
    "last_poll_at": "2026-06-09T13:25:05",
    "seconds_since_poll": 2.1,
    "poll_count": 5204,
    "ticks_in_ring": 31980,
    "seq": 48213,
    "last_error": null
  }
}
```
`seconds_since_poll` 由 `server_time - last_poll_at` 計算（`last_poll_at` 為 None 時為 `null`）。

## 4. 對既有程式碼的修改

唯一一處：`ui/search/tick_collector.py` 的 `TickCollector` 新增

```python
def latest_snapshot(self, symbols: list[str] | None = None) -> dict[str, dict]:
    """回 {symbol: 最新 tick dict}。從 ring 由新到舊掃，每 symbol 取第一筆命中。
    symbols 為 None 時回所有 symbol 的最新。"""
```

- 實作：在 `self._lock` 下反向走訪 `self._ring`，遇到尚未收錄的 symbol 就記錄，
  集滿要求的 symbols 即可提早結束。
- 不改 `start/stop/get_ticks/status` 等既有方法的簽名與行為。

`ui/search/app.py`：新增 `from ui.search.api_v1 import bp as api_v1_bp` 與
`app.register_blueprint(api_v1_bp)`。其餘不動。

## 5. 錯誤處理

- 參數驗證在端點入口做，回 `400 {"error": ...}`。
- `/snapshot` 當 `ensure=1` 但 `collector.start()` 因 MIS 網路錯誤無法啟動任何 symbol
  → 回 `503 {"error": "collector 無法啟動", "not_collected": [...]}`。
- `/snapshot` 當 `ensure=0` 且全部 symbol 都未採集 → `200`，`snapshots={}`，`not_collected` 列全部
  （非錯誤，是「沒資料」狀態，風控自行判斷）。
- 內部模組丟出的 `ValueError`（如 `tick_history` 日期格式）→ 轉 `400`。
- 未預期例外 → `500 {"error": "internal error"}`（不洩漏 stack）。

## 6. 測試（`tests/test_api_v1.py`，pytest）

**collector 單元**
- `latest_snapshot()`：fake ring 灌入多 symbol 多筆 → 驗每 symbol 取到最新、空 ring 回 `{}`、
  指定 symbols 子集只回子集。

**端點（Flask test client + monkeypatch collector/模組）**
- `/snapshot`：
  - happy：ring 有資料 → 正確 `change/change_pct/age_sec`、`live=true`
  - `ensure=1` 未採集 symbol → 觸發 `start()`（用 fake collector 驗呼叫）、回 `warming=true`
  - `ensure=0` 未採集 → 列 `not_collected`、`snapshots` 不含該 symbol
  - 缺 `symbols` → `400`
  - symbols 逗號/空白/大小寫解析
  - 超過 20 檔 → `dropped` 非空
- `/ticks`：`since_seq` 增量、`limit` clamp、`since_seq` 非整數 → `400`
- `/ticks/history`：包到 `get_history_ticks`、日期格式錯 → `400`
- `/bars`：包到 `get_timeseries`、查無 → `404`、`days` clamp 至 365
- `/health`：`seconds_since_poll` 計算、`last_poll_at=None` → `null`
- 共通：每端點回應含 `server_time` 且帶 `+08:00`、GET 回應含 `Access-Control-Allow-Origin: *`

`server_time` 與 `age_sec`/`seconds_since_poll` 牽涉「現在時間」：以可注入的 time
provider 或 monkeypatch `datetime` 取代，讓測試可斷言確定值。

## 7. 文件

`docs/api-v1.md`：每端點的 path / method / 參數表 / 回應 schema / `curl` 範例 /
staleness 使用建議（風控應先打 `/health` 看 `seconds_since_poll`，再信任 `/snapshot`
的 `age_sec`）。README 補一行指向此文件。

## 8. 安全與運維註記

- **無認證是刻意決定**，前提是 Tailscale ACL / 內網防火牆已限制 `5050` 只有信任機器可達。
  v1 對外**只暴露 GET 只讀端點**，不含 start/stop——即使無認證，外部也無法停掉採集器
  （這是補償）。dashboard 既有的無防護 `/api/live/ticks/start|stop` 為 pre-existing，
  本設計不擴大其暴露面，但在文件提醒運維以網路層收斂。
- snapshot 懶啟動會與 dashboard 共用同一個 process 級 collector 單例與 20 檔上限；
  兩個消費者同時要求超過 20 檔時會互相擠掉。此規模下可接受，文件需註明。

## 9. 明確不做（YAGNI）

- 不做 WebSocket / SSE 推送給風控（pull 輪詢 + 增量 `since_seq` 已足；SSE 留給 dashboard）。
- 不做 token / API key（已決定靠網路層）。
- 不做對外的 collector 寫入控制端點。
- 不做新的資料源接入（沿用 MIS + FinMind 既有鏈）。
- 不做歷史日線以外的衍生指標計算（ATR / 波動率由風控端自算，API 只給原料）。
