# 進度：Data Migration Dashboard

## 目標

在現有 `ui/search` Flask app 上加一個 **Migration** 頁面：填表（目標主機 OS type、
IP、hostname、user account、password、ssh port、目標路徑）按鈕即可執行
`scripts/migrate_to_host.sh` 把整個 repo + 18G 資料湖鏡像到目標主機。支援
**dry-run 預覽**與**即時 log 串流**。password 走 `sshpass`、**只在 subprocess
env 中存在、絕不落地 / 不寫 log / 不入庫 / 不回傳前端**。

## 安全前提（重要）

- password 只經 POST body → 後端塞進 subprocess 的 `SSHPASS` env → `sshpass -e`
  使用；不寫檔、不進 git、不寫進任何 log、不回傳。
- 後端用 **arg list（非 shell=True）**呼叫腳本，杜絕 shell injection；另對
  user/host/port 做白名單格式驗證。
- Flask app 預設 bind `0.0.0.0:5050`（LAN 可見）。Migration 頁面會在 UI 明標
  「在信任的內網才使用；password 僅本機 in-memory 使用」。
- 預設 **dry-run**；要真的搬必須在表單勾「我確認執行（--apply）」。

## 計畫 milestone

| M | 標題 | 預期產出 |
|---|------|----------|
| M1 | 腳本支援 password 認證 | `migrate_to_host.sh` 偵測 `SSHPASS` env → 用 `sshpass -e` 包 ssh/rsync，並切掉 `BatchMode`、改 `StrictHostKeyChecking=accept-new`（首連目標 host key 不卡）；無 password 仍走原 key-only 路徑 |
| M2 | Flask 後端 | `ui/search/migrate_runner.py`（組指令 + 設 env + Popen 串流 + 輸入驗證 + 遮蔽 password）+ `/migrate` 頁面路由 + `POST /api/migrate`（chunked log stream） |
| M3 | 前端表單 | `templates/migrate.html`（OS/IP/hostname/user/password/port/path 欄 + dry-run/apply/verify 開關）+ base.html nav 連結 + JS（fetch 串流貼 log） |
| M4 | 收尾 | launcher 補 sshpass 偵測/安裝提示、README/docs、pytest 輸入驗證單元測試、進度檔收尾 |

## 進度日誌

### M1 — 腳本支援 password 認證

- `migrate_to_host.sh`：偵測 `SSHPASS` env → 用 `sshpass -e` 包 ssh 與 rsync，
  並改 `StrictHostKeyChecking=accept-new`（首連目標 host key 不卡）；無 SSHPASS
  則維持原 `BatchMode=yes` key-only 路徑。
- 密碼只經 `SSHPASS` env 傳遞，**不出現在指令列**（sshpass -e 從 env 讀），
  ps/log 都看不到。
- preflight：需要 password 但沒裝 sshpass → 乾淨報錯帶安裝指引；SSH 連線測試
  區分 password / key 兩種失敗訊息。
- 驗過：`bash -n` 過、key-only 無 host 仍乾淨報錯、password 路徑無 sshpass 乾淨報錯。

### M2 — Flask 後端

- `ui/search/migrate_runner.py`：`validate()` 白名單驗證（os_type/user/host/port/
  path/bwlimit）、`build_command()` 組 arg list、`stream_migration()` Popen 逐行
  yield log。password 只進 subprocess `SSHPASS` env；`threading.Lock` 確保同時
  只跑一個遷移。
- `app.py`：`GET /migrate`（頁面）+ `POST /api/migrate`（`text/plain` chunked
  log stream，`X-Accel-Buffering: no`）。password 從 payload 取出後不進 validate
  回傳，不外洩。
- 驗過：validate 接受合法 dry-run/apply，並正確 reject 6 種惡意/錯誤輸入
  （shell injection user、壞 IP、爆 port、path 含單引號、缺 host、未知 os_type）；
  app import OK，路由 `/migrate`、`/api/migrate` 就位。

### M3 — 前端表單

- `templates/migrate.html`：OS type / user / IP / hostname / password / port /
  target_path / bwlimit 欄 + verify / no-delete / **確認執行** 三個 checkbox +
  「🔍 Dry-run 預覽」「🚀 執行遷移」兩顆按鈕 + log `<pre>`。安全提醒 banner
  明標 LAN-only、password 不落地。JS 用 `fetch` + `ReadableStream` 逐塊貼 log。
- `base.html` nav 加 `Migration` 連結。
- 驗過（起 5055 測試實例，不動使用者既有 5050）：`/migrate` 正常 render、首頁
  nav 有連結、`/api/migrate` 對壞 IP 回 400、dry-run 串流正確吐出 dashboard
  header + 腳本 preflight + SSH 失敗 + exit code；**送 password 後 grep 串流
  洩漏次數 = 0**（確認不外洩）。

### M4 — 收尾（launcher + 文件 + 測試）

- `run_search_ui.sh`：啟動時偵測 sshpass，缺則提示 `sudo apt install sshpass`；
  並印出 `/migrate` 入口。
- README 加「Migration dashboard（網頁版）」段落（含安全提醒）。
- `tests/test_migrate_runner.py`：24 個單元測試鎖住 validate / build_command
  的安全行為（injection / 壞格式 reject、password 不進指令列）。
- 全測試綠：`pytest -q` → **173 passed**。

## 完成狀態

4 個 milestone 全完成。用法：

```bash
scripts/run_search_ui.sh          # 起 UI → http://127.0.0.1:5050/migrate
# （要用密碼遷移先 sudo apt install sshpass；或用 ssh key 免密）
```

填表 → Dry-run 預覽 → 勾「確認執行」→ 🚀 執行遷移，log 即時串流。

## Fallback 指引

- 整功能可獨立 rollback：移除 `ui/search/migrate_runner.py`、`templates/migrate.html`、
  app.py 內 migration 路由、base.html nav 連結、`migrate_to_host.sh` 的 sshpass 區塊即可，
  Search UI 其餘功能不受影響。
- 後端不持久化任何遷移狀態（無 DB、無檔），所以沒有殘留資料要清。
