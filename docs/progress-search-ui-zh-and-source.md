# 2026-06-02 — Views dashboard (Search UI) 中文化 + 資料源 + 長描述

## 目標

gap_dashboard 已中文化 + 加 source pill；Search UI 的 views dashboard (`/`)
還是英文，且沒有 source / 長描述。本輪統一：

1. **views dashboard 中文 header** + 補資料源 column + 描述 column
2. **view detail (`/view/<v>`)** 也補上資料源 pill + 詳細說明
3. **共用 metadata 來源**：把 `_DATASET_META` 從 `scripts/gap_report.py`
   抽出到 `src/qd_ingest/common/dataset_meta.py`，避免兩處維護

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + 把 `_DATASET_META` + `DATA_SOURCES` 抽到 `src/qd_ingest/common/dataset_meta.py`；`scripts/gap_report.py` 改 import |
| **M2** | `ui/search/catalog_inspector.view_summary` + `get_view_meta` 加 `data_source` / `long_description`；index 路由傳給 template |
| **M3** | `index.html` 中文 header + 資料源 pill + 描述；`view.html` 在 title 區加 pill + 描述；CSS 在 `gs-theme.css` 加 source pill 樣式 |
| **M4** | 重啟 Flask + 收尾 |

## 進度日誌

### M1 — 進度檔  `(M1 commit)`

10 種 source enum + 共用 metadata 計畫。

### M2 — `src/qd_ingest/common/dataset_meta.py`（共用模組）  `(M2 commit)`

抽出 `DATA_SOURCES` + `DATASET_META`（60+ view 的中文描述對照）。
- `scripts/gap_report.py` 改 `from qd_ingest.common.dataset_meta import ...`
- `ui/search/catalog_inspector.view_summary` 補 `data_source` + `long_description`
- 驗 import OK，50/50 view 全有 metadata

### M3 — index.html / view.html 中文化 + source pill  `(M3 commit)`

**`templates/index.html`**：
- 全中文 header（View 名稱 / 說明 / 資料源 / 列數 / 最新日期 / 欄數 / 類型）
- 加 `<select id="source-filter">` 篩資料源（client-side JS filter）
- 「說明」column 顯示截斷 60 字 long_description + hover title 看全文
- 「資料源」column 用彩色 pill（ds-pill class）

**`templates/view.html`**：
- 標題後 inline 顯示 ds-pill（資料源）
- subtitle 下方加 📝 long_description 一段中文說明
- schema details summary 改「Schema（N 欄）」

**`ui/search/static/gs-theme.css`**：
- 加 .ds-pill base + 10 種 .ds-{source} 顏色（同 gap_dashboard）
- 加 td.desc-cell（max-width 360px + ellipsis，hover 展開）

**`ui/search/app.py`**：
- index 路由補 `sources=present_sources` 傳給 template（供 dropdown）
- view_page 路由補 `data_source`/`long_description` 傳給 template

驗證：
- curl `/` → DOM 含「資料表清單」「資料源」「ds-TEJ-API」「ds-FinMind」「時序」「查詢」
- 20/20 e2e (test_e2e_search_ui) 全綠

## 視覺一致性

兩個 dashboard（gap_dashboard 與 views dashboard）現在 **共享同一份 metadata 來源 + 同一套 pill 配色**，UI 風格統一。

## Fallback

```bash
git revert HEAD~3..HEAD
git checkout HEAD~3 -- scripts/gap_report.py ui/search/
rm -f src/qd_ingest/common/dataset_meta.py
```
