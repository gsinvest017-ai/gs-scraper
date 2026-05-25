# Gap dashboard 改用 trading-day-aware lag

> 啟動：2026-05-25
> 觸發：`/safe-yolo 目前gap dashboard有些日資料 因為遇到周日非交易日and/or當日交易還未結束導致顯示有Lag 但其實沒有Lag 請修正此問題`

## 問題

目前 `gap_report.py` 用 `lag = (today - max_date).days` 是純 calendar 日差。在這些情境下會誤判：

- 週一早上 11 AM：max=上週五；today 5/25 - max 5/22 = 3 calendar days = WARN，但其實 5/23、5/24 不開盤，5/25 EOD 也還沒落地，**fully current**
- 週五晚上未到 EOD：max=昨天 5/22；today 5/22 - max 5/22 = 0... wait this case is fine
- 但週五 EOD 之後到隔週一上午：show as 3d lag 但 fully current
- TW 國定假日（春節、端午、中秋）：可能整週 lag 累積到 5d 變 STALE

## 設計

引入 `expected_latest_trading_day(now)`：

1. 先用 `calendar_xtai` view 找 `is_trading=True` 的日子集合
2. **fallback**：calendar 沒覆蓋的日期（目前 view 只到 2025-12-31）退回 Mon-Fri 判斷
3. EOD cutoff hour 預設 **15:00 CST** (TPE)：今日若是交易日且 `now.hour ≥ 15`，expected = today；否則回推到上一個 trading day
4. 用 `ZoneInfo("Asia/Taipei")` 計算 TPE 當地時刻（避免 UTC vs 本地時差）

`effective_lag_days = max(0, (expected_latest - max_date).days)`

只對 `category in {"daily-trading"}` 套用；monthly / quarterly / event / snapshot / derived 維持原行為。

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 寫此進度檔 | ✅ |
| **M2** | gap_report.py 加 trading-day calendar loader + expected_latest_trading_day + 套用到 daily-trading probe；regen | ✅ |
| **M3** | strict build + push live | ✅ |

## 進度日誌

### M1 — plan doc

選擇「calendar_xtai 主 + weekday fallback」混合，EOD cutoff 15:00 TPE，scope 限 `daily-trading` category。
calendar_xtai 目前只到 2025-12-31，所以 2026 全部走 weekday fallback。後續若 calendar 補完 2026，自動切回 view 為主。
### M2 — gap_report.py 改寫

新增 module-level：
- `TPE_TZ = ZoneInfo("Asia/Taipei")`
- `EOD_CUTOFF_HOUR_TPE = 15`
- `TRADING_DAY_CATEGORIES = frozenset({"daily-trading"})`

新增三個 helper：
- `_load_trading_days(con)`：從 `calendar_xtai` view 抓 `is_trading=True` 的 set；view 缺則回 empty set
- `_is_trading_day(d, calendar_days)`：calendar 覆蓋範圍內 = look up；範圍外 fallback 到 weekday Mon-Fri
- `expected_latest_trading_day(now_tpe, calendar_days, eod_cutoff_hour=15)`：今日是交易日且 now_tpe.hour ≥ 15 就回 today；否則回推到上一個 trading day（30 days backstop）

`probe()` 內：
- `now_tpe = datetime.now(TPE_TZ)`
- `expected_td = expected_latest_trading_day(now_tpe, _load_trading_days(con))`
- 對 `category in TRADING_DAY_CATEGORIES` 改用 `lag = max(0, (expected_td - max_date).days)`；其他 category 維持 `(today - max_date).days`

Legend 補一行說明「Daily-trading 類別的 lag 為 trading-day-aware」。

驗證：
- 之前 WARN 9 → 1（8 個 daily-trading 從 calendar 3d 變 trading-day 0d）
- OK 4 → 12（補回那 8 個 + tx/mtx_continuous）
- STALE 8 → 8（真 stale 維持 STALE，chip_dist 仍 7d、tw_inst_futures_daily 仍 14d）
- INFO 4、EMPTY 1 不變
### M3 — push live

`git push origin main` (07f75ab) → docs.yml workflow success ~20s。第一輪 poll 還在 in_progress 同時 GitHub Pages CDN 還沒更新；20s 後 cache-bust 強制重抓，確認：

- 新 legend 行「Daily-trading 類別的 lag 為 trading-day-aware ...」已上 live
- `bars_1d` 的 `class="lag">0d</code>`，符合預期

## Fallback

- 改壞了：`git revert <M2-commit>`
- 假日邏輯沒涵蓋到（calendar 老掉、weekday fallback 錯判某 holiday）：手動 inspect dashboard，必要時加 `--ignore-trading-calendar` flag 暫時關掉
- 開盤前 / 開盤中誤算：調整 `_EOD_CUTOFF_HOUR_TPE`（預設 15）
