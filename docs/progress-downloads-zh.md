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

## Fallback

```bash
git revert HEAD~2..HEAD
git checkout HEAD~2 -- ui/search/templates/downloads.html ui/search/app.py
```
