# 2026-05-27 — Search UI 預設深色模式（跨專案 UI 一致）

## 觸發

`/safe-yolo 將dashboard預設配色設為深色模式以跟其他專案如 ~/gs-zipline-tej/ 在UI上兼容`

## 目標

QUANTDATA 的 DuckDB Search Web UI（`ui/search/`，Flask + Plotly, 127.0.0.1:5050）目前是淺色。`gs-zipline-tej` 的 Strategy Pool dashboard（同為 Flask + Plotly, :5001）已是 GitHub-dark 風格（見其截圖）。把 Search UI 預設改成同一套深色配色，讓兩個 dashboard 視覺一致。

## 參考配色（取自 `gs-zipline-tej/dashboard/static/style.css`）

| var | value |
|---|---|
| bg | `#0f1419` |
| panel | `#161c24` |
| panel-2 | `#1f2731` |
| border | `#2a323e` |
| text | `#e6edf3` |
| muted | `#7d8590` |
| accent (blue) | `#58a6ff` / hover `#79c0ff` |
| good | `#56d364` |
| warn | `#f0883e` |
| bad | `#f85149` |
| tag | `#2d3a4a` |

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + 配色對應 |
| **M2** | `ui/search/static/style.css` → 深色（`:root` 換深色 vars + 取代寫死的淺色 `#fff`/`#f3f4f6`/tag 底色 + 補表單控件深色規則）|
| **M3** | `main.js` Plotly layout 加深色 template（paper/plot bgcolor、font、grid/line color）+ `base.html` 加 `<meta name="color-scheme" content="dark">` |
| **M4** | 起 Flask 服務 curl 驗證 CSS/JS serve 正常 + commit |

## 影響範圍

僅 `ui/search/`（前端配色）。catalog/data/builders 完全不動。純前端、可逆（`git revert`）。

## Fallback

- 配色不滿意 → 調 `:root` 變數即可（其餘規則都吃變數）。
- rollback：`git revert` M2/M3 commit。

## 完成日誌

（M2-M4 後追加）
