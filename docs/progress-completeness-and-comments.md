# 2026-06-02 — 拉升完整度 + gap dashboard 加可編輯註解 panel

## 目標

兩件事：

1. **完整度 < 90% 的 12 條 view** — 逐條看是否能立刻 refresh（跑 fetcher）或
   說明卡哪、無法自動 refresh 的原因要寫死進 dashboard 註記。
2. **gap dashboard 加 panel** 可在 UI 直接編輯每條 view 的 comment（資料完成
   率/清洗率低的原因），存 `meta/gap_comments.json`，下次 `gap_report.py`
   regen 會把註解渲染進 HTML。Flask 端 `/api/gap_comments` GET/POST。

## 完整度 < 90% 清單（dataset audit）

| view | tier | sev | lag | 完整度 | 可否自動修 |
|---|---|---|---|---|---|
| accounting_raw | P2 | OK | 93d | **0%** | ✅ TEJ API（fetch_tej --table accounting_raw） |
| accounting_raw_snapshot | P2 | OK | 93d | **0%** | ✅ derived rebuild |
| bars_1m | P2 | STALE | 81d | **10%** | ❌ RAW manual dump (`RAW_SOURCES/MXF_1m_clean_all.parquet`) |
| bars_1m_daily_summary | P2 | STALE | 81d | **10%** | ❌ 同上（derived） |
| fundamentals_q | P1 | OK | 63d | **30%** | ❌ TEJ 訂閱包 CSV（無 API） |
| fundamentals_pit | P1 | OK | 63d | **30%** | ❌ 同上（derived） |
| stock_futures_continuous_d | P2 | STALE | 52d | **42%** | ❌ RAW manual dump（`RAW_SOURCES/股票期貨/`） |
| cross_market_features | P2 | INFO | 34d | **62%** | ❌ RAW manual dump（`SUPPLEMENT/DERIVED/cross_market_features.parquet`） |
| revenue_monthly | P0 | OK | 32d | **64%** | ✅ TEJ API（fetch_tej --table revenue_monthly） |
| revenue_factors | P0 | OK | 32d | **64%** | ✅ derived rebuild |
| tx_continuous_d | P1 | STALE | 24d | **73%** | ❌ RAW manual dump |
| mtx_continuous_d | P1 | STALE | 24d | **73%** | ❌ 同上 |

**可立刻 refresh：** 2 條 TEJ fetcher（accounting_raw / revenue_monthly），各自帶
1 條 derived。共 4 條可能可提升。

**無法自動 refresh（8 條）：**
- TEJ 訂閱包 CSV（fundamentals_q / fundamentals_pit）→ 需手動下載
- RAW_SOURCES 手動 dump（bars_1m + bars_1m_daily_summary / stock_futures /
  cross_market_features / tx_continuous_d / mtx_continuous_d）→ 需使用者重新
  dump RAW 後 daily_refresh step 3.55/3.56 自動 propagate

## 計畫 milestone

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + 完整度盤點（已寫上半段） |
| **M2** | 跑 `fetch_tej --table revenue_monthly` + `--table accounting_raw`，看新資料量；跑 derived rebuild；重生 dashboard 看完整度變化 |
| **M3** | `meta/gap_comments.json` schema + `gap_report.py` 渲染每條 view 的 comment；新增 inline "📝 編輯" 按鈕 + 註解區塊 |
| **M4** | Flask `/api/gap_comments` GET/POST endpoint + 新增 e2e test + 收尾 |

## Comments panel 設計

### 資料格式：`meta/gap_comments.json`

```json
{
  "_schema_version": 1,
  "updated_at": "2026-06-02T09:30:00Z",
  "comments": {
    "fundamentals_q": "TEJ 訂閱包 CSV，無 API；需季度手動更新",
    "bars_1m": "RAW_SOURCES/MXF_1m_clean_all.parquet 須手動 re-dump",
    ...
  }
}
```

### 渲染：在 dashboard 每條 row 多一格「📝 Note」column；若 view 有
comment 就顯示，沒就空。table 上方有「💬 編輯 N 條註解」按鈕→ modal
打開列出全部 view + 該 view 的 textarea。

### Flask：
- `GET /api/gap_comments` → 回 JSON
- `POST /api/gap_comments` → body `{"view": "x", "comment": "..."}`，寫進
  JSON，更新 `updated_at`

### 注意：
- gap_dashboard.html 由 `gap_report.py` regen 生靜態 HTML；註解區塊靠 JS
  從 `/api/gap_comments` fetch（不是 inline 寫死）→ 編輯不用 regen 即可看到。

## 進度日誌

### M2（task 1 探查 + 註解 column 渲染）  `7f81627`

**Task 1 — TEJ refresh 結果**：
- `revenue_monthly`：fetch_tej 跑了 0 new rows（TEJ 5/1 之後尚未公布）
- `accounting_raw`：寫了 6,320 rows 但都是 Q1 revision，`fiscal_month` max 仍 2026-03-01

**結論**：12 條 <90% 全卡資料源公布上限或 RAW 手動 dump，無法自動 lift。
轉戰：把原因寫進 dashboard（task 2 的目的）。

**Task 2 — 渲染**：
- 新增 `meta/gap_comments.json`（12 條初始註解）+ `.gitignore` 開白名單
- `gap_report.py` 載 JSON → 渲染 per-row note column；「📝 編輯註解」modal
- modal save 走 fetch `POST /api/gap_comments`（Flask endpoint M3 補）

### M3 — Flask `/api/gap_comments` GET/POST  `(M3 commit)`

`ui/search/app.py` 新增：
- `GET /api/gap_comments` → 回 `meta/gap_comments.json`
- `POST /api/gap_comments` body `{view, comment}`：upsert（空 comment 視為刪）
- 寫檔走 `tmpfile + os.replace`（atomic）
- 4 個 e2e（test_E013）：GET schema / upsert + delete / 3 種 validation 400
- **全 142 passed**（既有 138 + 4 新）

**API 真機 smoke 驗過**：GET / POST upsert / POST 空字串刪 / 缺 view → 400。

### M4 — 進度檔收尾

可用範例：

```bash
# 從 CLI 編一條註解
curl -X POST http://192.168.0.249:5050/api/gap_comments \
  -H 'Content-Type: application/json' \
  -d '{"view":"tx_continuous_d","comment":"5/8 之後 RAW 還沒更新，等下次 dump"}'

# 或直接在 dashboard 按「📝 編輯註解」開 modal 一次編全部
```

存檔後 page reload → 註解 column 即時反映。

## 還沒解的

- **未實作差異化欄位寬度** — note column 在窄螢幕會擠表格；目前用 `text-overflow:ellipsis` + hover 展開。下一輪可改 sticky right column 或固定寬。
- **historic comment 版本**：目前 only one snapshot。要 trace 改動歷史可加 `git add meta/gap_comments.json` 改自動 commit hook（這次 spec 沒要求）。
- **編輯權限**：目前 `/api/gap_comments` 沒 auth（內網 trusted）；對外暴露要加 token。

## Fallback

```bash
git revert HEAD~3..HEAD
rm -f meta/gap_comments.json
```
