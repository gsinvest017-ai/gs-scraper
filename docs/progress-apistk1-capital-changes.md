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

（每完成一個 milestone 追加）
