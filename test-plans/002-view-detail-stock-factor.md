---
id: 002-view-detail-stock-factor
title: 點開 stock_factor_daily 看 metadata + schema
runner: playwright-mcp
created: 2026-05-29
tags: [smoke, view-detail]
estimated_seconds: 45
---

## 我想知道

`/view/stock_factor_daily` 頁面顯示什麼？row_count、columns、completeness、
最新 max_date 是多少？視覺上有沒有破版。

## 提示

- 起點 http://192.168.0.249:5050/view/stock_factor_daily
- 截整頁
- 抓出 row_count 和 max_date 寫進 summary
- 若 chart-controls 區塊有 period / agg 兩個 select，再截一張展開的
