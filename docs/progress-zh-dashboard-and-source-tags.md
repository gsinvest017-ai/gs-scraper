# 2026-06-02 — 中文 dashboard + 資料源標籤 + 詳細描述

## 目標

1. **gap_dashboard 中文 header**：所有欄位 label 改中文（既有英文 header 看起來像 ops tool；中文 user 不直觀）
2. **資料源（data_source）標籤**：每筆 Dataset 加一個 enum：`FinMind` / `TQuant-Lab` / `TEJ-API` / `TEJ-訂閱包` / `yfinance` / `TAIFEX` / `TWSE` / `Yahoo-extracted` / `derived` / `manual-RAW` / `other`，dashboard 多一欄展示 + 可篩
3. **詳細描述（long_description）**：每筆 Dataset 加一段 1-3 行的「裡面放了什麼資料」中文說明，hover/click 看詳細

## 計畫 milestone

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + 擴 `Dataset` dataclass 加 `data_source` + `long_description` 兩欄；定義 source enum |
| **M2** | 為現有 ~47 條 Dataset 補 `data_source` + `long_description`（依現有 `description` + 對 `fetch_cmd` 推斷 source） |
| **M3** | gap_report.py HTML template 改中文 header；加「資料源」與「詳細說明」column；詳細說明用 `<details>` tag 摺疊或 hover tooltip |
| **M4** | 重生 dashboard + commit + 收尾 |

## data_source 分類規則

| Enum 值 | 來源辨識 |
|---|---|
| `TEJ-API` | `fetch_tej.py` 抓 TWN/Axxx (API-flavored) tables |
| `TEJ-訂閱包` | 手動匯出 CSV，例 `accounting_raw_extended`、`fundamentals_q` |
| `FinMind` | `bronze/finmind/finmind_*.sqlite`，由 `scripts/fetch_finmind.py` 抓 |
| `TQuant-Lab` | `RAW_SOURCES/日k 期貨tquant lab/`、`MXF_1m_clean_all.parquet`、`TXO_1min_merged_*.parquet` 等 tquant lab dump |
| `yfinance` | `scripts/fetch_macro.py` 抓 ^VIX/^TWII/USDTWD 等 |
| `TAIFEX` | TAIFEX OpenAPI（三大法人 / OI / large trader） |
| `TWSE` | TWSE web scraping |
| `Yahoo-extracted` | `RAW_SOURCES/三大法人買賣超/institutional_yahoo_value_clean.csv`（Notion + Yahoo） |
| `derived` | 純 silver→gold transformation，無外部新資料 |
| `manual-RAW` | 其他手動 dump（rf_daily CSV、cross_market_features 等） |

## 進度日誌

### M1 — 進度檔 + DATA_SOURCES enum 定義  `(M1 commit)`

10 種 source enum：`TEJ-API` / `TEJ-訂閱包` / `FinMind` / `TQuant-Lab` /
`yfinance` / `TAIFEX` / `TWSE` / `Yahoo-extracted` / `derived` / `manual-RAW` /
`other`。

### M2 — Dataset class 擴 + 50 條 metadata  `6f39acb`

- Dataset dataclass 加 `data_source` + `long_description` 兩欄（default
  `"other"` / `""`，向後相容）
- `__post_init__` 從 module-level lookup dict `_DATASET_META` 自動填，
  避免改 50 條 Dataset() 呼叫
- 50/50 view 全有 metadata。Source 分布：
  - TEJ-API ×14、derived ×23、TQuant-Lab ×5、FinMind ×2、TEJ-訂閱包 ×2、
    yfinance ×1、manual-RAW ×2、Yahoo-extracted ×1

### M3 — HTML 中文 header + source pill  `(M3 commit)`

- table header 全中文化（狀態 / Tier / View 名稱 / 說明 / **資料源** /
  類別 / 最新日期 / 延遲 / 完整度 / 填滿條 / Raw / Bronze / Silver / Gold /
  Catalog 列數 / 建議操作 / 註解）
- 「說明」column 加 ⓘ icon，hover title 顯示 `long_description`
- 「資料源」column 用彩色 pill，每個 source 一色（gold/blue/green/orange/...）
- legend 區整段中文化，加 source 速查；probe row 補 `long_description` /
  `data_source` 兩欄

## 視覺

每個資料源一個 pill：

| Source | 顏色 |
|---|---|
| TEJ-API | gold |
| TEJ-訂閱包 | champagne |
| FinMind | blue |
| TQuant-Lab | green |
| yfinance | orange |
| TAIFEX | copper |
| TWSE | bronze |
| Yahoo-extracted | rose-red |
| derived | grey |
| manual-RAW | amber |

## 後續

- 加 **source filter**（dashboard 上方一排 pill button 可篩）—— 下一輪 JS 工程
- 加 **「複製 view 清單為 markdown table」按鈕**（給 chat 貼） — 已有
  `/copy-commits-button` skill 可參考
- `long_description` 後續可以從 `meta/gap_comments.json` 同步進來（user
  editable 的長描述）

## Fallback

```bash
git revert HEAD~3..HEAD
git checkout HEAD~3 -- scripts/gap_report.py
```
