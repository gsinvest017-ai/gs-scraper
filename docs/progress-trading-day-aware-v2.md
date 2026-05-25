# 2026-05-25 — Trading-day-aware lag v2 (cover snapshot/derived + push cutoff to 18:00)

## 觸發

`/safe-yolo 改 polling cadence + 修正 3 個 view 仍誤判 lag 的 bug`

## 問題

之前的 trading-day-aware lag 只套用在 `daily-trading` category。三個 view（`finmind_stock_price_norm` / `finmind_stock_price_adj_norm` / `qc_stock_price_diff`）落在 `snapshot` 與 `derived` 兩個 category，沒被套用 → 仍顯示 3d / 97% completeness。

另一個更隱藏的問題：EOD cutoff 15:00 太早。**現在 TPE 15:08，已過 cutoff** → expected_latest_trading_day = today (5/25)，但今日 cron (17:30) 還沒跑 → silver max 仍是 Fri 5/22 → lag 變成 3d，**所有 daily-trading view 也都掛 WARN**。

## 修正

`scripts/gap_report.py` 兩處改動：

1. `TRADING_DAY_CATEGORIES = frozenset({"daily-trading", "snapshot", "derived"})` — 加入 snapshot 與 derived，因為它們的底層也是 TW 交易日語意
2. `EOD_CUTOFF_HOUR_TPE = 18`（原 15）— 對齊 cron 排程 17:30 + 30 min buffer，確保「資料應已在 silver」這個假設成立才把 expected 推到今天

`monthly` / `quarterly` / `event` 維持 raw calendar lag（這些 cadence 跟交易日無關）。

## 效果

跑 `gap_report.py --format all` 對比：

| view | 修前 | 修後 |
|---|---|---|
| finmind_stock_price_norm | 3d / 97% / INFO | **0d / 100% / INFO** |
| finmind_stock_price_adj_norm | 3d / 97% / INFO | **0d / 100% / INFO** |
| qc_stock_price_diff | 3d / 97% / INFO | **0d / 100% / OK**（derived lag ≤ 1 → OK） |
| tw_stock_bars / bars_1d / inst_stock / margin / large_trader / futures_full / tx_cont / mtx_cont | 3d / WARN | **0d / OK** |

Summary 從 `OK=4 WARN=9 STALE=8 INFO=4` 變 `OK=13 WARN=1 STALE=8 INFO=3`。

## Polling cadence 改 2 小時

前一輪 schedule 30/60 min cadence 是因為要抓 daily refresh 完成時刻。現在 daily refresh 已完，只剩 tick crawler（ETA 明日 04:07 TPE，~13 hr）。改成每 **2 小時** 一輪簡短報告 + ETA。

## Fallback

- 改壞了：`git revert <commit>`
- EOD cutoff 18 對未來 cron 改時間無法自適應 — 若有人把 install_cron.sh 從 17:30 改成別的時間，cutoff 也要跟著調
