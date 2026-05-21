# QUANTDATA

量化資料 medallion lakehouse（bronze → silver → gold）。

📖 **文檔網站**：<https://gsinvest017-ai.github.io/gs-scraper/>（MkDocs Material，每次 push 自動重新發佈）

其他入口：

- 完整設計、schema、Mermaid 圖：[`DATA_ARCHITECTURE.md`](./DATA_ARCHITECTURE.md)
- 分階段實作進度：[`docs/progress-data-arch-impl.md`](./docs/progress-data-arch-impl.md)
- 文檔站源碼：[`docs-site/`](./docs-site/)

## 目錄

```
bronze/      不可變原始檔 (taifex/tej/twse/yahoo/histdata)
silver/      標準化 canonical schema (bars/options/flows/fundamentals/macro)
gold/        research-ready features (features/continuous/universe)
reference/   symbol_map / contract_specs / calendar
catalog/     quant.duckdb (views + macros over silver/gold)
meta/        audit / schema / lineage
src/qd_ingest/   Python ingest pipeline (CLI: qd-ingest)
docs/        進度與設計補充文件
tests/       pytest
scripts/     一次性腳本 (dedup / smoke / migrations)
```

## 快速開始（W1 完成後）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[ingest,dev]"
qd-ingest --help
```

## Stack

- DuckDB + Parquet (zstd) 為主
- Polars / pandas 做 transform
- pandera 做 schema 驗證
- 詳見 `DATA_ARCHITECTURE.md` § 2
