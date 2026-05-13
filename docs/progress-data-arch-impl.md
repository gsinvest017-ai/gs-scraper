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
| M6 | histdata US futures 1m | NQ/ES/GC × 15 年 yearly parquet → `silver/bars/bars_1m/asset_class=us_futures/symbol=*/year=*` | `M6:` |
| M7 | TW 期貨 + 股票期貨 silver | MXF 1m/1d、TX/MXF 連續月 → silver + gold/continuous；股票期貨 daily/intraday → silver/bars | `M7:` |
| M8 | Gold features + derived | TXO daily features、cross-market features → gold；momentum/value factor stub | `M8:` |
| M9 | Zipline adapter + backup + 最終 catalog | silver→zipline tquant bundle adapter、`scripts/backup_snapshot.sh`、catalog refresh、最終 smoke | `M9:` |

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

### M3 — TEJ stock daily ingester（端到端）

- Commit: M3 (`TEJ stock_daily end-to-end ingester`)
- 做了：
  - `src/qd_ingest/sources/tej.py::ingest_stock_daily()`：完整 transform→validate→upsert
    - 從 `TEJ資料/TWN_EWPRCD_股價.csv` chunked 讀
    - `證券碼` 拆 `1101 台泥` → `stock_id="1101"`
    - 千股 → shares（`volume = kshare × 1000`，nullable Int64）
    - 日期 anchor 在 TWSE 收盤 13:30 Asia/Taipei → 轉 UTC `ts_utc`
    - `adj_factor = adj_close / close` 算出
    - PyArrow schema enforced on write、pandera validate 每 chunk 前 100 row
    - **重要 bug 發現並修**：chunked `read_csv` 給每 chunk non-zero RangeIndex（chunk 2 = 200000..399999），導致 DataFrame ctor 對 helper Series 做 outer-join 把列數翻倍 → 在 transform 一開始 `reset_index(drop=True)` 解掉
  - `src/qd_ingest/common/io.py::write_silver_partitioned()`：用 pyarrow `write_to_dataset` + `delete_matching` 行 idempotent upsert
  - **Ingest 結果**：`6,356,541` 列 2010-01-04~2025-12-31 → `silver/bars/bars_1d/asset_class=tw_stock/year=*/`（共 25 MB、16 個年分區、15.7 秒）
  - **Smoke**：DuckDB 直接 `read_parquet(...)` 撈 2330 2024 前 5 日，OHLC 正確
  - `tests/test_tej_stock.py`：5/5 通過
- 後續可接：M4 寫 TAIFEX 三大法人、TWSE bfi82u、TEJ 個股法人/財報/融資券

### M4 — TAIFEX + TWSE + TEJ inst/fund/margin ingesters

- Commit: M4 (`TAIFEX + TWSE + TEJ inst/fund/margin ingesters`)
- 做了：
  - **TEJ extension** (`sources/tej.py`)
    - `ingest_inst_stock_daily`：TWN_EWTINST1 → silver/flows/tw_inst_stock_daily（6,352,126 列，60 MB，~13s）
    - `ingest_margin_daily`：TWN_EWGIN → silver/flows/tw_margin_daily（3,498,545 列，87 MB，~5s）
    - `ingest_fundamentals_q`：TWN_EWIFINQ 單季 + 累季 → silver/fundamentals/fin_q（period_type 'Q' + 'YTD'，101281 + 101287 列，21 MB）
    - 規範：千股 == 1 lot（無 scale 變動）、買賣超(千股) 直接視為 `*_net_lot`、`財務資料日 YYYYMM` 解析成 `'2024Q1'` fiscal_period、`publish_date` 保留為 point-in-time anchor
  - **TAIFEX**（新檔 `sources/taifex.py`）：melt `SUPPLEMENT/TAIFEX/foreign_oi_daily.parquet`（wide：dealer/inv/fii × long/short/net 列）成 canonical long format（per `product + identity + trading_date`）。`inv → sitc` 正規化；`net_oi_z60` 帶過。寫 silver/flows/tw_inst_futures_daily（產品 MXF/TXF/TXO，2023-05~2026-05）
  - **TWSE**（新檔 `sources/twse.py`）：bfi82u combined long CSV → tw_inst_market_daily。修掉 csv 同時帶 `identity`（中）+ `identity_en`（英）造成 rename 後重複欄位的 bug。目前只有 1 日（18 列）覆蓋——後續需擴更多日
  - **🐞 重要 bug 修復**：chunked write 用 `existing_data_behavior='delete_matching'` 會在後續 chunk 寫 year=2010 時把上一 chunk 已寫的 year=2010 砍掉（pyarrow 把 partition value 視為刪除條件，但只 delete 出現在新 table 裡的值——不夠細，仍會清掉同 chunk 的不同列）。改成：在 ingest 開頭把整個 dest 目錄一次性 rm，之後 chunks 都用 `overwrite_or_ignore` 純追加。
    - **驗證**：2330 2024 從 160 個交易日 → 242 個交易日（完整一年），全表 tw_stock 從原本部分 → 6,356,541 列完整
  - **三條 join smoke** 全通：
    1. `bars × inst × margin` on `(trading_date, stock_id)` ✓
    2. `bars ASOF fundamentals_q` on `trading_date >= publish_date` ✓（point-in-time safe）
    3. `tw_inst_futures_daily` MXF 三 identity 全展開 ✓
- 後續可接：M5 SUPPLEMENT/* 整理進 silver/macro + DuckDB catalog + 最終 smoke

### M5 — Macro silver + DuckDB catalog + smoke test

- Commit: M5 (`macro silver + DuckDB catalog + end-to-end smoke`)
- 做了：
  - **Macro ingester** (`sources/macro.py`)：掃 `SUPPLEMENT/{US_INDEX,US_FUTURES,US_SECTOR_ETF,COMMODITY,FX,TW_INDEX,ASIA,CREDIT}/*.parquet`、normalize 成 long、寫單檔 `silver/macro/macro_daily.parquet`（45 個 canonical symbols × 91,048 列、2.6 MB、0.3s）
    - filename → canonical：GSPC→SPX、ES_F→ES、TWII→TAIEX、DX-Y_NYB→DXY、`<X>_TW`→`<X>`、JPY_X→USDJPY、000001_SS→SSEC 等
    - USDTWD 特殊處理 prefixed cols
  - **DuckDB catalog** (`common/catalog.py`)：`catalog/quant.duckdb`（268 KB）含 10 views + 2 巨集，全部讀 parquet（不複製資料）
    - views: bars_1d / tw_inst_futures_daily / tw_inst_stock_daily / tw_margin_daily / tw_inst_market_daily / fundamentals_q / macro_daily / symbol_map / contract_specs / calendar_xtai
    - macros: `tw_stock_with_inst(stock_id, start, end)`、`tw_stock_asof_fundamentals(...)`
  - **`scripts/smoke_query.py`** 7 段 demo 全通：
    1. catalog views 列表
    2. symbol_map sample（多 asset_class）
    3. **2330 bars × inst × margin** 2024-01-02~10 全 7 日對齊
    4. **bars ASOF fundamentals_q**：2024-05-15 收盤後讀到 2024Q1 EPS（publish 同日）→ point-in-time zero leakage 證明
    5. TAIFEX MXF fii net_oi_z60 趨勢
    6. macro_daily 抽樣（VIX/SPX/TAIEX/USDTWD）
    7. 全 silver row counts：bars 6.36M + inst_stock 6.35M + margin 3.5M + fund 200K + macro 91K + taifex 6.5K = 16.5M total
  - **silver 總計**：bars 63 MB + flows 214 MB + fundamentals 21 MB + macro 2.6 MB ≈ 300 MB（原 TEJ 1.46 GB CSV 縮 5×）
- Fallback：catalog 重建 `python -m qd_ingest.common.catalog`；silver 重建 `python -m qd_ingest.sources.<module>`

