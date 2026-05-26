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
| **M1** | 本進度檔 + 因子設計 | ⏳ |
| **M2** | 3 個 builder 進 derived.py + 跑 | ⏳ |
| **M3** | registry + catalog 註冊 | ⏳ |
| **M4** | rebuild catalog + dashboard + audit re-check | ⏳ |

## Fallback

- 跑壞：`git revert <commit>`；gold parquet 還在但 catalog 不見了，下次 rebuild 重生
- catalog backup：M4 開頭會 cp 一份 `quant.duckdb.bak_pre_goldify100_iter1_*`

## 完成日誌

（M2-M4 完成後追加）
