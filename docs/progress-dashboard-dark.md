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

### M2 — style.css 深色（commit `9393fd5`）

`:root` 換成 gs-zipline-tej 的 GitHub-dark vars；寫死的淺色全部換掉（header/table/card `#fff`→`--panel`、`th #f3f4f6`→`--panel-2`、tag pastel 底色→半透明深色、`.remove`/`.err`→`--bad`）；新增 `input/select/textarea` 深色規則（避免原生控件出現白底）。

### M3 — Plotly + base.html（commit `d9b6aaf`）

`Plotly.newPlot` layout 加 `paper_bgcolor/plot_bgcolor=#161c24`、`font #e6edf3`、axis grid/line/tick 深色；`base.html` 加 `<meta name="color-scheme" content="dark">`（讓表單控件/捲軸跟著深色）。

### M4 — 測試 + 重啟

- Flask app 起在 5051 驗證新 template：index + `/view/macro_factors` 皆 200，`color-scheme` meta 存在，無 error。
- 發現 **5050 有 5/26 留下的舊 process（PID 180595）佔埠**，serve 舊淺色 template；`kill` 後在 5050 重啟新程式碼 → 確認 serve 深色（`color-scheme` meta + `--bg:#0f1419` CSS）。
- 驗證方式為 curl（HTML/CSS/meta 正確 serve）；CSS 為 deterministic palette，未在瀏覽器實際渲染。

## 後續

- `docs/gap_dashboard.html`（gap_report.py 內嵌 CSS 的靜態報告）仍是淺色；若要全專案一致可另把它也 dark 化（獨立任務，不在本輪 scope）。
