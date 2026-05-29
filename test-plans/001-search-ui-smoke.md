---
id: 001-search-ui-smoke
title: Search UI 是否起來、首頁列出 view
runner: playwright-mcp
created: 2026-05-29
tags: [smoke, search-ui]
url: http://192.168.0.249:5050
estimated_seconds: 30
---

## 我想知道

QUANTDATA Search UI 現在還活著嗎、首頁 `/` 有沒有列出 views。

## 提示

- 直接開 http://192.168.0.249:5050（LAN）；若不通改用 http://127.0.0.1:5050（本機）
- 截一張首頁全景
- 從 DOM 抓出 view 數量寫在 summary 裡
- 順便確認 nav 列有 `Views` / `Downloads` / `Gap dashboard ↗` 三個連結
