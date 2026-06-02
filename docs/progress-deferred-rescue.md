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

## Fallback

```bash
git revert HEAD~2..HEAD
rm -f scripts/ingest_inst_market_daily.py
```
