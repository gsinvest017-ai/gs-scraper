# `gold.rs_rating_daily` — Spec Draft

> 狀態：draft，不入 production
> 建立日期：2026-05-18
> 來源演算法：`_quarantine/rs_rating_unpacked/RS_Rating/_internal/app.py` 的 `compute_rs_ranking()`（見 line 171-206）
> 依賴：本檔不執行；列為 `gold/` layer 待落實的派生因子規格

---

## 1. Purpose

把 RS_Rating 桌面工具計算的 IBD 風格 RS Rating（cross-sectional percentile，1–99 整數）作為 daily factor 落到 `gold/`，方便 strategy / backtest / screener 直接用。**演算法照搬，資料源換成我們已經有的 silver `tw_stock_bars`**，不重抓資料。

非目的：不打算重現 RS_Rating UI；不打算重現它的 `prev_week` / `hold_month` cross-sectional backtest engine（那是另一條路）。

---

## 2. Input

- `silver/bars/asset_class=tw_stock/...` 透過 `bars_1d` view
- 過濾條件：
  - `asset_class = 'tw_stock'`
  - `exchange IN ('TWSE','TPEX')`（暫不含 FinMind 帶進來的興櫃；可在 v2 加入）
  - `trading_date` between `as_of - 13 months` and `as_of`（給足夠 lookback buffer，因為 `find_anchor` 要往回找最近 ≤ 目標日的交易日）

**價格欄位選擇**：用 `close`（**非** `adj_close`），與 RS_Rating 原版一致。原版讀 `daily_close_wide.parquet`，該檔由 `update_data.py` 從 SQLite `daily_price.close` pivot 而來，是「除權息未還原」的原始 close。

> ⚠️ 開放問題（OQ-1）：除權息調整對 1Y RS 影響 → 大除權日附近會有假跌。是否改用 `adj_close` 或加一個 `rs_rating_daily_adj` 分身，留待回測比較後決定。

---

## 3. Algorithm（照搬 `compute_rs_ranking`）

### 3.1 Anchor 取點

對於每個 `as_of`，找 5 個 anchor 收盤價：

```
anchor_m  = 距 as_of 往回 m 個月 (m ∈ {0, 1, 3, 6, 9, 12})
         = max(close.trading_date) WHERE trading_date <= as_of - INTERVAL m MONTH
```

`m = 0` 永遠等於 `as_of` 當日（或 as_of 之前最近的交易日）。

> ⚠️ 開放問題（OQ-2）：原版用 `pd.DateOffset(months=m)` → 是 calendar month，遇到月底會跳到下個月最後一天的對應點。DuckDB `INTERVAL '3' MONTH` 行為一致，但 leap-day / 月底 edge case 需要對拍。

### 3.2 RS Score

| Lookback | 公式 | 用到的 anchor |
|---|---|---|
| 1M | `anchor_0 / anchor_1 - 1` | 0, 1 |
| 1Q | `anchor_0 / anchor_3 - 1` | 0, 3 |
| 1Y | `2·rQ1 + rQ2 + rQ3 + rQ4`，其中 `rQn = anchor_{(n-1)*3} / anchor_{n*3} - 1` | 0, 3, 6, 9, 12 |

1Y 是 IBD 標準公式：4 個不重疊季 return，最近季加權 2×。

### 3.3 Period Return（額外輸出，給 UI 配色用）

- 1M / 1Q：等同 `rs_score`
- 1Y：`anchor_0 / anchor_12 - 1`（**未加權**的 12 個月 return）

### 3.4 Cross-sectional Rank

```
valid = rs_score where notna AND finite
if count(valid) < 10:   # _MIN_RANKABLE
    return empty
pct_rank  = valid.rank(pct=True, method='average')   # DuckDB: PERCENT_RANK()
rs_rating = clip(floor(1 + 98 * pct_rank), 1, 99)    # 整數 1-99
```

> ⚠️ 開放問題（OQ-3）：原版 `rank(method='average')` 處理 tie 用平均 rank；DuckDB `PERCENT_RANK()` 用 `(rank - 1) / (n - 1)` 且 tie 拿同個 rank（min 規則），數值會略低 0.5/N 量級。要 100% bit-exact 需用 `ROW_NUMBER()` + average 後處理，或接受些微差異。先用 `PERCENT_RANK()`，差異留 v2 修正。

### 3.5 Universe Re-rank（**不放在 gold table**）

原版會在使用者用 industry / concept / market 過濾後 **重新計算 pct_rank**。這層語意屬於「查詢時」而非「儲存時」。**`gold.rs_rating_daily` 只儲存全市場 baseline ranking**。下游若要看子 universe rank，自己對篩過的 universe 重做 `PERCENT_RANK()`。

---

## 4. Output Schema

`gold/rs_rating/asset_class=tw_stock/lookback=1Y/year=YYYY/*.parquet`

```
trading_date    DATE          -- as_of date
symbol          VARCHAR       -- stock_id
lookback        VARCHAR       -- '1M' | '1Q' | '1Y'
rs_score        DOUBLE        -- raw weighted return (or simple return for 1M/1Q)
rs_rating       SMALLINT      -- 1-99 percentile rank
pct_rank        DOUBLE        -- 0-1 raw percentile
period_return   DOUBLE        -- unweighted lookback return, for display
universe_size   INTEGER       -- count of valid stocks on this as_of
source          VARCHAR       -- 'qd_gold_rs_rating_v1'
ingestion_ts    TIMESTAMP WITH TIME ZONE
```

每 `(trading_date, symbol, lookback)` 一列。3 個 lookback 平行存在於同表，下游用 `WHERE lookback = '1Y'` 過濾。

**Partitioning**：依 `lookback` × `year(trading_date)`。每檔 parquet 大小 5–30 MB（依 universe 與年份）。

---

## 5. DuckDB SQL Skeleton

```sql
-- 假設 bars_1d view 已存在；用 macro 包裝 anchor lookup
CREATE OR REPLACE MACRO anchor_close(bars_ref, as_of, months_back) AS TABLE (
    SELECT
        symbol,
        last(close ORDER BY trading_date) AS anchor_close
    FROM bars_ref
    WHERE asset_class = 'tw_stock'
      AND exchange IN ('TWSE','TPEX')
      AND trading_date <= (as_of::DATE - INTERVAL (months_back) MONTH)
      AND trading_date >  (as_of::DATE - INTERVAL (months_back + 1) MONTH)
    GROUP BY symbol
);
-- 註：上面 macro 為簡化說明；實作要避免 GROUP BY 把 multi-symbol 黏死，
--     正式版用 window function + DISTINCT ON (symbol)。

-- 主 query (1Y lookback example)
WITH a AS (
    SELECT symbol,
           MAX(CASE WHEN bucket = 0  THEN close END) AS a0,
           MAX(CASE WHEN bucket = 3  THEN close END) AS a3,
           MAX(CASE WHEN bucket = 6  THEN close END) AS a6,
           MAX(CASE WHEN bucket = 9  THEN close END) AS a9,
           MAX(CASE WHEN bucket = 12 THEN close END) AS a12
    FROM (
        SELECT symbol, close, trading_date,
               -- 對每個 bucket 取最後一筆 ≤ (as_of - bucket months)
               ROW_NUMBER() OVER (PARTITION BY symbol, bucket ORDER BY trading_date DESC) rn,
               bucket
        FROM bars_1d
        CROSS JOIN (VALUES (0),(3),(6),(9),(12)) v(bucket)
        WHERE asset_class = 'tw_stock'
          AND exchange IN ('TWSE','TPEX')
          AND trading_date <= ($as_of::DATE - INTERVAL (bucket) MONTH)
    )
    WHERE rn = 1
    GROUP BY symbol
),
scored AS (
    SELECT symbol,
           2.0 * (a0/a3 - 1) + (a3/a6 - 1) + (a6/a9 - 1) + (a9/a12 - 1) AS rs_score,
           a0/a12 - 1                                                    AS period_return
    FROM a
    WHERE a0 IS NOT NULL AND a3 IS NOT NULL AND a6 IS NOT NULL
      AND a9 IS NOT NULL AND a12 IS NOT NULL
),
ranked AS (
    SELECT *,
           PERCENT_RANK() OVER (ORDER BY rs_score) AS pct_rank,
           COUNT(*)      OVER ()                  AS universe_size
    FROM scored
    WHERE rs_score IS NOT NULL AND isfinite(rs_score)
)
SELECT $as_of::DATE   AS trading_date,
       symbol,
       '1Y'           AS lookback,
       rs_score,
       LEAST(99, GREATEST(1, CAST(floor(1 + 98 * pct_rank) AS SMALLINT))) AS rs_rating,
       pct_rank,
       period_return,
       universe_size,
       'qd_gold_rs_rating_v1'        AS source,
       now()                         AS ingestion_ts
FROM ranked
WHERE universe_size >= 10;
```

**Driver**：寫一個 Python loop（或 DuckDB UDF）走每個 trading date，把上面 query 的結果 UNION 起來，寫到分區 parquet。一年 ~250 個 as_of × 3 lookback × ~1,700 symbol ≈ 1.3 M 列 / 年。

---

## 6. Validation Plan

當 RS_Rating exe 有人在 Windows 跑過、產生 `rs_rating_data/data/` 之後：

1. 同步 `daily_close_wide.parquet` 進來，跑原版 `compute_rs_ranking(prices, as_of, '1Y')` 抽 5 個歷史 `as_of` 對比
2. 預期 `rs_rating` 整數完全一致（OQ-3 PERCENT_RANK 差異邊界 ≤ 1 等級）
3. `rs_score` 應該 bit-exact（IEEE 754 float 加減乘除順序一致時）
4. 若不 bit-exact：列出差異 stock_id × as_of，逐一定位 — 通常是 anchor 取點差一天造成

無 Windows 端 fixture 前，最低限度 sanity check：

- 每個 as_of 的 `rs_rating` 應該 uniform 分佈 1–99
- `rs_rating = 99` 的 symbol 在當下 1Y price chart 應該明顯上行
- universe_size 隨年份單調上升（2000-2009 用 FinMind 補完後 universe 會約 ~1,000）

---

## 7. Implementation Order (when M4+ unlocks)

| 步驟 | 內容 | 預估 LOC |
|---|---|---|
| 1 | 在 `catalog/quant.duckdb` 把上面 SQL 包成 macro / view | 60 |
| 2 | 寫 `src/qd_ingest/gold_rs_rating.py`，driver 逐日跑寫 parquet | 80 |
| 3 | 跑 2010-01-01 ~ 今天的全量；分區寫入 `gold/rs_rating/` | runtime ~30 min |
| 4 | 增量：每日 silver `bars_1d` ingest 完後追加當日 RS rating | 觸發點掛 Makefile target |
| 5 | 加 pytest：合成資料 → 已知 RS 結果（仿 `_internal/tests/test_backtest.py` 風格） | 100 |

---

## 8. 開放問題清單

| OQ | 問題 | 暫時決策 | 何時決 |
|---|---|---|---|
| OQ-1 | close vs adj_close | 用 close（與原版一致） | 跑回測比較後決定是否加 `_adj` 分身 |
| OQ-2 | calendar month vs trading-day anchor | calendar month（與原版一致） | edge-case 對拍時驗證 |
| OQ-3 | DuckDB PERCENT_RANK vs pandas rank(method='average') tie 規則 | 接受 ≤ 1 等級差異 | v2 若要 bit-exact 改用 ROW_NUMBER + AVG 後處理 |
| OQ-4 | 興櫃 (emerging) 是否納入 universe | 預設不納入（與原版「上市/上櫃」一致） | 等 FinMind 興櫃資料進 silver 後決定 |
| OQ-5 | 用「全市場 universe」還是允許 stored sub-universe rank | gold 只存全市場；sub-universe 查詢時算 | 不變 |

---

## 9. 參考

- 原版實作：`_quarantine/rs_rating_unpacked/RS_Rating/_internal/app.py` line 163-206
- 原版單元測試：`_quarantine/rs_rating_unpacked/RS_Rating/_internal/tests/test_backtest.py`（30 個 backtest engine 測試，無 I/O）
- 原版架構文件：`_quarantine/rs_rating_unpacked/RS_Rating/ARCHITECTURE.md`
- 主進度文件：`docs/progress-finmind-rsrating-integration.md`
