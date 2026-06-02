# 2026-06-02 — 把 RAW refresh 腳本串進 daily_refresh.sh

## 目標

讓使用者更新 `RAW_SOURCES/{日k 期貨tquant lab,股票期貨,MXF_1m_clean_all.parquet}`
後，**當天 cron 自動 propagate** 到 silver / gold，再被 step 3.7 derived rebuild
吃進去。免去手動跑兩支腳本。

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + daily_refresh.sh 加 step 3.55（continuous）+ step 3.56（bars_1m） |
| **M2** | smoke：跑兩個 step 對應指令確認 log 行為正確；commit |
| **M3** | 進度檔收尾 |

## 設計

兩條 step 都插在 **step 3.5（restore views）之後、3.7（derived rebuild）之前**：

```
3.5  restore finmind / qc views          ←既有
3.55 refresh continuous from RAW         ←新增（TX/MTX/個股期）
3.56 ingest bars_1m from RAW             ←新增（MXF 1m）
3.7  rebuild derived gold                ←既有；現在會吃到 3.55/3.56 寫出來的最新 silver/gold
```

兩條都 **non-fatal**（`|| log WARN ...`）；使用者沒更新 RAW 時也不會炸（腳本本身對「來源檔不存在」回 `ok=False` 但 exit 0；對「有檔但已最新」會重寫一份相同內容，無害）。

## 進度日誌

### M1 — daily_refresh.sh 加兩個 step  `(M1 commit)`

step 3.5 與 3.7 之間插入：

```
step 3.55: refresh continuous from RAW (TX/MTX/個股期 → gold/continuous)
step 3.56: ingest bars_1m (MXF) from RAW → silver/bars/bars_1m/
```

兩條都 `|| log WARN ...`（non-fatal）。`bash -n scripts/daily_refresh.sh` 通過。

### M2 — smoke  `(M2 commit)`

跟 cron 同款的 invoke 跑兩條：

```
step 3.55:
  TX             rows=2518   max_date=2026-05-08
  MTX            rows=2518   max_date=2026-05-08
  stock_futures  rows=539992 max_date=2026-04-10 (314 contracts)
  rc=0
step 3.56 (dry-run):
  rows=1,668,004  datetime_max=2026-03-11 23:59
  trading_date_max=2026-03-12
```

實際 cron 跑會走 full ingest（rc 也 0；MXF 完整 write 已於前一輪驗證過）。

### M3 — 進度檔收尾

整體 propagation 路徑（使用者更新 RAW 後 cron 自動跑）：

```
RAW_SOURCES update                       ← 使用者手動
        ↓
[cron 17:30 CST 跑 daily_refresh.sh]
        ↓
step 3.55 / 3.56 重寫 gold/silver
        ↓
step 3.7 derived rebuild
        ↓ (build_bars_1m_daily_summary / build_futures_bar_factors / ...)
gold 更新 → dashboard step 4 重生 → STALE 跟著降
```

從使用者更新 RAW → dashboard 看到變化，**最多隔 1 個工作日 cron 即可 cover**。

## Fallback

```bash
git checkout HEAD~2 -- scripts/daily_refresh.sh
rm -f docs/progress-daily-refresh-raw-wire.md
```
