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
