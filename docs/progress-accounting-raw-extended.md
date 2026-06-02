# 2026-06-02 — accounting_raw_extended ingest（TEJ 訂閱包 796-col CSV）

## 目標

整合使用者手動匯出的 `C:\Users\User\Downloads\台灣上市公司單季財報資料2005_2025.csv`
進 catalog。新增獨立 view `accounting_raw_extended`（不動現有 `accounting_raw`）：

- 796 cols vs 既有 121 cols（IFRS9 細項展開）
- 範圍 2005-Q2 ~ 2025-Q4 vs 既有 2022-Q1 ~ 2026-Q1
- 1,045 stocks，66,181 rows
- 兩 view 互補：existing API 抓新 + 訂閱包 CSV 補深歷史

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + `scripts/ingest_accounting_raw_extended.py` |
| **M2** | 跑 ingest → `silver/fundamentals/accounting_raw_extended/year=YYYY/`；catalog 註冊；gap_report DATASETS |
| **M3** | catalog rebuild + swap；dashboard 重生；`meta/gap_comments.json` 補註解；收尾 |

## 設計重點（從 peek 結果）

| 觀察 | 處置 |
|---|---|
| 358/796 cols 有 leading whitespace | `df.columns = [c.strip() for c in df.columns]` |
| 759 float64 + 22 int64 + 15 string — clean dtype | 不需強制 cast |
| `年/月` 是 ISO 日期字串 → 轉 `fiscal_month` | `pd.to_datetime` |
| `季別` 1/2/3/4 | 對應 fiscal_quarter |
| 無 column 重複 | OK |
| 227MB CSV | 一次讀沒問題（pandas 估 ~1.5GB memory） |
| 含中文 / 特殊字元（`合併(Y/N)`, `單季(Q)/單半年(H)`） | DuckDB SQL 用 `"..."` quote 即可，preserve as-is |

## 進度日誌

### M1 — 進度檔 + ingest script 骨架  `(M1 commit)`

`scripts/ingest_accounting_raw_extended.py`：自動偵測 `/mnt/c/Users/User/
Downloads/台灣上市公司單季財報資料*.csv`、strip column whitespace、加 7 個
標準欄（stock_id / fiscal_month / fiscal_quarter / period_type / year /
source / ingestion_ts）、dedup by (stock_id, fiscal_month, period_type)、
hive partition by year。

### M2 — 跑 ingest + catalog 註冊  `18ce64b`

- ingest 跑通：66,181 rows / 803 cols / 1,045 stocks / 2005-06-30 ~ 2025-12-31
- silver 寫到 21 個 year partition（2005~2025）
- catalog.py 加 view registration（hive_partitioning + union_by_name）
- gap_report.DATASETS 加 P2 entry
- catalog rebuild swap：**63 → 64 views**

### M3 — dashboard + gap_comments + 收尾

- dashboard 重生：OK 32 / **WARN 5**（accounting_raw_extended max 2025-12-31 →
  153d lag，quarterly 規則 60-120d=WARN，>120d=STALE，剛好踩線；目前 WARN）
- `meta/gap_comments.json` 補一條註解，說明與 accounting_raw 的互補關係

## 對照

| view | 範圍 | rows | cols | 更新機制 |
|---|---|---|---|---|
| `accounting_raw` | 2022~2026 | 240k | 121 | cron auto (TEJ API) |
| `accounting_raw_snapshot` | 同上（純 COPY） | 240k | 121 | derived |
| `accounting_raw_yearly` | 2022~2026 | 5 | 6 | derived summary |
| **`accounting_raw_extended`** | **2005~2025** | **66k** | **803** | **manual ingest** |
| `fundamentals_q` / `fundamentals_pit` | 2026-Q1（最新可獲取） | — | — | TEJ 訂閱包另一條 |

## 後續

- 使用者下次手動匯出新 CSV（涵蓋 2026 Q1+）後直接重跑 ingest，dedup 自動處理
  覆蓋寫入
- 若要做衍生 gold（例如 yearly snapshot / IFRS9 細項 z-score），加進 derived.py
- 可考慮把 `accounting_raw_extended` 與 `accounting_raw` 做 UNION view
  （需 column mapping table；下一輪工程）

## Fallback

```bash
git revert HEAD~3..HEAD
rm -rf silver/fundamentals/accounting_raw_extended scripts/ingest_accounting_raw_extended.py
```
