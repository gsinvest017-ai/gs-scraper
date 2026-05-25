# 2026-05-25 (final-final) — Goldify 全部 100% silver

## 觸發

`/safe-yolo 繼續處理 complete rate 100% 的 data catlog 將不是 gold quality 的全部變成 gold`

## 起始狀態

從上一輪 `progress-goldify-remaining-silver.md` 完成後，dashboard summary：
**OK=17 / INFO=3 / WARN=1 / STALE=8 / EMPTY=1 = 30 datasets**

複盤 100% 完整度但 **gold 欄空白** 的 silver-only views（4 個，前一輪被標 backlog）：

| view | silver size | rows | 上一輪結論 |
|---|---|---|---|
| `tw_inst_futures_full_daily` | 15.4 MB | 496K | "per-identity 複雜，留 backlog" |
| `tw_stock_trading_attrs_daily` | 2.1 MB | 3.9M | "flag 性質，無 derive 價值" |
| `cash_dividend_events` | 0.4 MB | 12.5K | "lookup / event 性，無 derive" |
| `tw_stock_futures_corp_actions` | 0.5 MB | 61.0K | "reference 性質" |

本輪重評：**這些其實都有可挖的 cross-sectional 因子**，前一輪的「無價值」判斷太武斷。dividend events 對台股是 alpha source（除權息行情），attrs 內含 index membership + 注意股 cohort 訊號，inst_futures_full 補滿 TXO/TFO 法人籌碼（與 large_trader 不同切面），corp_actions 是股票期套利必備調整表。

100% 完成度但故意保留無 gold 的（**仍跳過**）：
- `qc_stock_price_diff` — 純對帳 view，computed only，不需要 parquet
- `finmind_stock_price_norm` / `finmind_stock_price_adj_norm` — bronze snapshot 性質（INFO tier），下游已有 `qc_stock_price_diff`

## 範圍（pragmatic）

新建 **4 個 gold table** + **4 個 backlink**：

1. **`futures_inst_factors`** ← `tw_inst_futures_full_daily`：每 (trading_date, identity_code) 的法人籌碼因子
2. **`stock_attrs_status`** ← `tw_stock_trading_attrs_daily`：每 (trading_date, stock_id) 的 binary panel + 30d rolling
3. **`dividend_calendar`** ← `cash_dividend_events`：每 (ex_date, stock_id) per-share + yield + TTM
4. **`stock_futures_adjustments`** ← `tw_stock_futures_corp_actions`：每 (futures_code) 累計 adj + latest snapshot

## 因子設計

### `futures_inst_factors` (per trading_date, identity_code)

162 個 identity_code（11/12/13 主要法人 × TX/MTX/TE/TFO/TXO 等產品），sort by `(identity_code, trading_date)`。

| 欄位 | 公式 |
|---|---|
| `net_oi`, `long_oi`, `short_oi`, `long_volume_pct`, `short_volume_pct` | 從 silver 直拉 |
| `net_oi_chg_5d / _20d` | 5/20 天前後 `net_oi` 差 |
| `net_volume_zscore_60d` | `net_volume` 60d rolling z-score |
| `long_short_oi_ratio` | `long_oi / NULLIF(short_oi, 0)` |
| `volume_to_oi_ratio` | `(long_volume + short_volume) / NULLIF(long_oi + short_oi, 0)` |

### `stock_attrs_status` (per trading_date, stock_id)

從 silver flag varchar 轉 bool（`'Y' → TRUE`、空字串 → `FALSE`），保留靜態 metadata。Index membership flags 是 cross-sectional 過濾條件，attention/disposition 是 event 訊號。

| 欄位 | 公式 |
|---|---|
| `is_attention_bool / is_disposition_bool / is_suspended_bool / is_full_settle_bool` | `value = 'Y'` |
| `is_no_daytrade_bool` | `no_daytrade_buy_first = 'Y' OR no_daytrade_sell_first = 'Y'` |
| `is_twn50_bool / is_msci_bool / is_otc50_bool / is_otc200_bool / is_hdiv_bool / is_mcap_bool` | `value = 'Y'` |
| `attention_count_30d / disposition_count_30d` | 過去 30 天 rolling sum of bool |
| `main_industry_zh / sub_industry_zh / board_zh / market` | 直拉（panel 中可能變動，保留 daily） |

### `dividend_calendar` (per ex_date, stock_id)

Forward-looking event panel。`ex_date` 是 partition key（未來 ex_date 是 alpha 訊號）。

| 欄位 | 公式 |
|---|---|
| `ex_date`, `stock_id`, `period_end`, `dividend_type`, `pay_date`, `announce_date` | 直拉 |
| `cash_div_per_share` | `cash_div_earnings + cash_div_reserve + COALESCE(special_dividend, 0)` |
| `div_yield_pct` | `cash_div_per_share / NULLIF(prev_close, 0) * 100` |
| `ref_price`, `prev_close` | 直拉 |
| `ttm_cash_div_per_share` | 過去 365 天同 stock_id 之 `cash_div_per_share` rolling sum |
| `days_announce_to_ex` | `ex_date - announce_date` |
| `yoy_growth_pct` | 與同 stock_id 上次（≈1 年前）`cash_div_per_share` 比 |

### `stock_futures_adjustments` (per adjust_date, futures_code)

期股套利必備：每個 futures_code 的累積除權息 / 股票股利調整。

| 欄位 | 公式 |
|---|---|
| `futures_code`, `adjust_date`, `adjust_reason`, `ref_price`, `contract_type` | 直拉 |
| `cash_div_per_share`, `stock_div_per_share`, `cash_div_per_lot`, `equity_value_per_lot` | 直拉 |
| `cum_cash_div_per_share` | 同 `futures_code` 之 `cash_div_per_share` 累積 sum（按 `adjust_date` ASC） |
| `cum_stock_div_per_share` | 同 |
| `cum_equity_value_per_lot` | 同 |
| `prev_adjust_date` | 同 `futures_code` 上一次 `adjust_date`（shift +1） |
| `days_since_prev_adj` | `adjust_date - prev_adjust_date` |
| `adj_seq_no` | row_number over (futures_code) order by adjust_date |

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔（含因子設計） | ✅ |
| **M2** | 4 個新 gold builder 寫進 `derived.py` + 跑 build_all | ✅ |
| **M3** | DATASETS registry：4 個 backlink + 4 個新 view 條目；`catalog.py` 註冊 | ✅ |
| **M4** | `qd-ingest build-catalog` + `restore_finmind_views` + dashboard regen + docs sync + push | ✅ |

## Fallback

- 寫壞了：`git revert <commit>` → gold parquet 還在但 catalog view 不見了，下次 rebuild 重生
- factor 公式跑錯：直接修 `derived.py` 對應函式，重跑單一 builder
- 上次 catalog backup：`catalog/quant.duckdb.bak_pre_finalgold_20260525_154402`（在上一輪 M4 建立）

## 完成日誌

### M2 — 4 個新 gold builder

加進 `src/qd_ingest/sources/derived.py`，全在 1.5 秒內跑完：

| Gold parquet | 列數 | 主鍵唯一性 | size | 來源 silver dedup |
|---|---|---|---|---|
| `gold/features/futures_inst_factors.parquet`     | 466,047    | 162 identity_codes × 4781 days | 10.7 MB | 496K → 466K (−30K 重覆) |
| `gold/features/stock_attrs_status.parquet`       | 3,160,185  | 2864 stocks × ~1100 days       | 1.0 MB  | 3.88M → 3.16M (−720K 重覆，~19%！) |
| `gold/features/dividend_calendar.parquet`        | 10,458     | 2269 stocks × multiple ex_dates | 346 KB | 12.5K → 10.5K (−2K 重覆) |
| `gold/features/stock_futures_adjustments.parquet`| 56,049     | 40,487 futures_codes           | 405 KB  | 61K → 56K (−5K 重覆) |

**重要副產品**：silver layer 有大量 multi-ingest duplicates（trading_attrs 19%！），這次 goldify 順手把 dedup 邏輯內建到 builder 裡。每個 builder 都按 `ingestion_ts` keep last。

2330 抽查 dividend_calendar：2026-09-16 Q1 7.0/股、TTM=24.0、YoY=+16.67%；2026-03-17 Q3 6.0/股、div_yield=0.33%。數值合理。

### M3 — backlinks + registry

`scripts/gap_report.py`：
- `tw_inst_futures_full_daily.gold_paths` += `gold/features/futures_inst_factors.parquet`
- `tw_stock_trading_attrs_daily.gold_paths` += `gold/features/stock_attrs_status.parquet`
- `cash_dividend_events.gold_paths` += `gold/features/dividend_calendar.parquet`
- `tw_stock_futures_corp_actions.gold_paths` += `gold/features/stock_futures_adjustments.parquet`
- 加 4 個新 Dataset 條目：`futures_inst_factors`, `stock_attrs_status`, `dividend_calendar`, `stock_futures_adjustments`

`src/qd_ingest/common/catalog.py`：4 個新 gold view 加進註冊清單，`build()` 後 catalog 列出 **49 views**（含 finmind 還原）。

### M4 — catalog + dashboard

```
.venv/bin/python -m qd_ingest.common.catalog
.venv/bin/python scripts/restore_finmind_views.py       # 補 9 個 finmind/qc view
.venv/bin/python scripts/gap_report.py --format html ...
.venv/bin/mkdocs build --strict                          # PASS
```

Dashboard summary 變化：

| 指標 | M2 前 | M2 後 |
|---|---|---|
| 總 datasets | 30 | **34** (+4 新 gold) |
| OK | 17 | **21** (+4) |
| INFO | 3 | 3 |
| WARN | 1 | 1 |
| STALE | 8 | 8 |
| EMPTY | 1 | 1 |
| 🥇 Gold 總 size | 427.4 MB | ~440 MB |

**全 100% 完整度的 silver-only views 都消失了**——本輪是收尾。

剩下 EMPTY/STALE 的都是 **完整度本來就 < 100%** 的（macro_daily 83% / chip_dist 92% / inst_futures_daily 84% / revenue_monthly 40% 等），歸 STALE category 而非 silver→gold 缺口問題。

## Live

<https://gsinvest017-ai.github.io/gs-scraper/gap_dashboard.html>
