# 2026-05-27 — Gap Dashboard 深色模式

## 觸發

`/safe-yolo 我想要你把gap-dashboard改成深色模式`（接續 Search UI dark；本輪把靜態 gap dashboard 也 dark 化，全專案 UI 一致）

## 目標

`docs/gap_dashboard.html`（+ `docs-site/` mirror）由 `scripts/gap_report.py` 的 `HTML_TEMPLATE` 內嵌 CSS 生成，目前是淺色。改成跟 Search UI / gs-zipline-tej 同一套 GitHub-dark 配色。**必須改 generator（gap_report.py），不能改產物**（下次 regen 會覆蓋）。

## 配色對應（GitHub-dark）

| 元素 | 淺色（舊） | 深色（新） |
|---|---|---|
| body | `#1f2937` on `#f9fafb` | `#e6edf3` on `#0f1419` |
| table | `white` | `#161c24` |
| th | `#f3f4f6`/`#374151` | `#1f2731`/`#e6edf3` |
| border | `#e5e7eb` | `#2a323e` |
| pill/row OK | green `#d1fae5`/`#065f46` | `rgba(86,211,100,.15)`/`#56d364` |
| WARN | amber | `rgba(240,136,62,.15)`/`#f0883e` |
| STALE | red | `rgba(248,81,73,.15)`/`#f85149` |
| EMPTY | purple | `rgba(166,143,255,.15)`/`#b39dff` |
| INFO | blue | `rgba(88,166,255,.15)`/`#58a6ff` |
| bar fills | `#10b981/#f59e0b/#ef4444/#3b82f6/#9ca3af` | `#56d364/#f0883e/#f85149/#58a6ff/#6e7681` |
| code / ltot / bar track | `#f3f4f6` | `#1f2731` |
| muted (subtitle/legend) | `#6b7280` | `#7d8590` |

bar fill 只用 CSS class（inline 只有 width），所以改 `<style>` 即全覆蓋。

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + 配色對應 |
| **M2** | 改 `gap_report.py` `HTML_TEMPLATE` `<style>` → dark + 加 `color-scheme` meta；`gap_report.py --format all` 重生 docs + docs-site；`mkdocs --strict`；commit |

## Fallback

- 配色不滿意 → 調 `HTML_TEMPLATE` 內 CSS 值即可。
- rollback：`git revert` M2。產物會在下次 regen 重生。

## 完成日誌

### M2 — HTML_TEMPLATE dark + regen

- `gap_report.py` `HTML_TEMPLATE` `<style>` 全面換成 GitHub-dark：body `#e6edf3`/`#0f1419`、table `#161c24`、th `#1f2731`、border `#2a323e`、pills/rows 半透明深色 + 亮色文字（OK `#56d364`、WARN `#f0883e`、STALE `#f85149`、EMPTY `#b39dff`、INFO `#58a6ff`）、bar fills 同步亮色、code/ltot/bar track `#1f2731`、muted `#7d8590`。加 `<meta name="color-scheme" content="dark">`。
- `gap_report.py --format all` 重生 `docs/` + `docs-site/` 兩份；驗證 dark token 在產物中、無殘留 `#f9fafb`/`background: white`/`#f3f4f6`。
- `mkdocs build --strict` PASS。
- 旁註：本次 regen summary 顯示 WARN=2（`tx_continuous_d`/`mtx_continuous_d` 漂到 2d lag），是手動 dump 來源的自然時鐘漂移，與本 CSS 變更無關。

## 後續

- 全專案 dark 一致達成：`ui/search`（互動）+ `gap_dashboard`（靜態）+ gs-zipline-tej dashboard。
