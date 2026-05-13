# DuckDB 操作說明

> 對應資料庫：`catalog/quant.duckdb`
> 適用倉儲：QUANTDATA medallion lakehouse（bronze → silver → gold）
> 重建腳本：`python -m qd_ingest.common.catalog`（idempotent，silver 更新後重跑即可）

DuckDB catalog 是整個 lakehouse 的查詢入口。它本身不存資料，只在 `silver/` 與 `gold/` 的 Parquet 之上掛 view + macro。所有研究／回測程式都應透過此 catalog 存取資料，避免直接寫死 parquet 路徑。

---

## 1. 連線與基本設定

### 1.1 用 CLI 連線

```bash
cd /home/kevin/gs-scraper/QUANTDATA
duckdb catalog/quant.duckdb
```

進入 prompt 後，**第一件事**就是設定 file search path（catalog 內部用相對路徑掛 parquet）：

```sql
SET file_search_path='/home/kevin/gs-scraper/QUANTDATA';
```

> ⚠️ 若沒設定 `file_search_path`，所有 view 的 `read_parquet(...)` 都會找不到檔案而報錯。

### 1.2 用 Python 連線（推薦：read_only=True）

```python
import duckdb
from pathlib import Path

ROOT = Path("/home/kevin/gs-scraper/QUANTDATA")
con = duckdb.connect(str(ROOT / "catalog" / "quant.duckdb"), read_only=True)
con.execute(f"SET file_search_path='{ROOT}'")
```

> ℹ️ DuckDB 同時只允許**一個寫入連線**。如果 CLI 已開啟，Python 必須加 `read_only=True`，否則會撞到 `Conflicting lock` 錯誤。

### 1.3 探索 catalog

```sql
SHOW TABLES;                       -- 列出所有 view
DESCRIBE bars_1d;                  -- 看欄位
.schema tw_stock_bars              -- 看 view 定義（CLI 專用 dot-command）
SELECT * FROM duckdb_functions()   -- 列出所有 macro / function
  WHERE function_type='macro' AND internal=false;
```

---

## 2. Catalog 內容總覽

執行 `SHOW TABLES;` 會看到 18 個 view / macro：

| 類別 | View 名稱 | 來源 | 說明 |
|------|-----------|------|------|
| Reference | `symbol_map` | `reference/symbol_map.parquet` | 跨資產 symbol 對照（canonical / yahoo / TEJ） |
| Reference | `contract_specs` | `reference/contract_specs.parquet` | 期貨合約規格（tick size、乘數、到期） |
| Reference | `calendar_xtai` | `reference/calendar_xtai.parquet` | 台灣交易所交易日曆 |
| Silver bars | `bars_1d` | `silver/bars/bars_1d/**/*.parquet` | 日 K：tw_stock / tw_futures / tw_stock_futures / us_futures …統一 schema |
| Silver bars | `bars_1m` | `silver/bars/bars_1m/**/*.parquet` | 分 K：tw_futures (MXF/TXF) + us_futures (NQ/ES/GC) |
| Silver flows | `tw_inst_futures_daily` | `silver/flows/tw_inst_futures_daily/**` | 期貨三大法人留倉 |
| Silver flows | `tw_inst_stock_daily` | `silver/flows/tw_inst_stock_daily/**` | 個股三大法人買賣超 |
| Silver flows | `tw_inst_market_daily` | `silver/flows/tw_inst_market_daily/**` | 整體市場三大法人 |
| Silver flows | `tw_margin_daily` | `silver/flows/tw_margin_daily/**` | 融資融券餘額 |
| Silver fundamentals | `fundamentals_q` | `silver/fundamentals/fin_q/**` | 季度財報（含 YTD / Q 兩種 period_type） |
| Silver macro | `macro_daily` | `silver/macro/macro_daily.parquet` | VIX / SPX / TAIEX / USDTWD 等 daily |
| Gold continuous | `tx_continuous_d` | `gold/continuous/tx_continuous_d.parquet` | TX 連續期貨（換月接續） |
| Gold continuous | `mtx_continuous_d` | `gold/continuous/mtx_continuous_d.parquet` | MTX 連續期貨 |
| Gold continuous | `stock_futures_continuous_d` | `gold/continuous/stock_futures_continuous_d.parquet` | 個股期連續 |
| Gold features | `txo_daily_features` | `gold/features/txo_daily_features.parquet` | TXO 選擇權日級特徵（IV / skew / PCR） |
| Gold features | `cross_market_features` | `gold/features/cross_market_features.parquet` | 跨市場特徵 |
| Gold features | `stock_factor_daily` | `gold/features/stock_factor_daily.parquet` | 個股因子（mom_12_1 / vol_60d / ret_120d …） |
| Convenience | `tw_stock_bars` | `bars_1d` 過濾 | `WHERE asset_class='tw_stock' AND session='day'`（**重要：避開與股期同 symbol 撞表**） |

---

## 3. 自訂 Macro（參數化查詢）

Catalog 預先註冊三個 macro，讓常見 join pattern 變成單行查詢：

### 3.1 `tw_stock_with_inst(stock_id, start, end)`

個股 OHLCV × 三大法人 × 融資融券，最常用的個股研究入口。

```sql
FROM tw_stock_with_inst('2330', DATE '2024-01-01', DATE '2024-01-31');
```

回傳欄位：`trading_date, symbol, close, volume,
foreign_net_lot, sitc_net_lot, dealer_net_lot, total_net_lot,
margin_balance_lot, short_balance_lot, short_to_margin_pct`。

### 3.2 `tw_stock_asof_fundamentals(stock_id, start, end)`

Point-in-time ASOF join — 用「最近一次已公告財報」對齊每個交易日，避免前視偏差（look-ahead bias）。

```sql
FROM tw_stock_asof_fundamentals('2330', DATE '2024-01-01', DATE '2024-12-31');
```

回傳欄位：`trading_date, close, fiscal_period, publish_date, eps, roe_post`。

> ⚠️ Macro 內部已先 `WHERE period_type='Q'`，避免 ASOF 抓到 YTD 累計報表。

### 3.3 `bars_1m_for(asset_class, symbol, start_ts, end_ts)`

分 K 時序篩選 + 排序。

```sql
FROM bars_1m_for(
    'tw_futures', 'MXF',
    TIMESTAMPTZ '2024-01-02 00:00:00 UTC',
    TIMESTAMPTZ '2024-01-03 00:00:00 UTC'
);
```

---

## 4. 常用查詢範例

### 4.1 個股日 K（純 bars）

```sql
SELECT trading_date, open, high, low, close, volume
FROM tw_stock_bars
WHERE symbol = '2330'
  AND trading_date BETWEEN DATE '2024-01-01' AND DATE '2024-01-10'
ORDER BY trading_date;
```

> 💡 用 `tw_stock_bars`（已過濾 `asset_class='tw_stock' AND session='day'`）而非直接查 `bars_1d`，
> 否則 symbol='2330' 會同時拉到「現股 2330」與「股期 2330」的列，造成笛卡兒積。

### 4.2 個股 × 法人 × 融券（end-to-end join）

不想用 macro 時的原始寫法：

```sql
SELECT b.trading_date, b.close, b.volume,
       i.foreign_net_lot, i.total_net_lot,
       m.margin_balance_lot, m.short_to_margin_pct
FROM tw_stock_bars b
LEFT JOIN tw_inst_stock_daily i
  ON b.trading_date = i.trading_date AND b.symbol = i.stock_id
LEFT JOIN tw_margin_daily m
  ON b.trading_date = m.trading_date AND b.symbol = m.stock_id
WHERE b.symbol = '2330'
  AND b.trading_date BETWEEN DATE '2024-01-02' AND DATE '2024-01-10'
ORDER BY b.trading_date;
```

### 4.3 Point-in-time ASOF Join（避開前視偏差）

```sql
WITH fq AS (
    SELECT * FROM fundamentals_q WHERE period_type = 'Q'
)
SELECT b.trading_date, b.close,
       f.fiscal_period, f.publish_date, f.eps
FROM tw_stock_bars b
ASOF LEFT JOIN fq f
  ON b.symbol = f.stock_id
  AND b.trading_date >= f.publish_date
WHERE b.symbol = '2330'
  AND b.trading_date IN (DATE '2024-05-14', DATE '2024-05-15', DATE '2024-08-12')
ORDER BY b.trading_date;
```

回傳：

| trading_date | close | fiscal_period | publish_date | eps  |
|--------------|-------|---------------|--------------|------|
| 2024-05-14   | 825   | 2023Q4        | 2024-02-29   | 9.21 |
| 2024-05-15   | 839   | 2024Q1        | 2024-05-15   | 8.70 |
| 2024-08-12   | 940   | 2024Q1        | 2024-05-15   | 8.70 |

> 🔑 ASOF JOIN 的關鍵：`b.trading_date >= f.publish_date` 確保只用「當日已可知」的財報。

### 4.4 期貨分 K（bars_1m）

```sql
SELECT ts_utc, open, close, volume
FROM bars_1m
WHERE asset_class = 'tw_futures'
  AND symbol = 'MXF'
  AND session = 'day'
  AND ts_utc >= TIMESTAMPTZ '2024-01-02 00:00:00 UTC'
  AND ts_utc <  TIMESTAMPTZ '2024-01-03 00:00:00 UTC'
ORDER BY ts_utc
LIMIT 10;
```

> 📌 `ts_utc` 是 `TIMESTAMP WITH TIME ZONE`。輸入字面值務必用 `TIMESTAMPTZ '...'`，並建議以 UTC 比較。
> CLI 顯示時會自動轉為 `+08`（台北時區），這是顯示問題而非儲存問題。

### 4.5 期貨三大法人留倉趨勢

```sql
SELECT trading_date, identity, net_oi_contracts, net_oi_z60
FROM tw_inst_futures_daily
WHERE product = 'MXF' AND identity = 'fii'
ORDER BY trading_date DESC
LIMIT 5;
```

### 4.6 連續期貨 + 基差

```sql
SELECT tx.trading_date,
       tx.close AS tx_close,
       mtx.close AS mtx_close,
       tx.basis
FROM tx_continuous_d tx
JOIN mtx_continuous_d mtx USING (trading_date)
WHERE tx.trading_date >= DATE '2026-05-01'
ORDER BY tx.trading_date DESC;
```

### 4.7 個股因子（gold/stock_factor_daily）

```sql
-- 2024-12-31 動能 (12-1) 前 5 名
SELECT symbol, mom_12_1, vol_60d, ret_120d
FROM stock_factor_daily
WHERE trading_date = DATE '2024-12-31'
  AND mom_12_1 IS NOT NULL
ORDER BY mom_12_1 DESC
LIMIT 5;
```

### 4.8 Macro daily（VIX / SPX / TAIEX / USDTWD）

```sql
SELECT trading_date, symbol, close
FROM macro_daily
WHERE symbol IN ('VIX','SPX','TAIEX','USDTWD')
  AND trading_date >= DATE '2026-04-01'
ORDER BY trading_date DESC, symbol;
```

### 4.9 股票期貨日 K

```sql
SELECT trading_date, contract_id, close, volume, open_interest
FROM bars_1d
WHERE asset_class = 'tw_stock_futures'
  AND symbol = '2330'
  AND session = 'day'
  AND trading_date BETWEEN DATE '2024-12-27' AND DATE '2024-12-31'
ORDER BY trading_date DESC, contract_id;
```

---

## 5. Schema 重點（bars_1d / bars_1m）

`bars_1d` 與 `bars_1m` 採同一份 canonical schema，欄位（23 columns）：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `ts_utc` | `TIMESTAMPTZ` | UTC 時間戳；日 K 為當日收盤對應 UTC 時刻 |
| `trading_date` | `DATE` | 交易日（local calendar） |
| `asset_class` | `VARCHAR` | `tw_stock` / `tw_futures` / `tw_stock_futures` / `us_futures` / `us_index` / `fx` |
| `exchange` | `VARCHAR` | `XTAI` / `TAIFEX` / `CME` … |
| `symbol` | `VARCHAR` | 標的代碼（canonical） |
| `contract_id` | `VARCHAR` | 期貨合約月份（如 `TXFD4`），現股為 null |
| `session` | `VARCHAR` | `day` / `night` / `all` |
| OHLCV | `DOUBLE` / `BIGINT` | `open / high / low / close / volume` |
| `open_interest` | `BIGINT` | 期貨未平倉 |
| `vwap`, `settlement` | `DOUBLE` | 結算 / VWAP（非必填） |
| `adj_*` | `DOUBLE` | 調整後 OHLC，`adj_factor` 為累積調整因子 |
| `source` | `VARCHAR` | `taifex` / `tej` / `histdata` / `yahoo` |
| `ingestion_ts` | `TIMESTAMPTZ` | 入庫時間 |
| `quality_flag` | `VARCHAR` | `ok` / `imputed` / `suspicious` |

> 🔄 不同 asset_class 的分割鍵不同（tw_stock 用 `year`，tw_futures 用 `symbol+year`），
> 因此 catalog 用 `hive_partitioning=FALSE, union_by_name=TRUE` 掛 view。
> 分割欄已寫進 parquet 內部，沒有資訊損失。

---

## 6. 重建 Catalog

`catalog/quant.duckdb` 完全可重建。當 silver / gold parquet 有更新（新增、修補、新增資產類別）時：

```bash
cd /home/kevin/gs-scraper/QUANTDATA
python -m qd_ingest.common.catalog
```

腳本會：

1. 刪除並重建所有 view / macro（idempotent）
2. 跳過尚未生成的 gold 檔案（用 `if fp.exists()` 守門）
3. 結束時印出 `SHOW TABLES` 與 view 數量

驗證：

```bash
python scripts/smoke_query.py     # 11 段 end-to-end smoke test
```

---

## 7. Troubleshooting

| 現象 | 原因 | 解法 |
|------|------|------|
| `Conflicting lock is held in ... duckdb` | 另一個 process 持有寫入 lock | 關閉 CLI 或用 `read_only=True` 開 Python 連線 |
| `read_parquet`: file not found | 沒設 `file_search_path` 或 cwd 不對 | 在 query 前 `SET file_search_path='/home/kevin/gs-scraper/QUANTDATA';` |
| 個股查詢回傳重複列 | 用 `bars_1d` 沒過濾 asset_class，撞到股期同 symbol | 改用 `tw_stock_bars` view |
| ASOF join 抓到 YTD 報表而非單季 | `fundamentals_q` 同時有 `period_type='YTD'` 與 `'Q'` | 先 `WHERE period_type='Q'` 再 ASOF |
| `bars_1m` 時間範圍不對 | `ts_utc` 是 TZ-aware，與 naive timestamp 比較會失敗 | 用 `TIMESTAMPTZ '... UTC'` 字面值 |

---

## 8. 參考連結

- 整體資料架構：[`DATA_ARCHITECTURE.md`](../DATA_ARCHITECTURE.md)
- 實作進度：[`docs/progress-data-arch-impl.md`](./progress-data-arch-impl.md)
- Catalog 建構腳本：[`src/qd_ingest/common/catalog.py`](../src/qd_ingest/common/catalog.py)
- 端到端 smoke test：[`scripts/smoke_query.py`](../scripts/smoke_query.py)
- DuckDB 官方文件：<https://duckdb.org/docs/>
