# 2026-05-27 — FinMind 接 cron（bottleneck #3）

## 觸發

`/safe-yolo 陸續按照推薦排序解決還未解決的問題`（#1 derived rebuild、#2 macro scraper 已完成；本輪做 #3）

## 目標

5 個 FinMind-derived view 卡在 2026-05-22（4d INFO），因為 `bronze/finmind/finmind_*.sqlite` snapshot 沒有任何排程刷新——只有「restore view」（daily_refresh step 3.5）和「materialize gold」（step 3.7）接了 cron，**fetch 本身是手動**。本輪把 fetch + snapshot 自動化並接進 daily_refresh。

受影響的 5 個 view：

| view | tier | 依賴 |
|---|---|---|
| `finmind_stock_price_norm` | P1 | bronze sqlite |
| `finmind_price_canonical` | P1 | norm + adj_norm → gold |
| `finmind_stock_price_adj_norm` | P2 | bronze sqlite |
| `qc_stock_price_diff` | P2 | TEJ bars vs finmind_norm reconciliation |
| `qc_stock_price_diff_snapshot` | P2 | qc view → gold |

## 關鍵勘查結果

- **crawler 在 sibling repo** `/home/kevin/gs-scraper/FINMIND資料集/`，CLI：`PYTHONPATH=src .venv/bin/python -m finmind_dump run --only ... --start ...`，token 在該 repo `.env`（已驗證連線 OK，4113 檔）。
- **TaiwanStockPrice / Adj 註冊為 `per_stock`** → 增量也要 loop 3,088 檔 × 2 = ~6,176 calls ≈ 4h（@1500/hr）。**太重，不能塞進每日 17:30 cron。**
- **但 by-date bulk 可行**：實測 `client.fetch('TaiwanStockPrice', start_date='2026-05-22', end_date='2026-05-22')`（不帶 data_id）→ **單呼叫回 41,814 列（全市場）**。所以「增量 = 每個缺的交易日一次呼叫」≈ 數秒，可直接嵌入每日 cron。
- `Storage.upsert(ds, rows)` + `by_name(name)` 已處理 schema 建表 + `INSERT OR REPLACE` 去重（pk=stock_id,date），直接重用即可，不必手刻 SQL。
- live 庫 `FINMIND資料集/data/finmind.sqlite`（2.6GB，max date 2026-05-22）是 canonical store；`bronze/finmind/finmind_<DATE>.sqlite` 是每日不可變快照（cp + sha256）。
- `restore_finmind_views.py` 用 glob 取**字典序最大**（= 日期最新）的 `finmind_*.sqlite`。所以只要 fetch 寫出 `finmind_<TODAY>.sqlite`，step 3.5 自動接上。

## 設計：by-date 增量 fetcher

`scripts/fetch_finmind.py`（在 **FinMind venv** 下跑，因需要 client 的 httpx/retry/rate-limit）：

1. 解析 `FINMIND_REPO`（預設 `/home/kevin/gs-scraper/FINMIND資料集`），把 `<repo>/src` 插進 `sys.path`，import `FinMindClient` / `Storage` / `by_name`。
2. 對 live `data/finmind.sqlite` 取 `max(date)`（taiwan_stock_price）→ 增量起點 = max_date（重抓當天，靠 INSERT OR REPLACE 去重）。
3. by-date bulk（不帶 data_id）抓 `TaiwanStockPrice` + `TaiwanStockPriceAdj` 的 `[start..today]`；外加 `TaiwanStockInfo`（global，維持 universe 最新）。`Storage.upsert` 寫回 live 庫。
4. `cp` live → `bronze/finmind/finmind_<TODAY>.sqlite` + `sha256sum`。
5. GC：bronze snapshot 只留最新 5 份（含 .sha256），刪更舊的。
6. flags：`--dry-run`（印計畫、零 API、零寫入）、`--only`、`--full`（從 earliest 重抓，慎用）、`--keep N`。

**為何不改 FinMind repo 的 catalog（per_stock→global_date）**：會影響該 repo 的全史 backfill 效率（backfill 用 per_stock 較省），且跨 repo 改動風險高。增量用 by-date、backfill 用 per_stock 是正確分工。

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + 設計 + 連線驗證 | ⏳ |
| **M2** | `scripts/fetch_finmind.py` + dry-run + live 增量跑（補 2026-05-26/27） | ⏳ |
| **M3** | 接進 daily_refresh.sh（新 step，non-fatal，FinMind venv）+ docs | ⏳ |
| **M4** | rebuild catalog + restore + derived + dashboard 驗 5 view INFO→OK + commit | ⏳ |

## Fallback

- **網路/quota 擋**：fetcher 對 402/網路錯誤靠 client 內建 backoff；連續失敗 → 仍 commit 程式 + 標 stuck，不動 bronze。
- **by-date 對 Adj 不回資料**：若 `TaiwanStockPriceAdj` 不支援 no-data_id by-date，graceful skip 該 dataset 並 log；adj_norm view 維持舊 snapshot（non-fatal）。
- **寫壞 live 庫**：fetch 是 append/idempotent（INSERT OR REPLACE）；bronze 寫的是新檔，不覆蓋舊 snapshot。
- **rollback**：刪掉 `finmind_<TODAY>.sqlite`，restore_finmind 自動退回前一份快照。

## 完成日誌

（M2-M4 後追加）
