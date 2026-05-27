# 2026-05-27 — Search UI Chart 週期切換（日/周/月/半年/年）

## 觸發

`/safe-yolo 替...Quantdata search dashboard...run query...Chart 時間序列視覺化也提供日/周/月/半年/年不同週期資料的 view switch`

## 目標

Search UI（`ui/search`）query 結果的 Chart 分頁目前只畫原始（逐日）時間序列。加一個**週期切換**：日（raw）/ 周 / 月 / 半年 / 年，把 (x=date, y=value) 序列 resample 到該頻率再畫。resample 需要 aggregation 規則，附一個 agg 選單（last 期末 / mean / sum / min / max），預設 **last（期末值）** —— 對 price/factor 這類 level 序列最自然。

## 設計（純前端，main.js + view.html）

- view.html `chart-controls` 加兩個 select：
  - `chart-period`：`D 日(原始)` / `W 周` / `M 月` / `H 半年` / `Y 年`
  - `chart-agg`：`last 期末` / `mean 均值` / `sum 加總` / `min` / `max`
- main.js：
  - `periodKey(dateStr, period)`：W=該週週一(ISO)、M=`YYYY-MM`、H=`YYYY-H1/H2`、Y=`YYYY`、D=原值。
  - `resample(xs, ys, period, agg)`：在 `sortByX`（已排序）後，依 periodKey 分桶，桶內 y 依 agg 聚合，x 取桶內最後一個日期（= 期末，因已升冪排序）。D 直接回傳原序列。
  - group-by 與非 group-by 兩條路徑都套 resample。
  - xaxis title 標上週期 label。

不動後端 / SQL / 資料；純前端聚合，可逆。

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 |
| **M2** | view.html 兩個 select + main.js `periodKey`/`resample` + 套用兩路徑 |
| **M3** | node 語法檢查 main.js + curl 驗證 static/view serve + commit |

## Fallback

- resample 行為怪 → 預設 D（raw）等同舊行為，使用者可切回。
- rollback：`git revert` M2。

## 完成日誌

（M2-M3 後追加）
