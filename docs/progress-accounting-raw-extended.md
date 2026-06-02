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

## Fallback

```bash
git revert HEAD~3..HEAD
rm -rf silver/fundamentals/accounting_raw_extended scripts/ingest_accounting_raw_extended.py
```
