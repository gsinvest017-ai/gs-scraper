---
id: 004-downloads-bulk-csv
title: Downloads 頁面分桶按鈕 + 單檔 CSV 下載
runner: playwright-mcp
created: 2026-05-29
tags: [downloads, smoke]
estimated_seconds: 60
---

## 我想知道

`/downloads` 頁面 4 個分桶按鈕（≤100k / ≤1M / >1M / All / Clear）會不會正確
勾選；隨便點一支小 view 的「CSV ↓」能不能下載得到非空 CSV。

## 提示

- 起點 http://192.168.0.249:5050/downloads
- 截整頁
- 點 `≤ 100k` → 數一下勾起來幾個（DOM 內 checked count），對照右下「N selected」
- 點 `Clear` → 確認全清
- 找一個小 view（例如 `calendar_xtai`，~4k rows），按該列「CSV ↓」，下載後
  截一張 dev tools network 200 狀態 + 檔案大小
- summary 寫：(1) bucket 對不對 (2) CSV 第一行是否為 column header
