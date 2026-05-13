# 進度檔：QUANTDATA 資料庫架構分階段實作

> 由 `/safe-yolo` skill 觸發，依據 `DATA_ARCHITECTURE.md` 的 4 週實作計畫推進。
> 起始日：2026-05-13

## 目標

把 `DATA_ARCHITECTURE.md` 設計的 medallion lakehouse（bronze / silver / gold + reference + catalog）從零打造起來，並讓至少一條端到端 pipeline（外部 source → bronze → silver → DuckDB 查得到）跑通，作為後續所有 source ingester 的模板。

## 計畫 Milestone

| M | 名稱 | 預期產出 | Commit prefix |
|---|------|---------|---------------|
| M1 | 專案骨架 + git init + 進度檔 | `.gitignore`、`pyproject.toml`、目錄 skeleton（bronze/silver/gold/...）、`docs/progress-*.md`、`.git/` | `M1:` |
| M2 | Reference 表 + pandera schema + dedup | `reference/{symbol_map,contract_specs,calendar_xtai}.parquet`、`src/qd_ingest/common/validators/*.py`、D1/D2 重複檔搬到 `_quarantine/` | `M2:` |
| M3 | 第一個完整 ingester：TEJ stock daily | `src/qd_ingest/sources/tej.py` + CLI、`silver/bars/bars_1d/asset_class=tw_stock/year=*/...parquet`、`tests/test_tej.py` | `M3:` |
| M4 | 其餘台灣 ingester | TAIFEX 三大法人、TWSE bfi82u、TEJ 個股法人 / 財報 / 融資券 全部寫進 silver | `M4:` |
| M5 | Macro silver + DuckDB catalog + smoke | SUPPLEMENT/* 整理進 silver/macro、`catalog/quant.duckdb` 含 views、`scripts/smoke_query.py` 跑出 2330 join | `M5:` |

## 進度日誌

<!-- 每個 milestone 完成後追加一段 -->
