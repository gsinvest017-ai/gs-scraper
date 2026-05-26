# 2026-05-26 — Step 1+2 of bottleneck fix（trivial 2 wins）

## 觸發

`/safe-yolo 做 step 1 + 2`（接續之前的 bottleneck 分析報告）

## 目標

兩個 trivial 修補，把 `OK=28 / STALE=11` 推到 `OK ≈ 32 / STALE ≈ 7`：

**Step 1** — `tw_inst_market_daily` 改從 `tw_inst_stock_daily` 衍生  
**Step 2** — `stock_futures_continuous_d` 從 `bars_1d` 衍生（複製 `extend_continuous.py` 邏輯）

## 上游 inspection 結果

### Step 1 — tw_inst_market_daily

- **silver schema**: `(trading_date, identity, buy_twd, sell_twd, net_twd)`；只有 15 列（4/14-4/16，5 個 identities）；source=twse
- **`tw_inst_stock_daily`**：max 5/25，has `foreign_net_lot / sitc_net_lot / dealer_net_lot / total_net_lot / foreign_hold_pct / sitc_hold_pct / dealer_hold_pct`，但**單位是 lot 不是 TWD**
- **Decision**: 不修 silver schema（保留原 TWSE-direct 拉的 view 不動），改寫新 gold `market_inst_aggregated.parquet`：per trading_date 加總 `tw_inst_stock_daily` 所有 stock 的 net_lot + hold_pct (weighted avg)。把 gold 同時 backlink 到 `tw_inst_stock_daily` 與 `tw_inst_market_daily` 兩個 silver。

### Step 2 — stock_futures_continuous_d

- **gold parquet schema**: 24 cols (futures_code, delivery_month, OHLCV, OI, settlement, underlying_code, name, is_rollover, daily_return, ...)
- **`bars_1d` 涵蓋**:
  - `asset_class='tw_stock_futures'` (TAIFEX 後盤跡): max **4/13**, 3.4M 列
  - `asset_class='tw_futures'`: max **5/25**, 555K 列。內含個股期 codes（CAF/CBF/CDF/QFF 等）以及指數期（TX/MTX/TE/TF/GTF）
- **Decision**: 用 `bars_1d.tw_futures` 過濾掉指數期 + 商品期（symbol 在 `['MTX','TXF','TEF','TFF','GTF','MSF','M1F','XIF','TGF','SXF','I5F','TMF','RHF','SOF','ZBT','ZSQ','ZOK','ZEF','ZTE','ZUR','ZZE','OAF','URF','SIF','SQF','ZFF','RIF','USF','F1F','XAF','BTF','RTF','E4F','SHF','SMF']`），剩下的就是個股期。Append 到 gold parquet，源於 bars_1d 的列填 `source='qd_stock_futures_continuous_extended_from_bars1d'`、`is_rollover=NULL`、`underlying_code=NULL`、`name=NULL`（沒有清楚 mapping）。

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + inspection | ✅ |
| **M2** | gold builder `build_market_inst_aggregated()`；backlink 2 個 silver；register catalog | ✅ |
| **M3** | `scripts/extend_stock_futures_continuous.py` from bars_1d；run | ✅ |
| **M4** | catalog rebuild + dashboard + strict mkdocs + commit + push | ✅ |

## Fallback

- M2 列數出乎意料：`tw_inst_stock_daily` ~6.6M / 15+ years × ~900 stocks/day → aggregated 每天 1 row × 4000 days ≈ 4K rows，理論最快不到 1s
- M3 stock futures 個數 estimate: 53 unique on 5/25 × 4 個交易日（5/22-25） + 上次最大日 4/10 之後共 ~30 交易日 ≈ 1500 new rows
- 寫壞：`gold/continuous/stock_futures_continuous_d.parquet.bak` 已備援；revert commit 即可

## 完成日誌

### M2 — `build_market_inst_aggregated()`

加進 `src/qd_ingest/sources/derived.py`：
- 從 `silver/flows/tw_inst_stock_daily/year=*/*.parquet` 跑 `GROUP BY trading_date` 聚合
- 列數：4,015 days（2010-2026）；max_date **2026-05-25**（cron 18:10 跑完 silver 是 5/26 但這次 builder 跑時 silver 還是 5/25）
- elapsed: 6.4 sec
- 14 個欄位：foreign/sitc/dealer × {net,buy,sell}_lot + total_net + 3 個 hold_pct mean + stocks_count

### M3 — `extend_stock_futures_continuous.py`

新增 `scripts/extend_stock_futures_continuous.py`，類似 `extend_continuous.py` 但 for 個股期：
- 從 `bars_1d` 過濾 `asset_class='tw_futures' AND length(symbol)=3 AND symbol NOT IN (38 個指數/商品期 codes)`
- 每 (futures_code, trading_date) 取 max(volume) front contract
- 加 **11,093 列 / 70 個 futures_codes**；max_date **2026-04-10 → 2026-05-26**！

注意：cron 在 17:30 跑完後 bars_1d.tw_futures max 已經到 5/26，所以這批 extension 自動把今天的資料也拉進來。

新列 source='qd_stock_futures_continuous_extended_from_bars1d'，`underlying_code`/`name`/`is_rollover`/`hist_high`/`hist_low` 為 NULL（沒有 clean source mapping），不影響大多數使用情境。

### M4 — registry + catalog + dashboard

`scripts/gap_report.py`：
- `tw_inst_stock_daily.gold_paths` += `gold/features/market_inst_aggregated.parquet`
- `tw_inst_market_daily.gold_paths` += `gold/features/market_inst_aggregated.parquet`
- 新增 `Dataset("market_inst_aggregated", "trading_date", "daily-trading", ..., P1)`

`src/qd_ingest/common/catalog.py`：註冊 `market_inst_aggregated` view。

Dashboard 變化：

| 指標 | M3 末 | M4 末 |
|---|---|---|
| OK | 28 | **30** (+2) |
| WARN | 0 | 0 |
| STALE | 11 | **10** (-1，stock_futures_continuous_d 從 STALE 4/10 → OK 5/26) |
| INFO | 5 | 5 |
| EMPTY | 1 | 1 |
| Total | 45 | **46** (+1 market_inst_aggregated) |

**核心成果**：
- 1 個 view 從 STALE → OK（`stock_futures_continuous_d`，自動拉到今天的資料）
- 1 個新 P1 gold view 從零出現（`market_inst_aggregated`，4015 天從 2010 開始）
- `tw_inst_market_daily` silver 仍 STALE 4/16（TWSE 原始來源不變），但 gold backlink 多了個 fresh 的 `market_inst_aggregated`

剩下 10 STALE 都是 Group 1（沒寫 scraper）：tw_inst_futures_daily (TAIFEX scraper)、macro_daily (yfinance)、bars_1m (manual 1m)、txo_daily_features (TXO tick)、tw_inst_market_daily (TWSE 原版)，加上他們對應的 5 個 gold snapshot 鏡像。需要花較大工程量寫 scraper 才能消，留給後續批次。

## Live

<https://gsinvest017-ai.github.io/gs-scraper/gap_dashboard.html>
