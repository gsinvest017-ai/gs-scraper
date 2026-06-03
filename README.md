# QUANTDATA

量化資料 medallion lakehouse（bronze → silver → gold）。

📖 **文檔網站**：<https://gsinvest017-ai.github.io/gs-scraper/>（MkDocs Material，每次 push 自動重新發佈）

其他入口：

- 完整設計、schema、Mermaid 圖：[`DATA_ARCHITECTURE.md`](./DATA_ARCHITECTURE.md)
- 分階段實作進度：[`docs/progress-data-arch-impl.md`](./docs/progress-data-arch-impl.md)
- 文檔站源碼：[`docs-site/`](./docs-site/)

## 目錄

```
bronze/      不可變原始檔 (taifex/tej/twse/yahoo/histdata)
silver/      標準化 canonical schema (bars/options/flows/fundamentals/macro)
gold/        research-ready features (features/continuous/universe)
reference/   symbol_map / contract_specs / calendar
catalog/     quant.duckdb (views + macros over silver/gold)
meta/        audit / schema / lineage
src/qd_ingest/   Python ingest pipeline (CLI: qd-ingest)
docs/        進度與設計補充文件
tests/       pytest
scripts/     一次性腳本 (dedup / smoke / migrations)
```

## 快速開始（W1 完成後）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[ingest,dev]"
qd-ingest --help
```

## Stack

- DuckDB + Parquet (zstd) 為主
- Polars / pandas 做 transform
- pandera 做 schema 驗證
- 詳見 `DATA_ARCHITECTURE.md` § 2

## 一鍵搬到另一台主機（migrate）

把整個 repo（程式碼 + `.git` + 18G 資料湖 + DuckDB catalog）以 **rsync-over-SSH**
idempotent 鏡像到另一台主機。catalog 的 view 全用相對路徑（`read_parquet('silver/...')`），
所以目標端只要 repo 樹一致就能原樣開，不必改任何 SQL。

```bash
# 1. 設定目標主機（一次性）
cp scripts/migrate.conf.example scripts/migrate.conf
# 編輯 migrate.conf：MIGRATE_HOST / MIGRATE_PATH / MIGRATE_SSH_PORT
#   （前提：ssh key 已設好，能 `ssh <host> true` 免密登入）

# 2. 先 dry-run 預覽要傳什麼（不會動到目標）
./scripts/migrate_to_host.sh

# 3. 真的傳 + 傳完驗證
./scripts/migrate_to_host.sh --apply --verify

# 之後重跑只送變動（delta sync）；只想比對不傳：
./scripts/migrate_to_host.sh --verify-only
```

- **預設 dry-run**，`--apply` 才寫目標端。
- `--apply` 前自動檢查 catalog 沒被鎖（`duckdb -ui`）並 `CHECKPOINT` 落盤。
- `--verify` 比對 bronze/silver/gold/reference 的檔數與位元組、catalog view 數，
  並對核心 view 跑 row-count smoke（證明目標端透過相對路徑讀得到 parquet）。
- 跨 WAN 可加 `--bwlimit <KB/s>` 限速；`--no-delete` 保留目標端多出來的檔。
- **Windows**：用 `scripts\migrate_to_host.ps1`（參數相同，自動轉進 WSL 執行）。

設計與進度：[`docs/progress-migrate-to-host.md`](./docs/progress-migrate-to-host.md)。

### Migration dashboard（網頁版）

不想記指令的話，Search UI 內建 **Migration** 頁面（`scripts/run_search_ui.sh`
→ <http://127.0.0.1:5050/migrate>）：填目標主機 OS type / IP / hostname / 帳號 /
密碼 / port / 目標路徑，按「🔍 Dry-run 預覽」或「🚀 執行遷移」，log 即時串流到頁面。

- 密碼走 `sshpass`（需 `sudo apt install sshpass`；留空則用 ssh key 免密），
  **只在本機 subprocess 記憶體使用，不寫檔 / 不入 git / 不寫 log / 不回傳前端**。
- 預設 dry-run；要真的搬須勾「我確認要真的執行遷移」。
- ⚠️ Flask 預設綁 `0.0.0.0:5050`，**請只在信任的內網開這個頁面**。

設計與進度：[`docs/progress-migration-dashboard.md`](./docs/progress-migration-dashboard.md)。
