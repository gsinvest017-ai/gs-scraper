# Safe-YOLO: P0 補完 — 期貨日 K、大額交易人、月營收

> 啟動：2026-05-18
> 觸發：`/safe-yolo 爬完P0`
> 接續：`progress-fetch-tej-rewrite.md`（已建好 fetch_tej.py 三 logical table；本任務再加三條 logical table）

## 目標

把 gap analysis 找到的三條 P0 資料補進 DuckDB：

1. **`TWN/AFUTR`** → 擴張 `silver/bars/bars_1d` (asset_class=tw_futures)：補大台 (TXF)、台 50 期、商品期等所有期貨日 K（目前只有 MXF 1 個 symbol）
2. **`TWN/AFUTRHU`** → 新增 `silver/flows/tw_futures_large_trader_daily`：期貨大額交易人前 5 / 前 10 大未沖銷部位
3. **`TWN/APISALE`** → 新增 `silver/fundamentals/revenue_monthly`：月營收 + YoY / MoM 成長率

完成後 DuckDB catalog 多 3 個（或擴張現有）view、smoke test 全綠。

## 起始狀態

- venv 已裝 tejapi 0.1.31
- `TEJAPI_KEY` 已在 fish universal var
- `scripts/fetch_tej.py` 目前支援 3 個 logical table：stock_daily / inst_stock / margin（全用 API-flavored 表）
- silver 最新：個股 2026-05-18、期貨（MXF only）2026-03-12、財報 2026Q1
- DuckDB UI（PID 18348）開著 — catalog rebuild 要走 staging

## Milestone 計畫

| M | 目標 | 預期產出 |
|---|---|---|
| M1 | 摸 3 張 TEJ 表 schema + 小量試打 + 寫對應到 silver 的 mapping | 進度檔表格；commit |
| M2 | `scripts/fetch_tej.py` 加 3 個 logical table；`src/qd_ingest/sources/tej.py` (或新 `tw_futures_tej.py`) 加 3 個 ingester；`paths.py` 加新路徑；`catalog.py` 加 view 定義 | 程式碼；commit |
| M3 | 全 history fetch + ingest 進 silver（先驗證 AFUTR/AFUTRHU 涵蓋哪幾年；APISALE 從 2022 起） | silver/ 新增資料；commit |
| M4 | Rebuild catalog（staging due to UI lock）+ smoke test 驗證新 view 都可查 | catalog/quant_new.duckdb + smoke PASS；commit |

## 進度日誌

### M4 — Catalog 重建 + smoke test PASS

DuckDB UI PID 18348 仍持鎖，build 到 `catalog/quant_p0.duckdb` staging。

新 view 全部出現：`revenue_monthly`、`tw_futures_large_trader_daily`。`bars_1d` 也自然擴張到 56 個新期貨 symbol。

Smoke 抽樣：

| view | 範圍 | 行數 | 樣本 |
|---|---|---|---|
| `bars_1d tw_futures TX` | 2020-01-02 → 2020-12-31 | 1,470 | TX202001 open=12044 close=12102 vol=100401 oi=87608 ✓ |
| `bars_1d tw_futures MXF` | 2020-03-02 → 2026-03-12 | 1,523 | 沒被 AFUTR 覆寫（skip-if-exists 起作用） ✓ |
| `revenue_monthly` 2330 2024 | 2024-01..12 | 12 | 215.8B → 278.2B；YoY 7.87% → 57.78% ✓ |
| `tw_futures_large_trader_daily` | 2026-04-07 → 2026-05-18 | 29,940 | 僅 ~6 週覆蓋，TEJ 30K row 上限截斷 |

使用者 swap 步驟：
```bash
kill 18348
mv catalog/quant.duckdb catalog/quant.duckdb.bak3
mv catalog/quant_p0.duckdb catalog/quant.duckdb
scripts/smoke_query.py
```

### M3 — Fetch 結果（部分成功、TEJ 限流為主要阻擋）

**成功**：

- `APISALE` 2013-2026 → 95,061 rows，5 個 year partition 完整覆蓋（單次 fetch 拿到所有歷史）
- `AFUTRHU` 2020-2026 → 29,940 rows，但 **TEJ 單次 API call 約 30K row 上限**，這份只覆蓋 2026-04-07 → 2026-05-18 ~6 週
- `AFUTR` 2020 → 54,105 rows / 56 個 non-MXF 期貨 symbol（TX/MTX/TE/TF/E4F/G2F/MSCI/TWNF…），每 symbol ~1,470 rows ≈ 完整 250 個交易日 × 6 個月合約

**卡關**：

- 第一次 `AFUTR 2020-2026` 單 call ⇒ TEJ `LimitExceededError`（**rows-per-call 上限**）。
- 改 year-by-year 後，2020 跑了 12 分鐘成功；**2021 跑了 30+ 分鐘 TCP 連線靜默 44 秒、4KB 累計傳輸後 hang**。Python alive，TEJ 連線 ESTAB 但無資料。判定 TEJ **per-key request rate limit**，需要在 paginate page 間加 sleep / exponential backoff。
- 終止 stuck process，silver 留下 AFUTR 2020 + AFUTRHU 2026-04 + APISALE 2022-2026。

**未完成**：AFUTR 2021-2026（6 年）、AFUTRHU 2020-2026 backfill。

**後續修法（不在本次 /safe-yolo 範圍）**：

1. `tejapi` 沒內建 rate-limit 退避；在 `_tej_get` 包 retry-with-backoff（catch `LimitExceededError`、sleep 60s+、重試）
2. 改用 `coid` 縮小 query：例如先取 product list 再逐個 fetch（FXF / EXF / GTF…）— 每 call 更小、不容易撞上限
3. 用 `gs-zipline-tej` 的 `morning_txf` bundle 走 `zipline ingest -b morning_txf`，看它的限流邏輯 — 可能已內建退避
4. AFUTRHU 同上策略

### M2 — fetch_tej.py 擴張 + catalog 新增 view

- `scripts/fetch_tej.py`:
  - `LOGICAL_TABLES` 從 3 個擴到 6 個（多 `futures_daily / futures_large_trader / revenue_monthly`）
  - 3 個 adapter functions（API 中文 cols → canonical 英文 silver schema）
  - 3 個 write_silver_* functions：跳過 RAW-CSV 中間層、直接寫 silver parquet partitioned by year（這 3 個是 TEJ-API-only，沒有 vendor file 概念）
  - AFUTR 內建 `^\d{4}$` underlying_id filter，避免和 `tw_stock_futures` 雙寫
  - AFUTR 對 MXF symbol 做 skip-if-exists 檢查（避免和 mxf_clean 來源衝突）
  - `_silver_max_date` 新增三個 view 對應，`--append-since-silver` 自動可用
  - `fetch()` orchestrator 加 3 個 dispatch 分支，print 都加 `flush=True`（修上次 9 分鐘黑箱問題）

- `src/qd_ingest/common/catalog.py`:
  - silver/flows 迴圈加 `tw_futures_large_trader_daily`
  - silver/fundamentals 新增 `revenue_monthly` view

- 不需要新 CLI subcommand：直接 `python scripts/fetch_tej.py --table futures_daily` 即可。
- 不需要新 ingester module：fetch_tej.py 直接 adapter + parquet write 一步搞定。
- 語法檢查通過、`--help` 正確列出 6 個 choices。

### M1 — 三張 TEJ 表 schema + mapping

**TWN/AFUTR**（24 欄、per-contract per-day、2008 起）：

| TEJ 中文 | 對應 bars_1d 欄 | 註 |
|---|---|---|
| `期貨名稱` | `contract_id`（如 TXFE6） | 完整月份契約代碼 |
| 抽前 3 字 | `symbol` | 商品根（TXF / MXF / BRF...） |
| `日期` | `trading_date` + `ts_utc` | TZ 加 Asia/Taipei 13:45 收盤 |
| OHLC | `open/high/low/close` | |
| `成交張數(量)` | `volume` | 1 張 = 1 口 |
| `未平倉合約數` | `open_interest` | |
| `每日結算價` | `settlement` | |
| `標的證券價格` (wclose_d) | `vwap`（暫存於此） | |
| 標的證券碼 `\d{4}$` | **過濾條件** | 4 位數股票代號是個股期，已在 tw_stock_futures，跳過避免重複 |

→ asset_class=`tw_futures`、exchange=`TAIFEX`、session=`day`、adj_* NaN（期貨無除權息）

**TWN/AFUTRHU**（20 欄、per-contract per-day、2008 起）：

| TEJ 中文 | 對應新 silver col |
|---|---|
| 期貨名稱 | `contract_id` |
| 抽前 3 字 | `product` |
| 日期 | `trading_date` |
| 到期月 | `expiry_month` |
| 全市場未沖銷部位 | `total_oi` |
| 前五大買方未沖銷部位-交易人 | `top5_buy_traders` |
| 前五大賣方未沖銷部位-交易人 | `top5_sell_traders` |
| 前十大買方未沖銷部位-交易人 | `top10_buy_traders` |
| 前十大賣方未沖銷部位-交易人 | `top10_sell_traders` |
| %-交易人 | `*_traders_pct` |
| -特定法人 | `*_institutional` |
| %-特定法人 | `*_institutional_pct` |

→ silver/flows/`tw_futures_large_trader_daily`/year=YYYY/

**TWN/APISALE**（33 欄、per-stock per-month、2013 起）：

| TEJ 中文 | 對應新 silver col |
|---|---|
| 公司 | `stock_id` |
| 年月 | `fiscal_month` (DATE) |
| 營收發布日 | `publish_date` (point-in-time) |
| 單月營收(千元) | `revenue_monthly_ktwd` |
| 去年單月營收(千元) | `revenue_yoy_ktwd` |
| 單月營收成長率％ | `revenue_yoy_growth_pct` |
| 單月營收與上月比％ | `revenue_mom_growth_pct` |
| 累計營收(千元) | `revenue_cum_ktwd` |
| 累計營收成長率％ | `revenue_cum_yoy_growth_pct` |
| 近12月累計營收(千元) | `revenue_ttm_ktwd` |
| 近12月累計營收成長率％ | `revenue_ttm_growth_pct` |
| 近 3月累計營收(千元) | `revenue_3m_ktwd` |
| 近3月累計營收成長率％ | `revenue_3m_growth_pct` |
| 流通在外股數(千股) | `shares_outstanding_kshare` |
| 單月每股營收(元) | `revenue_monthly_per_share` |
| 累計/近12月/近3月 每股營收 | `revenue_{cum,ttm,3m}_per_share` |

歷史最高/最低、創新高/低 flag 等衍生欄位略過（可從 base 重算）

→ silver/fundamentals/`revenue_monthly`/year=YYYY/

**容量估算**：
- AFUTR：~2,400 contracts/day × ~250 days × ~18 years ≈ 10.8M rows
- AFUTRHU：~1,000 contracts/day × ~250 days × ~18 years ≈ 4.5M rows
- APISALE：~2,000 stocks × 12 months × 13 years ≈ 312K rows

## Fallback 指引

```bash
cd /home/kevin/gs-scraper/QUANTDATA
git log --oneline -15
git reset --hard <hash-before-M2>           # 回到沒擴張 fetch_tej 前

# silver 新資料若要清掉
rm -rf silver/bars/bars_1d/asset_class=tw_futures/symbol=TXF
rm -rf silver/flows/tw_futures_large_trader_daily
rm -rf silver/fundamentals/revenue_monthly

# catalog 若 swap 後想 rollback：
mv catalog/quant.duckdb catalog/quant_post_p0.duckdb
mv catalog/quant.duckdb.bak2 catalog/quant.duckdb     # 之前的好版
```
