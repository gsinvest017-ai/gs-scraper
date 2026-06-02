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

## Fallback

```bash
git checkout HEAD~1 -- scripts/daily_refresh.sh
```
