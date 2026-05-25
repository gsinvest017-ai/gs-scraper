# 2026-05-25 (final) — Goldify 剩下的 silver views

## 觸發

`/safe-yolo C:\Users\User\Pictures\gap-silver.png 目前為甚麼還有silver沒有被處理成gold please fix it`

## 起始狀態

Dashboard 顯示 10 個 OK 的 silver view 但 **gold 欄空白**：

| view | silver size | 已有 gold? |
|---|---|---|
| `tw_stock_bars` | 67.7 MB | 有 (`stock_factor_daily`) — **沒 backlink 到 Dataset.gold_paths** |
| `tw_inst_stock_daily` | 116.8 MB | 有 (`inst_flow_factors`) — **沒 backlink** |
| `tw_margin_daily` | 106.4 MB | 真缺 |
| `tw_inst_futures_full_daily` | 15.4 MB | 真缺 |
| `tw_futures_large_trader_daily` | 4.4 MB | 真缺 |
| `bars_1d`（含期貨） | 120.6 MB | 部分（tx/mtx/sf 連續期）— 期貨整體 OI feature 還缺 |
| `tw_stock_trading_attrs_daily` | 2.1 MB | 跳過（多為 flag 性質） |
| `fundamentals_q` | 20.1 MB | 真缺 |
| `cash_dividend_events` | 0.4 MB | 跳過（lookup 表性質） |
| `tw_stock_futures_corp_actions` | 0.5 MB | 跳過（reference 表） |

## 範圍（pragmatic）

新建 **3 個 gold table** + **2 個 backlink**：

1. **`margin_factors`** ← `tw_margin_daily`：6 個融資融券時序因子
2. **`fundamentals_pit`** ← `fundamentals_q`：以 `publish_date` 對齊的 PIT 財務 panel + TTM / YoY
3. **`futures_large_trader_factors`** ← `tw_futures_large_trader_daily`：大額交易人集中度因子
4. backlink `tw_stock_bars.gold_paths = (gold/features/stock_factor_daily.parquet,)`
5. backlink `tw_inst_stock_daily.gold_paths = (gold/features/inst_flow_factors.parquet,)`

跳過：`tw_stock_trading_attrs_daily`（flag 性）、`cash_dividend_events`（lookup）、`tw_stock_futures_corp_actions`（reference）、`tw_inst_futures_full_daily`（schema 較複雜，留 backlog）、`bars_1d` 期貨段（已有 continuous）。

## 因子設計

### `margin_factors` (per trading_date, stock_id)

| 欄位 | 公式 |
|---|---|
| `margin_balance_chg_5d / _20d` | 5/20 天前後 `margin_balance_lot` 差 |
| `short_balance_chg_5d / _20d` | 同上對 `short_balance_lot` |
| `margin_util_zscore_60d` | `margin_util_pct` 60d rolling z-score |
| `short_to_margin_chg_20d` | `short_to_margin_pct` 20d 差 |

### `fundamentals_pit` (per publish_date, stock_id)

Filter `period_type='quarterly' AND consolidated=TRUE`，按 (stock_id, publish_date) sort。

| 欄位 | 公式 |
|---|---|
| `eps`, `roe_post`, `revenue`, `net_income` | 從 silver 過來（current quarter） |
| `eps_ttm` | 連續 4 季 EPS rolling sum |
| `revenue_ttm` | 連續 4 季 revenue rolling sum |
| `ni_yoy_chg_pct` | 與 4 季前淨利相比 |
| `revenue_yoy_chg_pct` | 與 4 季前營收相比 |
| `roe_ttm_avg` | 4 季 roe_post 平均 |

### `futures_large_trader_factors` (per trading_date, product, expiry_month)

| 欄位 | 公式 |
|---|---|
| `top10_net_pct` | `top10_buy_traders_pct - top10_sell_traders_pct` |
| `top10_institutional_net_pct` | 同上對 institutional |
| `top5_concentration_avg` | `(top5_buy_traders_pct + top5_sell_traders_pct) / 2` |
| `oi_chg_5d` | `total_oi` 5d diff |
| `oi_chg_20d` | 同上 20d |

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔（含因子設計） | ⏳ |
| **M2** | 3 個新 gold builder（margin / fundamentals_pit / futures_large_trader）寫進 `derived.py` + 跑 build_all | ✅ |
| **M3** | DATASETS registry：2 個 backlink + 3 個新 view 條目；catalog.py 註冊新 view | ✅ |
| **M4** | qd-ingest build-catalog + restore_finmind_views + regen dashboard + push | ✅ |

## Fallback

- 寫壞了：`git revert <commit>` → gold parquet 還在但 catalog view 不見了，下次 rebuild 重生
- factor 公式跑錯：直接修 `derived.py` 對應函式，重跑


---

## 完成日誌

### M2 — 3 個新 gold builder

加進 `src/qd_ingest/sources/derived.py`：

- **`build_margin_factors`** → `gold/features/margin_factors.parquet` — **3,713,424 列 / 2,507 stocks / 0.9s**
- **`build_fundamentals_pit`** → `gold/features/fundamentals_pit.parquet` — **93,525 列 / 1,936 stocks / 0.1s**（filter `period_type='Q'` 而非 `'quarterly'`，silver 用 `Q`/`YTD` 兩值）
- **`build_futures_large_trader_factors`** → `gold/features/futures_large_trader_factors.parquet` — **99,162 列 / 2,350 contracts / 0.1s**

全列入 `build_all()`，cron / daily_refresh 整批 rebuild 可一次到位。

### M3 — backlinks + registry

- `tw_stock_bars.gold_paths` += `gold/features/stock_factor_daily.parquet`
- `tw_inst_stock_daily.gold_paths` += `gold/features/inst_flow_factors.parquet`
- `tw_margin_daily.gold_paths` += `gold/features/margin_factors.parquet`
- `tw_futures_large_trader_daily.gold_paths` += `gold/features/futures_large_trader_factors.parquet`
- `fundamentals_q.gold_paths` += `gold/features/fundamentals_pit.parquet`
- 加 3 個新 Dataset 條目（margin_factors / fundamentals_pit / futures_large_trader_factors）
- `catalog.py` 註冊 3 個新 view

### M4 — catalog + dashboard

`qd-ingest build-catalog` 40 views（含 3 新 gold）；`restore_finmind_views.py` 補 finmind/qc。Dashboard summary：`OK=15 → 17`（+margin_factors, +futures_large_trader_factors 都 0d/OK；fundamentals_pit max 3/31，category=derived，~52d 變 INFO）。

剩下沒處理的 silver：
- `tw_inst_futures_full_daily`（per-identity 較複雜，留 backlog）
- `tw_stock_trading_attrs_daily`、`cash_dividend_events`、`tw_stock_futures_corp_actions`（lookup/event 性質，無 derive 價值）
- `bars_1d` 期貨 OI 衍生（留 backlog；continuous 已有）

## Live

<https://gsinvest017-ai.github.io/gs-scraper/gap_dashboard.html>
