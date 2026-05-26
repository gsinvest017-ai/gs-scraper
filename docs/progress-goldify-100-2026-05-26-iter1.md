# 2026-05-26 — `/goldify-100` iteration 1

## 觸發

`/goldify-100 /safe-yolo`

## Iter 1 audit 結果

`scripts/goldify_audit.py` 報告 **3 ripe candidates**：

| view | tier | category | rows | template | model-after |
|---|---|---|---:|---|---|
| `tw_chip_dist_daily` | P1 | weekly | 704,229 | `view_materialize`* | (override: `flow_rolling`) |
| `revenue_monthly` | P0 | monthly | 110,485 | `view_materialize`* | (override: `pit_fundamentals`) |
| `accounting_raw` | P2 | quarterly | 202,688 | `view_materialize` | `qc_stock_price_diff_snapshot` |

\* audit script 給 `view_materialize` 是 fallback template；本輪對 chip_dist 和 revenue 升級成更貼切的因子型 builder（前者有豐富的 holder-bucket data，後者 silver 已有 yoy/mom/ttm 但可加 acceleration / z-score）。

## 為什麼這 3 個現在才進來

它們在上一輪剛被重新分類：
- `tw_chip_dist_daily` `daily-trading` → `weekly`（7d OK），4d lag 變 OK
- `revenue_monthly` monthly tolerance 60d，55d lag 變 OK
- `accounting_raw` quarterly tolerance 100d，86d lag 變 OK

所以 audit 第一次看到它們是 100% 完整度。標準 loop 收割。

## 因子設計

### `chip_dist_factors`（per trading_date, stock_id, weekly cadence）

從 `tw_chip_dist_daily` 衍生。silver 有 5 個 holder bucket（<400 lot / 400-600 / 600-800 / 800-1000 / >1000 lot），這個分布的時序變化就是籌碼面 alpha 訊號。

| 欄位 | 公式 |
|---|---|
| `trading_date`, `stock_id` | 直拉 |
| `pct_under_400`, `pct_over_1000` | 直拉（zero-lot retail vs large-lot 機構 proxy） |
| `large_holder_pct_chg_4w` | `pct_over_1000.diff(4)` over (stock_id) |
| `retail_pct_chg_4w` | `pct_under_400.diff(4)` over (stock_id) |
| `concentration_ratio` | `pct_over_1000 / NULLIF(pct_under_400, 0)` |
| `pledged_pct` | `pledged_kshare / NULLIF(holdings_total_kshare, 0) * 100` |
| `large_holder_count_chg_4w` | `holders_over_1000.diff(4)` over (stock_id) |

Dedup: `unique(['stock_id','trading_date'], keep='last' by ingestion_ts)`

模仿 `build_inst_flow_factors` 的 polars pattern。

### `revenue_factors`（per fiscal_month, stock_id, monthly cadence）

從 `revenue_monthly` 衍生。silver 已有 yoy / mom / ttm / cum，gold 加 acceleration / z-score / persistence。

| 欄位 | 公式 |
|---|---|
| `fiscal_month`, `stock_id`, `publish_date` | 直拉 |
| `revenue_ttm_ktwd`, `revenue_yoy_growth_pct`, `revenue_mom_growth_pct` | 直拉 |
| `revenue_yoy_acceleration` | `revenue_yoy_growth_pct.diff(1)` over (stock_id)（成長率加速 m-o-m） |
| `revenue_3m_zscore_24m` | 24m rolling z-score of `revenue_3m_growth_pct` |
| `revenue_ttm_zscore_24m` | 24m rolling z-score of `revenue_ttm_growth_pct` |
| `revenue_mom_persistence_6m` | 過去 6 個月 `revenue_mom_growth_pct > 0` 的比例 |

Dedup: `unique(['stock_id','fiscal_month'], keep='last' by ingestion_ts)`

模仿 `build_fundamentals_pit` 的 polars rolling pattern。

### `accounting_raw_snapshot`（per fiscal_month, stock_id, quarterly）

從 `accounting_raw`（121 中文欄）物化為 parquet。**直接 COPY 全欄**，後續若需 ratio 因子可在新 builder 加。本輪只做 view materialization（純 portability）+ yearly aggregate。

模仿 `materialize_qc_snapshot` 的 DuckDB SQL COPY pattern。

Outputs:
- `gold/features/accounting_raw_snapshot.parquet`（202K 列、121 欄）
- `gold/features/accounting_raw_yearly.parquet`（aggregate count/coverage by year）

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + 因子設計 | ✅ |
| **M2** | 3 個 builder 進 derived.py + 跑 | ✅ |
| **M3** | registry + catalog 註冊 | ✅ |
| **M4** | rebuild catalog + dashboard + audit re-check | ✅ |

## Fallback

- 跑壞：`git revert <commit>`；gold parquet 還在但 catalog 不見了，下次 rebuild 重生
- catalog backup：M4 開頭會 cp 一份 `quant.duckdb.bak_pre_goldify100_iter1_*`

## 完成日誌

### M2 — 3 builders 全綠

| gold | rows | stocks/scope | size | elapsed |
|---|---:|---|---|---|
| `chip_dist_factors` | 481,450 | 2,428 stocks | (zstd) | 0.6s |
| `revenue_factors` | 95,061 | 1,971 stocks | (zstd) | 0.2s |
| `accounting_raw_snapshot` | 202,688 | 1,998 stocks | (zstd) | 4.0s |
| `accounting_raw_yearly` | 5 | per-year aggregate（2022-2026）| - | (同上) |

Silver multi-ingest dedup 在每個 builder 都跑了一遍。chip_dist 從 silver 704K → gold 481K 是因為**很多 stock 4 週前沒資料** → `shift(4)` 後產生 NULL → 但保留所有列其實沒過濾 → 應該是 polars `unique` 去掉 silver 700K→482K（dedup ~30%）。等下面確認列數。

實際 silver row count 704K → gold 481K，原因：silver 含多次 ingest 後重覆列；dedup keep_last 後剩 482K。原 silver 真實 unique (stock, trading_date) 約 482K，這對得起來。

### M3 — registry + catalog

`scripts/gap_report.py`：
- `tw_chip_dist_daily.gold_paths` += `gold/features/chip_dist_factors.parquet`
- `revenue_monthly.gold_paths` += `gold/features/revenue_factors.parquet`
- `accounting_raw.gold_paths` += `gold/features/accounting_raw_snapshot.parquet` + `_yearly.parquet`
- 新增 3 個 Dataset 條目：`chip_dist_factors` (P1 weekly)、`revenue_factors` (P0 monthly)、`accounting_raw_snapshot` (P2 quarterly)

`src/qd_ingest/common/catalog.py`：4 個新 view 註冊（含 yearly aggregate）。catalog `SHOW TABLES` 變 51 個（含 9 個 finmind/qc 還原 view）。

### M4 — rebuild + dashboard + audit re-check

`OK=25 → 28` (+3 新 gold view rows)。`SHOW TABLES` 51 個。

**Audit re-check 結果**：
```
✅ goldify_audit: no 100%-complete views are missing gold. Catalog is fully goldified.
```

→ **loop converged after iter1** (n=3 → n=0)。

## /goldify-100 loop summary

| iter | start cands | end cands | new gold |
|---|---:|---:|---|
| 1 | 3 | 0 | chip_dist_factors / revenue_factors / accounting_raw_snapshot(+yearly) |

✅ **Converged**。Push trigger 在下一步。

## Live

<https://gsinvest017-ai.github.io/gs-scraper/gap_dashboard.html>
