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

### M1 — 專案骨架 + git init + 進度檔

- Commit: `6416db8`
- 做了：
  - `git init -b main`，commit user 設為本地 `kevin / gsinvest017`
  - `.gitignore` 排除全部既有 825 GB 原始資料、archive、影像、`bronze/silver/gold/...` 下的資料檔（保留 `.gitkeep` 與 `reference/seeds/`）
  - 建好 17 個目錄 skeleton（`bronze/{taifex,tej,twse,yahoo,histdata}`、`silver/{bars,options,flows,fundamentals,macro}`、`gold/{features,continuous,universe}`、`meta/{audit,schema,lineage}`、`reference/seeds`、`src/qd_ingest/{common/validators,sources}`、`tests`、`docs`、`scripts`、`_staging`、`_quarantine`）
  - `pyproject.toml`：`qd_ingest` package、`qd-ingest` CLI entry、依賴含 duckdb/polars/pyarrow/pandas/pandera/click/rich，optional groups `ingest/zipline/dev`
  - `src/qd_ingest/`：`cli.py`（click group + 3 subcommand stubs）、`common/paths.py`（單一 source of truth 的目錄常數 + helper）、`common/audit.py`（`IngestRecord` dataclass + `write_audit()` JSONL appender + `sha256_file()`）
  - `README.md` 簡介、`docs/progress-data-arch-impl.md` 此檔
- 後續可接：M2 寫 reference 表 + pandera schema + D1/D2 dedup

