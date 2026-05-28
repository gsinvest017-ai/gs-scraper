# 2026-05-28 — `/safe-yolo /one-button-launch /platform-compatible`

## 目標

把 QUANTDATA 從「手動開 venv → 手動 pip install → 手動 ingest → 手動跑 dashboard」
壓成**一鍵啟動**，同時把跨平台地雷（CRLF / 路徑 / shell-only entry）掃過一遍補上
最低限度的 Windows 相容。

## 偵測（M1）

### `/one-button-launch` 偵測

| 項目 | 狀態 |
|---|---|
| top-level `run.sh` / `run.ps1` | ✗ 沒有 |
| `Makefile` / `justfile` | ✗ 沒有 |
| `pyproject.toml` + entry `qd-ingest` | ✓ 有 |
| `.venv/` | ✓ 已建好 |
| `requires-python` | `>=3.11` |
| optional extras | `[ingest]` = yfinance/requests/feedparser/bs4/tejapi |
| README quickstart | 有，但要手敲三條指令 |
| `scripts/*.sh` | 7 支（單任務：daily_refresh / run_search_ui / install_cron / backup_snapshot / ngrok_tunnel / tailscale_funnel / duckdb_public_ui） |

**結論**：沒有單一入口的 launcher。值得補上。

### `/platform-compatible` 偵測（8 類）

| 類 | 觀察 | 等級 |
|---|---|---|
| 1. 路徑分隔符 | `catalog.py:17` 已 `.replace("\\", "/")` 防呆；`gap_report.py` 全用 `os.path.join`。code path 乾淨 | ✅ |
| 2. Shell scripts | 7 支 `.sh`、**0 支 `.ps1` / `.bat`**：Windows clone 後沒入口 | ⚠️ medium |
| 3. EOL / `.gitattributes` | **無 `.gitattributes`** → Windows clone 後 `.sh` 可能變 CRLF 跑不動 | ⚠️ high |
| 4. 既有 CRLF 文件 | `grep -rIl $'\r' src/ scripts/ ui/` 0 hit → 全 LF | ✅ |
| 5. 檔名 case / Windows 保留字 | 未深掃，目錄都 lowercase（除了 `bronze/twse-mkt-eqty/`等保持 dataset 名）| ✅ |
| 6. 環境變數 | `scripts/fetch_finmind.py:37` 預設值寫死 `/home/kevin/gs-scraper/FINMIND資料集`；`scripts/daily_refresh.sh:45` 用 `/tmp` lock —— Linux-only cron 腳本，刻意 Linux 化，不強推跨平台 | ⚠️ low（已知設計 trade-off） |
| 7. 原生相依 | duckdb / polars / pandas / pyarrow 三平台都有 wheel | ✅ |
| 8. CI matrix | 只有 `docs.yml`（MkDocs deploy）；無 pytest 跨平台 matrix | ℹ️ N/A（pytest 還沒跑 CI） |

**結論**：補 `run.sh` + `run.ps1` + `.gitattributes` 三檔，重要洞就補完。daily-refresh / cron / tunnel
類腳本維持 Linux-only（設計如此，文件註記即可）。

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔（plan + audit） |
| **M2** | top-level `run.sh` + `run.ps1`：偵測 Python → 建/啟動 `.venv` → `pip install -e ".[ingest]"` → 子命令 menu（`run.sh ui` / `run.sh ingest` / `run.sh dashboard` / `run.sh test`） |
| **M3** | `.gitattributes`：`*.sh` LF、`*.parquet`/`*.duckdb` binary、Python/CSS/JS/MD 強制 LF |
| **M4** | smoke：跑 `./run.sh --help`，commit + 進度檔收尾 |

## Fallback

要 rollback：

```bash
git revert HEAD~3..HEAD     # 撤掉 M2/M3/M4
rm -f run.sh run.ps1 .gitattributes
```

`/one-button-launch` 與 `/platform-compatible` 都不會動 ingest 邏輯、catalog、bronze/silver/gold
資料，純粹是新增 launcher 與 git 屬性檔。
