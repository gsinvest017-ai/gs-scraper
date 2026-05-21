# docs-site 進度

> 建立日期：2026-05-21
> 範圍：仿 `/mnt/c/Users/User/autogo/docs-site/` 的 MkDocs Material 風格，幫 QUANTDATA 建一個 UI / DB / 架構 / 操作 文檔網站。
> 上線方式：GitHub Actions → `gh-pages` branch。

---

## 目標

把 QUANTDATA 散落在 README / DATA_ARCHITECTURE / docs/progress-* 的內容，整理成一個結構化、可瀏覽、自動發佈的文檔站。內容覆蓋：

- **架構**：medallion 三層、目錄結構、資料流
- **DB**：DuckDB catalog views、canonical schema、FinMind / RS_Rating 整合
- **UI**：DuckDB Web UI、gap dashboard、Tailscale Funnel
- **Ops**：安裝、daily refresh、cron 排程、手動 ingest、troubleshooting

技術選型：與參考 docs-site 完全一致（MkDocs 1.6.1 + Material 9.7.6 + pymdownx + zh-TW + indigo palette）。

---

## Milestone 規劃

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | scaffold：`mkdocs.yml` + `.github/workflows/docs.yml` + `docs-site/index.md` + 目錄 | ✅ |
| **M2** | 架構頁三張（overview / medallion / dataflow） | ✅ |
| **M3** | DB 頁五張（overview / views / schema / finmind / rs-rating） | ✅ |
| **M4** | UI 頁四張（overview / duckdb-ui / gap-dashboard / funnel） | ✅ |
| **M5** | Ops 頁五張 + changelog；本地 `mkdocs build --strict` 跑通 | ⏳ |

每完成一個 milestone commit 一次。

---

## 進度日誌

### M1 — scaffold

落地 `mkdocs.yml`（site_name "QUANTDATA 文檔" / docs_dir `docs-site` / Material zh-TW indigo），`.github/workflows/docs.yml`（與 autogo 同版本：mkdocs 1.6.1 + material 9.7.6；trigger paths 改為 QUANTDATA 自己的 `docs-site/`、`scripts/`、`src/`），`docs-site/index.md`（含 Mermaid 系統圖、4 grid card、3 tabs 快速跑起來示例），5 個子目錄（architecture / db / ui / ops / assets）。

Nav 與參考站對齊但分支不同：架構 / DB / UI / Ops + 變更紀錄。

### M2 — 架構頁

落地三張：

- `architecture/overview.md` — 系統地圖 mermaid + 三層分工 + 工作流線 + 兩條相關 repo
- `architecture/medallion.md` — bronze / silver / gold 三層規則 + 邊界規則 + catalog 角色（catalog DuckDB 不存資料）
- `architecture/dataflow.md` — 7 條資料流（TEJ daily、FinMind snapshot、TAIFEX、histdata、gold derive、QC、latency 概況）含 sequenceDiagram

mermaid 用 `flowchart` + `sequenceDiagram` 混用，全部能被 pymdownx superfences 渲染。

### M3 — DB / Catalog 頁

5 張頁面：

- `db/overview.md` — DuckDB 選型 rationale、單檔三種用法（CLI / Python / Web UI）、36 view 分類表、安全規則（read_only / 備份 / 寫鎖）
- `db/views.md` — 11 段（bars / flows / fundamentals / futures / options / macro / events / gold / FinMind / QC / reference），每段表格列 view + rows + date range + 描述
- `db/schema.md` — silver canonical schema 逐張表逐欄位（bars, inst_stock, margin, fundamentals_q, macro, symbol_map, contract_specs）+ 設計原則 + 加欄位流程
- `db/finmind.md` — 為什麼接、如何接（sqlite_scan view-baked path）、8 個 view、QC 結果（100% bit-exact）、4 種典型查詢、deferred M8/M9 路線
- `db/rs-rating.md` — RS rating IBD 公式、設計總覽、output schema、完整 DuckDB SQL skeleton、5 個 open question、implementation order、來源參考

### M4 — UI 頁

4 張：

- `ui/overview.md` — 三條看資料路徑（Web UI / CLI / gap dashboard）、遠端存取三選一比較、工具版本
- `ui/duckdb-ui.md` — 啟動 + 雙 catalog DB 分工 + 寫鎖 troubleshooting + 5 個 sample query（content tabs）
- `ui/gap-dashboard.md` — gap_report.py 用法 + 5 個 severity + classify SLA 邏輯 + 加新 view 步驟 + 自動化
- `ui/funnel.md` — Tailscale Funnel WIP-blocked 紀錄（DuckDB UI 內建 token-auth funnel 不行）+ 三條替代方案（SSH / 靜態 funnel / FastAPI playground）+ 安全清單

### M5 — pending

---

## Fallback 指引

- **本地預覽**：`.venv/bin/pip install mkdocs==1.6.1 mkdocs-material==9.7.6 && .venv/bin/mkdocs serve -a 127.0.0.1:8080`
- **strict build**：`.venv/bin/mkdocs build --strict`（CI 也跑這個）
- **Rollback**：`rm -rf docs-site site mkdocs.yml .github/workflows/docs.yml && git revert <commit>`

GitHub Action 第一次跑前要先確認 `Settings → Pages → Source` 選 `gh-pages` branch（一次性手動設定）。
