# Gap dashboard 預設按完整度排序

> 啟動：2026-05-25
> 觸發：`/safe-yolo 將gap dashboard的data catalog預設按照目前爬取的資料完整度(爬取資料所佔的百分比 Lag Visual)來重新排序`

## 目標

把 `docs/gap_dashboard.html` 與 `docs-site/gap_dashboard.html` 的 catalog 表格，預設排序從「severity-by-bucket（STALE → WARN → OK）」改成「**完整度從高到低**」。直覺：完整度 = 100% - 標準化 lag。

## 解讀

使用者括弧內定義「爬取資料所佔的百分比 = Lag Visual」。現有 Lag Visual 是 bar 越長代表 lag 越久（越紅、越右）。要改成排序時用一個連續的完整度數值（非 bucket），讓使用者一眼看到「最不完整的在哪 / 最完整的有哪些」。

兩種選擇：

| | A. 完整度 = 100% - lag_days/90 (cap 0-100) | B. 直接用負 lag_days 連續排 |
|---|---|---|
| 公式直觀 | 是（完整度有上下限） | 否（沒有 0-100 的語意） |
| 對 forward-looking (cash_dividend_events, lag = -143d) 怎麼處理 | 100%（lag ≤ 0 表示完整） | top（負數最小） |
| 對 EMPTY (lag = None) | 0%（沒資料 = 完全不完整） | 排尾 |

選 **A**。lag ≤ 0 算 100%，lag ≥ 90 算 0%，線性。EMPTY = 0%。

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 寫此進度檔 | ✅ |
| **M2** | 改 `scripts/gap_report.py`：算 `completeness_pct`、預設排序按它 DESC、新欄「完整度 %」、bar 視覺改成「填滿 = 完整」、regen 兩份 HTML | ✅ |
| **M3** | strict build + push live | ⏳ |

## 進度日誌

### M1 — plan doc

選定方案 A（completeness = 100% - lag/90, clamp 0-100）。EMPTY = 0%；forward-looking 負 lag = 100%（資料已涵蓋至未來日期）。
### M2 — gap_report.py edit + regen

新增 `_completeness(lag_days)` 函式（cap=90d，lag≤0→1.0、lag≥90→0.0、None→0.0）。`render_html` 改寫：

- 預設 sort key 變為 `(-completeness, tier, view)` — 完整度 DESC、tier ASC（P0 在前）、view 名稱 ASC stable
- bar 視覺改為 **完整度填充**：fill_px = round(completeness × 180px)，填滿即代表 100% 完整；EMPTY 用 grey
- 新欄 `<th class="pct">完整度</th>`，值 `{c*100:.0f}%`
- legend 補說明：「Completeness = clamp(1 − lag/90, 0, 1) × 100%」

Regen 兩份 HTML（docs/ + docs-site/）。實際排序頂端：cash_dividend_events (100%) → tw_stock_futures_corp_actions (100%) → tw_stock_trading_attrs_daily (100%) → bars_1d (97%) ... → 底部最 stale 的視圖。
### M3 — pending

## Fallback

- 改壞了：`git revert <M2-commit>`；`scripts/gap_report.py` 與兩份 html 回復
- 想關掉新排序：往後加 `--sort severity` flag（不在本次範圍）
