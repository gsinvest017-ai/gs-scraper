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

### M2 — Reference 表 + pandera schema + dedup

- Commit: M2 (`reference tables + pandera schemas + W1 dedup`)
- 做了：
  - **Reference seeds**：`reference/seeds/contract_specs.yaml`（12 個合約：TXF/MXF/TXO + ES/NQ/YM/RTY + GC/CL/NG/HG/SI）、`reference/seeds/symbol_map.yaml`（30 個 canonical symbol，含台期 / 台股 ETF / US futures / US index / US ETF / FX / Asia index）
  - `scripts/build_reference.py` 編譯成 parquet：`contract_specs.parquet`、`symbol_map.parquet`、`calendar_xtai.parquet`（由 TEJ EWPRCD 衍生 3924 個交易日 2010-01-04 ~ 2025-12-31）
  - **9 個 pandera schemas** 在 `src/qd_ingest/common/validators/`：bars_1d / bars_intraday / options_chain_1d / tw_inst_futures_daily / tw_inst_stock_daily / tw_margin_daily / tw_inst_market_daily / fundamentals_q / macro_daily（全部 `strict="filter" + coerce=True + unique=PK`）
  - **D1/D2 dedup**：`scripts/dedup_w1.py` SHA256-verify 比對通過後 `mv` 到 `_quarantine/`（不 rm，可逆）
    - `MXF_1m_clean_all/`（16 個檔 SHA256 全 match）
    - `GC_1min_2010-2024/`（15 個檔 SHA256 全 match）
    - 共 126 MB 騰出
    - manifest 寫在 `_quarantine/manifest_2026-05-13.jsonl`
  - **Asset inventory baseline**：`scripts/build_inventory.py` 寫出 `meta/audit/asset_inventory.csv`（2526 files、2.9 GB），按 size desc 排序
  - 修正 `DATA_ARCHITECTURE.md` 中誤把 ls 1K-blocks 解讀為 GB 的尺寸數字（825 GB → 2.9 GB）
- Fallback：若要 rollback，`git revert M2` 後手動把 `_quarantine/{MXF_1m_clean_all,GC_1min_2010-2024}` mv 回 root；reference/seeds/*.yaml 即使刪掉 parquet 也可用 `python scripts/build_reference.py` 一鍵重建

