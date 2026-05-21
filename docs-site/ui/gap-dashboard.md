# Gap dashboard

`scripts/gap_report.py` 走遍 catalog 裡監控中的 view，計算 `today - MAX(date_col)` 當 lag，按 view 類型分級成 OK / WARN / STALE / EMPTY / INFO，輸出文字 / JSON / HTML 三種格式。

!!! tip "📊 看當前 live dashboard"

    [**→ 開啟 `gap_dashboard.html`**](../gap_dashboard.html){ target=_blank }

    這份 HTML 由最近一次 commit 觸發 docs.yml workflow 時的 snapshot。要看更即時的，本地跑 `.venv/bin/python scripts/gap_report.py --format html` 即可。

## 跑

```bash
.venv/bin/python scripts/gap_report.py                  # 文字（彩色）
.venv/bin/python scripts/gap_report.py --format json    # → meta/audit/gap_report.json
.venv/bin/python scripts/gap_report.py --format html    # → docs/gap_dashboard.html
.venv/bin/python scripts/gap_report.py --format all     # 三種一起
```

`daily_refresh.sh` 結尾會自動跑 `--format all`，所以平常不用手動觸發。

## 看更新後的 HTML

=== "WSL → Windows 瀏覽器"

    ```bash
    explorer.exe docs/gap_dashboard.html
    # 或
    wslview docs/gap_dashboard.html
    ```

=== "起個小 http server"

    ```bash
    .venv/bin/python -m http.server 8765 --directory docs
    # 開 http://localhost:8765/gap_dashboard.html
    ```

=== "直接看 HTML 源碼（不推薦）"

    ```bash
    less docs/gap_dashboard.html
    ```

## 五個 severity

| Sev | 意義 | 觸發條件 |
|---|---|---|
| ✅ **OK** | 在 SLA 內 | 依 category 不同（見下） |
| ⚠️ **WARN** | 超出 SLA 一點 | 中度 lag |
| 🔴 **STALE** | 嚴重 lag | 需要立刻去抓 |
| ❓ **EMPTY** | view 是空的 | `MAX(date) IS NULL` |
| ℹ️ **INFO** | 純資訊 / snapshot | derived view 或 bronze snapshot；不觸發 alert |

## SLA 怎麼算（依 category）

```python
def classify(lag_days, category):
    if category == "event":          # 看 forward-looking events
        if lag_days <= 0: return "OK"        # MAX(date) >= today, 還有未來事件
        return "WARN" if lag_days < 30 else "STALE"
    if category == "monthly":        # 月營收等
        if lag_days <= 15: return "OK"
        if lag_days <= 45: return "WARN"
        return "STALE"
    if category == "quarterly":      # 季報
        if lag_days <= 60:  return "OK"
        if lag_days <= 120: return "WARN"
        return "STALE"
    if category == "derived":        # gold layer
        if lag_days <= 1: return "OK"
        return "INFO"                # 只資訊，不 alert
    if category == "snapshot":       # bronze one-shot
        return "INFO"                # 永遠 INFO（有資料的話）
    # 預設 daily-trading
    if lag_days <= 1: return "OK"
    if lag_days <= 5: return "WARN"
    return "STALE"
```

## 加新 view 進 dashboard

編輯 `scripts/gap_report.py` 的 `DATASETS` registry：

```python
DATASETS = [
    # ...
    Dataset("your_view_name",       "trading_date", "daily-trading",
            "fetch_xxx.py --table your_table --append-since-silver",
            "view 的中文描述", "P1"),
]
```

欄位語意：

| field | 內容 |
|---|---|
| `view` | DuckDB main schema 下的 view name |
| `date_col` | 新鮮度信號用哪個欄位（通常是 `trading_date` / `publish_date` / `ex_date`） |
| `category` | `daily-trading` / `monthly` / `quarterly` / `event` / `derived` / `snapshot` |
| `fetch_cmd` | 顯示給人看的「stale 時去跑什麼」 |
| `description` | view 的中文人話描述 |
| `tier` | `P0` / `P1` / `P2` priority |

存檔後重跑 `--format all` 就會出現在 dashboard。

## 範例輸出（text 模式）

```
QUANTDATA gap report — generated 2026-05-21
====================================================================================================
view                             tier max_date      lag  status  action
----------------------------------------------------------------------------------------------------
revenue_monthly                  P0   2026-04-01    50d  🔴 STALE     fetch_tej.py --table revenue_monthly --append-since-silver
tw_inst_futures_daily            P0   2026-05-08    13d  🔴 STALE     (TAIFEX scraper — currently no auto-refresh)
tw_stock_bars                    P0   2026-05-18     3d  ⚠️  WARN    fetch_tej.py --table stock_daily --append-since-silver
finmind_stock_price_norm         P1   2026-05-15     6d  ℹ️  INFO    (re-sync bronze/finmind/finmind_*.sqlite ...)
tw_futures_large_trader_daily    P0   2026-05-20     1d  ✅ OK        —
bars_1d                          P0   2026-05-20     1d  ✅ OK        —
----------------------------------------------------------------------------------------------------
summary: ✅ OK=7  ⚠️  WARN=2  🔴 STALE=12  ❓ EMPTY=1  ℹ️  INFO=4
```

## 自動化

```yaml
# crontab 範例（已掛在 daily_refresh.sh 內）
30 14 * * 1-5 cd /home/kevin/gs-scraper/QUANTDATA && bash scripts/daily_refresh.sh
```

每個工作日 14:30 跑完 daily_refresh，最後一步會重畫 `docs/gap_dashboard.html`。要遠端隨時看，可以開個簡單 nginx serve `docs/`，或用 [Funnel](funnel.md) 暴露。
