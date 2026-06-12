# QUANTDATA Data API — 對外資料串接設計

**Date:** 2026-06-12
**Status:** Approved design (pre-implementation)
**Goal:** 讓其他專案（gs-zipline-tej / gs-strategy / 未來遠端或跨語言專案）能串接 QUANTDATA
catalog 的**全部**資料（77 views：bars / fundamentals / flows / factors / taifex ticks /
insider / macro…），同時保留本機 Python 回測的零複製速度。

---

## 1. 背景與需求

- 現況：`/api/v1/*` 只有**即時行情**（health/snapshot/ticks/bars-of-one-symbol）。bulk catalog
  資料只能透過 UI 的 `/api/query`（filter form，非穩定契約）或 `/download/*`（CSV/zip）取得。
- 消費端現況：`gs-zipline-tej` 直接 `pd.read_parquet(...)` 建 bundle；`gs-strategy` 跑在那些 bundle 上。
  → 消費端是 **Python、bulk 歷史、同機/同網**為主，但也要支援**遠端/跨語言**。

### 已確認決策
1. **本機直讀 + REST 雙軌都要**（consumer 不論在哪都寫一樣的程式）。
2. REST 查詢模型：**欄位篩選為主 + 唯讀 SQL 進階**。
3. 安全：**API token 認證（env，不入庫）+ 預設綁 LAN**。

---

## 2. 架構

```
        catalog/quant.duckdb  (77 views over silver/gold/reference parquet)
                    │ read-only snapshot (既有 catalog_inspector)
                    ▼
   ┌────────────────────────────────────────────────────────┐
   │  qd_access core  (新, 薄層, 包既有元件)                   │
   │   • list_views() / view_schema(v)   ← catalog_inspector  │
   │   • query(view, filters, order, limit, cursor) ← query_builder │
   │   • safe_sql(select)  ← 新增, SELECT-only / read-only / timeout / row cap │
   └───────────┬───────────────────────────────┬─────────────┘
   in-process  │                                │ in-process
   ┌───────────▼──────────┐        ┌────────────▼─────────────────┐
   │ quantdata client     │        │ /api/v1 catalog REST (Flask bp)│
   │  local: qd_access     │        │  /views /views/{v}/schema     │
   │         direct(Arrow) │        │  /data/{view}  /sql           │
   │  remote: REST+token   │        │  + bearer token + OpenAPI     │
   └──────────────────────┘        └───────────────────────────────┘
```

**設計原則：** 重用既有 `catalog_inspector`（read-only 快照連線 + list/schema）與 `query_builder`
（filter→safe parameterized SQL），**不開平行 stack**。新程式只有：`safe_sql()`、REST blueprint、
token middleware、client package。

---

## 3. 元件

### 3.1 `qd_access` core（新模組，`ui/search/qd_access.py`）
單一唯讀 catalog 存取層，被 client(local) 與 REST 共用：
- `list_views() -> list[dict]` — 重用 `catalog_inspector.view_summary`。
- `view_schema(view) -> dict` — 重用 `catalog_inspector.get_view_meta`。
- `query(view, filters, select, order, dir, limit, cursor) -> (columns, rows, next_cursor)` —
  重用 `query_builder.build_sql`；新增 keyset cursor。
- `safe_sql(sql, row_cap) -> (columns, rows)` — **新**。在 read-only 快照連線上跑；
  guard：parse 後僅允許單一 `SELECT`/`WITH`（拒 ATTACH/COPY/INSTALL/PRAGMA/DDL/DML/多語句）；
  ~30s timeout（thread + `con.interrupt()`）；row cap（超過回錯）。

### 3.2 `/api/v1` catalog endpoints（新 Flask blueprint，`ui/search/api_catalog.py`）
| Endpoint | 說明 |
|---|---|
| `GET /api/v1/views` | 全 view + meta（source / row_count / 日期範圍 / 描述） |
| `GET /api/v1/views/{view}/schema` | 欄位型別 + row range |
| `GET /api/v1/data/{view}` | 篩選讀取（見參數） |
| `POST /api/v1/sql` | 唯讀 SELECT（跨 view join/聚合） |

`/data/{view}` query params：`col=val`(eq)、`col__gte/__lte/__in`、`start/end`（view 的日期欄）、
`select=`(投影)、`order=`+`dir=`、`limit=`（default 10000，parquet cap ~5,000,000）、
`cursor=`（keyset → response `next_cursor`）、`format=json|csv|parquet`。
- json → `{columns, rows, next_cursor}`；csv/parquet → 串流檔。

`POST /api/v1/sql` body `{"sql":"SELECT …","format":"json|parquet"}` → 經 `safe_sql`。

### 3.3 `quantdata` client package（新 top-level `quantdata/`，pip-installable）
```python
from quantdata import QuantData
qd = QuantData()  # auto-detect transport
qd.views(); qd.schema("bars_1d")
qd.get("bars_1d", symbol="2330", start="2020-01-01", end="2024-12-31")  # → DataFrame
qd.sql("SELECT ...")                                                     # → DataFrame
qd.live.snapshot(["2330","TAIEX"])                                       # wraps realtime /api/v1
```
- **Transport 解析：** 明確 `url=` → remote；否則 `QUANTDATA_CATALOG`/預設檔存在 → local；
  否則 env `QUANTDATA_API_URL` → remote。token 來自 `QUANTDATA_API_TOKEN`。
- **回傳一律 pandas DataFrame。** local = DuckDB read-only → Arrow → pandas；
  remote = 請求 `format=parquet` → `pd.read_parquet(BytesIO)`。`duckdb` 為 local-only 可選 import。
- 方法：`views() / schema(v) / get(view, *, select, order, dir, limit, start, end, **filters) /
  sql(q) / live.{snapshot,ticks,bars,health}`。

### 3.4 Token-auth middleware
- bearer token 來自 env `QUANTDATA_API_TOKEN`。
- **只 gate catalog endpoints**（`/views`,`/data`,`/sql`）：env token 有設時，缺/錯 token → 401。
- **realtime endpoints（/health,/snapshot,/ticks,/bars）維持現狀**（不加認證）→ 不破壞現有風控消費端。
- env token 未設 → catalog 也不認證（dev/LAN）。預設 bind `0.0.0.0:5050`；對外走 Tailscale funnel。

### 3.5 OpenAPI / Swagger
擴充既有 `openapi_spec.build_spec()` 收錄新 endpoints → `/api/v1/docs` Swagger 可見可測。

---

## 4. Data flow
- `qd.get("bars_1d", symbol="2330", start=…)`
  - **local**：DuckDB read-only(snapshot) → `query_builder` SQL → Arrow → DataFrame（無 HTTP）。
  - **remote**：`GET /api/v1/data/bars_1d?symbol=2330&start=…&format=parquet`（Bearer token）→
    server `query_builder` → 串流 parquet → `pd.read_parquet` → DataFrame。
- 消費端**同一行程式**，本機快、遠端通。

---

## 5. 錯誤處理
- 一個 `QuantDataError` base。
- local：catalog 缺檔 → 清楚錯誤；SQL guard 違規 → `ValueError`/`QuantDataError`。
- remote：401 → `AuthError`；其他 4xx → `APIError(server message)`；網路錯 → 重試/拋出。
- 兩種 transport 拋同類例外。
- REST：未預期例外 → JSON 500（不外洩 stack，沿用 api_v1 既有 errorhandler）。

---

## 6. 安全 / 限制
- 全程 **read-only DuckDB 快照連線**（寫入不可能）。
- `/sql`：SELECT/WITH-only、單語句、~30s timeout、row cap。
- `/data`：欄位經 `query_builder` whitelist（防 injection）；limit 上限。
- 大量歷史走 `format=parquet`（一次拉到 cap）；json 走 cursor 分頁。
- rate-limit / CORS allowlist：v1 不做（LAN + token 已足）；列為後續。

---

## 7. 測試
1. **qd_access**：`safe_sql` guard（拒 ATTACH/COPY/INSTALL/PRAGMA/DDL/DML/多語句；收 SELECT/WITH）、
   filter builder、row cap、cursor。
2. **REST**（Flask test client + monkeypatch qd_access 用小 in-memory duckdb）：token 401（設了 token 缺帶）、
   `/views`、`/data` 篩選、`/sql` parquet、format negotiation；realtime 仍免認證。
3. **client**：local mode（指向小測試 `.duckdb`）回 DataFrame；remote mode（mock `requests`）回相同 shape；
   transport auto-detect 分支。

---

## 8. 打包 / 部署
- `quantdata/` package + pyproject。
  - 同機：`pip install -e ~/gs-scraper/QUANTDATA` → `from quantdata import QuantData`（local，零複製）。
  - 遠端：`pip install "git+https://github.com/<you>/QUANTDATA.git"`，設 `QUANTDATA_API_URL` + `QUANTDATA_API_TOKEN`（remote）。
- 不新增 server：catalog blueprint 註冊進既有 Flask app（`run_search_ui.sh`）；啟動前 `export QUANTDATA_API_TOKEN=…`。
- 文件：擴充 `docs/realtime-api-v1.md` 或新增 `docs/quantdata-client.md`（client 用法 + REST 契約 + 範例）。

---

## 9. 不做（YAGNI）
- GraphQL、gRPC、WebSocket 串流（除既有 SSE 即時）。
- 寫入端點（API 純讀；ingest 仍走既有 scripts）。
- 多租戶、per-view ACL、計費。
- ORM / 型別 codegen（client 回 DataFrame 即可）。

---

## 10. 影響檔案
- 新：`ui/search/qd_access.py`、`ui/search/api_catalog.py`、`quantdata/__init__.py`(+client.py)、
  `tests/test_qd_access.py`、`tests/test_api_catalog.py`、`tests/test_quantdata_client.py`、
  `docs/quantdata-client.md`、根 `pyproject` 補 `quantdata` package。
- 改：`ui/search/app.py`（註冊 catalog blueprint + token middleware）、
  `ui/search/openapi_spec.py`（收錄新 endpoints）、`ui/search/query_builder.py`（補 cursor / __gte 等 op，若缺）。
- 不動：realtime endpoints 行為、ingest scripts、medallion 結構。
