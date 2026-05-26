# Search Web UI (form-based DuckDB query)

> 啟動：`bash scripts/run_search_ui.sh` → 開 <http://127.0.0.1:5050>

針對「不想寫 SQL 也想撈資料」的場景做的 Flask + Plotly.js 輕量級搜尋介面。對著 `catalog/quant.duckdb` 的 58 個 view 提供：

1. **首頁**：列出全部 view 的 row_count / max_date / 欄位數 / 時間序列 vs tabular 標籤
2. **Query builder**：點某個 view 進入過濾介面
   - 每個欄位依型別自動產生對應 widget（date picker / 數字輸入 / 字串 / 低基數欄位變 dropdown）
   - 可加多條 filter，operator 動態切換（`>=`, `<=`, `IN`, `contains`, `is null`, ...）
   - 排序 + Limit 在同一行
   - 即時 SQL preview（不能執行，只是給看的）
3. **結果**：
   - **Table** 分頁：可橫向 scroll，最大 5000 列
   - **Chart** 分頁（**僅時間序列 view**）：X 軸選日期欄、Y 軸多選 numeric 欄、可選 group_by，Plotly 線圖

## 啟動

```bash
bash scripts/run_search_ui.sh
# → http://127.0.0.1:5050
```

或手動：

```bash
.venv/bin/python -m ui.search.app
```

Port 預設 5050（避開 DuckDB UI 的 4213 與 mkdocs serve 的 8000）；只 bind `127.0.0.1`。

## 與 DuckDB Web UI 的差別

| 維度 | DuckDB Web UI（4213）| Search Web UI（5050）|
|---|---|---|
| 觸發 | `scripts/duckdb_public_ui.sh start` | `bash scripts/run_search_ui.sh` |
| 介面 | SQL editor（需自己寫 query）| 表單填欄位、選 op |
| 結果視覺化 | 純 table | Table + Plotly 時間序列圖 |
| Catalog 同步 | 直接讀 live catalog | 讀 tmp 快照（不影響 live lock）|
| 適用對象 | 寫 SQL 的工程師 | 不會 / 不想寫 SQL 的分析師 |
| 互鎖 | 是（會搶 lock）| 否（temp copy + read_only=True）|

兩者**可同時運行**：DuckDB UI 在 4213，Search UI 在 5050，不會搶 lock。

## 架構

```
ui/search/
├── app.py                    # Flask routes
├── catalog_inspector.py      # 探 catalog views + 欄位型別 + 低基數 distinct values
├── query_builder.py          # 把 form filter 組成 parameterized SELECT（防 SQL injection）
├── templates/
│   ├── base.html             # 共用 layout
│   ├── index.html            # 首頁 (views list)
│   └── view.html             # 過濾 + 結果 + Chart
└── static/
    ├── style.css
    └── main.js
scripts/
└── run_search_ui.sh          # 啟動腳本
```

## 安全

- 只接受 `SELECT`；不允許 DML/DDL（沒有 endpoint 寫 raw SQL）
- 欄位名 / view 名 **強制 whitelist**（白名單來自 `list_views()` + `DESCRIBE view`）
- 所有 user input 都走 DuckDB `?` parameterized query，不字串拼接
- 結果強制 `LIMIT 5000`（看 `query_builder.MAX_LIMIT`）
- Catalog connection 一律 `read_only=True`，且讀 temp 快照（`tmp/search_ui_catalog.duckdb`）— 不會跟 daily_refresh 或 `duckdb -ui` 搶 write lock

## 限制 / 已知 issue

- 同 view 內 cross-column filter 用 AND；OR 與 NOT 還沒做（後續可加）
- Multi-view JOIN 不支援（v1 scope 外）
- 大 view 即使加 filter 跑很慢的話，會卡 5050 thread；目前單 thread Flask dev server（生產用要套 gunicorn / waitress）
- 重啟服務後 cache 才會抓到新 catalog 寫入；可按首頁的 **↻ Refresh catalog snapshot** 強制刷新

## 觸發範例

對著首頁搜尋：「找 `2330` 在 2026 年的日 K + 法人買賣超」

1. 進首頁，輸入框打 `tw_stock_bars` → 點 Query →
2. 加 filter `symbol = 2330`、`trading_date >= 2026-01-01`
3. 排序 `trading_date DESC`，Limit 1000，Run
4. 切到 Chart tab：X = trading_date、Y 多選 close / volume / ... → Render → 互動圖
5. 想看法人，回首頁找 `tw_inst_stock_daily` 重複同 stock_id 2330

## 為什麼這 UI 存在

DuckDB Web UI（4213）很強但要寫 SQL；很多時候只想「給我 stock 2330 在 5/01 後的 OHLCV」這種微觀過濾。Search UI 把這 80% 用法 form 化，不用打字。Power user 仍可去 DuckDB UI 或 CLI。
