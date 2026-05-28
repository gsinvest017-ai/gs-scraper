# 2026-05-28 — Search UI 提供 CSV bulk download（單檔 + 批次 zip）

## 觸發

`/safe-yolo 提供可以讓不會使用資料庫的人...以 csv 的 .zip 檔格式下載...資料量 10G 也要提供批次下載功能`

## 目標

讓非工程使用者不用 DuckDB / SQL 就能拿到資料：

1. **單一 view → CSV**：點按鈕就下載該 view 的 CSV（一檔；row 數大可能 100MB+，需 streaming）。
2. **批次 → ZIP**：勾選多個 view → 下載 zip，內含每 view 一個 `.csv`。
3. **預設批次**：依 row 數分桶（small ≤100k / medium ≤1M / large >1M）按鈕一鍵全選，避免使用者誤抓全部 10GB。

## 設計

### Endpoints（`ui/search/app.py`）

| route | 行為 |
|---|---|
| `GET /downloads` | 渲染 downloads.html：列出所有 view + row_count + 單檔下載連結 + 多選表單 |
| `GET /download/view/<v>.csv` | 串流 CSV：`stream_with_context` + DuckDB `fetchmany(50_000)` 逐 chunk yield；`Content-Disposition: attachment` |
| `GET /download/bundle.zip?v=a&v=b&...` | 串流 zip：寫到 `NamedTemporaryFile`（stdlib `zipfile`，DEFLATED、`allowZip64=True`，每 entry `force_zip64=True`），用 `send_file` 回傳；`after_this_request` 刪 temp 檔 |

**為何 zip 用 temp file，不用 zipstream**：stdlib `zipfile` 不支援真 streaming（central directory 需 seek）。`zipstream-ng` 是另一個依賴；本機 server，10GB 寫 temp 不是大問題。CSV 內容仍 chunked 寫進 entry，記憶體可控。

### Frontend（`templates/downloads.html` + `static/downloads.js`）

- 表格：☐ checkbox / view name / row_count（千分位）/ 「Download CSV ↓」 連結（單檔）
- 篩選列：搜尋框（filter by name substring）+ 預設批次按鈕：
  - `Small (≤100k)` `Medium (100k–1M)` `Large (>1M)` `Select All` `Clear`
- 下方：「Download N selected as .zip」按鈕 → submit GET `/download/bundle.zip?v=...&v=...`
- 警告 banner：若 selected total rows > 1M，顯示 estimated size + 「可能很慢」提示

### CSV 細節

- 用 stdlib `csv` 模組逐 row encode；header = column names；用 utf-8。
- escape 規則：包含 `,` `"` `\n` `\r` 的欄位用 `"..."` 包覆，內部 `"` 倍化。
- NULL → 空字串。
- 日期 / Timestamp 用 ISO 格式（`str(v)` 即可）。

### Size 估算（可選；本輪僅 row_count）

不做精確 byte 估算；row_count 已足以讓使用者判斷規模。

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 |
| **M2** | `app.py` 加 3 endpoints + CSV helper |
| **M3** | `downloads.html` + base.html nav + 預設批次按鈕 |
| **M4** | curl 小 view CSV + 2-view bundle.zip 驗證；重啟 5050；commit |

## Fallback

- zip 太大記憶體不夠 → temp file 在 disk，OOM 不會發生（zipfile 邊寫邊刷）。
- bundle 多 view 一支 query 卡很久 → 每 view 各自 DuckDB cursor + fetchmany；單 view 失敗 logger 印錯誤，繼續下一支（zip entry 不完整則該檔損壞，但其他 view 仍可用）。
- rollback：`git revert` M2/M3；endpoints 與 UI 都 additive，與既有功能無衝突。

## 完成日誌

（M2–M4 後追加）
