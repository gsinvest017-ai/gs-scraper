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
| **M2** | 4 個新 gold builder 寫進 `derived.py` + 跑 build_all | ⏳ |
| **M3** | DATASETS registry：4 個 backlink + 4 個新 view 條目；`catalog.py` 註冊 | ⏳ |
| **M4** | `qd-ingest build-catalog` + `restore_finmind_views` + dashboard regen + docs sync + push | ⏳ |

## Fallback

- 寫壞了：`git revert <commit>` → gold parquet 還在但 catalog view 不見了，下次 rebuild 重生
- factor 公式跑錯：直接修 `derived.py` 對應函式，重跑單一 builder
- 上次 catalog backup：`catalog/quant.duckdb.bak_pre_finalgold_20260525_154402`（在上一輪 M4 建立）

## 完成日誌

（待 M2-M4 完成後追加）
