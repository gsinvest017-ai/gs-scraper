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
| **M2** | gap_report.py 加 trading-day calendar loader + expected_latest_trading_day + 套用到 daily-trading probe；regen | ⏳ |
| **M3** | strict build + push live | ⏳ |

## 進度日誌

### M1 — plan doc

選擇「calendar_xtai 主 + weekday fallback」混合，EOD cutoff 15:00 TPE，scope 限 `daily-trading` category。
calendar_xtai 目前只到 2025-12-31，所以 2026 全部走 weekday fallback。後續若 calendar 補完 2026，自動切回 view 為主。
### M2 — pending
### M3 — pending

## Fallback

- 改壞了：`git revert <M2-commit>`
- 假日邏輯沒涵蓋到（calendar 老掉、weekday fallback 錯判某 holiday）：手動 inspect dashboard，必要時加 `--ignore-trading-calendar` flag 暫時關掉
- 開盤前 / 開盤中誤算：調整 `_EOD_CUTOFF_HOUR_TPE`（預設 15）
