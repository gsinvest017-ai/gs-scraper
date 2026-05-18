# FinMind + RS_Rating 整合設計與進度

> 建立日期：2026-05-18
> 範圍：把 `RAW_SOURCES/FINMIND資料集.zip`（800 MB，內含 2.4 GB SQLite）與 `RAW_SOURCES/RS_Rating.7z`（287 MB，PyInstaller bundle）整合進 QUANTDATA medallion lakehouse。
> 目標讀者：QUANTDATA 維運者（人 + 其他 Claude session）。

---

## 目標

1. 把 FinMind 那份 2.4 GB SQLite 裡 **真正能補上 TEJ 不足的部分** 接進現有 silver schema（不要重複 ingest 10M 列已有資料）。
2. 把 RS_Rating 的 IBD 風格 RS 評分**演算法**重新實作成 `gold/` 的派生因子，餵 source 用我們已有的 `tw_stock_bars`，而不是 RS_Rating 自己的 crawler。
3. 留下對帳（QC）機制：TEJ vs FinMind 在 2010+ 的重疊段做 cross-check。

## 非目標

- 不打算 ingest FinMind tick（378K 列、只爬到 1 天 / 296 檔，<0.01% 完成率，無實用價值）。
- 不打算把 FinMind 爬蟲專案搬進 QUANTDATA repo；那是一個獨立的 dump pipeline，留在自己的 home。
- 不打算在 Linux/WSL 跑 `RS_Rating.exe`（PyInstaller Windows bundle，跑不起來；硬要等 Windows 端有人雙擊才會有 `rs_rating_data/`）。
- 不打算把 RS_Rating bundle 進 bronze；它沒有 immutable 資料，只是 Python source + DLL。

---

## 兩包來源的真相

### `RAW_SOURCES/RS_Rating.7z` (287 MB)

| 內容 | 比例 | 是否要留 |
|---|---|---|
| `_internal/` DLL + Python 3.12 runtime + pyarrow / numpy.libs | ~95% | 全丟 |
| `test_venv/` | ~4% | 全丟 |
| `RS_Rating.exe` (26 MB PyInstaller launcher) | ~1% | 不要 |
| `_internal/app.py` + `_internal/core/*.py` + `_internal/tests/` | <0.5% | **留** — 是 RS 演算法的權威實作 |
| `AI_CONTEXT.md` / `ARCHITECTURE.md` / `HANDOFF.md` / `OPERATIONS.md` / `TEST_REPORT.md` / `使用說明.txt` | <0.1% | **留** — 演算法與資料庫 schema 文件 |

**結論**：抽出 Python source + docs 共約 50 KB 進 `_quarantine/rs_rating_unpacked/` 當參考實作，其餘忽略。原 7z 留在 `RAW_SOURCES/` 不動。

### `RAW_SOURCES/FINMIND資料集.zip` (805 MB → 解壓 2.5 GB)

唯一一個大檔：`FINMIND資料集/data/finmind.sqlite` (2.4 GB)。其餘是爬蟲 Python 專案（~70 KB）。

**`SESSION_HANDOFF.md` 列出的 6 張完整資料表（snapshot 日 2026-05-14）：**

| FinMind table | 列數 | 涵蓋 | 與 QUANTDATA 現況的關係 |
|---|---:|---|---|
| `taiwan_stock_info` | 3,088 | 全市場含興櫃 | 重疊 `reference/symbol_map`（symbol_map 沒興櫃） |
| `taiwan_stock_info_with_warrant` | 126,311 | 含權證快照 | 沒有 → 可獨立放 `reference/` |
| `taiwan_stock_price` | 10,578,728 | **2000-01-04** ~ 2026-05-15、3,087 檔 | 與 TEJ `tw_stock_bars` 2010+ 重疊；**TEJ 沒有 2000-2009** |
| `taiwan_stock_price_adj` | 10,571,636 | 還原權息 | TEJ 有自己的 `除權息調整價`；FinMind 用 FinMind 自己的還原方法 |
| `taiwan_stock_week_price` | 2,225,018 | 週 K | 可從日 K 推算；不需獨立存 |
| `taiwan_stock_trading_date` | 6,512 | 2000-2026 交易日曆 | 重疊 `calendar_xtai`，FinMind 多 2000-2009 |

**Tick (`taiwan_stock_price_tick`)**：只 378K 列、僅 2026-05-14 一天 × 296/2,721 檔，丟棄。

**40+ 個未爬的 dataset**：USStockPrice、News、可轉債、技術指標等 — **多數已被 TEJ + TAIFEX 路線覆蓋**，少數獨特的（USStockPrice、TaiwanStockNews）暫不在範圍內。

### FinMind 真正能補上 TEJ 的洞

1. **2000-01 ~ 2009-12 共 10 年歷史**：TEJ 從 2010 開始，FinMind 補這段。
2. **興櫃 (emerging) 約 100+ 檔股票** TEJ universe 沒有。
3. **獨立的還原權息序列** — 跟 TEJ 互為 cross-check，找出兩邊不一致的 `(stock_id, date)`。

---

## 整合架構（推薦：方案 B）

### 路線

```
RAW_SOURCES/FINMIND資料集.zip           (immutable, 不動)
        │
        │  unzip
        ▼
bronze/finmind/finmind_2026-05-18.sqlite   (+ .sha256)
        │
        │  DuckDB sqlite scanner: SELECT WHERE date < '2010-01-01'
        ▼
silver/bars/asset_class=tw_stock/source=finmind/year=YYYY/*.parquet
        │
        │  catalog/quant.duckdb 更新 bars_1d view:
        │  TEJ 2010+  UNION ALL  FinMind 2000-2009
        ▼
bars_1d  view  (帶 source 欄位區分)


reference/symbol_map_with_warrant.parquet  ← taiwan_stock_info_with_warrant

catalog/quant.duckdb 多一個 view:
qc_stock_price_diff  ← TEJ ⨯ FinMind 2010+ 重疊段對帳
```

### 為何不是方案 A 或 C

- **方案 A（直接 ATTACH，不 ingest）**：SQLite 不是列存，10M 列跨年查詢慢；schema 欄名 `max/min` 跟 silver 的 `high/low` 對不齊，下游每個 query 都要 rewrite。**只用來探索、不是 production**。
- **方案 C（全量 ingest）**：2010+ 兩邊都有，重複 ~10M 列。下游每次 query 都要選 source、儲存空間翻倍。**不划算**。

### 邊界條件

- TEJ 始終是 2010+ 的 **canonical** 來源；FinMind 只負責 2000-2009 + 興櫃補洞。
- `bars_1d` view 帶 `source` 欄位，預設不過濾；下游想只看 TEJ 可加 `WHERE source = 'tej'`。
- 若 QC 發現 TEJ vs FinMind 2010+ 差異 > 0.5% 的 row 數佔比 > 1%，停下來重審 FinMind 還原方法。

---

## Milestone 規劃

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 寫本份進度文件（設計 + 路線） | ✅ |
| **M2** | 解 RS_Rating 7z 抽出 source code → `_quarantine/rs_rating_unpacked/` + 留 manifest | ✅ |
| **M3** | 寫 `docs/spec-gold-rs-rating-daily.md`（演算法規格 + DuckDB SQL skeleton，**不執行**） | ⏳ |
| M4 | 等 `quantdata-scraper` session 完成 TEJ 寫入，解 FinMind zip 到 `bronze/finmind/finmind_2026-05-18.sqlite` + SHA256 + manifest | 未開始 |
| M5 | DuckDB ATTACH FinMind + 寫 `qc_stock_price_diff` view（100 檔 sample 對 TEJ 5%） | 未開始 |
| M6 | 若 QC 通過：寫 `src/qd_ingest/finmind.py` 跑 2000-2009 silver 補完 | 未開始 |
| M7 | 更新 `bars_1d` view + 加 `source` 欄位 + `reference/symbol_map_with_warrant.parquet` | 未開始 |
| M8 | （選）實作 `gold/stock_factor_daily.rs_rating` 因子表 | 未開始 |

**這次 /safe-yolo 只跑 M1 + M2 + M3**。M4 之後等下個 session 或人類接手。

---

## 進度日誌

### M1 — 設計文件落地（此檔案）

- 把上一輪對話的分析整理成單一進度文件，方便其他 session 接手。
- 結論：RS_Rating 是程式不是資料 → 抽 source；FinMind 真有用的部分只有 2000-2009 + 興櫃 → 走方案 B 選擇性合併。
- Commit: 見 `git log` `M1: …` commit。

### M2 — RS_Rating 原始碼抽出

從 `RAW_SOURCES/RS_Rating.7z`（287 MB）中只抽出 15 個檔案（合計 176 KB）到 `_quarantine/rs_rating_unpacked/RS_Rating/`：

- 5 份設計文件（`AI_CONTEXT.md` / `ARCHITECTURE.md` / `HANDOFF.md` / `OPERATIONS.md` / `TEST_REPORT.md` / `使用說明.txt`）
- `_internal/app.py` + `_internal/update_data.py`
- `_internal/core/{__init__, config, db, crawler, indices, backtest}.py`
- `_internal/tests/test_backtest.py`（30 個合成資料 unit test，0 I/O）
- `_internal/.streamlit/config.toml`（深色主題設定，留作 UI 參考）

排除：`_internal/` 下其餘 1.5 GB Python 3.12 runtime + DLL + pyarrow/numpy libs、整個 `test_venv/`、`RS_Rating.exe`、pyc、dist-info。

Manifest 落在 `_quarantine/manifest_rs_rating_2026-05-18.jsonl`（受 `.gitignore` `manifest_*.jsonl` 例外規則納入版控），含原 7z 的 SHA256 與每個抽出檔的 SHA256，便於將來驗證或重抽。

`_quarantine/rs_rating_unpacked/` 本身依 `.gitignore` 規則不入版控，避免把第三方 source 灌進 repo。要刪除直接 `rm -rf` 即可，manifest 留下追溯記錄。

### M3 — pending

---

## Fallback / 接手指引

**若這個 session 中斷，下個人最少要做什麼接手？**

1. 讀本份 `progress-finmind-rsrating-integration.md`。
2. 跑 `git log --oneline | grep -E '^M[0-9]'` 看到哪個 milestone。
3. 若 M2 完成、M3 未完成：直接看 `_quarantine/rs_rating_unpacked/_internal/core/`，照著 `app.py` / `backtest.py` 內 RS 計算邏輯寫 `docs/spec-gold-rs-rating-daily.md`。
4. 若進入 M4+：需要先確認 `quantdata-scraper` session 已釋放 `catalog/quant.duckdb` 寫鎖（`fuser catalog/quant.duckdb` 或 `lsof | grep quant.duckdb`），否則 ingest 會失敗。
5. FinMind zip 解 sqlite 的指令範例：
   ```bash
   cd bronze/finmind && \
   .venv/bin/python -c "import zipfile; \
       z = zipfile.ZipFile('/home/kevin/gs-scraper/RAW_SOURCES/FINMIND資料集.zip'); \
       z.extract('FINMIND資料集/data/finmind.sqlite', '.')" && \
   mv FINMIND資料集/data/finmind.sqlite finmind_2026-05-18.sqlite && \
   rmdir -p FINMIND資料集/data && \
   sha256sum finmind_2026-05-18.sqlite > finmind_2026-05-18.sqlite.sha256
   ```

**Rollback：**

- M1 only：`git revert <m1-commit>` — 純刪文件。
- M2：`rm -rf _quarantine/rs_rating_unpacked/` + `git revert <m2-commit>`（manifest commit）。
- M3：`git revert <m3-commit>`。
