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
| **M2** | 架構頁三張（overview / medallion / dataflow） | ⏳ |
| **M3** | DB 頁五張（overview / views / schema / finmind / rs-rating） | ⏳ |
| **M4** | UI 頁四張（overview / duckdb-ui / gap-dashboard / funnel） | ⏳ |
| **M5** | Ops 頁五張 + changelog；本地 `mkdocs build --strict` 跑通 | ⏳ |

每完成一個 milestone commit 一次。

---

## 進度日誌

### M1 — scaffold

落地 `mkdocs.yml`（site_name "QUANTDATA 文檔" / docs_dir `docs-site` / Material zh-TW indigo），`.github/workflows/docs.yml`（與 autogo 同版本：mkdocs 1.6.1 + material 9.7.6；trigger paths 改為 QUANTDATA 自己的 `docs-site/`、`scripts/`、`src/`），`docs-site/index.md`（含 Mermaid 系統圖、4 grid card、3 tabs 快速跑起來示例），5 個子目錄（architecture / db / ui / ops / assets）。

Nav 與參考站對齊但分支不同：架構 / DB / UI / Ops + 變更紀錄。

### M2 — pending

### M3 — pending

### M4 — pending

### M5 — pending

---

## Fallback 指引

- **本地預覽**：`.venv/bin/pip install mkdocs==1.6.1 mkdocs-material==9.7.6 && .venv/bin/mkdocs serve -a 127.0.0.1:8080`
- **strict build**：`.venv/bin/mkdocs build --strict`（CI 也跑這個）
- **Rollback**：`rm -rf docs-site site mkdocs.yml .github/workflows/docs.yml && git revert <commit>`

GitHub Action 第一次跑前要先確認 `Settings → Pages → Source` 選 `gh-pages` branch（一次性手動設定）。
