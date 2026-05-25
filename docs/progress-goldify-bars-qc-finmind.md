# 2026-05-25 (cleanup-remain) — Goldify 最後 4 個 100% 完整度 view

## 觸發

`/safe-yolo C:\Users\User\Pictures\clean-remain.png 把 complete rate 100% 的 data catalog 剩下沒有變成 gold 的 catalog 也整理成 gold`

## 起始狀態

從上一輪 (`progress-goldify-final-silver.md`) 完成後 dashboard：**OK=21, INFO=3, WARN=1, STALE=8, EMPTY=1 = 34 datasets**

複盤 registry，**100% 完整度但 gold_paths 仍為空** 的 view 還有 4 個：

| view | tier | category | severity | 為何沒 gold |
|---|---|---|---|---|
| `bars_1d` | P0 | daily-trading | OK | 跨 3 個 asset_class（tw_stock / tw_futures / tw_stock_futures）union view；tw_stock 子集對應 `stock_factor_daily`、futures 子集對應 `*_continuous_d`，但 view 層級 registry 沒掛 gold backlink |
| `qc_stock_price_diff` | P2 | derived | OK | 純對帳 view，從未持久化到 parquet |
| `finmind_stock_price_norm` | P1 | snapshot | INFO | bronze sqlite-only，未有 gold canonical |
| `finmind_stock_price_adj_norm` | P2 | snapshot | INFO | 同上，但是還原權息序列 |

## 範圍

**3 個新 gold artifact + 4 個 backlink**：

1. **`futures_bar_factors`** ← `bars_1d` 過濾 asset_class IN ('tw_futures', 'tw_stock_futures')：日 K 衍生 momentum + vol + ATR + turnover factor panel（個股期 + 期指）
2. **`qc_stock_price_diff_snapshot`** ← `qc_stock_price_diff`：把對帳 view 物化為 parquet，外加 yearly aggregated stats（mean abs diff、max diff、count diff）
3. **`finmind_price_canonical`** ← `finmind_stock_price_norm` + `finmind_stock_price_adj_norm`：把 bronze sqlite 中的 raw + adj 系列合併成單一 parquet（含 close、adj_close、volume），未來無 sqlite 也可直接用

外加 **`bars_1d` 的 gold backlink**（直接指向已存在的 4 個 derived gold：`stock_factor_daily`、`tx_continuous_d`、`mtx_continuous_d`、`stock_futures_continuous_d`）。

## 因子設計

### `futures_bar_factors` (per trading_date, asset_class, symbol)

過濾 `asset_class IN ('tw_futures', 'tw_stock_futures')`、`session = 'day'`、`close NOT NULL`，sort by (asset_class, symbol, trading_date)。

| 欄位 | 公式 |
|---|---|
| `ret_5d / _20d / _60d` | `close / close.shift(N) - 1` |
| `vol_20d / _60d` | log-return rolling std |
| `atr_14` | rolling mean of `high - low` 過 14 天（簡化版 ATR，因為 prev_close 未必齊全） |
| `turnover_20d` | `(close * volume)` rolling 20d mean |
| `oi_chg_5d / _20d` | `open_interest.diff(5)` / `.diff(20)`（tw_futures 與 stock_futures 都有 OI） |

預計列數：~3.9M（520K tw_futures + 3.4M stock_futures）

### `qc_stock_price_diff_snapshot` (per trading_date, stock_id)

直接 `SELECT * FROM qc_stock_price_diff` 寫 parquet（~6.4M 列）。  
副產品：`qc_stock_price_diff_yearly` aggregated — 每年的 mean(pct_diff)、max(abs(pct_diff))、count(diff != 0)、coverage stocks 數，作為 sanity-check 的快速儀表板。

### `finmind_price_canonical` (per trading_date, stock_id)

LEFT JOIN raw + adj on (trading_date, stock_id)：

| 欄位 | 來源 |
|---|---|
| `trading_date`, `stock_id` | 共用 |
| `open / high / low / close / volume / spread / amount_twd / turnover` | `finmind_stock_price_norm`（raw） |
| `adj_open / adj_high / adj_low / adj_close` | `finmind_stock_price_adj_norm` 的 OHLC（rename 加 `adj_` 前綴） |
| `source` | `qd_gold_finmind_price_canonical_v1` |

預計列數：~10.6M（raw 的全部，adj 部分 LEFT JOIN）

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + 因子設計 | ✅ |
| **M2** | 3 個 builder 加進 `derived.py` 並執行 | ⏳ |
| **M3** | Registry：3 個新 Dataset + 4 個 backlink；catalog.py 註冊新 view | ⏳ |
| **M4** | `qd-ingest build-catalog` + `restore_finmind_views` + dashboard regen + strict mkdocs build + commit/push | ⏳ |

## Fallback

- builder 失敗：直接 `git revert`，gold parquet 還在但 catalog view 不見了，下次 rebuild 重生
- 上次 catalog backup：`catalog/quant.duckdb.bak_pre_finalfinalgold_20260525_*`
- finmind canonical 太大跑不出來：把 LEFT JOIN 分批寫成 yearly parquet（partition_by='year'）

## 完成日誌

（待 M2-M4 完成後追加）
