# Safe-YOLO: 設定 Tailscale Funnel 公開 DuckDB UI

> 啟動：2026-05-18
> 觸發：`/safe-yolo 幫我設定tailscale funnel`
> 操作者：claude-opus-4-7
> 接續：`progress-duckdb-public-url.md`（已建好 `scripts/duckdb_public_ui.sh`、ngrok 走不通）

## 目標

放棄 ngrok（authtoken 一直不對），改用機器已裝好的 Tailscale Funnel 做公開 HTTPS URL。
URL 格式預期：`https://desktop-p44q3ni-1.tailffb0ce.ts.net`，自帶 Let's Encrypt 憑證、tailnet ACL 管控。

## 起始狀態

- Tailscale 已安裝、已登入 tailnet `tailffb0ce.ts.net`，machine name `desktop-p44q3ni-1`
- Tailscale Funnel 嘗試 `tailscale funnel --bg 4213` 結果：
  - `Funnel is not enabled on your tailnet.` → 需要 admin 開啟功能（一個按鈕）
  - `Access denied: serve config denied` → 本機 non-root 不能操作 serve/funnel，需要設 operator 或 sudo
- `tailscale cert` 顯示 `HTTPS cert support is not enabled/configured for your tailnet` → 需要 admin 開啟 HTTPS cert
- DuckDB UI（read-only snapshot, PID 14614）已在 `127.0.0.1:4213` running，等接 funnel

## Funnel 啟用三道門檻

| # | 動作 | 在哪做 | 一次性 |
|---|---|---|---|
| 1 | 啟用 Funnel feature on tailnet（node attribute） | 瀏覽器點 magic-link：`https://login.tailscale.com/f/funnel?node=nfAHt8nSqn11CNTRL` | ✅ |
| 2 | 啟用 HTTPS certificates on tailnet | https://login.tailscale.com/admin/dns → HTTPS Certificates → Enable | ✅ |
| 3 | 設定本機 operator 讓 non-root 能控 funnel | `sudo tailscale set --operator=$USER`（要 sudo 密碼） | ✅ |

## Milestone 計畫

| M | 目標 | 預期產出 |
|---|---|---|
| M1 | 盤點所有 Funnel 阻擋條件 + 建 progress doc | 上面表格；commit |
| M2 | 寫 `scripts/tailscale_funnel.sh` wrapper（start / stop / status / url），prerequisites 都做完後一行啟動 | 新 script；commit |
| M3 | 由 user 完成三道門檻（無法繞過：admin click + sudo），完成後實際拉起 funnel | funnel status 顯示公開 URL；commit 操作紀錄 |
| M4 | 驗證公開 URL 真的能存取 DuckDB UI；收尾 doc | curl 公開 URL HTTP 200；最終 commit |

## 進度日誌

### M3 — 卡關：三道門檻全部需要使用者親自完成

不在 agent 能執行範圍內：

| # | 動作 | 為何 agent 不能做 |
|---|---|---|
| A | `sudo tailscale set --operator=kevin` | sudo 要密碼，agent 的 Bash 沒 stdin、無法輸入 |
| B | 點 `https://login.tailscale.com/f/funnel?node=nfAHt8nSqn11CNTRL` | 要瀏覽器（且機器沒 `xdg-open`） |
| C | 點 https://login.tailscale.com/admin/dns 啟用 HTTPS Certificates | 同上 |

按 /safe-yolo 第 4 條卡關處理規則：commit 已可運行狀態（M1+M2），把 handoff 寫進進度檔。

**使用者要依序做的三件事**（合計約 30 秒）：

```bash
# 1. 設定本機 operator（sudo 提示輸密碼）
sudo tailscale set --operator=$USER

# 2. 開瀏覽器點以下 magic-link：
#    https://login.tailscale.com/f/funnel?node=nfAHt8nSqn11CNTRL
#    → 會帶你到 Tailscale Admin → 直接 enable funnel for this node

# 3. 開瀏覽器點：
#    https://login.tailscale.com/admin/dns
#    → 找 "HTTPS Certificates" 區 → 點 Enable

# 4. 確認三道都過：
cd /home/kevin/gs-scraper/QUANTDATA
scripts/tailscale_funnel.sh check
# 預期 4 項都顯示 OK

# 5. 啟動 funnel
scripts/tailscale_funnel.sh start
# 預期輸出：https://desktop-p44q3ni-1.tailffb0ce.ts.net

# 6. 瀏覽器打開那個 URL，應該就會看到 DuckDB UI
```

### M4 — Blocked

需 user 完成 M3 三步後才能進行（actual funnel 拉起 + 公開 URL HTTP 200 驗證）。
做完後 user 跑 `scripts/tailscale_funnel.sh start` 並把輸出貼回，agent 就能驗證並收尾。

### M2 — `scripts/tailscale_funnel.sh`

- 新增 wrapper script，支援 `check / start / stop / status / url`。
- `check` 用 `tailscale serve reset` 當 write-side probe，能正確判讀 operator 是否設好；operator 未設時跳過後續兩道檢查（因為 funnel/cert 也都被 access-denied，看不出真實狀態）。
- `start` 嘗試 `tailscale funnel --bg`，依錯誤字串輸出 DIAGNOSIS：
  - `access denied` → 提示 `sudo tailscale set --operator=$USER`
  - `funnel is not enabled` → 提示 admin magic-link
  - `https.*cert` → 提示 admin HTTPS toggle
- 過程踩到的 bug：`set -e` 會讓 `out=$(tailscale funnel --bg ...)` 在 non-zero exit 時導致 script 早退，改包 `set +e/-e`。
- 實測 `check` 正確抓到「operator 未設」，`start` 也正確輸出 DIAGNOSIS。

## Fallback 指引

```bash
# 關閉 funnel + 公開的 read-only UI
tailscale funnel reset
scripts/duckdb_public_ui.sh stop

# 回 commit 前
cd /home/kevin/gs-scraper/QUANTDATA
git log --oneline -10
git reset --hard <hash>
```
