# RS_Rating gold 規格

> **狀態**：規格 draft 已落地，**尚未實作**。下次 ingest milestone 才會建表。

完整規格見 repo 內 [`docs/spec-gold-rs-rating-daily.md`](https://github.com/)。本頁是高階摘要 + 動機 + 路線。

## 什麼是 RS Rating

IBD（Investor's Business Daily）標準的 **Relative Strength Rating**：把全市場個股按過去 12 個月加權報酬率排名，給每檔一個 1-99 的整數百分位。

公式（12-month weighted）：

```
rQ1 = price_0M / price_3M  - 1   (最近季)
rQ2 = price_3M / price_6M  - 1
rQ3 = price_6M / price_9M  - 1
rQ4 = price_9M / price_12M - 1   (最久季)
rs_score = 2·rQ1 + rQ2 + rQ3 + rQ4
pct_rank = rs_score.rank(pct=True)
rs_rating = floor(1 + 98 · pct_rank).clip(1, 99)
```

## 為什麼接

- RS_Rating 桌面工具（在 `RAW_SOURCES/RS_Rating.7z`，已抽 source 到 `_quarantine/`）有完整的 Python 實作 + 30 個 unit test，可以參照。
- 演算法本身很簡單但需要 cross-sectional context，所以放 gold layer 才合理（silver 是 per-row standardize）。
- 量化研究員會用 `rs_rating > 80` 當動量 filter，是很普遍的因子。

## 設計總覽

| 項目 | 決策 |
|---|---|
| 資料源 | `bars_1d` view，filter `asset_class='tw_stock'` AND `exchange IN ('TWSE','TPEX')` |
| 價格欄位 | `close`（非 `adj_close`，與 RS_Rating 原版一致） |
| Lookback | 1M / 1Q / 1Y 三種，同一張表用 `lookback` 欄區分 |
| Universe | 全市場 baseline；下游若要 sub-universe rank 自己對篩過的 universe 重做 `PERCENT_RANK()` |
| 最小排名數 | 10（少於此 return empty） |
| 儲存 | `gold/rs_rating/asset_class=tw_stock/lookback=<L>/year=YYYY/*.parquet` |

## Output schema

```
trading_date    DATE
symbol          VARCHAR
lookback        VARCHAR   -- '1M' | '1Q' | '1Y'
rs_score        DOUBLE
rs_rating       SMALLINT  -- 1-99
pct_rank        DOUBLE    -- 0-1
period_return   DOUBLE
universe_size   INTEGER
source          VARCHAR   -- 'qd_gold_rs_rating_v1'
ingestion_ts    TIMESTAMP WITH TIME ZONE
```

## DuckDB SQL skeleton

```sql
WITH a AS (
    SELECT symbol,
           MAX(CASE WHEN bucket = 0  THEN close END) AS a0,
           MAX(CASE WHEN bucket = 3  THEN close END) AS a3,
           MAX(CASE WHEN bucket = 6  THEN close END) AS a6,
           MAX(CASE WHEN bucket = 9  THEN close END) AS a9,
           MAX(CASE WHEN bucket = 12 THEN close END) AS a12
    FROM (
        SELECT symbol, close, trading_date,
               ROW_NUMBER() OVER (PARTITION BY symbol, bucket
                                   ORDER BY trading_date DESC) rn,
               bucket
        FROM bars_1d
        CROSS JOIN (VALUES (0),(3),(6),(9),(12)) v(bucket)
        WHERE asset_class='tw_stock'
          AND exchange IN ('TWSE','TPEX')
          AND trading_date <= ($as_of::DATE - INTERVAL (bucket) MONTH)
    )
    WHERE rn = 1
    GROUP BY symbol
),
scored AS (
    SELECT symbol,
           2.0*(a0/a3 - 1) + (a3/a6 - 1) + (a6/a9 - 1) + (a9/a12 - 1) AS rs_score,
           a0/a12 - 1                                                  AS period_return
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
SELECT $as_of::DATE AS trading_date,
       symbol,
       '1Y'         AS lookback,
       rs_score,
       LEAST(99, GREATEST(1, CAST(floor(1 + 98*pct_rank) AS SMALLINT))) AS rs_rating,
       pct_rank,
       period_return,
       universe_size,
       'qd_gold_rs_rating_v1' AS "source",
       now() AS ingestion_ts
FROM ranked
WHERE universe_size >= 10;
```

Driver 寫一個 Python loop 對每個 trading date 跑這段，UNION 起來寫分區 parquet。一年 ≈ 250 個 as_of × 3 lookback × 1,700 stocks ≈ 1.3 M 列。

## Open questions

| OQ | 問題 | 暫定 |
|---|---|---|
| OQ-1 | close vs adj_close？除權息日會假跌 | 用 close（與原版一致），需要時加 `_adj` 分身 |
| OQ-2 | calendar month vs trading-day anchor | calendar month（原版 `pd.DateOffset(months=m)`） |
| OQ-3 | DuckDB `PERCENT_RANK()` tie 規則 vs pandas `rank(method='average')` | 接受 ≤ 1 等級差異 |
| OQ-4 | 興櫃納入 universe？ | 預設不納入（與原版一致） |
| OQ-5 | sub-universe rank 存還是不存 | 不存，下游查詢時算 |

## Implementation order

| 步驟 | 預估 |
|---|---|
| 1. 把 SQL 包成 catalog macro | 60 LOC |
| 2. 寫 `src/qd_ingest/gold_rs_rating.py` driver | 80 LOC |
| 3. 跑 2010-至今 全量 | ~30 分鐘 |
| 4. 增量 wired 進 daily_refresh | Makefile target |
| 5. pytest（合成資料 → 已知結果） | 100 LOC |

## 來源實作參考

[`_quarantine/rs_rating_unpacked/RS_Rating/_internal/app.py`](https://github.com/) line 171-206 `compute_rs_ranking()`（已從原 PyInstaller bundle 抽出，176 KB Python source）。其原 unit test 在 `_internal/tests/test_backtest.py`（30 個 backtest engine 測試，無 I/O）。
