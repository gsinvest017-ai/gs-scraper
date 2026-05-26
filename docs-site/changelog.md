# 變更紀錄

本頁只記錄**面向使用者 / 文件 / 介面契約**的變動。內部 refactor / 重命名變數請看 `git log`。

---

## 2026-05-26 — Cron 自動 daily_refresh 證實生效（昨日首次完整 4 步綠燈）

不是新功能、而是「自動化證實生效」的里程碑：

- **昨日 2026-05-25 17:30 CST cron 第一次跑完整 4 階段**（含 step 3.5 `restore_finmind_views`，這是 5/25 上午才加的步驟）並結尾 `daily_refresh OK`
- TEJ 4 個主表 max_date 從 2026-05-22 → **2026-05-25**：`tw_stock_bars` / `tw_inst_stock_daily` / `tw_margin_daily` / `tw_stock_trading_attrs_daily` / `bars_1d`
- 仍有 STALE（`macro_daily` / `tw_inst_futures_daily` / `bars_1m` / `txo_daily_features` / `tw_inst_market_daily` 等），這些 dataset 不在 cron `fetch_tej.py --table all` 範圍，要手動或加 scraper
- **已知 gap**：cron step 沒包 `python -m qd_ingest.sources.derived`，所以新 silver 不會自動 propagate 到 gold parquet。Dashboard 在 cron 跑完後 derived gold 全變 INFO（lag 3d）；下次跑 goldify routine 或在 cron 加 step 5 可關掉這個 gap

日誌：`meta/audit/daily_refresh_2026-05-25.log`，cron wrapper `meta/audit/daily_refresh_cron.log`。

## 2026-05-25 (night) — Goldify routine 自動化（agent + audit script）

把過去 3 輪手動執行的 silver→gold 流程包成可重複呼叫的工具，未來新 silver view 滿格時不再依賴人類掃 dashboard：

- **`scripts/goldify_audit.py`** — 偵測 100% 完整度但 `gold_paths` 為空的 view，輸出 stdout / `--json` / `--markdown` 三種格式；用 9 個 factor template（time_series_bar / flow_rolling / balance_zscore / per_entity_oi / event_panel / boolean_panel / pit_fundamentals / view_materialize / left_join_merge）heuristically 對應 silver schema → 建議仿哪支既有 builder。當前 catalog 跑出 0 candidates（已 fully goldified）。
- **`.claude/agents/goldify-100pct.md`** — repo-scoped agent，使用者說「跑 goldify routine / 處理剩下的 silver→gold」就觸發；強制 4-milestone 流程（plan / builder / registry+catalog / rebuild+dashboard+push），silver multi-ingest dedup 為 builder 責任、progress doc 不可省。
- **`docs-site/ops/goldify-routine.md`** — 使用者文檔，含 ripe candidate 定義、9 個 template 對應表、與 incremental-crawler / `/update-doc` 的分工。
- `docs-site/ops/automation.md` 從 2 agent → 3 agent 表格。

commits `b12d924..b9df543`。進度檔：`docs/progress-goldify-agent.md`。

## 2026-05-25 (evening) — Goldify 全部 100% 完整度 view（3 輪收尾）

把 dashboard 上 **所有 100% 完整度但沒有 gold artifact 的 view** 全部 goldify，分 3 輪推進；最終 dashboard `OK=17 → 24`、總 datasets `30 → 37`、Gold 總量 `~140 MB → ~830 MB`、catalog `36 views → 49 views`。

**Round 1**（commits `a8b6c55..6b71e93`）— 第一批 100% silver：補 5 個 silver→gold backlink + 1 個新 gold

**Round 2**（commits `1bcfce6..0c27ffa`）— 剩下 4 個 silver-only 加 gold builder：

| 新 gold view | 來源 silver | rows | 主要因子 |
|---|---|---:|---|
| `margin_factors` | `tw_margin_daily` | 3.7M | 6 個融資融券時序因子 |
| `fundamentals_pit` | `fundamentals_q` (Q+consolidated) | 93K | PIT panel + TTM/YoY |
| `futures_large_trader_factors` | `tw_futures_large_trader_daily` | 99K | top10 net, OI Δ |
| `futures_inst_factors` | `tw_inst_futures_full_daily` | 466K | per-identity net_oi, L/S ratio |
| `stock_attrs_status` | `tw_stock_trading_attrs_daily` | 3.16M | 11 個 bool flag + 30d 計數 |
| `dividend_calendar` | `cash_dividend_events` | 10.5K | per-share + yield + TTM + YoY |
| `stock_futures_adjustments` | `tw_stock_futures_corp_actions` | 56K | cum cash/stock div + seq |

**Round 3**（commits `e936402..80e2f7e`）— view 層級剩下 4 個 100% 但無 gold 的 view：

| 新 gold view | 來源 | rows | 用途 |
|---|---|---:|---|
| `futures_bar_factors` | `bars_1d` (tw_futures+tw_stock_futures) | 1.74M | 期貨/個股期日 K 衍生（mom/vol/ATR/OI Δ），補齊 stock_factor_daily 期貨對應 |
| `qc_stock_price_diff_snapshot` | `qc_stock_price_diff` view | 6.4M | TEJ vs FinMind 對帳結果 parquet 持久化 |
| `qc_stock_price_diff_yearly` | 同上 | 17 | 逐年 mean/max abs diff（2011+ 完全 zero diff，確認對帳一致）|
| `finmind_price_canonical` | `finmind_stock_price_norm` + `*_adj_norm` | 10.6M | raw OHLCV + adj OHLC merged，下游不需 sqlite |

**Backlink 大整理**：所有有 derived 對應的 silver/view 都新增 `gold_paths` 條目，`bars_1d` 一次掛 5 個 backlink（stock_factor + futures_bar + tx/mtx/sf continuous）。

**Silver dedup 副產品**：本輪 builder 順手把 silver multi-ingest 重覆列在 gold 層去重（trading_attrs 19%、其他 6-8%）。每個 builder 統一 `unique(subset=key, keep='last' by ingestion_ts)`，所以 gold rows 通常 < silver rows，這是 feature 不是 bug。

剩下的 dashboard STALE/WARN/EMPTY 都是**上游完整度本身 < 100%** 的 dataset（macro/chip_dist/revenue_monthly/tw_inst_futures_daily 等），非 silver→gold 結構性缺口。

進度文件：[`docs/progress-goldify-remaining-silver.md`](https://github.com/gsinvest017-ai/gs-scraper/blob/main/docs/progress-goldify-remaining-silver.md) / [`progress-goldify-final-silver.md`](https://github.com/gsinvest017-ai/gs-scraper/blob/main/docs/progress-goldify-final-silver.md) / [`progress-goldify-bars-qc-finmind.md`](https://github.com/gsinvest017-ai/gs-scraper/blob/main/docs/progress-goldify-bars-qc-finmind.md)

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
