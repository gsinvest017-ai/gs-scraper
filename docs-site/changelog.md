# 變更紀錄

本頁只記錄**面向使用者 / 文件 / 介面契約**的變動。內部 refactor / 重命名變數請看 `git log`。

---

## 2026-05-25 (late PM) — Goldify 100% silver + trading-day-aware lag v2

- **Goldify**：重新整批跑 `python -m qd_ingest.sources.derived`：
  - `stock_factor_daily` 從 142d 過時 → 6.6M 列 / 2,911 stocks / max 2026-05-22
  - `cross_market_features` 從 EMPTY → 2,080 列 / 27 cols
  - 新增 `gold/features/inst_flow_factors.parquet`（6.57M 列，2,615 stocks，2010-2026，9 個法人流量因子）；builder 列入 `build_all()`
- **Trading-day-aware lag v2**：
  - `TRADING_DAY_CATEGORIES` 加入 `snapshot` + `derived` — 修正 `finmind_*_norm` / `qc_stock_price_diff` / `stock_factor_daily` 等 view 在週末 / 盤中時誤判 lag 的 bug
  - `EOD_CUTOFF_HOUR_TPE` 從 15:00 推到 **18:00**（對齊 cron 17:30 + buffer）— 13:30-17:30 之間不會誤標今日為 expected
- **FinMind 2026-05-25 snapshot** 上 bronze：`finmind_stock_price_norm` / `*_adj_norm` max 從 5/15 / 5/13 推到 **5/22**
- Dashboard summary：`OK=4 WARN=9 STALE=8 INFO=4 → OK=15 WARN=1 STALE=8 INFO=2`

## 2026-05-25 (PM) — Dashboard 三大改進

- **完整度排序**：gap_dashboard 預設按 `clamp(1 − lag/90, 0, 1)` 完整度 DESC 排（同分 P0 優先），補進 lag-bar 顯示「填滿 = 完整」。新增「完整度」百分比欄
- **Trading-day-aware lag**：`daily-trading` category 對齊到 `expected_latest_trading_day`（calendar_xtai + Mon-Fri fallback + 15:00 TPE EOD cutoff），週末與盤中時段不再誤判 lag。effect: OK=4 → OK=12
- **Storage layer columns**：每筆 row 新增 5 個欄（📦 Raw / 🥉 Bronze / 🥈 Silver / 🥇 Gold / 📊 Catalog rows）含 size·file-count + path tooltip；頂部 4 個 layer-total pill 用 pattern union 去重計總；可作為資料遷移 checklist
- **架構頁 cleaning-criteria.md**：新增 raw → bronze / bronze → silver / silver → gold 三段 transition 的完整規則 + checklist + 失敗模式速查表

## 2026-05-25 (AM) — 自動化 + 大量 STALE 補完

- **新增 `incremental-crawler` repo agent**（`.claude/agents/incremental-crawler.md`）：每次跑增量爬蟲都強制 regen gap_dashboard 並 commit
- **新增 `/update-doc` 全域 skill**（`~/.claude/skills/update-doc/`）：任何 repo 都能用，根據 git log 提案 doc 更新
- **新增 `scripts/restore_finmind_views.py`**：`qd-ingest build-catalog` 後自動還原 9 個 FinMind / qc views，daily_refresh.sh 在 step 3.5 自動呼叫
- **TX / MTX 連續期延伸到 2026-05-22**：從 `bars_1d` 衍生（每日 max(volume) 月份合約為 front），不依賴 `RAW_SOURCES/日k 期貨tquant lab/` 手動檔；新列 `source='qd_{tx|mtx}_continuous_extended_from_bars1d'`，`adj_factor=NULL`
- **新增 docs-site/ops/automation.md** 一頁說明上述兩個自動化
- **TEJ catch-up 大規模補洞**：`stock_trading_attrs +244,557` / `chip_dist +44,086` / `inst_futures_full +10,260` / `accounting_raw +6,290` / `security_attrs +3,405` / `stock_futures_corp_actions +1,865` / `cash_dividend +698` 列
- **啟動 FinMind tick crawler**（PID-managed background process）：8 trading days × 3,088 stocks ≈ 24,704 calls / sponsor tier 1500/hr ≈ ETA ~16 hr；首 30 秒驗證 +224K rows
- **`gap_report.py` 新增 `snapshot` category**：bronze one-shot 資料（如 finmind_*）surface as INFO 而非 STALE

## 2026-05-21 — 文檔站上線（v0.1）

- 加 `docs-site/` 完整 MkDocs Material 站台
- 加 `.github/workflows/docs.yml`，每次 push 監聽 `docs-site/ / mkdocs.yml / scripts/ / src/`，build → push 到 `gh-pages`
- nav 分四大支柱：架構 / DB / UI / Ops
- 進度文件：`docs/progress-docsite.md`

## 2026-05-21 — Gap dashboard 加 FinMind 系列

- `scripts/gap_report.py` 加 `snapshot` category 分支（bronze one-shot dump → INFO，不觸發 alert）
- 在 `DATASETS` 註冊 3 個 view：`finmind_stock_price_norm`、`finmind_stock_price_adj_norm`、`qc_stock_price_diff`

## 2026-05-21 — FinMind sqlite snapshot 整合（M4-M7）

- 從 `RAW_SOURCES/FINMIND資料集.zip` 解出 2.5 GB sqlite 到 `bronze/finmind/finmind_2026-05-18.sqlite`（含 SHA256）
- 在 `catalog/quant.duckdb` 與 `quant_public.duckdb` 各建 8 個 `finmind_*` view（含 2 個 canonical `*_norm`）
- 建 `qc_stock_price_diff` view：TEJ vs FinMind 2010+ 對帳；100 檔 sample × 6 年 → 100% bit-exact
- 進度文件：`docs/progress-finmind-rsrating-integration.md`

## 2026-05-18 — RS_Rating 整合規格（M1-M3）

- 從 `RAW_SOURCES/RS_Rating.7z`（287 MB Windows PyInstaller bundle）抽 15 個 Python source + docs 到 `_quarantine/rs_rating_unpacked/`（176 KB）
- 寫 `docs/spec-gold-rs-rating-daily.md`：RS-rating IBD 公式 → DuckDB SQL skeleton + 5 個 open question
- 不會在 silver / catalog 留 RS_Rating bundle 痕跡；演算法重新實作為 gold factor 時才落地

## 2026-05-18 — Daily refresh orchestrator

- `scripts/daily_refresh.sh`：flock + idempotent fetch_tej → ingest → catalog rebuild → gap_report 流線
- `scripts/install_cron.sh`：idempotent crontab 安裝/移除/replay；預設 Mon-Fri 17:30 CST
- `scripts/gap_report.py`：跑遍 catalog view 計算 freshness lag，輸出 text / json / html

## 2026-05-18 — DuckDB public URL 嘗試

- 嘗試 ngrok：authtoken 跑不通，棄用
- 嘗試 Tailscale Funnel：三道門檻過了，但 DuckDB UI 1.5.x 內建 token-auth 擋 funnel-exposure → WIP-blocked
- 留下 `scripts/duckdb_public_ui.sh`（snapshot + read-only UI on 4214）給 SSH tunnel 路線
- 文件：[`ui/funnel.md`](ui/funnel.md)、`docs/progress-tailscale-funnel.md`、`docs/progress-duckdb-public-url.md`

## 2026-05-18 — TEJ P0+P1+P2 dataset 全部接通

- AFUTR / AFUTRHU / APISALE / ADIV / AFUTRSTK / AFINST / APISTOCK / APISTKATTR / AINVFINB 全部寫入 silver
- 對應 catalog view 全部建好（見 [Catalog views](db/views.md)）
- 規模：bars_1d 10.4M, tw_inst_stock_daily 6.5M, tw_margin_daily 3.7M, ...

## 2026-05-13 — Medallion lakehouse 初始 schema 鎖定

- `DATA_ARCHITECTURE.md` v1.0 落地：bronze / silver / gold 三層職責、canonical schema 規範
- 第一批 silver view：`tw_stock_bars`、`fundamentals_q`、`tw_inst_stock_daily`、`tw_margin_daily`、`macro_daily`
- Catalog 引擎決定為 DuckDB（理由見 [DB 概覽](db/overview.md)）
