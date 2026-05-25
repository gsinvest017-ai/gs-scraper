# 2026-05-25 — 將 100% silver 處理成 gold level

## 觸發

`/safe-yolo 將所有資料完整度100%的catalog全部處理成golden level`

## 目標

對目前 dashboard 顯示 **資料完整度 100% / OK** 的 silver 資料表，**確保有對應的 gold-level 衍生產物**：

- **Refresh existing gold tables** （`stock_factor_daily` 已 142d 舊；`cross_market_features` EMPTY）
- **Add new gold table for institutional flows** — `tw_inst_stock_daily` 是 100% silver 但沒對應 gold

不在範圍內：新建 `tw_margin_daily` / `tw_futures_large_trader_daily` 等的 gold 因子（留 follow-up）。

## 起始狀態（pre-M2）

100%+ silver 表（lag = 0d）：
- `tw_stock_bars` (P0, 6.6M)
- `bars_1d` (P0)
- `tw_inst_stock_daily` (P0, 6.5M)
- `tw_margin_daily` (P0, 3.7M)
- `tw_futures_large_trader_daily` (P0)
- `tw_inst_futures_full_daily` (P1)
- `tw_stock_trading_attrs_daily` (P2)
- `qc_stock_price_diff` (P2)
- + `cash_dividend_events` / `tw_stock_futures_corp_actions` (forward-looking events)

已有 gold (但部分過時)：
- `gold/continuous/tx_continuous_d.parquet` ✅ (2026-05-22, 由 M2 of progress-extended-crawl 修)
- `gold/continuous/mtx_continuous_d.parquet` ✅
- `gold/continuous/stock_futures_continuous_d.parquet` (stale, underlying 4/13)
- `gold/features/stock_factor_daily.parquet` (stale, 142d) ⚠️
- `gold/features/cross_market_features.parquet` (EMPTY) ⚠️

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + assessment | ⏳ |
| **M2** | `python -m qd_ingest.sources.derived` 重建 stock_factor_daily / cross_market_features / txo_daily_features | ⏳ |
| **M3** | 新 gold table `gold/features/inst_flow_factors.parquet`（個股法人流量因子）+ Dataset registry 條目 | ⏳ |
| **M4** | qd-ingest build-catalog + restore_finmind_views + regen dashboard + push | ⏳ |

## 因子設計（M3）

從 `tw_inst_stock_daily` 衍生：

| 因子 | 公式 |
|---|---|
| `foreign_net_5d` | 過去 5 個交易日外資淨買賣超累計 |
| `foreign_net_20d` | 同上 20 天 |
| `foreign_net_60d` | 同上 60 天 |
| `sitc_net_5d` / `_20d` | 投信 |
| `dealer_net_5d` / `_20d` | 自營商 |
| `foreign_hold_pct_chg_20d` | 外資持股率 20 天變化（趨勢） |
| `inst_net_persistence_20d` | 三大法人合計淨流入正天數 / 20 |

Output: `gold/features/inst_flow_factors.parquet`，主鍵 `(trading_date, stock_id)`，partition by year。

## Fallback

- M2 build_stock_factor_daily 跑太久（>5 min）→ commit WIP，document
- M3 寫壞了 → `rm gold/features/inst_flow_factors.parquet`，從 silver 重跑
