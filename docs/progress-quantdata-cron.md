# quantdata-cron — 每天定期抓 TEJ 新資料

> 啟動：2026-05-21
> 觸發指令：`/safe-yolo 幫我寫一個每天定期排程定期抓取TEJ新的資料的爬蟲`
> 操作者：claude-opus-4-7

## 目標

把現有 `scripts/fetch_tej.py`（已支援 12 個 logical table、`--append-since-silver`、`--mode merge`）封裝成可由 cron 排程的每日批次：

1. 拉最新 TEJ 資料到 RAW + silver
2. 跑 ingest CLI 把 CSV-backed 三張表（股價/三大法人/融資融券）推進 silver
3. Rebuild DuckDB catalog（含 UI lock fallback）
4. 完整 log 落到 `meta/audit/daily_refresh_<YYYY-MM-DD>.log`
5. 安裝 idempotent crontab，每天台股收盤後（17:30 CST）跑

整套要能在 cron 環境下（無 fish env、無互動 shell）獨立執行；script 自己會從 `~/.config/fish/fish_variables` 解 TEJAPI_KEY / TEJAPI_BASE。

## 起始狀態（2026-05-21）

- `scripts/fetch_tej.py` 已成熟（M5 完成 P0+P1+P2 所有 logical table）
- TEJAPI_KEY / TEJAPI_BASE 已存於 `~/.config/fish/fish_variables`（fish universal var）
- 既有 crontab 已有 `gs-claude-config night-shift` 區塊（每天 0:00 跑 6h），不可破壞
- DuckDB catalog 偶爾被 `duckdb -ui` UI session lock — 必須走 staging 路徑避開
- Branch: `main`；working tree 乾淨（除上次遺留的 `meta/audit/ingest_2026-05-18.jsonl`）

## Milestone 計畫

| M | 目標 | 預期產出 |
|---|---|---|
| M1 | `scripts/daily_refresh.sh` 整合 fetch + ingest + catalog rebuild | 一個可獨立跑的 bash 腳本，含 flock / log / staging swap |
| M2 | `scripts/install_cron.sh` idempotent 安裝 crontab 區塊 | 不破壞既有 night-shift 區塊；附 uninstall 指令 |
| M3 | dry-run smoke + 實際安裝 cron | crontab 含新區塊；script 跑得通 |
| M4 | 進度檔最終化 + commit | 全部納入 git；後續可由 `git log` 復原 |

## 進度日誌

### M1 — daily_refresh.sh

完成項目：

- 新增 `scripts/daily_refresh.sh`（154 行）。執行流程：
  1. `flock /tmp/quantdata_daily_refresh.lock` 防止並發
  2. 若 `$TEJAPI_KEY` 未設，從 `~/.config/fish/fish_variables` 解 `SETUVAR --export TEJAPI_KEY:...` 行（用 Python 解 `\xHH` escape）
  3. `python scripts/fetch_tej.py --table all --append-since-silver --mode merge`
  4. 對 CSV-backed 三張表（股價/三大法人/融資融券）跑 `qd-ingest tej-{stock,inst-stock,margin}`
  5. 用 `fuser` 偵測 catalog lock；若有 UI session 持有 → 寫到 `catalog/quant_refresh.duckdb` staging，提示手動 swap；否則 build-catalog 到 staging 後 atomic mv
  6. 所有 stdout/stderr 追加到 `meta/audit/daily_refresh_<YYYY-MM-DD>.log`
- Exit codes 設計：0 ok / 1 fetch / 2 ingest / 3 catalog / 10 locked / 11 missing-env / 130 signal
- `chmod +x` 已套用

### M2 — install_cron.sh

完成項目：（待補）

### M3 — dry-run + cron 安裝

完成項目：（待補）

### M4 — Commit + 收尾

完成項目：（待補）

## Fallback 指引

### 暫停每日排程

```bash
# 移除新增的 crontab 區塊（保留 gs-claude-config night-shift）
/home/kevin/gs-scraper/QUANTDATA/scripts/install_cron.sh --uninstall
```

### 完整 rollback

```bash
cd /home/kevin/gs-scraper/QUANTDATA
git log --oneline -10                          # 找這次的 commit hash
git revert <hash>                              # 或 git reset --hard <hash-before>
/home/kevin/gs-scraper/QUANTDATA/scripts/install_cron.sh --uninstall
```

### 手動跑一次（不靠 cron）

```bash
cd /home/kevin/gs-scraper/QUANTDATA
bash scripts/daily_refresh.sh
tail -f meta/audit/daily_refresh_$(date +%Y-%m-%d).log
```

### 災難復原：catalog 壞掉

`daily_refresh.sh` 建 catalog 時走 staging（`catalog/quant_refresh.duckdb`），swap 失敗會留 `catalog/quant.duckdb.prev`。要 rollback：

```bash
mv catalog/quant.duckdb.prev catalog/quant.duckdb
```

若連 staging 都壞，直接重跑 `qd-ingest build-catalog` 即可（idempotent）。
