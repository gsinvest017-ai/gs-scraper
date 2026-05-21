# gap-dashboard — DuckDB 待補齊資料視覺化看板

> 啟動：2026-05-21
> 觸發指令：`/safe-yolo 幫我寫一個統計目前 duckdb 資料庫所有待補齊資料的視覺化看板`
> 操作者：claude-opus-4-7

## 目標

建一個工具掃 `catalog/quant.duckdb` 中所有監控的 view，每張表算 lag（今天 - max_date），分級（OK / WARN / STALE / EMPTY / INFO），輸出：

1. Terminal 表（含 ANSI 顏色 + emoji 視覺化）
2. HTML dashboard（給人類看；放 `docs/gap_dashboard.html`）
3. JSON（給機器吃；放 `meta/audit/gap_report.json`）

整合進 `daily_refresh.sh` 收尾，這樣每次 cron 跑完就自動更新看板。

## Milestone 計畫

| M | 目標 | 預期產出 |
|---|---|---|
| M1 | `scripts/gap_report.py` 基本掃描 + 分級 + text 輸出 | 23 個 dataset 註冊；exit code 反映嚴重度 |
| M2 | `--format html` HTML dashboard（含 lag 視覺化 bar） | `docs/gap_dashboard.html` |
| M3 | 整合 daily_refresh.sh + 進度檔 + commit | refresh 結束自動重生 dashboard |

## 進度日誌

### M1 + M2 — gap_report.py（含 text/json/html 三種輸出）

完成項目：

- 新增 `scripts/gap_report.py`（450+ 行）。核心設計：
  - `DATASETS` registry：23 個 dataset，每筆含 `view` / `date_col` / `category` / `fetch_cmd` / `description` / `tier`（P0/P1/P2）
  - 分類規則：
    - `daily-trading`：0-1d=OK / 2-5d=WARN / >5d=STALE
    - `monthly`：0-15d=OK / 15-45d=WARN / >45d=STALE
    - `quarterly`：0-60d=OK / 60-120d=WARN / >120d=STALE
    - `event`（forward-looking）：MAX<today 才警告
    - `derived`（衍生表）：只標 INFO，動作是 rebuild upstream
  - DuckDB lock 處理：用 `tempfile.mkdtemp()` 把 catalog 複製到 temp，連 snapshot（同 `fetch_tej.py` 的做法）
  - Exit code：0=all-OK / 1=有 WARN / 2=有 STALE（給 cron 跟 CI 用）
- 三種輸出：
  - `--format text`（預設）：ANSI 顏色 + emoji 表格，summary 在底
  - `--format json`：dump 到 `meta/audit/gap_report.json`
  - `--format html`：純 HTML + inline CSS，含 summary pills、lag 視覺 bar、severity 表色
  - `--format all`：三者同時跑
- 第一次跑的結果（2026-05-21）：
  - ✅ OK = 7（含 tw_futures_large_trader_daily、bars_1d、tw_inst_futures_full_daily、tw_stock_trading_attrs_daily）
  - ⚠️ WARN = 2（tw_stock_bars 3d、accounting_raw 81d）
  - 🔴 STALE = 12（含 revenue_monthly 50d、tw_inst_stock_daily 6d、tw_margin_daily 6d、bars_1m 70d、macro_daily 14d、tx/mtx_continuous 13d 等）
  - ❓ EMPTY = 1（cross_market_features 沒 date 值）
  - ℹ️ INFO = 1（stock_factor_daily 141d，標 INFO 因為要 rebuild upstream）
- 每筆 STALE 都附建議指令，例：`fetch_tej.py --table inst_stock --append-since-silver`

### M3 — 整合 daily_refresh + commit

完成項目：

- `scripts/daily_refresh.sh` 加 `step 4/4: regenerate gap dashboard`：跑完 catalog rebuild 後自動 `gap_report.py --format all`，把 STALE/WARN exit code 轉成 log WARN（不讓 pipeline 失敗）。
- `.gitignore` 加 `meta/audit/daily_refresh_*.log` / `daily_refresh_cron.log` 規則；HTML 故意保留追蹤（提供 GitHub 線上瀏覽快照）；JSON 由 `meta/**` 既有規則自動忽略。
- Dry-run 路徑保持不動（dry-run 不會跑到 step 4）。
- Commits:
  - `63a493b` — M1-M2: gap_report.py + 初始 dashboard
  - 後續 commit — M3: 整合 daily_refresh + 進度檔終稿

## 視覺看板使用方式（更新）

```bash
# 看終端表
.venv/bin/python scripts/gap_report.py

# 產 HTML（瀏覽器開 docs/gap_dashboard.html）
.venv/bin/python scripts/gap_report.py --format html

# 機器讀取（JSON，落到 meta/audit/gap_report.json）
.venv/bin/python scripts/gap_report.py --format json

# 三者一起（cron / daily_refresh.sh 用）
.venv/bin/python scripts/gap_report.py --format all
```

cron 自動跑（`30 17 * * 1-5`）會把最新 dashboard 寫回 `docs/gap_dashboard.html`。

## 視覺看板使用方式

```bash
# 看終端表
.venv/bin/python scripts/gap_report.py

# 產 HTML（瀏覽器開 docs/gap_dashboard.html）
.venv/bin/python scripts/gap_report.py --format html

# 機器讀取（JSON）
.venv/bin/python scripts/gap_report.py --format json

# 三者一起（cron 用）
.venv/bin/python scripts/gap_report.py --format all
```

## Fallback 指引

- 純 read 工具，不會改 catalog；rollback 直接 `git revert`/`rm scripts/gap_report.py` 即可
- 若 catalog 路徑改變：`scripts/gap_report.py --catalog <path>`
- 若新增 view 後沒進報表：在 `DATASETS` registry 加一筆即可
