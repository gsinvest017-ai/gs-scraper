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
