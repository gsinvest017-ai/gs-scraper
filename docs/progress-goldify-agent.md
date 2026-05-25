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
| **M1** | 本進度檔 + 設計 | ✅ |
| **M2** | `scripts/goldify_audit.py` | ✅ |
| **M3** | `.claude/agents/goldify-100pct.md` | ✅ |
| **M4** | `docs-site/ops/goldify-routine.md` + nav + strict build + commit + push | ✅ |

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

### M2 — `scripts/goldify_audit.py`

178 行純 Python。功能：

1. `load_registry()` 用 `importlib.util` import `gap_report.py` 的 `DATASETS`（先把 module 放進 `sys.modules` 才能跑 dataclass introspection）
2. `load_completeness()` 跑 `gap_report.py --format json` 寫 `meta/audit/gap_report.json` 後讀回。gap_report 對 STALE view 會 exit 2，所以**不能** `check=True`
3. `_proxy_completeness()` 用 `severity=OK` 或 `INFO + abs(lag)<=1` 當成 100%（JSON 不直接帶 completeness_pct，是 HTML 渲染端算的）
4. `suggest_template()` 依 silver schema 對 9 個 factor template 做 best-match
5. 三種輸出：text (stdout) / `--json` / `--markdown`

驗證：
- 正常呼叫（catalog 已 fully goldified）→ ✅ 0 candidates 訊息
- 注入合成 missing gold（patch `gold_paths=()` for tw_stock_bars）→ 正確抓出 1 candidate + 建議 `time_series_bar` template

### M3 — `.claude/agents/goldify-100pct.md`

148 行 markdown，frontmatter `name + description + tools: Bash/Read/Edit/Write/Grep`。沿用 `incremental-crawler.md` 的風格但題目換成 silver→gold。

不變式：
1. 必先跑 audit；0 candidate 直接回報，不動檔
2. 4 milestone 必獨立 commit（M1=plan, M2=builder, M3=registry+catalog, M4=rebuild+dashboard+push）
3. silver multi-ingest dedup 是 builder 責任
4. progress doc 不可省

對應表把 9 個 template 對到既有 builder，未來新人不用挖代碼也能找到該仿哪支：

| template | 樣板 |
|---|---|
| time_series_bar | build_stock_factor_daily |
| flow_rolling | build_inst_flow_factors |
| balance_zscore | build_margin_factors |
| per_entity_oi | build_futures_inst_factors |
| event_panel | build_dividend_calendar |
| boolean_panel | build_stock_attrs_status |
| pit_fundamentals | build_fundamentals_pit |
| view_materialize | materialize_qc_snapshot |
| left_join_merge | materialize_finmind_canonical |

### M4 — `docs-site/ops/goldify-routine.md` + nav + push

`docs-site/ops/goldify-routine.md` 解釋給人類看：
- 「ripe candidate」定義
- audit + agent 使用方式
- 9 個 template 對應表
- 與 incremental-crawler / `/update-doc` 的分工 mermaid

`mkdocs.yml` nav 加 `操作手冊 → Goldify routine (silver→gold)`。

`docs-site/ops/automation.md` 從 2 個自動化資產更新為 3 個。

Strict build PASS（mkdocs-material 內建 MkDocs 2.0 警告無關）。

## 後續

- 下次有 silver 新 view 滿格時，跑 `.venv/bin/python scripts/goldify_audit.py` 應該會看到該 view 列為 candidate
- agent 觸發語：「跑 goldify routine」 / 「audit 一下有沒有沒 goldify 的」
- 可考慮加進 `daily_refresh.sh`（爬完 → ingest → catalog → restore finmind → gap_report → **audit goldify candidates，0 則跳過 / 非 0 則 notify**）

## Live

<https://gsinvest017-ai.github.io/gs-scraper/ops/goldify-routine/>
