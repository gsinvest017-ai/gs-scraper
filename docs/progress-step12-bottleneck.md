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
| **M1** | 本進度檔 + inspection | ⏳ |
| **M2** | gold builder `build_market_inst_aggregated()`；backlink 2 個 silver；register catalog | ⏳ |
| **M3** | `scripts/extend_stock_futures_continuous.py` from bars_1d；run | ⏳ |
| **M4** | catalog rebuild + dashboard + strict mkdocs + commit + push | ⏳ |

## Fallback

- M2 列數出乎意料：`tw_inst_stock_daily` ~6.6M / 15+ years × ~900 stocks/day → aggregated 每天 1 row × 4000 days ≈ 4K rows，理論最快不到 1s
- M3 stock futures 個數 estimate: 53 unique on 5/25 × 4 個交易日（5/22-25） + 上次最大日 4/10 之後共 ~30 交易日 ≈ 1500 new rows
- 寫壞：`gold/continuous/stock_futures_continuous_d.parquet.bak` 已備援；revert commit 即可

## 完成日誌

（M2-M4 後追加）
