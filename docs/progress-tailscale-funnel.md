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

### 下一步建議（need user 決定方向後再開新 /safe-yolo）

DuckDB UI 不能直接 expose。三條替代：

| 選項 | 工作量 | 描述 |
|---|---|---|
| **A. Datasette + DuckDB plugin** | 中（裝 datasette + datasette-duckdb，2 小時內） | 專為 SQLite/DuckDB 設計的 web frontend，內建 auth、API、SPA UI |
| **B. FastAPI/Flask SQL playground** | 小（自己寫 ~80 行） | textarea 輸入 SQL、後端 read-only 連 DuckDB，HTML table 輸出；可加 basic auth |
| **C. SSH tunnel + 本機 browser** | 0（user 端設定） | `ssh -L 4213:localhost:4213 ...` 把遠端 4213 映成本機，瀏覽器照走 localhost — 解 auth 也解資料外洩。**不是 "public" 但安全** |

我認為 **B（FastAPI playground）** 是 ROI 最高 — 既滿足 "public URL"、又有 auth control、不依賴 DuckDB UI 的 internal token。

### M5 — 真實阻擋：DuckDB UI 強制 token-based auth，funnel-exposing 行不通

User 回報瀏覽器 console 顯示 `/localToken` 跟 `/ddb/run` 都 401。M4 的「換成 writable」沒解決問題。實打驗證：

```
curl http://127.0.0.1:4213/localToken          → 401
curl http://127.0.0.1:4213/ddb/run (POST)     → 401
curl https://...ts.net/localToken              → 401
curl https://...ts.net/ddb/run (POST)         → 401
```

**所有 endpoint 都 401，連 localhost 也一樣**。DuckDB UI 的 hatchling bundle 設計：

1. 依賴 Browser cookie / localStorage 從早先的 localhost session 拿到 token
2. 沒 cookie → 試 Auth0 silent sign-in（MotherDuck 雲端，未登入也回 unauthenticated mode）
3. unauthenticated mode 仍要 /ddb/run 才能裝 autocomplete / httpfs / icu / json extensions
4. /ddb/run 沒 token → 401 → DataView 解析空回應炸成 RangeError

User 之前能用 UI 是因為 PID 12818 的 writable session 走過 localhost-only 的 init 流程，cookie 留在 browser，重啟 duckdb-ui 後那 session token 就 invalidated。

**結論：DuckDB UI 不能直接 funnel-expose**，這是設計上限制，不是 funnel 或 cert 問題。

### M4 — Funnel 拉起來但 UI 初始化錯誤（已解）

User 完成三道門檻後 `scripts/tailscale_funnel.sh start` 真的拉起 funnel：

```
# Funnel on:
#     - https://desktop-p44q3ni-1.tailffb0ce.ts.net
```

但瀏覽器打開該 URL 出現：
```
Initialization Error
Failed to resolve app state with user - RangeError: Offset is outside the bounds of the DataView
Username: unknown
User E-mail: unknown
```

#### 診斷

- 確認 HTTPS 端點 OK：`curl -sI https://...` 回 HTTP/2 200，含 COOP/COEP（WASM 需要的 cross-origin isolation header）
- 確認資產傳輸 OK：8.2MB 的 `hatchling.bundle.js` 在 local vs funnel 兩端 **MD5 完全相同**，沒被 proxy 截斷或重壓
- 排除網路層後，最可能：DuckDB UI（MotherDuck "Hatchling"）試圖把 user prefs 寫進 DB，但我們起 `-readonly`，初始化卡死成 DataView 範圍錯誤

#### 修法

`scripts/duckdb_public_ui.sh` 改為**預設 writable 模式 on snapshot**（snapshot 是 274KB 副本，與 live catalog 隔離，被破壞可從 live 重建）。要強制唯讀的話 `DUCKDB_PUBLIC_READONLY=1 scripts/duckdb_public_ui.sh start`。

順手修了同 script 的 pgrep pattern bug（empty `$mode_flag` 留下 double-space 導致 PID file 沒寫入）。

#### 使用者要做的最後一步

之前瀏覽器存了 read-only 失敗時的 state 在 IndexedDB / localStorage，會繼續 cache 壞 state。三選一：

1. `Ctrl+Shift+R` 強制重新整理
2. 開無痕視窗連 `https://desktop-p44q3ni-1.tailffb0ce.ts.net/`
3. DevTools → Application → Storage → Clear site data

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
