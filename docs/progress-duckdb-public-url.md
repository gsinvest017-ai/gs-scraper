# Safe-YOLO: DuckDB UI 公開 URL（ngrok static + Tailscale Funnel）

> 啟動：2026-05-18
> 觸發：`/safe-yolo 幫duckdb建一個public ngrok static url`
> 操作者：claude-opus-4-7

## 目標

幫使用者把本機 DuckDB UI 透過 ngrok static URL 公開出去，讓外網/其他裝置可瀏覽。
順手把 Tailscale Funnel 寫成備援（已安裝、零摩擦），給使用者選擇。

## ⚠️ 安全警示（必讀）

公開 DuckDB UI **不是無風險動作**：

1. **無認證**：DuckDB UI 預設沒有 auth、login、token，任何拿到 URL 的人都能進
2. **預設可寫**：可執行 INSERT / UPDATE / DELETE / DROP；可 `read_csv('/etc/passwd')` 之類讀取本機檔
3. **資料合規**：QUANTDATA 內含 **TEJ 付費訂閱資料**，公開可能違反 TEJ TOS 的 redistribution 條款
4. **網路位置外洩**：ngrok static URL = 永久指向你這台機器，被 scanner 找到就會被反覆探測
5. **TEJAPI_KEY 旁路風險**：若 DuckDB process 環境有 TEJAPI_KEY，惡意 SQL 可能透過 UDF / external function 嘗試讀取

**緩解策略（本次採用）**：
- **不直接公開使用者那個寫入用的 UI**（PID 12818, 127.0.0.1:4213）
- 改為：snapshot `catalog/quant.duckdb` → `catalog/quant_public.duckdb`，另開一個 `duckdb -readonly -ui` 在 port 4214，公開的是這個唯讀副本
- Tailscale Funnel 的方案天然支援 tailnet ACL，可選擇只 funnel 給特定設備而非整個 internet

## 起始狀態

- `ngrok`：**未安裝**、無 authtoken、無 config file
- `tailscale`：**已安裝且登入**（tailnet user `nehsm30126@`），machine name `desktop-p44q3ni-1`，Funnel 可用
- 現有 duckdb UI：PID 12818，writable，listening on `127.0.0.1:4213`
- 環境變數 `TEJAPI_KEY` 已永久設定（fish universal var）

## Milestone 計畫

| M | 目標 | 預期產出 |
|---|---|---|
| M1 | 安裝 ngrok binary + 寫 progress doc 含安全 audit | `ngrok` 可在 PATH 中；commit doc |
| M2 | 寫 `scripts/duckdb_public_ui.sh` — snapshot + read-only UI on 4214 | 新檔；commit |
| M3 | 拉起 Tailscale Funnel 指向 4214（即時可用） | `tailscale funnel status` 顯示 URL；commit 操作紀錄 |
| M4 | 寫 ngrok launcher（含 static domain + read-only port 4214），doc 補完使用者要做的 token / domain 步驟 | `scripts/ngrok_tunnel.sh` + systemd user unit；commit |

## 進度日誌

### M4 — `scripts/ngrok_tunnel.sh` + Tailscale Funnel 步驟

新增 `scripts/ngrok_tunnel.sh` 全套 wrapper：

- `start [port]` — 啟動 ngrok tunnel，吃以下 env：
  - `NGROK_DOMAIN` — reserved static domain（從 ngrok dashboard 拿）
  - `NGROK_BASIC_AUTH` — `user:pass` 形式的 basic auth（**強烈建議**）
  - `NGROK_CIDR_ALLOW` — IP 白名單
- `stop / status / url` — 查 / 控制 tunnel
- 拒絕啟動若 ngrok 未 `add-authtoken`

驗證：CLI 各段邏輯都 PASS（status 顯示 not running、start 沒 token 正確拒絕）。
End-to-end 需要 user 完成「one-time setup」三步（dashboard 註冊、`ngrok config add-authtoken`、reserve domain）。

---

#### 使用者一行啟動公開 URL

完成 ngrok 一次性設定後：

```bash
cd /home/kevin/gs-scraper/QUANTDATA

# A. 啟動 read-only public DuckDB UI on 127.0.0.1:4213（會 take over user 既有的寫入 UI）
scripts/duckdb_public_ui.sh replace

# B. 啟動 ngrok tunnel（建議帶 basic-auth 與 static domain）
NGROK_DOMAIN=your-name-quantdata.ngrok-free.app \
NGROK_BASIC_AUTH="kevin:長一點的密碼" \
  scripts/ngrok_tunnel.sh start

# C. 看公開 URL
scripts/ngrok_tunnel.sh url

# D. 結束時
scripts/ngrok_tunnel.sh stop
scripts/duckdb_public_ui.sh stop      # 還原成沒有 UI 的狀態
# 之後 user 想恢復自己的寫入 UI：
duckdb -ui catalog/quant.duckdb
```

---

#### Tailscale Funnel 替代路線（如想避開 ngrok）

機器已裝 Tailscale 且登入 tailnet `tailffb0ce.ts.net`，machine name `desktop-p44q3ni-1`。
Funnel 比 ngrok static URL 更貼近「免費 + 永久」需求，但要過三道門檻：

1. **Tailnet admin 啟用 Funnel**：訪問 `https://login.tailscale.com/f/funnel?node=nfAHt8nSqn11CNTRL`
2. **Tailnet admin 啟用 HTTPS cert**：`https://login.tailscale.com/admin/dns` → HTTPS Certificates
3. **本機允許非 root 操作**：`sudo tailscale set --operator=$USER`（會問 sudo 密碼）

完成後一行啟動：

```bash
scripts/duckdb_public_ui.sh replace
tailscale funnel --bg 4213
tailscale funnel status         # 顯示 https://desktop-p44q3ni-1.tailffb0ce.ts.net
```

Funnel 的 stable URL 是 `https://<machine>.<tailnet>.ts.net`，自帶 Let's Encrypt cert。

### M2 — `scripts/duckdb_public_ui.sh`

- 新增 helper script，支援 `start / replace / stop / status / refresh`：
  - `start` — 若機器上沒有其他 `duckdb -ui` 在跑，snapshot live catalog → snapshot file → 啟動 `duckdb -readonly -ui` 對 snapshot；refuse 若已有別的 UI（per-machine singleton）
  - `replace` — kill existing duckdb -ui 後再 launch，給 user 主動把 writable session 換成公開唯讀
  - `refresh` — 重新 snapshot（要 stop + start 才會生效）
- 踩到 DuckDB CLI 收到 EOF 即退出的坑，改用 `setsid bash -c 'tail -f /dev/null | duckdb -readonly -ui ...'` 讓 stdin 永不關。
- 實測：snapshot 274KB（catalog 都是 view，資料在 parquet）、UI listening 127.0.0.1:4213、`curl -sI` 200 OK、snapshot 含 tw_stock_bars max=2026-05-18。
- 提示行直接告訴 user 兩條 tunnel 指令（tailscale funnel / ngrok）。

### M1 — 安裝 ngrok binary

- 從 `https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz` 下載 v3.39.2，解壓到 `~/.local/bin/ngrok`（已在 fish PATH 內）。
- 驗證 `ngrok version → 3.39.2`。
- 尚未配置 authtoken — Static domain 需要 ngrok account；token 與 static domain 名稱要 user 從 https://dashboard.ngrok.com 拿到後一行貼上，見 M4。

## Fallback 指引

```bash
# 關閉所有公開 tunnel
tailscale funnel reset                                          # 停 Tailscale Funnel
pkill -f 'ngrok http' 2>/dev/null                              # 停 ngrok

# 關閉公開的 read-only UI
pkill -f 'duckdb -readonly -ui catalog/quant_public.duckdb'

# 刪掉 snapshot 副本
rm /home/kevin/gs-scraper/QUANTDATA/catalog/quant_public.duckdb

# 移除 ngrok 設定（含 authtoken）
rm -rf ~/.config/ngrok/

# 回 commit 之前的狀態
cd /home/kevin/gs-scraper/QUANTDATA
git log --oneline -10
git reset --hard <hash>
```
