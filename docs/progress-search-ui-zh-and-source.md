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

## Fallback

```bash
git revert HEAD~3..HEAD
git checkout HEAD~3 -- scripts/gap_report.py ui/search/
rm -f src/qd_ingest/common/dataset_meta.py
```
