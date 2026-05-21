# Canonical schema

silver 層每張表的欄位、型別、語意都鎖在這頁。任何 view / ETL / 下游策略都應以本頁為準。

## 設計原則

1. **時間有兩個欄位**：`ts_utc` (timestamp tz-aware UTC) + `trading_date` (純日，無時區)。前者支援跨時區運算，後者方便日級 partition。
2. **永遠帶 source + ingestion_ts**：方便追資料來源、debug stale row、做 QC。
3. **沒有中文欄名 / 空白欄名 / 大寫欄名**：snake_case lowercase。
4. **單位寫進欄名**：`volume_lot`（張）vs `volume_shares`（股）；`amount_twd`、`amount_ktwd`（千 TWD）。
5. **NULL 有語意**：缺資料 vs 「該欄位對此 row 不適用」要分清楚（例：股票沒有 `open_interest`，期貨才有）。

## bars — OHLCV

silver path: `silver/bars/asset_class=<class>/[symbol=<sym>/]year=<yyyy>/*.parquet`

| 欄位 | 型別 | 必填 | 語意 |
|---|---|:--:|---|
| `ts_utc` | `TIMESTAMP WITH TIME ZONE` | ✓ | bar 結束時刻 UTC（日 K = 當日 13:30 TPE / 期貨 night close 視合約而定） |
| `trading_date` | `DATE` | ✓ | partition key |
| `asset_class` | `VARCHAR` | ✓ | `tw_stock` / `tw_future` / `tw_option` / `us_future` / `us_etf` |
| `exchange` | `VARCHAR` | ✓ | `TWSE` / `TPEX` / `TAIFEX` / `CME` / `NYSE` |
| `symbol` | `VARCHAR` | ✓ | 本地短碼（`2330`、`TXFD4`、`SPY`） |
| `contract_id` | `VARCHAR` | ⛌ | 期貨完整月份碼；股票留 NULL |
| `session` | `VARCHAR` | ✓ | `day` / `night` / `combined`（日 K 預設 `combined`） |
| `open` / `high` / `low` / `close` | `DOUBLE` | ✓ | 未調整原值 |
| `volume` | `BIGINT` | ✓ | 股票 = 股、期 / 選 = 口（單位寫進欄名是 future work，目前隱式） |
| `open_interest` | `BIGINT` | ⛌ | 期 / 選未沖銷部位 |
| `vwap` | `DOUBLE` | ⛌ | 成交均價（amount / volume） |
| `settlement` | `DOUBLE` | ⛌ | 期 / 選結算價 |
| `adj_open/high/low/close` | `DOUBLE` | ⛌ | 還原權息 |
| `adj_factor` | `DOUBLE` | ⛌ | 累積調整係數 |
| `source` | `VARCHAR` | ✓ | bronze 來源 ID (`tej` / `finmind` / `histdata` / `yahoo`) |
| `ingestion_ts` | `TIMESTAMP WITH TIME ZONE` | ✓ | 寫入 silver 時刻 |
| `quality_flag` | `VARCHAR` | ⛌ | 預留：`L1_anomaly` / `L2_inconsistent` 等 |
| `year` | `INTEGER` | ✓ | partition |

## flows.inst_stock — 個股三大法人

| 欄位 | 型別 | 語意 |
|---|---|---|
| `trading_date` | `DATE` | partition |
| `stock_id` | `VARCHAR` | TWSE 4 碼 |
| `exchange` | `VARCHAR` | `TWSE` / `TPEX` |
| `foreign_net_lot` / `sitc_net_lot` / `dealer_net_lot` | `BIGINT` | 外資 / 投信 / 自營淨買賣（張） |
| `total_net_lot` | `BIGINT` | 三家加總 |
| `foreign_buy_lot` / `foreign_sell_lot` | `BIGINT` | 拆買 / 拆賣 |
| `sitc_buy_lot` / `sitc_sell_lot` | `BIGINT` | |
| `dealer_buy_lot` / `dealer_sell_lot` | `BIGINT` | |
| `foreign_hold_lot` / `foreign_hold_pct` | `BIGINT` / `DOUBLE` | 外資累積持股（張、% of outstanding） |
| `sitc_hold_lot` / `sitc_hold_pct` | | |
| `dealer_hold_lot` / `dealer_hold_pct` | | |
| `source` / `ingestion_ts` | | audit |

## flows.margin — 融資融券

| 欄位 | 型別 | 語意 |
|---|---|---|
| `trading_date` | `DATE` | |
| `stock_id` | `VARCHAR` | |
| `margin_buy_lot` / `margin_sell_lot` | `BIGINT` | 融資買賣（張） |
| `short_buy_lot` / `short_sell_lot` | `BIGINT` | 融券買賣 |
| `margin_balance_lot` / `short_balance_lot` | `BIGINT` | 餘額 |
| `margin_balance_ktwd` / `short_balance_ktwd` | `DOUBLE` | 餘額（千 TWD） |
| `margin_util_pct` / `short_util_pct` | `DOUBLE` | 使用率 |
| `short_to_margin_pct` | `DOUBLE` | 券資比 |
| `margin_maint_pct` / `short_maint_pct` | `DOUBLE` | 維持率 |
| `account_maint_pct` | `DOUBLE` | 整戶維持率 |
| `source` / `ingestion_ts` | | audit |

## fundamentals.q — 季報

| 欄位 | 型別 | 語意 |
|---|---|---|
| `stock_id` | `VARCHAR` | |
| `fiscal_period` | `VARCHAR` | `2024Q3` 形式 |
| `period_type` | `VARCHAR` | `quarterly` / `cumulative` |
| `consolidated` | `BOOLEAN` | 合併報表？ |
| `currency` | `VARCHAR` | `TWD` |
| `publish_date` | `DATE` | **公告日（point-in-time 用此欄）** |
| `eps` | `DOUBLE` | |
| `roa_pre` / `roe_post` | `DOUBLE` | |
| `gross_margin` / `op_margin` / `net_margin` | `DOUBLE` | |
| `rev_growth` / `gross_growth` / `op_growth` | `DOUBLE` | YoY |
| `total_assets` / `total_liab` / `total_equity` | `BIGINT` | |

關鍵設計：**用 `publish_date` 而不是 fiscal_period 做時序對齊**，避免 lookahead bias。

## macro — 總體日資料

| 欄位 | 型別 | 語意 |
|---|---|---|
| `trading_date` | `DATE` | |
| `symbol` | `VARCHAR` | `^VIX` / `USDTWD=X` / `^TNX`（10Y treasury） |
| `category` | `VARCHAR` | `volatility` / `fx` / `rate` / `commodity` |
| `open / high / low / close` | `DOUBLE` | |
| `adj_close` | `DOUBLE` | |
| `volume` | `BIGINT` | |
| `source` / `ingestion_ts` | | |

## reference.symbol_map

| 欄位 | 型別 | 語意 |
|---|---|---|
| `symbol` | `VARCHAR` | 本地短碼 |
| `vendor_symbol` | `VARCHAR` | 對方系統的 symbol |
| `asset_class` | `VARCHAR` | |
| `exchange` | `VARCHAR` | |
| `name_zh` | `VARCHAR` | |
| `multiplier` | `DOUBLE` | 期貨乘數 |

## reference.contract_specs

| 欄位 | 型別 | 語意 |
|---|---|---|
| `symbol` | `VARCHAR` | |
| `tick_size` | `DOUBLE` | 最小跳動點 |
| `multiplier` | `DOUBLE` | 每點 TWD |
| `currency` | `VARCHAR` | |
| `settlement_type` | `VARCHAR` | cash / physical |
| `last_trading_day_rule` | `VARCHAR` | 最後交易日規則描述 |

## 不入 silver 的東西

- **逐筆 tick**：太大、太雜，獨立放 `silver/options/txo_tick/` 或留 bronze；不進 canonical bars。
- **未經驗證的 derived**：直接寫成 view 而非 parquet；下游 explicit ack 才落地。
- **個別策略產出**：那是 `gs-strategy/` 的事，不是 lakehouse 範疇。

## 加新欄位的流程

1. 在 silver parquet 新增欄位（DuckDB 1.5+ 容忍）
2. 更新 view DDL（`scripts/rebuild_catalog.py` 自動）
3. 更新本頁
4. （選）更新對應 `qd-ingest` adapter 的 pandera schema
