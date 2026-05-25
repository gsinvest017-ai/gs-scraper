# 2026-05-25 增量爬取 — 優先順序: 價格 → 量 → 籌碼 → tick

> 啟動：2026-05-25
> 觸發：`/safe-yolo 請幫我根據目前gap_dashboard.html目前還有哪些資料沒有爬取...`
> 約束：嚴格優先順序 — 高 prio 跑完才能跑低 prio。tick 在「價格日資料」完成後才開始。

---

## 起始快照（today=2026-05-25, 已 regen json）

26 個 dataset，按用戶定義的優先順序歸類：

### 🥇 價格（priority 1）

| view | sev | tier | lag | max_date | 來源 |
|---|---|---|---:|---|---|
| `tw_stock_bars` | WARN | P0 | 3d | 2026-05-22 | TEJ stock_daily ✅ 可增量 |
| `bars_1d` | WARN | P0 | 3d | 2026-05-22 | TEJ futures_daily 等 ✅ 可增量 |
| `mtx_continuous_d` | STALE | P1 | 17d | 2026-05-08 | RAW_SOURCES/日k 期貨tquant lab/ — 手動 ⛔ |
| `tx_continuous_d` | STALE | P1 | 17d | 2026-05-08 | RAW_SOURCES/日k 期貨tquant lab/ — 手動 ⛔ |
| `stock_futures_continuous_d` | STALE | P2 | 45d | 2026-04-10 | RAW_SOURCES/股票期貨/ — 手動 ⛔ |
| `bars_1m` | STALE | P2 | 74d | 2026-03-12 | RAW_SOURCES/MXF_1m/ — 手動 ⛔ |
| `txo_daily_features` | STALE | P2 | 54d | 2026-04-01 | 衍生自 tick parquet ⛔ |

> 重點：今日是週一，weekend 0 trading day。週五 (2026-05-22) 已在 silver。`--append-since-silver` 會嘗試 2026-05-23+；若 TEJ EOD 已落 2026-05-25 才會有資料。

### 🥈 量（priority 2）

OHLCV 內含 `volume` 欄；**沒有獨立的量資料表**。M2 完成 = 量也完成。

### 🥉 籌碼（priority 3）

| view | sev | tier | lag | max_date | 來源 |
|---|---|---|---:|---|---|
| `tw_inst_stock_daily` | WARN | P0 | 3d | 2026-05-22 | TEJ inst_stock ✅ |
| `tw_margin_daily` | WARN | P0 | 3d | 2026-05-22 | TEJ margin ✅ |
| `tw_chip_dist_daily` | STALE | P1 | 10d | 2026-05-15 | TEJ chip_dist ✅ |
| `tw_inst_futures_full_daily` | WARN | P1 | 3d | 2026-05-22 | TEJ inst_futures_full ✅ |
| `tw_futures_large_trader_daily` | WARN | P0 | 3d | 2026-05-22 | TEJ futures_large_trader ✅ |
| `tw_inst_futures_daily` | STALE | P0 | 17d | 2026-05-08 | TAIFEX scraper 沒安裝 ⛔ |
| `tw_inst_market_daily` | STALE | P2 | 39d | 2026-04-16 | aggregation derived ⛔ |

### 4️⃣ Tick — 在 M2 (價格) 完成後開始

QUANTDATA repo **沒有 tick crawler**。可用來源盤點：

| 來源 | 覆蓋 | 狀態 |
|---|---|---|
| `RAW_SOURCES/選擇權日盤逐筆原始資料_TXO.parquet/` | TXO 2020-03-02 ~ 2026-04-01 | 靜態檔，需手動更新 |
| FinMind `taiwan_stock_price_tick` (bronze sqlite) | 2026-05-14 只 296/2721 檔 / 0.01% | crawler 在另一個 repo `/home/kevin/gs-scraper/FINMIND資料集/`，**未安裝** |
| TEJ API tick | (`fetch_tej.py` 的 `--table` 沒這個選項) | 訂閱可能有，但 fetcher 沒寫 |

M4 會把 tick 狀態 + 後續路徑寫進進度檔，**不會實際開抓**（沒 crawler）。

### Out-of-scope（手動 / derived）

`mtx_continuous_d` / `tx_continuous_d` / `stock_futures_continuous_d` / `bars_1m` / `txo_daily_features` / `tw_inst_futures_daily` / `tw_inst_market_daily` / `stock_factor_daily` / `cross_market_features` / `macro_daily` / `finmind_*`（finmind 是 snapshot view，不是 crawl 對象）。

### 副發現：finmind_* 與 qc_stock_price_diff view 不見了

`information_schema.tables` 沒有任何 `finmind_*` 或 `qc_*` view。原因：daily_refresh 跑 `qd-ingest build-catalog` 時不知道這幾個 view，重建時被砍。M4 順手復原。

sqlite 本檔仍在：`bronze/finmind/finmind_2026-05-18.sqlite` (2.5 GB, sha256 in sidecar)。

---

## Milestone

| Mn | 範圍 | 狀態 |
|---|---|---|
| **M1** | 寫此 doc + 起始 snapshot | ✅ |
| **M2** | 🥇 價格：fetch_tej `stock_daily` + `futures_daily` | ⏳ |
| **M3** | 🥉 籌碼：fetch_tej `inst_stock` + `margin` + `chip_dist` + `inst_futures_full` + `futures_large_trader` | ⏳ |
| **M4** | 其他 TEJ STALE（revenue_monthly P0! / accounting_raw / stock_trading_attrs / cash_dividend / stock_futures_corp_actions / security_attrs）+ catalog rebuild + 還原 finmind_* + qc view | ⏳ |
| **M5** | tick 盤點與後續路徑 + gap_report 重生 + push | ⏳ |

每個 milestone 一個 commit。

---

## 進度日誌

### M1 — snapshot

26 個 dataset 分類完，鎖定 M2-M3 真的能 crawl 的 7 個 TEJ table；4 個 manual-source / derived 不在範圍內已標 ⛔；FinMind / TXO tick 沒 crawler 已留 M4 探討。順手抓到副 issue：daily_refresh 上次跑 `qd-ingest build-catalog` 砍掉 finmind_* views（sqlite 本檔還在）。
### M2 — pending
### M3 — pending
### M4 — pending
### M5 — pending

---

## Fallback

若爬取中斷：
- 已 ingest 的進 silver 就還在；重跑 `--append-since-silver` 是 idempotent，不會 double-write
- 看哪一步斷了：`tail meta/audit/daily_refresh_*.log` 或 `cat meta/audit/ingest_*.jsonl`
- 跨日 retry：直接重跑 `bash scripts/daily_refresh.sh`，或單獨 `fetch_tej.py --table <x> --append-since-silver`
