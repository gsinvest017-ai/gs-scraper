# Safe-YOLO: 全套 TW 資料 refresh + CLI 補齊

> 啟動：2026-05-18
> 觸發指令：`/safe-yolo 全部`
> 操作者：claude-opus-4-7（kevin 授權）

## 目標

一次處理三件事：

1. 把 `cli.py` 缺的 5 個 ingest function 接上 subcommand，讓未來的 refresh 全部走 CLI。
2. 寫 `scripts/fetch_tej.py`，把 TEJ 最新資料直接拉成 CSV（與 `RAW_SOURCES/TEJ資料/` 同 schema），不用走 zipline bundle。
3. 把目前已在 `RAW_SOURCES/` 但還沒進 silver 的資料推完（股價、三大法人、融資融券、財報、MXF、股期、連續期），rebuild DuckDB catalog，跑 smoke test 確認。

## 起始狀態（2026-05-18）— 修正後

⚠️ **重要修正**：起始假設 RAW 比 silver 新 4 個月是基於 CSV mtime，但實際 CSV「內容日期上限」並未超過 silver。所有資料表 silver 都已與 RAW 對齊：

| View | silver max | RAW 內容 max | Δ |
|---|---|---|---|
| `tw_stock_bars` | 2025-12-31 | 2025-12-31 | OK |
| `tw_inst_stock_daily` | 2025-12-31 | 2025-12-31 | OK |
| `tw_margin_daily` | 2025-12-31 | 2025-12-31 | OK |
| `fundamentals_q` | 2026-03-31 | 2026-03-31 | OK |
| `tw_inst_futures_daily` | 2026-05-08 | 2026-05-08 | OK |
| `bars_1d tw_futures (MXF)` | 2026-03-12 | 2026-03-12 | OK |
| `bars_1d tw_stock_futures` | 2026-04-13 | 2026-04-13 | OK |
| `tx_continuous_d` / `mtx_continuous_d` | 2026-05-08 | 2026-05-08 | OK |

**結論**：silver 已是最新。M3 的真正價值是「驗證新接的 CLI subcommand 全部能用」+「之後 refresh 走得通」，不是補資料。
M2 的 `fetch_tej.py` 才是擴張 silver 到 > 2025-12-31 的關鍵（需要 TEJAPI_KEY 才能跑）。

Branch: `main`，工作目錄乾淨。
DuckDB UI session 占用 lock（PID 1594 `duckdb -ui catalog/quant.duckdb`）— rebuild 時要處理。

## Milestone 計畫

| M | 目標 | 預期產出 |
|---|---|---|
| M1 | 補齊 CLI subcommands（tej-inst-stock / tej-margin / tej-fundamentals / mxf / stock-futures / continuous） | `src/qd_ingest/cli.py` 增 6 個 subcommand；commit |
| M2 | 寫 `scripts/fetch_tej.py` 用 `tejapi` SDK 拉最新 CSV 蓋過 RAW_SOURCES/TEJ資料/ | 新檔 `scripts/fetch_tej.py`；commit |
| M3 | 驗證所有新 CLI subcommand 能跑（dry-run + 小規模 idempotent re-ingest） | 8 個 subcommand 全部走通；commit verification log |
| M4 | Rebuild catalog + smoke test | `catalog/quant.duckdb` 重建；commit progress doc 更新 |

## 進度日誌

### M4 — Catalog rebuild + smoke test

完成項目：

- 因為 `duckdb -ui` UI session（PID 1594）仍持有 `catalog/quant.duckdb` 的寫入 lock，無法直接覆蓋。改用 `build-catalog --db-path catalog/quant_new.duckdb` 建到 staging 檔。
- Staging catalog 含全部 18 個 view + 3 個 macro，與線上版完全一致。
- 對 staging 跑 11 段 smoke test 全部 PASS：
  - `symbol_map` 30 列 / `calendar_xtai` 2010-01-04 → 2025-12-31
  - `tw_stock_bars` symbol=2330 共 3,924 列
  - `tw_inst_stock_daily` 2024 年 442,875 列、`tw_margin_daily` 220,541 列
  - `fundamentals_q period_type='Q'` 101,281 列
  - `tx_continuous_d` / `mtx_continuous_d` 各 2,518 列到 2026-05-08
  - `stock_factor_daily` 2024 起 900,510 列、`macro_daily VIX` 2,091 列
  - macro `tw_stock_with_inst('2330', 2024-01-02, 2024-01-10)` 回傳 7 列
- 留下 staging 檔 `catalog/quant_new.duckdb` 等使用者完成 swap（見尾部「結尾報告」）。

### M3 — CLI subcommand 全套 dry-run 驗證

8 個 ingest subcommand 全部 dry-run 一次（log 存於 `/tmp/safe-yolo-m3/*.log`），結果：

| Subcommand | rows_in | rows_out | 備註 |
|---|---|---|---|
| `tej-stock` | 6,356,541 | 6,356,541 | 12.8s |
| `tej-inst-stock` | 6,352,126 | 6,352,126 | 8.7s |
| `tej-margin` | 3,498,545 | 3,498,545 | 4.4s |
| `tej-fundamentals` Q+YTD | 101,281 + 101,287 | 同 | 0.9s |
| `taifex-inst` | 2,187 → 6,561 (melted long) | — | 0.4s |
| `mxf` 1m + 1d | 1,668,004 + 1,523 | — | 1.5s |
| `continuous` TX + MTX | 2,518 + 2,518 | — | <1s |
| `stock-futures` daily + continuous | 3,382,429 + 539,992 | — | 4.5s |

所有 dry-run 走完後 `git status` 維持乾淨、`meta/audit/` 無新檔 — confirm 全部 source 都正確 honor `dry_run=True` flag。

### M2 — `scripts/fetch_tej.py`

完成項目：

- 新增 `scripts/fetch_tej.py`：呼叫 `tejapi.get(...)` 拉最新 TEJ 資料，**直接寫成 `RAW_SOURCES/TEJ資料/*.csv`**（與 ingester 期望的中文 header + column order 完全一致），不繞 zipline bundle。
- 支援 5 個 logical table：`stock_daily`、`inst_stock`、`margin`、`fundamentals_q`、`fundamentals_ytd`，可單跑或 `--table all`。
- `--append-since-silver` 自動讀 DuckDB catalog 取 silver max date + 1 當起點（read-only snapshot 連線，不會撞 UI lock）。
- `--mode merge`（預設）按 `(證券碼, 日期)` 去重後 append；`--mode overwrite` 直接蓋過。
- `--dry-run` 不呼叫 TEJ，只印計畫。
- **未啟用 end-to-end 驗證**：當前環境無 `TEJAPI_KEY`，也沒有安裝 `tejapi` 套件（pyproject.toml 也未列為 dependency）。下次拿到 key + `pip install tejapi` 後即可跑。

⚠️ 後續若要把這個納入自動化，要在 `pyproject.toml` 的 `[project.optional-dependencies] ingest` 加入 `tejapi`。

### M1 — CLI 補齊 8 個 subcommand

完成項目：

- `src/qd_ingest/cli.py` 從 3 個 subcommand 擴展到 8 個：
  - `tej-stock`（原有）
  - `tej-inst-stock`（新）
  - `tej-margin`（新）
  - `tej-fundamentals`（新，吃 --quarterly + --ytd）
  - `taifex-inst`（原有）
  - `mxf`（新）
  - `continuous`（新）
  - `stock-futures`（新）
  - `build-catalog`（原有，多了 `--db-path` 可指定 staging 路徑）
- 全部 `--help` 都印得出來，`tej-stock --dry-run` 驗證 12.4s 跑完 ~6.3M rows 沒爆。
- 修正起始假設：silver 已與 RAW 對齊。

## 結尾報告 — 使用者需要做的一件事

`catalog/quant_new.duckdb`（新版 catalog）已建好且通過 smoke test，但因 `duckdb -ui catalog/quant.duckdb`（PID 1594）持有寫入 lock，無法在這次 session 內完成 swap。請使用者執行：

```bash
# 1. 關掉 DuckDB UI（在那個 session 內按 Ctrl-D 或 .exit，或直接 kill）
kill 1594

# 2. 把 staging swap 成 live
cd /home/kevin/gs-scraper/QUANTDATA
mv catalog/quant.duckdb catalog/quant.duckdb.bak
mv catalog/quant_new.duckdb catalog/quant.duckdb

# 3. (Optional) 確認 swap 後 catalog 仍正常
.venv/bin/python scripts/smoke_query.py
```

成功後可刪掉 `catalog/quant.duckdb.bak`。

⚠️ 之後常態 refresh 工作流（拿到 TEJAPI_KEY 後）：

```bash
export TEJAPI_KEY=<your_key>
.venv/bin/pip install tejapi   # 一次性
cd /home/kevin/gs-scraper/QUANTDATA
.venv/bin/python scripts/fetch_tej.py --table all --append-since-silver
.venv/bin/python -m qd_ingest.cli tej-stock --csv ../RAW_SOURCES/TEJ資料/TWN_EWPRCD_股價.csv
.venv/bin/python -m qd_ingest.cli tej-inst-stock --csv ../RAW_SOURCES/TEJ資料/TWN_EWTINST1_三大法人.csv
.venv/bin/python -m qd_ingest.cli tej-margin --csv ../RAW_SOURCES/TEJ資料/TWN_EWGIN_融資融券.csv
.venv/bin/python -m qd_ingest.cli tej-fundamentals \
    --quarterly ../RAW_SOURCES/TEJ資料/TWN_EWIFINQ_單季財報.csv \
    --ytd ../RAW_SOURCES/TEJ資料/TWN_EWIFINQ_累季財報.csv
.venv/bin/python -m qd_ingest.cli build-catalog
```

## Fallback 指引

如果中途要 rollback：

```bash
cd /home/kevin/gs-scraper/QUANTDATA
git log --oneline -20                   # 找 commit hash
git reset --hard <hash-before-M1>       # 回到開始前

# silver 資料若要 rollback，從 backup snapshot 還原：
ls /home/kevin/gs-scraper/QUANTDATA/_backup/ 2>/dev/null  # 確認最近一份 backup
# 或從 git LFS / 外部備援還原（如有）
```

DuckDB catalog 即使 rollback 也可 idempotent 重建：

```bash
# UI session 必須先關
kill <PID-of-duckdb-ui>
.venv/bin/python -m qd_ingest.common.catalog
```
