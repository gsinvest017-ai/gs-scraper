# QUANTDATA 對外即時行情 API v1

給另一台機器上的系統（例如風控系統）拉取當日即時行情的只讀 HTTP API。

- Base URL：`http://<host>:5050/api/v1`（內網 / Tailscale，例如 `http://100.104.1.39:5050/api/v1`）
- 無認證：靠 Tailnet ACL / 內網防火牆做邊界，請勿暴露到公網
- 只讀：v1 不含任何 collector 啟停寫入端點
- 回應一律 JSON，含 `server_time`（ISO8601 `+08:00`）；GET 回應帶
  `Access-Control-Allow-Origin: *`
- 錯誤：`{"error": "..."}` + HTTP 400 / 404 / 503

## 互動式文件（Swagger UI）

其他專案的開發者可直接開 **Swagger UI** 看所有端點、參數、回應 schema，並按
「Try it out」直接打（CORS 已 allow-all、皆為只讀 GET）：

- Swagger UI：`http://<host>:5050/api/v1/docs`
- OpenAPI 3.0 規格（機器可讀，給 Postman / codegen 等工具）：`http://<host>:5050/api/v1/openapi.json`

前端資產 vendored 在 `ui/search/static/swagger/`，**離線可用**（不依賴外網 CDN）。

## 建議用法（staleness guard）

風控在信任快照前，先打 `/health` 看 `seconds_since_poll`（collector 多久沒輪詢），
再看 `/snapshot` 每檔的 `age_sec`（該檔最新 tick 距現在幾秒）。兩者皆大 → 資料不新鮮，
應觸發降級或告警，勿據以下單。

## GET /health

collector 健康 + 資料新鮮度。

```bash
curl http://100.104.1.39:5050/api/v1/health
```
回應：`{server_time, collector:{running, collected_symbols, poll_sec, started_at,
last_poll_at, seconds_since_poll, poll_count, ticks_in_ring, seq, last_error}}`

## GET /snapshot

每個 symbol 當下最新快照（mark-to-market / kill-switch 主力）。

| 參數 | 必填 | 說明 |
|---|---|---|
| `symbols` | ✓ | 逗號/空白/分號分隔，如 `2330,TAIEX,0050`（大小寫不敏感）|
| `ensure` | | 預設 `1`：未採集的 symbol 自動加入 watchlist 開始採集（上限 20）；`0`=純讀 |

```bash
curl "http://100.104.1.39:5050/api/v1/snapshot?symbols=2330,TAIEX,0050"
```
回應：`{server_time, snapshots:{<sym>:{symbol,name,price,bid,ask,open,high,low,
prev_close,cum_vol,tick_vol,change,change_pct,time,tlong,age_sec,live,warming}},
not_collected:[...], dropped:[...]}`
- `change = price - prev_close`；`change_pct = change/prev_close*100`（prev_close 缺或為 0 → 兩者皆 null）
- `age_sec`：該 tick 距 server_time 秒數
- `live=false, warming=true`：剛開始採集、ring 還沒資料
- `not_collected`：`ensure=0` 時未採集的 symbol（snapshots 不含）
- `dropped`：因超過 20 檔上限被丟掉的 symbol（只列於此，不重複列入 not_collected，且 snapshots 也不含）
- collector 啟動失敗且完全無 tick 可回 → `503`；若有過期 tick 仍回 `200`，由 `age_sec` 判斷

## GET /ticks

逐 tick 增量流（ring buffer）。

| 參數 | 必填 | 說明 |
|---|---|---|
| `symbol` | | 省略 = 所有採集中 symbol 合併流 |
| `since_seq` | | 上次回傳的 `seq`，只拿之後的新 tick（預設 0）|
| `limit` | | 預設 5000，上限 20000 |

```bash
curl "http://100.104.1.39:5050/api/v1/ticks?symbol=2330&since_seq=48000"
```
回應：`{server_time, symbol, ticks:[{symbol,time,price,tick_vol,cum_vol,bid,ask,tlong}],
seq}`。下次帶 `since_seq=<回應的 seq>`。

## GET /ticks/history

任一日某標的逐 tick（三層 fallback：自收 JSONL → FinMind cache/sqlite → FinMind API）。

| 參數 | 必填 | 說明 |
|---|---|---|
| `date` | ✓ | `YYYY-MM-DD` |
| `symbol` | ✓ | 標的代碼 |

```bash
curl "http://100.104.1.39:5050/api/v1/ticks/history?date=2026-06-06&symbol=2330"
```
回應：`{server_time, date, symbol, source, count, ticks:[...]}`（`source` 標明命中層級）

## GET /bars

當日 + 歷史日線 OHLCV（算波動率 / ATR / 回撤基準）。

| 參數 | 必填 | 說明 |
|---|---|---|
| `symbol` | ✓ | 標的代碼 |
| `days` | | 預設 60，上限 365 |

```bash
curl "http://100.104.1.39:5050/api/v1/bars?symbol=2330&days=60"
```
回應：`{server_time, symbol, asset_class, days, series:{dates,open,high,low,close,volume},
latest:{trading_date,open,high,low,close,volume,prev_close,change,change_pct}}`

## 運維註記

- snapshot 懶啟動與 dashboard 共用同一個 process 級 collector 單例與 20 檔上限；
  兩個消費者同時要求超過 20 檔時會互相擠掉。
- collector 資料源為 TWSE MIS（約 5 秒快照），非逐筆撮合等級；歷史逐 tick 才走 FinMind。
- `/bars` 會把 symbol 轉大寫，因此 `macro:` 前綴的總經序列（`live_timeseries`
  以 `macro:` 開頭 dispatch）目前無法經 `/bars` 取得；股票/期貨代碼不受影響。
- snapshot 懶啟動呼叫 `collector.start(merged)` 時會對清單中未 cache 的 symbol
  做同步 MIS 探測（先 tse 後 otc）；若 MIS 緩慢或無回應，該次請求會被拖住。
  已 cache 的 symbol 會短路略過。風控對延遲敏感時，建議先用 `ensure=0` 純讀，
  或預先暖機 watchlist。
