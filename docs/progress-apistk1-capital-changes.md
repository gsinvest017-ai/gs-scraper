# progress — APISTK1（資本形成 / 股本變動事件）接增量爬蟲、落 silver

## 目標

把 TEJ `TWN/APISTK1`（資本形成）接進現有 `scripts/fetch_tej.py` 增量爬蟲框架，
落地成 silver parquet（`silver/fundamentals/capital_changes/year=YYYY/`），在
DuckDB catalog 開 `capital_changes` view，並讓 gap_dashboard 認得這張新表。

`APISTK1` 是 **event-based** 表：每筆 = 一家公司一次股本變動事件，primary key
`(coid, mdate=除權日)`，內容涵蓋現金增資 / 盈餘配股 / 公積增資 / 員工分紅 / 減資 /
CB 轉換 / 特別股轉換 / 庫藏股註銷 / 合併 / 受讓 / 員工認股權證 / IPO / 私募 等
75 個欄位（含多組除權相關日期、認購率、除權參考價）。

## 計畫 milestone

- **M1 — 程式接線**：fetch_tej.py 加 `adapt_apistk1_to_silver` + `write_silver_capital_changes`，
  註冊 logical table `capital_changes`、`_silver_max_date` view_map、`fetch()` branch；
  catalog.py 加 view；dataset_meta.py + gap_report.py 註冊。產出：可跑的程式（尚無資料）。
- **M2 — 回補 + catalog + 驗證**：`fetch_tej.py --table capital_changes --start 20200101`
  回補 2020→今天，落 silver；`build-catalog`；DuckDB 驗證行數 / 欄位 / 年份分布。
- **M3 — 重生 dashboard**：`gap_report.py --format all` 重生 gap_dashboard（local +
  docs-site mirror），確認 `capital_changes` 出現、silver 標 OK。

## 設計決策

- **欄位策略**：比照 `accounting_raw`（AINVFINB 寬表）的做法——只把 key 正規化成英文
  （`stock_id` ← 公司、`ex_right_date` ← 除權日），其餘 73 個中文欄名原樣保留，
  pyarrow 自動推 schema。理由：75 欄全 remap 風險高且與既有 accounting_raw 慣例一致，
  策略端照中文欄名 select 即可。
- **分區**：`year=` 取 `ex_right_date`（除權日）的年份。
- **增量**：`_silver_max_date["capital_changes"] = ("capital_changes", "ex_right_date")`，
  `--append-since-silver` 會從 silver 最大除權日 +1 起抓。
- **catalog view**：`read_parquet(..., hive_partitioning=TRUE, union_by_name=TRUE)`，
  union_by_name 防未來欄位漂移。
- **回補起點 20200101**：sibling API 表（accounting_raw 2022~、cash_dividend）深度約近
  五年，與訂閱包 API table 可得範圍一致；event 表量小（~3K/年），yearly chunk 安全。
- **無 gold**：本次只要求落 silver；比照 `rf_daily` 留空 gold_paths。

## 進度日誌

### M1 — 程式接線（commit 2989f5b）

fetch_tej.py 加 `adapt_apistk1_to_silver` + `write_silver_capital_changes`、
logical table `capital_changes`、`_silver_max_date` view_map、`fetch()` yearly-chunk
branch；catalog.py `capital_changes` view（union_by_name）；dataset_meta.py +
gap_report.py（event / P2）註冊。adapter 過 2-row 合成測試、4 檔 py_compile OK。

探查 `table_info` 得知 APISTK1 共 75 欄、PK `(coid, mdate)`、頻率日、來源證交所/櫃買。

### M2 — 回補 + catalog + 驗證

`fetch_tej.py --table capital_changes --start 20200101 --mode overwrite`：
yearly chunk 抓 2020-01-01→2026-06-03 共 **19,446 rows**（7 chunks，各 ~2.5–3.5K），
落 `silver/fundamentals/capital_changes/year={2020..2026}/`。`build-catalog` 後 view
數 64→65。DuckDB 驗證：

- 19,446 rows / 2,118 distinct stock_id / 除權日 2020-01-01 → 2026-06-03
- 78 欄（75 原始 − 2 drop key + stock_id/ex_right_date/source/ingestion_ts/year）
- 事件欄（`庫藏股註銷(仟股)` / `現金增資(仟股)` …）可用中文欄名直接 query
- 年份分布：2020=2677, 2021=2988, 2022=3000, 2023=3294, 2024=3468, 2025=3021, 2026=998

`pytest -q tests/` → 148 passed。

> 增量：之後 `--table capital_changes --append-since-silver` 會從 silver 最大除權日
> +1 起抓（view_map 已接）。注意 event 表用 plain read_parquet 無 query-time dedup，
> 增量只抓非重疊 mdate 視窗即可（比照 cash_dividend）。

### M3 — 重生 gap_dashboard

`gap_report.py --format all` 重生 dashboard，capital_changes 列出現：
`P2 · latest 2026-06-03 · 0d lag · ✅ OK`。docs/gap_dashboard.html 與
docs-site/gap_dashboard.html（MkDocs mirror）皆已更新並各含 1 處 capital_changes。

## Fallback / 接手指引

- 程式接線全在 commit 2989f5b（M1）：scripts/fetch_tej.py、
  src/qd_ingest/common/{catalog,dataset_meta}.py、scripts/gap_report.py。
- 重抓 / 修資料：`./.venv/bin/python scripts/fetch_tej.py --table capital_changes
  --start 20200101 --mode overwrite`（overwrite 會 rmtree 對應 year 分區再寫）。
- 增量：`--table capital_changes --append-since-silver`。
- 重建 view：`./.venv/bin/python -m qd_ingest.cli build-catalog`。
- Rollback：silver/catalog 為 gitignore，刪 `silver/fundamentals/capital_changes/`
  後重 build-catalog 即移除 view；程式碼 `git revert 2989f5b`。
