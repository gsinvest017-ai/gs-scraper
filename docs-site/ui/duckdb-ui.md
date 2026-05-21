# DuckDB Web UI

DuckDB 1.5+ 內建一個 web-based explorer（在 `--ui` flag 後啟動）。它跑在 `127.0.0.1:4213`，支援：

- SQL editor + query history
- Table / view browser（schema 顯示、sample 列）
- 結果集 inline 圖表（line / bar / scatter）
- `.duckdb` 檔多視窗
- Notebook-style cell

## 啟動

```bash
~/.local/bin/duckdb -ui catalog/quant.duckdb
# 或如果只想 read-only（推薦給 explore）
~/.local/bin/duckdb -readonly -ui catalog/quant.duckdb
```

Console 會印類似：

```
Web UI started on http://127.0.0.1:4213
```

開瀏覽器到那個 URL。

## 兩個 catalog DB

| 檔案 | 用途 | 是否可寫 |
|---|---|---|
| `catalog/quant.duckdb` | 主 catalog | 可寫；daily_refresh 寫入；別開 -ui 寫鎖 |
| `catalog/quant_public.duckdb` | 同步副本 | 只讀；給 funnel / 公開展示用；rebuild_catalog 同步寫 |

平常 explore 建議用 read-only 模式打開 `quant_public.duckdb`，避免跟 daily_refresh 撞鎖。

## 寫鎖問題

DuckDB 對單檔強制 **OS-level lock**。若你開了一個 writable UI session，其他 writer 全部會被擋：

```
_duckdb.IOException: IO Error: Could not set lock on file
"catalog/quant.duckdb": Conflicting lock is held in
/home/kevin/.local/bin/duckdb (PID 1105).
```

查鎖：

```bash
fuser catalog/quant.duckdb       # 印持有鎖的 PID
lsof catalog/quant.duckdb        # 看完整 process info
```

解鎖（**先備份再殺**）：

```bash
cp catalog/quant.duckdb "catalog/quant.duckdb.bak_$(date +%Y%m%d_%H%M%S)"
kill <PID>   # 或 kill -9 若不回應
```

實務上：**永遠用 `-readonly` 模式開 UI**，就不會卡到。

## UI 端常用查詢

=== "看所有 view"

    ```sql
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'main'
    ORDER BY 1;
    ```

=== "看 view 的 schema"

    ```sql
    DESCRIBE tw_stock_bars;
    ```

=== "看某張 view 最新 5 列"

    ```sql
    SELECT * FROM tw_stock_bars
    WHERE symbol = '2330'
    ORDER BY trading_date DESC LIMIT 5;
    ```

=== "畫 TSMC 5 年收盤線"

    ```sql
    SELECT trading_date, close
    FROM tw_stock_bars
    WHERE symbol = '2330'
      AND trading_date >= DATE '2020-01-01'
    ORDER BY trading_date;
    -- 然後點結果集上方的 chart icon → Line
    ```

=== "三大法人連續買超 TSMC 超過 5 天"

    ```sql
    WITH consec AS (
        SELECT trading_date, foreign_net_lot,
               SUM(CASE WHEN foreign_net_lot > 0 THEN 0 ELSE 1 END)
                   OVER (ORDER BY trading_date) AS grp
        FROM tw_inst_stock_daily
        WHERE stock_id = '2330'
    )
    SELECT MIN(trading_date) AS streak_start,
           MAX(trading_date) AS streak_end,
           COUNT(*)          AS days
    FROM consec
    WHERE foreign_net_lot > 0
    GROUP BY grp
    HAVING COUNT(*) >= 5
    ORDER BY streak_start DESC;
    ```

## 跑長 query 卡住怎麼辦

DuckDB UI 沒有「cancel running query」UI（DuckDB 1.5.x 限制）。卡了：

1. 從 console（啟動 UI 的 terminal）按 `Ctrl-C` 一兩次
2. 不行就找 PID `kill`
3. 重開 UI

## 不支援的事

- **不支援 multi-statement transaction**（一個 cell 一個 statement）
- **不支援儲存 query**（要自己存外面，repo 內可放 `docs/queries/*.sql`）
- **不支援 user / auth**（**這是為什麼不能 expose 到 internet**；見 [Funnel 頁](funnel.md)）
- **大結果集（> 100K rows）渲染會卡** — 用 `LIMIT` 或 export 到 parquet

## 替代方案：CLI 直跑

不想用 UI 也可以一行解決：

```bash
~/.local/bin/duckdb catalog/quant.duckdb -c "SELECT COUNT(*) FROM bars_1d"

# 帶 -markdown 印漂亮表格
~/.local/bin/duckdb catalog/quant.duckdb -markdown -c "SELECT * FROM symbol_map LIMIT 5"

# Python script
.venv/bin/python -c "
import duckdb
print(duckdb.connect('catalog/quant.duckdb', read_only=True)
        .execute('SELECT trading_date, close FROM tw_stock_bars WHERE symbol=\\'2330\\' ORDER BY trading_date DESC LIMIT 5').fetchdf())
"
```
