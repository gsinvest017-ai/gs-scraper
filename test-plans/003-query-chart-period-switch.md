---
id: 003-query-chart-period-switch
title: query 跑出來、chart 切日/周/月看圖會不會跟著重畫
runner: playwright-mcp
created: 2026-05-29
tags: [interaction, chart]
estimated_seconds: 90
---

## 我想知道

在某個有時間序列的 view（例如 `inst_flow_factors` 或 `margin_factors`）跑 query
後，chart 區塊切換 period（D/W/M/H/Y）時 Plotly 圖會不會跟著 resample 重畫。

## 提示

- 起點 http://192.168.0.249:5050
- 隨便點一個 P0 view（找 row_count > 1000 的）
- Run query（預設 limit 1000 就行）
- 切 period 從 D → W → M，每次切完截一張 chart 截圖
- summary 寫：(1) chart 有沒有真的變稀疏 (2) x 軸 label 格式有沒有變
- 若 chart 不出來或 console 有 error，標 FAIL 並貼錯誤訊息
