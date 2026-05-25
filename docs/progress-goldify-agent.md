# 2026-05-25 — Goldify agent + audit pipeline

## 觸發

`/safe-yolo 寫一個做 data engineering 處理讓 data catalog 中 complete rate 100% 的 catalog 還沒有完全變成 gold level 的 catalog 處理成 gold level 的 data pipeline routine agent command`

## 目標

把過去 3 輪手動執行的 「goldify 100% silver→gold」流程包裝成 **可重複呼叫的 agent + audit script**，未來只要 silver 又長出新 100% 完整度 view，跑一次 agent 就能自動完成偵測 → 設計 → 建 builder → wire registry → regen dashboard → commit。

## 範圍

3 個產出：

1. **`scripts/goldify_audit.py`** — 機器可讀的 audit script
   - 讀 `catalog/quant.duckdb` + `scripts/gap_report.py` 的 `DATASETS` registry
   - 找出 「completeness 100% 且 `gold_paths` 為空」的 view（排除 INFO snapshot 與 reference）
   - 輸出 markdown 報告：每個 view 的 schema、列數、建議 factor 設計（heuristic：時序資料就 mom/vol、事件資料就 cum/yoy）
   - 也輸出 JSON 給後續自動化吃

2. **`.claude/agents/goldify-100pct.md`** — Agent definition
   - 描述何時觸發（使用者說「goldify 剩下的」/「100% 沒 gold 全部變 gold」/「跑 goldify routine」等）
   - 不變式：必跑 audit → 必更新 derived.py / gap_report.py / catalog.py 三檔 → 必 regen dashboard → milestone commit
   - 參考既有的 `incremental-crawler.md` 風格

3. **`docs-site/ops/goldify-routine.md`** — 使用者文檔
   - 解釋這個 routine 在 medallion 中的位置
   - audit script 用法
   - agent 觸發語法
   - 何時該手動跑 vs 何時讓 daily_refresh.sh 順帶跑

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + 設計 | ⏳ |
| **M2** | `scripts/goldify_audit.py` | ⏳ |
| **M3** | `.claude/agents/goldify-100pct.md` | ⏳ |
| **M4** | `docs-site/ops/goldify-routine.md` + nav + strict build + commit + push | ⏳ |

## Fallback

- audit script 抓不出來時：直接讀 `scripts/gap_report.py DATASETS` 看哪些有 `silver_paths` 而 `gold_paths` 為空
- agent 跑壞：手動執行 M2-M4 即可（已在 3 個歷史 progress doc 留下範本）

## 設計重點

### Heuristic factor templates

audit script 依 silver schema 自動推薦 factor 類型，但**不自動產生 builder**（手動撰寫安全）：

| Schema 特徵 | 推薦 factor 類型 |
|---|---|
| `trading_date + stock_id + close/volume` | Time-series factors: mom/vol/turnover (見 stock_factor_daily) |
| `trading_date + stock_id + net_lot/balance` | Flow factors: rolling sum / z-score / persistence (見 inst_flow_factors) |
| `trading_date + identity_code + oi/volume` | Per-entity factors: net change / L/S ratio |
| `(ex/adjust/announce)_date + ...` | Event panel: cum sum / yoy / TTM / days-since |
| Many `is_*` varchar flags | Boolean panel + rolling counts (見 stock_attrs_status) |
| Pure view (no parquet on disk) | Materialize to parquet snapshot (見 qc_stock_price_diff_snapshot) |

### 不做自動建 builder 的理由

每個 factor 設計都需要 domain knowledge（哪些是 leading indicator、哪個 window 對台股有效、要不要 PIT correction）。  
audit 提案 + 人類拍板比「黑箱自動生」更穩。

## 完成日誌

（待 M2-M4 完成後追加）
