# Safe-YOLO: 補完 P0 + 推進 P1 / P2 TEJ 資料

> 啟動：2026-05-21
> 觸發：`/safe-yolo 按照你的優先順序先爬取1 沒有被rate-limit再繼續爬取2然後是3`
> 接續：`progress-p0-fetch.md`（P0 因 TEJ rate-limit 卡在 AFUTR 2020 only + AFUTRHU 6 週）

## 目標

按使用者指定的優先順序推進：

1. **解 rate-limit + backfill P0 partial**：給 `_tej_get` 加 timeout + retry-with-backoff，重跑 AFUTR 2021-2026 + AFUTRHU 2008-2026
2. **P1 資料**（在 rate-limit 仍可控時繼續）：
   - `TWN/APISHRACTW` — 集保庫存（千張大戶）
   - `TWN/ADIV` + `TWN/APIDV1` — 現金股利 + 公告
   - `TWN/AFUTRSTK` — 個股期貨除權息
   - `TWN/AFINST` — 期交所三大法人 2005-2023 backfill（補 silver 缺口）
3. **P2 資料**（仍餘力時）：
   - `TWN/AINVFINB` — 會計師簽證 118 科目
   - `TWN/EWISAMPLE` — 指數成分股
   - `TWN/APISTOCK` + `TWN/APISTKATTR` — 證券屬性

## 起始狀態

- 訂閱：TQ高手過招-期貨+TQ初入江湖-個股，2026-05-06 → 2027-05-06
- silver max dates（最後一次確認 2026-05-21）：
  - tw_stock_bars: 2010-01-04 → 2026-05-18
  - tw_inst_stock_daily / tw_margin_daily: → 2026-05-15
  - bars_1d tw_futures: **2020 only (55,628 rows, 57 symbols)** + MXF 2020-2026
  - tw_futures_large_trader_daily: **2026-04-07 → 2026-05-18 ~6 週**
  - revenue_monthly: 2022-01 → 2026-04
- TEJ rate-limit 觀察到的兩種失敗模式：
  - `LimitExceededError`（單次 row 過多，~30K 上限）
  - **TCP ESTAB 但連線靜默 44+ 秒 hang**（rate-limit 沒回 error，直接 stop responding）

## Milestone 計畫

| M | 目標 | 預期產出 |
|---|---|---|
| M1 | 寫 `_tej_get_resilient`：signal timeout + exponential backoff + chunked date range | `fetch_tej.py` 改 helper，小範圍 dry-test 通過；commit |
| M2 | 用 chunked fetcher 補 AFUTR 2021-2026 + AFUTRHU 2008-2026 | silver 對應 view 範圍延伸；commit |
| M3 | P1 datasets：APISHRACTW / ADIV / AFUTRSTK / AFINST backfill | 4 個新 logical table + silver paths + catalog views；commit |
| M4 | P2 datasets：AINVFINB / EWISAMPLE / APISTOCK / APISTKATTR | 4 個新 view；commit |
| M5 | Rebuild catalog + smoke test 全套 | catalog/quant.duckdb 上線 + smoke PASS；commit |

## 進度日誌

### M2 — AFUTR + AFUTRHU backfill (resilient fetcher 實戰驗證)

| 表 | before | after | 細節 |
|---|---|---|---|
| AFUTR (bars_1d tw_futures) | 55,628 rows / 57 sym / 2020 only | **449,736 rows / 120 sym / 2020-2026** | 220 chunks × 10 天，30 分鐘完成、無 hang |
| AFUTRHU | 29,940 rows / 2026-04 → 05 | **32,016 rows / ~2024-04 onwards** | 228 chunks × 30 天，11 分鐘完成 |

**重大發現**：AFUTRHU 在 TEJ 上**只有 2024 後才有資料**。chunks 1-200 (covering 2008-2024) 全部 0 rows，到 chunk 224 (2026-04) 才開始有 14K rows。subscription user info 雖然標 `dataStartYear=2005`，實際上 TEJ 對外只暴露最近兩年。已知限制，無解。

Resilient fetcher 在 ~450 個 API 呼叫中無一次 hang / no retry triggered — 比之前 unchunked 模式穩健多了。

### M3 (code only) — P1 datasets schema + adapters

`scripts/fetch_tej.py` 加 4 個新 logical table：

| logical | TEJ 表 | silver 路徑 | schema |
|---|---|---|---|
| `chip_dist` | TWN/APISHRACTW | `silver/flows/tw_chip_dist_daily/` | 27 cols：集保庫存、6 級距 × (人數/張數/占比) + over_400 合計 |
| `cash_dividend` | TWN/ADIV | `silver/fundamentals/cash_dividend_events/` | 21 cols：除息日 / 股利 / 股息總額 / 發放日 / 除息參考價 |
| `stock_futures_corp_actions` | TWN/AFUTRSTK | `silver/flows/tw_stock_futures_corp_actions/` | 14 cols：契約調整因 / 每口折算股數 / cash adjust YN |
| `inst_futures_full` | TWN/AFINST | `silver/flows/tw_inst_futures_full_daily/` | 24 cols：每身份×每商品 多空交易/未平倉 口數+金額（vs 既有 `tw_inst_futures_daily` 是 SUPPLEMENT scraper 來源、欄位較少） |

`catalog.py`:
- silver/flows 迴圈加 3 個新 view
- silver/fundamentals 加 `cash_dividend_events`

Dispatcher：`chip_dist` 用 60 天 chunk、`cash_dividend` 用 365 天 chunk、`stock_futures_corp_actions` 單 shot、`inst_futures_full` 用 60 天 chunk。

驗證：syntax check 過、AFINST live 1-day fetch 114 raw → 114 silver rows，所有欄位映射正確（11=自營商 long_volume=179467 等）。

### M1 — resilient fetcher (timeout + backoff + chunked)

新增 3 個 helper：

- `_tej_get_with_timeout(dataset, timeout_sec=120, ...)` — SIGALRM 包 `tejapi.get`，捕獲 TCP hang（rate-limit 不會給 exception、直接靜默）
- `_tej_get_resilient(...)` — 上層加 exponential backoff（60/120/240/480/960 s，最多 5 次），LimitExceededError 直接 raise 不重試
- `_tej_get_chunked(dataset, start, end, chunk_days=10, ...)` — 切日期視窗 fetch、遇 LimitExceeded 自動 halve chunk 遞迴重試

fetch() 把 `futures_daily` 改用 `chunk_days=10`、`futures_large_trader` 用 `chunk_days=30`、`revenue_monthly` 用 resilient（不切 chunk，APISALE 上次單擊 14 年 OK）。

驗證 dry-test：`AFUTR 2026-01-01..20` 兩個 chunk 各 14.3K + 16.7K rows，無 hang 無錯誤。

## Fallback 指引

```bash
cd /home/kevin/gs-scraper/QUANTDATA
git log --oneline -20
git reset --hard <hash-before-M1>

# 清新 silver 路徑（不會碰已有資料）
rm -rf silver/flows/tw_chip_dist_daily             # APISHRACTW
rm -rf silver/fundamentals/cash_dividend_daily     # ADIV
rm -rf silver/flows/tw_stock_futures_corp_actions  # AFUTRSTK
rm -rf silver/reference/index_constituents         # EWISAMPLE
# etc

# 取消 AFINST 的 2005-2023 backfill（只刪那段範圍）
# 透過重新 ingest 既有的 TAIFEX SUPPLEMENT parquet
.venv/bin/python -m qd_ingest.cli taifex-inst --parquet ../RAW_SOURCES/SUPPLEMENT/TAIFEX/foreign_oi_daily.parquet
```
