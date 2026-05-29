---
id: 005-gap-dashboard-health
title: gap dashboard 看 catalog 健康度（STALE / EMPTY）
runner: playwright-mcp
created: 2026-05-29
tags: [health, dashboard]
estimated_seconds: 45
---

## 我想知道

`/gap_dashboard.html` 現在 catalog 整體狀況：OK / WARN / STALE / EMPTY / INFO
各幾條，有沒有掉到完全空的 view、最久沒更新的是哪幾支。

## 提示

- 起點 http://192.168.0.249:5050/gap_dashboard.html
- 截整頁（dark mode）
- 從頁面內 summary line 抓五個數字寫在 summary 裡
- 若 STALE > 5 或 EMPTY > 0 → 把那些 view 名單列出來
- 若 GS-gold 配色沒套上（背景看起來不是 warm-black + 金 radial）→ 提醒
