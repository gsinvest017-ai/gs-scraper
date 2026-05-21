# Cron 排程

QUANTDATA 預設用 cron（不是 systemd timer），因為對單機 / WSL 環境 friction 最低。

## 安裝

```bash
bash scripts/install_cron.sh             # 預設 Mon-Fri 17:30 CST
bash scripts/install_cron.sh --show      # 印會寫入什麼但不執行
bash scripts/install_cron.sh --uninstall # 移除
bash scripts/install_cron.sh --hour 20 --minute 0  # 自訂時間
```

```bash
crontab -l | grep quantdata-daily-refresh -A 1
```

應顯示類似：

```
# >>> quantdata-daily-refresh <<<
30 17 * * 1-5 cd /home/kevin/gs-scraper/QUANTDATA && bash scripts/daily_refresh.sh >> meta/audit/cron.log 2>&1
# <<< quantdata-daily-refresh >>>
```

## 為什麼是 17:30 CST

| 時間 | 意義 |
|---|---|
| 13:30 CST | 台股現貨收盤 |
| 14:00 CST | TEJ 系統開始整理當日 EOD 資料 |
| **17:30 CST** | EOD 資料**通常**已落地 TEJ API；給 4 小時 buffer |
| 21:00 CST | 期貨夜盤開盤前 |

太早跑：TEJ API 回的還是昨天的（fetch_tej 抓不到新東西，gap_report 顯示 stale）。
太晚跑：浪費 evening trading 前的 dashboard 更新時機。

Mon-Fri 5 天：台股周末無交易，跑也沒新資料；省 TEJ quota。

## 安裝細節

`install_cron.sh` 是 idempotent 的：

- 重跑只會 **替換** marker block 內容，不會新增重複行
- `--uninstall` 只移除 marker block；其他 crontab entry（例 gs-claude-config 的 night-shift）保留
- marker block 規格：

  ```
  # >>> quantdata-daily-refresh <<<
  <cron line>
  # <<< quantdata-daily-refresh >>>
  ```

## Log 檔在哪

```
meta/audit/cron.log                         ← cron stdout/stderr 全部
meta/audit/daily_refresh_<YYYY-MM-DD>.log   ← daily_refresh 自己的結構化 log
```

`cron.log` 會無限增長，建議偶爾 `truncate -s 0 meta/audit/cron.log` 或設 logrotate。

## 確認 cron daemon 跑了

```bash
# WSL2：
sudo service cron status
# 沒裝 / 沒跑：
sudo service cron start

# Ubuntu native：
systemctl status cron
```

WSL2 預設**不會**自動起 cron daemon。要在 boot 時起：

```bash
# 編輯 ~/.bashrc 或 ~/.config/fish/config.fish
sudo service cron start 2>/dev/null || true
```

或用 [genie / wsl-systemd 方案](https://learn.microsoft.com/en-us/windows/wsl/systemd) 啟用 systemd，這樣 cron 就自動跑。

## 看下次什麼時候跑

```bash
# Linux 沒有 cron next-run 工具，自己算
date
crontab -l | grep daily_refresh
```

或裝 `cronie-cron` 套件取得 `crontab -V` 的 next run。

## 用 systemd timer 替代（進階）

如果你的環境有 systemd（不是 WSL2 預設）：

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/quantdata-daily-refresh.service <<'EOF'
[Unit]
Description=QUANTDATA daily refresh

[Service]
Type=oneshot
WorkingDirectory=/home/kevin/gs-scraper/QUANTDATA
ExecStart=/bin/bash scripts/daily_refresh.sh
EOF

cat > ~/.config/systemd/user/quantdata-daily-refresh.timer <<'EOF'
[Unit]
Description=Run QUANTDATA daily refresh weekdays at 17:30

[Timer]
OnCalendar=Mon..Fri 17:30
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now quantdata-daily-refresh.timer
systemctl --user list-timers --all | grep quantdata
```

systemd 對 missed runs 處理更好（`Persistent=true` 讓開機後補跑）。WSL2 預設不啟動 user systemd，要 [enable systemd](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#systemd-support) 才能用。

## Failure modes

| 症狀 | 排查 |
|---|---|
| Cron 完全沒跑 | `sudo service cron status` / `crontab -l` |
| 跑了但 exit 11 | `meta/audit/cron.log` 看：`TEJAPI_KEY missing` — fish universal var 在 cron 環境拿不到，要 hard-code 在 cron line 或 `daily_refresh.sh` 內 source |
| 跑了 exit 10 | 上一次 instance 還沒結束（rare；可能是 hang）；用 `pgrep -af daily_refresh` 看是否真的還在 |
| Cron 跑但 silver / catalog 沒更新 | 看 `daily_refresh_<date>.log`，找 ERROR 行 |
| Cron 跑但 dashboard 沒更新 | gap_report 最後失敗；手動跑 `.venv/bin/python scripts/gap_report.py --format all` 看錯誤 |

## TEJAPI_KEY 在 cron 環境拿不到？

cron 不會載 fish universal vars。三種解：

1. **在 daily_refresh.sh 內 source**：腳本內已自動嘗試 `set -q TEJAPI_KEY; or source ~/.config/fish/conf.d/tej.fish`
2. **直接寫 crontab line 裡**（明文，會暴露在 `crontab -l`，**不推薦**）：

   ```
   30 17 * * 1-5 cd /home/kevin/gs-scraper/QUANTDATA && \
       TEJAPI_KEY=xxxx TEJAPI_BASE=https://api.tej.com.tw \
       bash scripts/daily_refresh.sh >> meta/audit/cron.log 2>&1
   ```

3. **用 `.env` + load-dotenv**（推薦）：

   ```bash
   cat > .env <<'EOF'
   TEJAPI_KEY=xxxx
   TEJAPI_BASE=https://api.tej.com.tw
   EOF
   chmod 600 .env
   # 在 daily_refresh.sh 開頭 source ./env，或讓 fetch_tej.py 用 python-dotenv 自動讀
   ```

`.env` 已在 `.gitignore` 內。
