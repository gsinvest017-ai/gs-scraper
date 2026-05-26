# 2026-05-26 — Search UI bugfix（filter 丟失 + chart 蛛網）

## 觸發

使用者貼 `query-bug.png` 截圖，呼救「query 結果做時間序列視覺化會出現此問題」。

## 觀察 + Root cause

### 截圖事實

- view: `bars_1m`，filter `trading_date ≥ 2025/05/26`，order by `ingestion_ts DESC`、Limit 1000
- 結果頁 SQL preview: `SELECT * FROM bars_1m ORDER BY "ingestion_ts" DESC LIMIT 1000`（**沒 WHERE！**）
- Chart X=ts_utc / Y=close / group_by=asset_class → 顯示 2022-01-03 的資料（早於使用者設的 5/26 filter）
- Chart 線條來回交叉成蛛網

### 3 個 bug

| # | 嚴重度 | 位置 | 根因 |
|---|---|---|---|
| **1** | 🔴 critical | `static/main.js` `refreshOps()` | `val` 是 closure 捕的 input ref；第一次 `val.replaceWith(newVal)` 後 val 變孤兒元素。使用者改 col/op 觸發 refreshOps，`val.value` 讀到孤兒（空字串）、`val.replaceWith(newVal)` no-op。`_valEl` 指到 orphan、DOM 還是舊 input。Run query 時 `collectPayload` 讀 `row._valEl.value=""` → filter 整個被 skip |
| **2** | 🔴 critical | `static/main.js` chart 區塊 | 結果是 `ORDER BY ingestion_ts DESC` 排序，但 chart X 軸要看 `ts_utc` 升冪。Plotly `mode:'lines'` 按 array 順序連線 → X 不單調 → 蛛網 |
| **3** | 🟡 minor | `static/main.js` L116 | `val.addEventListener('input', ...)` 綁到孤兒，DOM 上的實際 input 無 listener，SQL preview 不即時更新 |

## 修法

### Bug #1 — refreshOps 用動態 ref

```diff
- const oldVal = val.value;
+ const currentEl = row._valEl || val;
+ const oldVal = currentEl && currentEl.value !== undefined ? currentEl.value : '';
...
- val.replaceWith(newVal);
+ currentEl.replaceWith(newVal);
```

Plus 把 `input` / `change` listener 綁到 `newVal`（每次切換都掛新的）。

### Bug #2 — chart 用 sortByX helper

新增 `sortByX(xs, ys)` 函式，按 X 升冪重排兩個 array 後再交給 Plotly。對 group_by 也是每組獨立排序。

副產品：`mode: 'lines'` → `mode: 'lines+markers'`，當資料疏密不均時 markers 能顯示資料點位置。
group_by 產生 > 20 series 時 console.warn（不再靜默截斷）。

### Bug #3 — listener 綁新 element

`newVal.addEventListener('input', updateSqlPreview)` + `change` 事件兩種一起綁。

## 驗證

API smoke test：

```bash
curl -X POST http://127.0.0.1:5050/api/query \
  -d '{"view":"bars_1m","filters":[{"column":"trading_date","op":"date_from","value":"2025-05-26"}],...}'

# 修前: SQL: SELECT * FROM bars_1m ORDER BY "ingestion_ts" DESC LIMIT 1000  (no WHERE)
# 修後: SQL: SELECT * FROM bars_1m WHERE "trading_date" >= ? ORDER BY "ingestion_ts" DESC LIMIT 5
#       params: ['2025-05-26']
#       rows[0]: '2025-06-27T02:53:00+08:00' ✅ (>=  2025-05-26)
```

Bug #2 + #3 是純 client-side，需手動在瀏覽器驗，無 unit test framework。

## Commit

單一 commit，因為三個 bug 都在同一檔案 + 同一段邏輯，分開 commit 反而難 review。
