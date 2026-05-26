# 2026-05-26 — `/goldify-100` 範圍修正（review + rewrite）

## 觸發

`/safe-yolo /code-review 幫我 review goldify-100 此 agent command 的描述是否不符合我的目標: 將所有含有非 gold 品質(raw, bronze, silver) 的資料的 data catalog 都處理成 gold 沒有處理成 gold 不要停下來 如果不符合請幫我修改此 command 功能`

## Code Review — current `/goldify-100` 與目標的差距

### 使用者目標

> 將所有含有非 gold 品質（raw / bronze / silver）的資料的 data catalog 都處理成 gold；**沒有處理成 gold 不要停下來**。

### 目前實作

**`scripts/goldify_audit.py`** L207-211：
```python
pct = _proxy_completeness(comp)
if pct != 100.0:
    continue  # ← 過濾掉所有非 100% 完整度的 view
```

**`.claude/commands/goldify-100.md`** 描述：
> The goal: every catalog view **at 100% completeness** (`OK` severity) must have at least one entry in `gold_paths`...

**結論：目前實作只處理「100% 完整度」的 view，與使用者目標「所有非 gold 品質的 view」不符。**

### 範圍差距具體影響

跑現在的 audit：「✅ no 100%-complete views are missing gold」。

但 dashboard 上 STALE 或 INFO 的 view 其實有 silver 但沒 gold：

| view | tier | severity | silver max | rows | 為何沒被處理 |
|---|---|---|---|---:|---|
| `tw_inst_futures_daily` | P0 | STALE 17d | 2026-05-08 | 6,561 | 完整度不足 84%（< 100%），被現實作跳過 |
| `bars_1m` | P2 | STALE 74d | 2026-03-12 | 15.6M | 完整度 21%，被跳過 |
| `macro_daily` | P1 | STALE 18d | 2026-05-07 | 91K | 完整度 80%，被跳過 |
| `txo_daily_features` | P2 | STALE 54d | 2026-04-01 | 1,481 | 完整度 40%，被跳過 |
| `tw_inst_market_daily` | P2 | STALE 39d | 2026-04-16 | 15 | 完整度 57%，被跳過 |

5 個 view 都有 silver 資料（從幾千列到 15M 列），但因為沒人寫 scraper / manual update / 上游不齊全，停在某個過去的日期。**從「gold 品質」角度，它們本來就該做成 gold parquet snapshot**，gold 反映「截至 silver 當前 max_date」的快照，使用者要更新就重跑爬蟲再 rebuild gold。

### 命名問題

`goldify-100` 名字會誤導：
- 字面：「100% 完整度才 goldify」← 現實作的 narrow scope
- 使用者意圖：「100% 把所有 catalog goldify」← broader scope

決定 **保留 `goldify-100` 命名**（不重新命名以免散播改動），但改寫描述把 "100" 重新詮釋為「100% catalog coverage」而非「100% per-view completeness」。

## 修正方案

### M2 — `scripts/goldify_audit.py` 範圍擴大

把過濾條件從：
```python
if pct != 100.0:
    continue
```

改成：
```python
# 接受任何「有 silver/bronze/raw 但沒 gold」的 view，不論完整度
if not (ds.silver_paths or ds.bronze_paths or ds.raw_paths):
    continue  # 真的什麼都沒有就跳（避免 EMPTY view 進來）
if not comp or comp.get("row_count", 0) == 0:
    continue  # row_count=0 也跳
```

同時報告中增加 `severity` 與 `completeness_pct` 兩欄，讓使用者知道哪些 cand 是「100%」、哪些是「STALE但已有資料」。

新增 `--complete-only` flag 保留舊行為（向後相容）。

### M3 — `.claude/commands/goldify-100.md` 描述+ loop 修正

描述改寫：
> Goldify every catalog view that has non-gold data (silver/bronze/raw with `row_count > 0`) and no `gold_paths` backlink. Loop until 0 candidates or stuck — regardless of upstream staleness. Gold reflects "as of current silver" snapshot; refresh upstream separately if you want fresher gold.

Loop 主要邏輯不變（4 milestones / 上限 5 輪 / stuck 偵測）。

### M4 — 跑新 audit + goldify + push

- 預期會抓出 5 個 STALE 候選
- 每個用 audit 自動 suggest 的 template 對應 model-after builder
- 套標準 4-milestone 跑完

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + code review | ✅ |
| **M2** | 修 `scripts/goldify_audit.py` 範圍 + 加 `--complete-only` flag | ✅ |
| **M3** | 改寫 `.claude/commands/goldify-100.md` + `docs-site/ops/goldify-routine.md` | ✅ |
| **M4** | 跑新 audit；如有 cand 則 goldify；regen dashboard；commit；push | ✅ |

## Fallback

- 若 M2 audit 改錯把已 goldify 的 view 也報出來，先用 `--complete-only` 回到舊行為救急
- 5 個 STALE view 中 bars_1m 列數最大（15M），builder 可能 > 10s；其餘都應該 < 1s
- 若某些 view 的 gold 設計需要 domain knowledge（e.g. macro_daily 的因子）→ 用 `view_materialize` 先把 silver materialize 成 gold snapshot，避免卡住 loop

## 完成日誌

### M2 — audit 範圍擴大

`scripts/goldify_audit.py`：
- `audit(complete_only=False)` 參數；預設不過濾完整度，改成「`row_count > 0` AND `gold_paths` 為空」
- 候選 `Candidate` 新增 `severity` 欄位（OK / WARN / STALE / INFO）
- text / markdown 輸出加完整度 % + severity 標記，分「100% 完整」與「partial」兩群
- CLI 加 `--complete-only` flag 保留舊行為

驗證：新 audit 抓出 5 個 STALE 但有 silver 的 view（tw_inst_futures_daily / bars_1m / macro_daily / txo_daily_features / tw_inst_market_daily）。

### M3 — command + docs 改寫

`.claude/commands/goldify-100.md` description 更新：
> Goldify EVERY catalog view that has non-gold data (silver/bronze/raw) and no gold_paths — regardless of completeness. Loop until 0 candidates or stuck.

加 reinterpretation 段：「"100" 重新詮釋為 100% catalog coverage of gold，而非 100% per-view completeness」。

`docs-site/ops/goldify-routine.md` 同步：新增「2026-05-26 範圍更新」對照框、「candidate」3 條件改為 row_count>0 + 空 gold_paths + 可 DESCRIBE。

Skills list 確認 description 已生效。

### M4 — 跑 5 個新 builder + 收斂

5 個 builder 加進 `derived.py`（用 `_materialize_view_snapshot()` helper 減少重複）：

| gold view | 來源 | rows | 設計 | elapsed |
|---|---|---:|---|---:|
| `tw_inst_futures_daily_snapshot` | catalog view | 6,561 | DuckDB COPY snapshot | 0.1s |
| `txo_daily_features_snapshot` | catalog view | 1,481 | DuckDB COPY snapshot | 0.1s |
| `tw_inst_market_daily_snapshot` | catalog view | 15 | DuckDB COPY snapshot | 0.0s |
| `bars_1m_daily_summary` | catalog view | 14,859 | DuckDB GROUP BY 1d (15.6M → 15K) | 1.4s |
| `macro_factors` | silver parquet | 91,048 | Polars: ret/vol/atr per 45 symbols | 0.5s |

`scripts/gap_report.py` + `src/qd_ingest/common/catalog.py`：5 個 backlink + 5 個新 Dataset 條目 + 5 個 catalog view 註冊。

**Re-audit**: `goldify_audit` 報告 **0 candidates** → ✅ 收斂。

Dashboard 變化：

| 指標 | M3 末 | M4 末 |
|---|---|---|
| OK | 28 | 28 |
| WARN | 0 | 0 |
| STALE | 6 | 11 |
| EMPTY | 1 | 1 |
| INFO | 5 | 5 |
| Total datasets | 40 | 45 |

**注意 STALE 從 6 → 11**：5 個新 gold snapshot Dataset 條目繼承原 silver 的 max_date（5/7、5/8、4/16 等），按 dashboard category lag 規則仍是 STALE。**這不是 bug，是設計**：gold 是「截至 silver max_date」的快照；上游 silver 不更新，gold 也不更新（從 dashboard 「**100% catalog coverage of gold**」角度看任務已完成，每個非 gold 的 view 都有 gold backlink，目標達成）。

## 與使用者目標的 alignment 確認

| 使用者目標 | 達成？ |
|---|:---:|
| 所有含非 gold 品質的 view 都有 gold | ✅ `goldify_audit` 報 0 |
| 沒處理成 gold 不要停 | ✅ loop 跑到收斂 |
| 完整度不是過濾條件 | ✅ STALE 也被處理 |
| `/goldify-100` 名稱保留 | ✅（reinterpreted as 100% coverage）|

## Live

<https://gsinvest017-ai.github.io/gs-scraper/gap_dashboard.html>
