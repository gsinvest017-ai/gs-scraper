# 2026-05-25 延伸爬取 — 6 個指定 dataset

> 啟動：2026-05-25
> 觸發：`/safe-yolo TX 連續期 / MTX 連續期 / bars_1m MXF / 個股期連續近月 / bars_1d 期貨 / FinMind 個股日 K / FinMind 還原權息日 K`
> 衍生自：`progress-incremental-crawl-2026-05-25.md`（前一輪 P0+P1+P2 + tick crawler 啟動）

---

## 評估 — 哪些今天能 100%、哪些不行

| 指定 dataset | 現況 | 來源 | 今天能 100% 嗎？ | 預估量 / 成本 |
|---|---|---|---|---|
| `tx_continuous_d` (TX 連續期) | STALE 17d, max 2026-05-08 | `RAW_SOURCES/日k 期貨tquant lab/TX_continuous_*.parquet`，手動檔；underlying 在 bars_1d.tw_futures 已到 2026-05-22 | **YES — 用 bars_1d 直接重建近月連續** | 10 個交易日 × 2 series 衍生；0 API 呼叫 |
| `mtx_continuous_d` (MTX 連續期) | 同上 | 同上 | **YES — 同樣 rebuild** | 同上 |
| `bars_1m` (MXF 1-min) | STALE 74d, max 2026-03-12 | `RAW_SOURCES/MXF_1m_clean_all/` 靜態檔 | **NO** — repo 內無 minute-bar feed；TEJ 也沒提供 MXF intraday 1m | ~50 交易日 × ~300 bars/day × MXF = ~15K bars；需自架 broker tick→1min |
| `stock_futures_continuous_d` (個股期連續近月) | STALE 45d, max 2026-04-10 | `RAW_SOURCES/股票期貨/continuous_near_month.parquet` 手動；underlying `tw_stock_futures` 也只到 2026-04-13 | **partial** — underlying 卡 4/13 之後沒新檔，無法 build 到今天；可往前推 3 天到 4/13 | 加 underlying 30 天 → 30 days × 261 stocks ≈ 7.8K rows underlying；本次只能補到 4/13 |
| `bars_1d` 含 MXF/TXF/個股期 | WARN 3d 整體；`tw_futures` 已到 5/22；`tw_stock_futures` 卡 4/13 | TEJ AFUTR raw 同時含兩類；adapter 把 4-digit 丟到 `tw_stock_futures`，但目前未生效（手動檔停在 4/13） | **mixed** — `tw_futures` 已 OK；`tw_stock_futures` 同上限制 | 需要新寫 adapter 把 AFUTR 4-digit underlying 寫進 tw_stock_futures（中等工作量） |
| `finmind_stock_price_norm` | INFO 10d, max 2026-05-15 | bronze sqlite snapshot；FinMind crawler 可更新 | **YES — FinMind run 模式** | 3,088 檔 × 1 chunk = 3,088 呼叫；FinMind sponsor rate；ETA ~30 分鐘（觀察到的實際 rate）｜需先暫停 tick |
| `finmind_stock_price_adj_norm` | INFO 12d, max 2026-05-13 | 同上 | **YES — 同樣 run** | 同上，再 ~30 分鐘 |

**結論**：今天可達 100% 的：`tx_continuous_d`、`mtx_continuous_d`、`finmind_stock_price_norm`、`finmind_stock_price_adj_norm`（4 項）。`stock_futures_continuous_d` 可向前推到 4/13；`bars_1m` 與 `tw_stock_futures` 4/13+ 的真實缺口需另外接資料源（不在今天範圍）。

---

## 預算 / Quota 衝突

FinMind crawler 1500/hr 配額是「per token」全局共享。tick crawler 已在跑 (24,704 calls，ETA ~16h)。要做 daily 個股日 K + adj：
- 暫停 tick → 跑 daily（共 6,176 calls, ~30 分鐘）→ 重啟 tick
- 暫停期間損失 ~7,500 個 tick calls quota 但 tick 進度本身不丟（crawler 用 `_meta_progress` 記點）

---

## Milestone

| Mn | 範圍 | 狀態 |
|---|---|---|
| **M1** | 評估 + plan + 進度檔 | ✅ |
| **M2** | TX / MTX continuous rebuild（從 bars_1d 衍生，0 API） | ✅ |
| **M3** | 暫停 tick → FinMind run `TaiwanStockPrice` + `TaiwanStockPriceAdj` → snapshot 回 bronze → restart tick | ⏳ launched |
| **M4** | 個股期 continuous rebuild + 寫入限制文件（underlying 4/13 為止） | ✅ document only |
| **M5** | bars_1m 缺口 documentation + gap_report regen + push | ⏳ |

---

## 進度日誌

### M1 — 評估

詳評估見上表。實際可 100% 達成：4 項（2 個 continuous + 2 個 FinMind daily）。partial：1 項（stock_futures_continuous_d 補到 4/13）。out-of-scope：1 項（bars_1m，無 source）。
### M2 — TX / MTX 連續期延伸

從 `bars_1d` `asset_class='tw_futures'` 抽 TX / MTX 月份合約 (filter regex `^{P}[0-9]{6}$`，排除 weekly suffix W)，**每日選 max(volume) 為 front**，補進現有 `gold/continuous/{tx,mtx}_continuous_d.parquet` 尾端（2026-05-09 ~ 2026-05-22）：

- TX +10 rows → 2,528 列（max 從 2026-05-08 推到 2026-05-22）
- MTX +10 rows → 2,528 列（同）

**注意：** 新追加的 10 列 `adj_factor=NULL` + `*_adj=raw`，因為跨 rollover 的 back-adjustment 鏈未連續（缺乏歷史 settle 對應）。這意謂 `source='qd_{tx|mtx}_continuous_extended_from_bars1d'` 的列在拿來做 OOS 回測時不能直接接歷史 back-adj 序列。完整 back-adj rebuild 留待未來真正換 adapter 時做。

`.bak` 寫在 `gold/continuous/*.parquet.bak` 保留一份備援。0 API 呼叫；view 自動 pick up（`read_parquet(...)`）。
### M3 — FinMind daily price refresh (launched)

1. `kill 311436`（tick crawler python process），驗證 `pgrep finmind_dump high-freq` 空
2. 起 daily refresh：

   ```bash
   PYTHONPATH=src nohup .venv/bin/python -m finmind_dump run \
     --only TaiwanStockPrice,TaiwanStockPriceAdj \
     --start 2026-05-15 \
     > logs/daily_refresh_<ts>.log 2>&1 &
   ```

   PID 寫 `/home/kevin/gs-scraper/FINMIND資料集/daily.pid` (313335)。Log 顯示 `[per_stock] TaiwanStockPrice × 3088 檔 (2026-05-15 ~ 2026-05-25)`，**從近到遠** chunking 模式。

3. 初步觀察：`TaiwanStockPrice` 已 +155 rows 達 max 2026-05-22；3,088 stocks × 5 trading days = 預計 ~15K 新列。31 stock-chunks 完成 / 分鐘 → ETA ~80 分鐘 for TaiwanStockPrice，再 ~80 分 for `TaiwanStockPriceAdj` → 總 ~2.5 hr。

4. **狀態：背景跑中**。
   - Wakeup poll @ 11:31（+49 min）：TaiwanStockPrice 1,274 / 3,088 = 21% / 22 stocks·min⁻¹
   - Wakeup poll @ 12:56（+2h 14min）：**TaiwanStockPrice 完成於 12:39:26**（共 ~117 min，2,744 done + 344 empty）；TaiwanStockPriceAdj 已起跑 ~17 min，257 done + 31 empty = 9%，max date 已推到 2026-05-22；按相同 rate 全部完成 ETA **~14:36**。下一次 wakeup +100 min（14:36）再 check。

   完成後手動：
   ```bash
   cp /home/kevin/gs-scraper/FINMIND資料集/data/finmind.sqlite \
      /home/kevin/gs-scraper/QUANTDATA/bronze/finmind/finmind_2026-05-25.sqlite
   sha256sum bronze/finmind/finmind_2026-05-25.sqlite > bronze/finmind/finmind_2026-05-25.sqlite.sha256
   .venv/bin/python scripts/restore_finmind_views.py  # glob picks new sqlite
   ```
   然後 `nohup ... finmind_dump high-freq ...` 重啟 tick crawler。

### M4 — 個股期 continuous（限制紀錄）

**現況**：
- `stock_futures_continuous_d` parquet max 2026-04-10（539,992 列，314 unique futures_codes）
- 上游 `bars_1d` `asset_class='tw_stock_futures'` max 2026-04-13（3,382,429 列）
- TEJ 來源 `RAW_SOURCES/股票期貨/{stock_futures_daily,continuous_near_month}.parquet` 卡 2026-04-13（手動檔，4/13 後無新檔）

**為何不在今天 rebuild**：
1. continuous 與 underlying 都同步停在 4/13，連 3 天延伸的價值都不大（continuous max 4/10 → underlying max 4/13 = 只能往前推 3 天 ≈ 0.15% 行數）
2. continuous schema 含 `change`、`change_pct`、`best_bid`、`best_ask`、`hist_high`、`hist_low`、`halted`、`spread_volume` 這些欄位 `bars_1d` 沒有 → 填 NULL 會破壞 downstream
3. 真正缺口是 underlying 4/14 → 5/22 (~27 trading days × ~261 stocks ≈ 7K rows)，需要新寫 TEJ adapter（`adapt_afutr_to_bars_1d` 目前 filter 掉 4-digit underlying；應另外寫 adapter 把它導向 silver tw_stock_futures）

**Action items 留 backlog**：
- M-future.A：寫 `adapt_afutr_to_stock_futures()` 把 AFUTR 4-digit underlying 寫到 silver `bars/asset_class=tw_stock_futures/`
- M-future.B：在 M-future.A 之後，rebuild continuous（含 best_bid/best_ask NULL）
### M4 — pending
### M5 — pending

---

## Fallback

- M2 把 tx_continuous_d gold parquet 寫壞：留 `.bak` 備份；rollback `mv ../gold/continuous/tx_continuous_d.parquet.bak ../gold/continuous/tx_continuous_d.parquet`
- M3 tick 中斷：crawler 的 `_meta_progress` 表保證 idempotent re-launch；restart 用相同指令
- M3 daily 抓到一半：重跑同樣 `run --only TaiwanStockPrice,TaiwanStockPriceAdj --start 2026-05-15` 即可
- 整段卡關：`tail meta/audit/daily_refresh_*.log` + 看本 doc 已完成 milestones
