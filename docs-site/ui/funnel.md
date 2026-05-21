# Tailscale Funnel 遠端存取

> **狀態**：**WIP-blocked**。DuckDB UI 內建 token-based auth，funnel-exposing 不行；本頁記錄踩過的雷與三條替代方案。

## 為什麼想做遠端存取

- 在外面用 iPad 想查最近 stock_bars
- 給合作者一個只讀 URL 看 gap_dashboard
- 不想開 SSH port 給外網

## 為什麼不能直接 funnel DuckDB UI

DuckDB UI 1.5.x 用一個內部 `localToken` 驗證每個 `/ddb/run` 請求。token 是 process-local secret，**只能由 localhost 拿到**。Funnel 把 4213 暴露到公開 HTTPS 後：

| 路徑 | 結果 |
|---|---|
| `GET https://...ts.net/localToken` | 401 |
| `POST https://...ts.net/ddb/run` | 401 |
| `GET https://...ts.net/`（HTML） | 200，但 JS 拿不到 token，UI 一直 spinner |

curl 驗證：

```bash
curl http://127.0.0.1:4213/localToken          # 401
curl https://desktop-...ts.net/localToken      # 401
```

**結論**：DuckDB UI **不能 funnel**，這是 DuckDB 設計面的限制，不是 Tailscale 問題。

## Funnel 本身是設好的

雖然 UI 不能 funnel，但 Tailscale Funnel 機制全部建好了，**任何 localhost-bound HTTP server 都可以暴露**。

### 一次性設定（三道門檻）

| # | 動作 | 在哪做 |
|---|---|---|
| 1 | 啟用 Funnel feature on tailnet（node attribute） | 瀏覽器點 magic-link：`https://login.tailscale.com/f/funnel?node=<your-node-id>` |
| 2 | 啟用 HTTPS certificates on tailnet | https://login.tailscale.com/admin/dns → HTTPS Certificates → Enable |
| 3 | 設定本機 operator 讓 non-root 能控 funnel | `sudo tailscale set --operator=$USER` |

### 啟動 / 停止

```bash
# 啟動（暴露 localhost:8765 到 https://<machine>.<tailnet>.ts.net）
tailscale funnel --bg 8765

# 看狀態
tailscale funnel status
# 列出當前正在 funnel 的所有 port

# 停止
tailscale funnel --https=443 off
```

或用 wrapper script（如果 repo 有）：

```bash
bash scripts/tailscale_funnel.sh start 8765
bash scripts/tailscale_funnel.sh status
bash scripts/tailscale_funnel.sh stop
```

## 三條可行的替代方案

不能 funnel DuckDB UI，但可以暴露 **不需 UI token 的東西**：

### A. SSH tunnel（最推薦）

零工作量、最安全：

```bash
# 在你的筆電 / iPad 上跑：
ssh -L 4213:localhost:4213 kevin@<machine>
# 然後本機開 http://localhost:4213 即可
```

UI token 在 localhost 拿得到（因為 ssh tunnel 讓你成為 localhost），就會正常運作。

### B. 暴露 gap_dashboard.html（靜態檔，零 auth 風險）

```bash
# 起一個 static http server bind 0.0.0.0
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory docs

# 把它 funnel 出去
tailscale funnel --bg 8765
# → https://<machine>.<tailnet>.ts.net/gap_dashboard.html
```

只有靜態 HTML，沒辦法跑 query，安全等級高。**唯一想看 dashboard 而非互動查詢時的最佳選擇**。

### C. 自寫 FastAPI SQL playground（中等工作量）

~80 LOC 的 FastAPI app：

- `POST /query` 收 SQL，後端用 `read_only=True` 連 `quant_public.duckdb` 跑，HTML 表格輸出
- Basic auth middleware（HTTP basic header → check against env var）
- 拒絕任何 `INSERT/UPDATE/DELETE/DROP/ATTACH/COPY` 開頭（regex 黑名單）

裝完後 funnel 出去，遠端就能跑 SELECT。code 範本見 `docs/progress-tailscale-funnel.md` 第 5 節。

## 安全清單

公開任何 catalog 內容前必做：

| 檢查 | 怎麼做 |
|---|---|
| 是否暴露付費資料？ | TEJ 訂閱資料**不可** redistribution；只開 derived / aggregate view |
| 是否暴露原始檔路徑？ | DuckDB UI 可跑 `read_csv('/etc/passwd')`；funnel 前要驗 read_only 連線 |
| 是否有 rate limit？ | nginx in front 或 FastAPI 加 slowapi |
| 是否能被搜尋引擎索引？ | `robots.txt` + Tailscale Funnel access logs |
| `TEJAPI_KEY` 是否在 process env？ | 公開 UI process 起動前 `unset TEJAPI_KEY` |

## 歷史紀錄

| Doc | 內容 |
|---|---|
| `docs/progress-duckdb-public-url.md` | 第一次嘗試（ngrok），authtoken 始終跑不通 |
| `docs/progress-tailscale-funnel.md` | 第二次嘗試（Funnel），三道門檻過了但 UI token 卡死 |
| 本頁 | 兩次嘗試的結論與三條替代 |

任何重啟嘗試都應該從**方案 A（SSH tunnel）** 開始 — 最便宜也最安全。
