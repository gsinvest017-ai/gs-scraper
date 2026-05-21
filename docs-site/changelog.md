# 變更紀錄

本頁只記錄**面向使用者 / 文件 / 介面契約**的變動。內部 refactor / 重命名變數請看 `git log`。

---

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
