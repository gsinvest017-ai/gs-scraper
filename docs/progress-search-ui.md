# 2026-05-26 — DuckDB Search Web UI

## 觸發

`/safe-yolo 因為目前 duckdb 需要透過 sql query 才能夠查詢資料 替 duckdb 寫一個 search web UI 可以透過填入 database 中 data catalog 的 fields etc. 來搜尋到資料 query 回來的資料可以以 table 格式呈現 如果是時間序列資料除了 table 格式以外還可以以時間序列的圖表格式呈現`

## 目標

替 `catalog/quant.duckdb` 寫一個輕量級 web UI（Flask + Plotly.js CDN，無 npm build），讓非 SQL 使用者：

1. 進首頁看到 catalog 全部 56 個 view，含 row_count / max_date / column count
2. 點某個 view → form 化的過濾介面：每個欄位依型別自動產生對應 widget（date range picker / 字串模糊比對 / 數值範圍 / enum dropdown）
3. 送出 → 後端組 `SELECT ... FROM view WHERE ... LIMIT 5000` 動態 SQL
4. 結果頁：sortable table + 若偵測為時間序列（含 `trading_date` / `date` 等 + 至少一個 numeric col）→ 額外 Plotly 線圖（X = date_col、Y = 使用者選的 numeric cols、可選 group by）

## 技術選型

- **Backend**: Flask（已用於 sister repo `gs-zipline-tej/dashboard/`），單檔 ~200 行
- **DB connection**: `duckdb-python`（catalog 唯讀打開避免鎖；併發只讀沒問題）
- **Frontend**: Server-rendered Jinja templates + Plotly.js 從 CDN 載入 + vanilla JS（無 build step）
- **Port**: 5050（127.0.0.1 only；要 expose 用 SSH tunnel 或 Tailscale Funnel）
- **Style**: 抄 docs-site Material 風的 dark-mode-aware 簡潔配色

## 範圍邊界

- ✅ 唯讀 SELECT；不允許 DELETE/UPDATE/INSERT（後端只組 SELECT）
- ✅ 自動 LIMIT 5000，超過給「too many rows, refine your filter」提示
- ✅ 安全：所有 user input 走 parameterized DuckDB query，不 string concat
- ❌ 不做使用者帳號 / 權限分層（127.0.0.1 only）
- ❌ 不做 cross-view JOIN（單一 view 過濾）— 第 2 階段才考慮
- ❌ 不做 chart 互動性（zoom 用 Plotly 內建即可）

## 檔案結構

```
ui/
└── search/
    ├── app.py                      # Flask app
    ├── catalog_inspector.py        # introspect catalog views + column types
    ├── query_builder.py            # build safe parameterized SQL
    ├── templates/
    │   ├── base.html
    │   ├── index.html              # list of views
    │   └── view.html               # filter form + result table + chart
    └── static/
        ├── style.css
        └── main.js
scripts/
└── run_search_ui.sh                # launcher: source venv, set TPE_TZ, run flask
docs-site/ui/
└── search.md                       # user-facing usage page
```

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + ui/search/ Flask 骨架 + landing template | ✅ |
| **M2** | catalog introspection（views list, columns + types, distinct values for enums）+ form widgets | ✅ |
| **M3** | query endpoint + table + Plotly chart for time-series | ✅ |
| **M4** | `docs-site/ui/search.md` + `scripts/run_search_ui.sh` + strict mkdocs + commit + push | ✅ |

## Fallback

- Flask 5050 port 衝突：改用 5051+
- DuckDB UI（4213）的 lock 衝突：catalog connection 一律用 `read_only=True`，不會搶 lock
- 大 view（bars_1d 10M+ 列）查詢慢：強制加 LIMIT 5000 + 提示 user 加 filter

## 完成日誌

### M1-M3 — Flask UI 全套（合併 commit）

實作清單：
- `ui/search/app.py`（130 行 Flask）— routes：`/`, `/view/<name>`, `/api/query` (POST), `/api/refresh` (POST)
- `ui/search/catalog_inspector.py`（180 行）— `list_views()`、`get_view_meta()` 含欄位型別分類（date/numeric/string/bool）+ 低基數欄位（≤ 50 distinct）自動 dropdown；catalog 走 temp copy + `read_only=True` 不搶 lock
- `ui/search/query_builder.py`（90 行）— `Filter` dataclass + 9 種 op（eq/contains/in/range_min/range_max/date_from/date_to/is_true/is_false/isnull/notnull）；強制 view + column whitelist，所有 value 走 `?` parameterized query
- `templates/{base,index,view}.html` — Jinja Material-style；index 列 58 個 view + 即時 client-side filter；view page 含 schema details + filter builder + result table + Plotly chart tabs
- `static/{style.css,main.js}` — 400 行 vanilla JS：動態 filter row 工廠（依欄位型別切 widget）、SQL preview、JSON API 呼叫、Plotly 多 trace 圖（含 group-by ≤ 20 series cap）

驗證：
- `python -m ui.search.app` 啟動 OK，title `<title>Views — QUANTDATA Search</title>`
- `POST /api/query` 餵 `view=tw_stock_bars, filters=[symbol=2330, trading_date>=2026-05-20]` → 5 列 24 欄回傳，含 TSMC 5/26 OHLCV (2270.0/2325.0/2270.0/2270.0)、5/25 ...

### M4 — launcher + docs + nav

- `scripts/run_search_ui.sh`（30 行 bash）— sanity check venv + catalog 後 exec `python -m ui.search.app`
- `docs-site/ui/search.md` — user-facing 文件：與 DuckDB UI 4213 的差異對照表、架構 tree、安全機制（whitelist + parameterized + read_only + LIMIT 5000）、觸發範例
- `mkdocs.yml` nav 加「Search Web UI（form-based）」進 UI section
- `mkdocs build --strict`：PASS

## Live

啟動：`bash scripts/run_search_ui.sh` → <http://127.0.0.1:5050>

文件：<https://gsinvest017-ai.github.io/gs-scraper/ui/search/>（push 後生效）
