# QUANTDATA

量化資料 medallion lakehouse（bronze → silver → gold）。

📖 **文檔網站**：<https://gsinvest017-ai.github.io/gs-scraper/>（MkDocs Material，每次 push 自動重新發佈）

其他入口：

- 完整設計、schema、Mermaid 圖：[`DATA_ARCHITECTURE.md`](./DATA_ARCHITECTURE.md)
- 分階段實作進度：[`docs/progress-data-arch-impl.md`](./docs/progress-data-arch-impl.md)
- 文檔站源碼：[`docs-site/`](./docs-site/)

## 目錄

```
bronze/      不可變原始檔 (taifex/tej/twse/yahoo/histdata)
silver/      標準化 canonical schema (bars/options/flows/fundamentals/macro)
gold/        research-ready features (features/continuous/universe)
reference/   symbol_map / contract_specs / calendar
catalog/     quant.duckdb (views + macros over silver/gold)
meta/        audit / schema / lineage
src/qd_ingest/   Python ingest pipeline (CLI: qd-ingest)
docs/        進度與設計補充文件
tests/       pytest
scripts/     一次性腳本 (dedup / smoke / migrations)
```

## 快速開始（W1 完成後）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[ingest,dev]"
qd-ingest --help
```

## Stack

- DuckDB + Parquet (zstd) 為主
- Polars / pandas 做 transform
- pandera 做 schema 驗證
- 詳見 `DATA_ARCHITECTURE.md` § 2

## 一鍵搬到另一台主機（migrate）

把整個 repo（程式碼 + `.git` + 18G 資料湖 + DuckDB catalog）以 **rsync-over-SSH**
idempotent 鏡像到另一台主機。catalog 的 view 全用相對路徑（`read_parquet('silver/...')`），
所以目標端只要 repo 樹一致就能原樣開，不必改任何 SQL。

```bash
# 1. 設定目標主機（一次性）
cp scripts/migrate.conf.example scripts/migrate.conf
# 編輯 migrate.conf：MIGRATE_HOST / MIGRATE_PATH / MIGRATE_SSH_PORT
#   （前提：ssh key 已設好，能 `ssh <host> true` 免密登入）

# 2. 先 dry-run 預覽要傳什麼（不會動到目標）
./scripts/migrate_to_host.sh

# 3. 真的傳 + 傳完驗證
./scripts/migrate_to_host.sh --apply --verify

# 之後重跑只送變動（delta sync）；只想比對不傳：
./scripts/migrate_to_host.sh --verify-only
```

- **預設 dry-run**，`--apply` 才寫目標端。
- `--apply` 前自動檢查 catalog 沒被鎖（`duckdb -ui`）並 `CHECKPOINT` 落盤。
- `--verify` 比對 bronze/silver/gold/reference 的檔數與位元組、catalog view 數，
  並對核心 view 跑 row-count smoke（證明目標端透過相對路徑讀得到 parquet）。
- 跨 WAN 可加 `--bwlimit <KB/s>` 限速；`--no-delete` 保留目標端多出來的檔。
- **Windows**：用 `scripts\migrate_to_host.ps1`（參數相同，自動轉進 WSL 執行）。

設計與進度：[`docs/progress-migrate-to-host.md`](./docs/progress-migrate-to-host.md)。

### Migration dashboard（網頁版）

不想記指令的話，Search UI 內建 **Migration** 頁面（`scripts/run_search_ui.sh`
→ <http://127.0.0.1:5050/migrate>）：填目標主機 OS type / IP / hostname / 帳號 /
密碼 / port / 目標路徑，按「🔍 Dry-run 預覽」或「🚀 執行遷移」，log 即時串流到頁面。

- 密碼走 `sshpass`（需 `sudo apt install sshpass`；留空則用 ssh key 免密），
  **只在本機 subprocess 記憶體使用，不寫檔 / 不入 git / 不寫 log / 不回傳前端**。
- 預設 dry-run；要真的搬須勾「我確認要真的執行遷移」。
- ⚠️ Flask 預設綁 `0.0.0.0:5050`，**請只在信任的內網開這個頁面**。

設計與進度：[`docs/progress-migration-dashboard.md`](./docs/progress-migration-dashboard.md)。

## 當日增量爬蟲即時監控（/live）

Search UI 內建 **Live** 頁面（`scripts/run_search_ui.sh` →
<http://127.0.0.1:5050/live>）：即時顯示當日 `meta/audit/ingest_<date>.jsonl`
的增量爬蟲審計事件，作為實盤前/盤中的資料完備度監控模組。

### 逐 tick 實盤監控（主視圖）

內建 tick collector，盤中輪詢 **TWSE MIS 即時行情**（`mis.twse.com.tw`，
免費、無需 API key、約 5 秒快照粒度）對 watchlist 標的收當日逐 tick：

- **即時走勢**：價格階梯線 + 單量 bars + 昨收參考線；大字報價含漲跌
  （紅漲綠跌）；逐筆明細表（時間/成交/單量/總量/買賣一檔）。
- **collector 控制**：頁面按鈕開始/停止；「開頁自動啟動」預設開；
  狀態列顯示輪詢標的、poll 數、tick 數與最近錯誤。
- **持久化**：tick 落地 `meta/realtime/ticks_<date>.jsonl`（gitignored）；
  server 重啟自動 backfill，dedup 基準（`tlong` + 累積量）不會重複記錄。
- **支援標的**：上市/上櫃個股與 ETF（自動偵測 tse/otc）、`TAIEX` 加權指數、
  `OTC` 櫃買指數；與 watchlist chips 連動切換。
- **歷史模式（非交易日回看）**：今日非交易日時自動切到「📼 歷史」顯示
  **最後交易日**的逐筆成交；日期下拉可回看任一可用日。資料三層 fallback：
  自收 JSONL → FinMind sqlite（`FINMIND資料集` repo）→ **FinMind API 即抓**
  （`TaiwanStockPriceTick`，交易所全量逐筆，首抓數秒後自動 cache）。
- 注意：MIS 為快照型行情（≈5 秒一筆最新成交），非交易所全量逐筆（歷史模式
  的 FinMind 才是全量）；台指期（mis.taifex）尚未接 — 列於進度檔後續方向。

- **即時更新**：SSE 推播（2s 檢查），斷線自動降級為 5s 輪詢；輪詢帶 byte offset
  只讀檔案新增部分，不重 parse 整檔。
- **統計列**：事件數 / 資料表數 / 成功 / 失敗 / 寫入列數；各 source pills 彙總。
- **資料表狀態**：每個 (source, table) 取最後一次 run — status / rows / 資料最新日 /
  耗時 / runs；**失敗排最前**且整列標紅。
- **事件 feed**：新→舊串流，最多保留 200 筆，新事件 flash 提示。
- **回看歷史**：日期下拉切任一天（`/live?date=YYYY-MM-DD`）。
- **標的時間序列**：watchlist chips 顯示各標的最新交易日收盤 + 漲跌%（紅漲綠跌），
  點選即出 Plotly K 線 + 成交量圖（20/60/120/240 日，最新交易日虛線標記）；
  涵蓋 `bars_1d`（台股/台期/股期 3298 檔）+ `macro_daily`（45 個總經標的），
  搜尋框 autocomplete 全標的；watchlist 存 localStorage。
- 審計事件純讀 `meta/audit/`；行情查 catalog 的 read-only 快照（不會與
  ingest / `duckdb -ui` 搶鎖）。

設計與進度：[`docs/progress-live-crawl-dashboard.md`](./docs/progress-live-crawl-dashboard.md)。

- 對外即時行情 API（給風控系統等跨機器消費者，只讀）：見 [`docs/api-v1.md`](docs/api-v1.md)
