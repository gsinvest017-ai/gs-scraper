# 2026-06-02 — 處理棘手 deferred items（5 個）

## 目標

上輪盤點留下 5 個 deferred；本輪逐條深入後分類：

| # | item | 之前判斷 | 探勘後實況 | 處置 |
|---|---|---|---|---|
| 1 | `選擇權日盤逐筆 TXO` | 與 finmind 重疊 | finmind 2.84M vs RAW 2.68M；RAW 多 6 欄（歷史最高/低、結算價、漲跌%、契約到期日、最後最佳買賣價）但 core OHLC 重疊 | **deferred**（複雜，價值小） |
| 2 | `institutional_clean.parquet` | 完全重疊 tw_inst_stock_daily | 確認重疊 | **skip**（確定無新資訊） |
| 3 | `三大法人 .md` | 只是 .md / .png 筆記 | ❗ 有 `institutional_yahoo_value_clean.csv` 是 cleaned 474 rows 市場層級三大法人 TWD 資料（2024-05-02 → 2026-04-16） | **M2 復活 tw_inst_market_daily** |
| 4 | `台指期一分鐘/` 巢狀錯置 | 結構不清 | 實是 TWSE market_scale 11 rows TAIEX close + turnover；非 TXF 1m bars | **skip**（macro_daily 已有 TAIEX） |
| 5 | `RS_Rating.7z` | 內容未知 | `7z l` 顯示是 RS_Rating Python 專案壓縮（含 test_venv/site-packages/）；不是資料 | **skip**（非資料） |

**淨進展**：5 → 4 個確認 skip；1 個（#3）**真實可救**。

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔（探勘 + 重新分類） |
| **M2** | 寫 `scripts/ingest_inst_market_daily.py`：`institutional_yahoo_value_clean.csv` → `silver/flows/tw_inst_market_daily/year=YYYY/`；catalog 加 view；un-retire gap_report Dataset；補進 daily_refresh |
| **M3** | dashboard regen + 進度檔收尾；mark 其餘 4 條為「永久 skip」（連帶寫 `meta/gap_comments.json` 給 deferred views） |

## TWD 三大法人 schema 對照

source CSV（16 cols）→ silver schema：

```
date                       → trading_date (date)
foreign_ex_dealer_billion  → foreign_ex_dealer_twd_bn
foreign_dealer_billion     → foreign_dealer_twd_bn       (proprietary 自營商)
foreign_total_billion      → foreign_total_twd_bn
sitc_billion               → sitc_twd_bn
dealer_self_billion        → dealer_self_twd_bn
dealer_hedge_billion       → dealer_hedge_twd_bn
dealer_total_billion       → dealer_total_twd_bn
three_inst_total_billion   → three_inst_total_twd_bn
foreign_sitc_billion       → foreign_sitc_twd_bn         (彙總 蓮花)
extra_1..4                 → 略 (TBD: 期貨 OI? — meta 沒文件)
+ source / ingestion_ts
```

## 進度日誌

### M1 — 探勘 + 重新分類  `(M1 commit)`

逐條探勘後改寫之前的判斷。最大發現：`institutional_yahoo_value_clean.csv`
是真實可用的市場層級三大法人 TWD 資料，**之前我們誤判它跟 institutional_clean
（per-stock lots）一樣**。實際 schema 完全不同。

### M2 — 復活 tw_inst_market_daily  `9e96a42`

- `scripts/ingest_inst_market_daily.py`：CSV → silver/flows/tw_inst_market_daily/
  hive year=YYYY；rename 9 個欄為 `<entity>_twd_bn`
- catalog 重建 swap：view 從 15 dead rows → **474 live rows**
- gap_report.DATASETS 復活該條
- daily_refresh step 3.59 串接
- meta/gap_comments.json 補註解

### M3 — 收尾 + 4 條永久 skip 文件化

下列 4 條經探勘確認**永遠不該再花時間**：

| item | 為什麼永久 skip |
|---|---|
| `選擇權日盤逐筆 TXO.parquet` | finmind_txo_option_daily 已涵蓋 core OHLC（2.84M rows）。RAW 多 6 欄歷史最高低 / 結算 / 漲跌% 但屬於 nice-to-have，對主流 IV/factor 計算無影響。要做的話需寫 chinese→english header mapper + 合併策略，工時>價值。 |
| `institutional_clean.parquet`（外資投信） | tw_inst_stock_daily（6.6M rows, 2010-2026）完整涵蓋；RAW 只到 2026-04-13、3.8M rows。無新資訊。 |
| `台指期一分鐘/三大法人買賣超/twse_market_scale/` | 11 rows TAIEX close + turnover（2026-01-02 ~ 01-16）。macro_daily 早就有 TAIEX 1966 rows 從 2018 起。目錄名 misleading（巢狀錯置）。 |
| `RS_Rating.7z`（287 MB） | `7z l` 顯示是 RS_Rating Python 專案壓縮（含 test_venv/site-packages/colorama/）。**不是資料**，是 venv 備份。 |

進度總計（含本輪 + 過往兩輪）：

| 階段 | OK | WARN | STALE | EMPTY | INFO | 備註 |
|---|---|---|---|---|---|---|
| 起點（兩輪前 dashboard） | 19 | 9 | 7 | 1 | 10 | — |
| 解 lock + 跑 derived | 31 | 4 | 7 | 1 | 3 | 12 derived 解鎖 |
| 修 cross_market + 退役 inst_market | 31 | 4 | 5 | 0 | 4 | EMPTY 清空 |
| 加 rf_daily + txo_1min | 32 | 4 | 6 | 0 | 4 | 新 view 進 STALE |
| **本輪復活 tw_inst_market_daily** | **32** | **4** | **7** | **0** | **4** | view 復活進 STALE |

STALE 7 條 = TX/MTX/個股期/MXF/MXF_daily_summary/txo_1min/tw_inst_market_daily，
**全是 RAW 自身 lag**，cron 隔日自動 propagate 已 wire 好。EMPTY 0，無遺漏資料。

## 下一輪建議

- **txo_1min → gold**：intraday vol cone / IV time-of-day pattern；補進 derived.py
- **rf_daily 串進 BS-IV**：取代 `_TXO_RF=0.015` 寫死
- **tw_inst_market_daily 衍生 gold**：z-score / 60d rolling 等 factor
- **`選擇權日盤逐筆 TXO` enrichment**（**只在有空時**）：抓 6 個 finmind 沒有的欄合併進 finmind_txo_option_daily

## Fallback

```bash
git revert HEAD~3..HEAD
rm -f scripts/ingest_inst_market_daily.py
```
