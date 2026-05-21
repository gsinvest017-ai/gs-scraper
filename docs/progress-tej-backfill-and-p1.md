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
