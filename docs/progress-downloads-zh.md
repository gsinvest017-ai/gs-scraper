# 2026-06-02 — Downloads dashboard 中文化 + 資料源 + 描述

## 目標

`/downloads` 頁面與 gap_dashboard / views dashboard 視覺一致：

- 中文 header / 按鈕 / 互動字串
- 加「資料源」column（pill）+ 上方 source filter dropdown
- 加「說明」column（截斷的 long_description + hover 全文）

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + `app.py /downloads` route 傳 data_source / long_description / sources 給 template |
| **M2** | `downloads.html` 全中文化 + 加 2 個 column + source filter dropdown + 兼容既有 bucket buttons |
| **M3** | 重啟 Flask + e2e 驗（既有 E-011 download_page 測試） + 收尾 |

## 進度日誌

### M1 — route 傳 metadata  `(M1 commit)`

`/downloads` route 用 `get_meta(v)` 從 dataset_meta 取每 view 的
data_source + long_description；同時建 `present_sources` 給 dropdown。

### M2 — template 中文 + pill + source filter  `(M2 commit)`

- header / 按鈕 / 互動字串全中文化：
  - 「Bulk CSV downloads」→「批次 CSV 下載」
  - 「Select All / ≤ 100k / ≤ 1M / > 1M / Clear」→「全選 / ≤ 10 萬列 / ≤ 100 萬列 / > 100 萬列 / 清除」
  - 「Download selected as .zip」→「打包成 .zip 下載」
  - 「N selected · X rows」→「已選 N 個 view · X 列」
  - alert 訊息也中文化
- 加 2 個 column：「說明」（截斷 55 字 + hover 全文）、「資料源」（彩色 pill）
- 加 `<select id="source-filter">` 上方下拉，與 name filter 共同 AND filtering
- bucket button 仍只勾「被 filter 顯示的列」（避免隱藏的列被誤勾）

### M3 — 收尾

驗證：
- `/downloads` DOM 含「批次 CSV 下載」「資料源」「ds-TEJ-API」「全選」「已選」「打包成」
- 20/20 e2e 全綠（既有 E-011 download_page lists views 仍 OK，因為新欄位是 additive）

## 三個 dashboard 視覺統一

至此整套 UI **三個入口** 都吃同一份 `dataset_meta.py`：

| Dashboard | URL | 角色 |
|---|---|---|
| Views（資料表清單）| `/` | 探勘 / 跳到 view 詳情 |
| View detail | `/view/<v>` | 對單一 view 跑 query / 看 chart / schema |
| Downloads | `/downloads` | 批次匯出 CSV / zip |
| Gap dashboard | `/gap_dashboard.html` | 監控完整度 / 延遲 / source |

統一：中文 header、ds-pill 10 色、long_description 截斷 hover、source filter 篩選。

## Fallback

```bash
git revert HEAD~2..HEAD
git checkout HEAD~2 -- ui/search/templates/downloads.html ui/search/app.py
```
