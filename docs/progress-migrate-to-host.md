# 進度：一鍵跨主機 migrate（Approach A — rsync-over-SSH 鏡像）

## 目標

把整個 QUANTDATA repo（程式碼 + `.git` + 18G 資料湖 `bronze/`/`silver/`/`gold/`
+ DuckDB catalog）以**單一指令、可重複執行（idempotent）**的方式，鏡像到一台
SSH 可達的目標主機。核心是 rsync delta 傳輸：第一次跑搬 18G，之後每次只送
變動的檔案。DuckDB catalog 的 view 全用**相對路徑**（`read_parquet('silver/...')`），
所以目標端不需要改任何 SQL，只要 repo 樹狀結構一致就能直接開。

## 為什麼是 Approach A

- SSH 可達 → rsync-over-SSH 最自然（incremental + resumable + 一條指令）
- 要完整鏡像（含 15G bronze）→ rsync delta 比 tar 重送整包好
- 要 idempotent 重複同步 → rsync `--delete` 做精確鏡像
- 目標 OS 可能是 Windows/WSL/Ubuntu → 核心用 bash（跑在 WSL/Linux），Windows
  端用 `.ps1` wrapper 轉進 WSL

## 計畫 milestone

| M | 標題 | 預期產出 |
|---|------|----------|
| M1 | 核心 rsync 腳本 | `scripts/migrate_to_host.sh`（預設 dry-run，`--apply` 才搬）+ pre-flight（工具檢查 / SSH 連線測試 / DuckDB 鎖檢查 + checkpoint）+ exclude 清單 + config 機制（`migrate.conf`）+ `.gitignore` |
| M2 | Post-flight 驗證 | `--verify` / `--verify-only`：來源 vs 目標 per-layer 檔數/位元組比對 + 目標端開 catalog 跑 smoke query（view 數比對） |
| M3 | Windows wrapper + 文件 | `scripts/migrate_to_host.ps1`（轉進 WSL）+ `migrate.conf.example` + README/docs 區段 + 本進度檔收尾 |

## 進度日誌

（隨 milestone 追加）

## Fallback 指引

- 腳本預設 **dry-run**，不加 `--apply` 不會動到目標端任何檔案。
- 要回滾「已建立的工具」：刪除 `scripts/migrate_to_host.sh`、`scripts/migrate_to_host.ps1`、
  `scripts/migrate.conf.example`，並還原 `.gitignore` 對 `scripts/migrate.conf` 的忽略行即可，
  本機資料湖與 catalog 完全不受影響（這些腳本只讀來源、寫遠端）。
- 目標端若要重來：直接刪掉遠端 repo 目錄，重跑 `--apply`。
