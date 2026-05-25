# Data cleaning criteria（三段 transitions）

> 目的：寫新 source adapter / 做 code review / 排查 migration 時，**照這份**判斷每一段轉換的合格條件。
> 這頁是 [Medallion 三層](medallion.md) 的延伸，前者講「每層放什麼」，這頁講「promote 到下一層**之前**要做完什麼」。

---

## 0. 整體原則（cross-cutting）

下面 4 條對 3 段 transition **都**適用。

| 原則 | 規則 | 為什麼 |
|---|---|---|
| **Idempotency** | 同一份輸入跑 N 次 → 同一份輸出 | cron 重跑、人類補跑、兩個 instance 撞時間都不會壞 |
| **Audit trail** | 每次寫入留 `meta/audit/ingest_<date>.jsonl` 一行：`{source, table, ingestion_ts, sha256, rows, ...}` | 出事時能追到某個 row 是哪一筆 ingest 帶進來的 |
| **Atomic write** | 寫 `.tmp` → `os.replace()` 改名；catalog rebuild 用 staging swap | crash 中斷不會留下半個檔 |
| **Schema versioning** | derived 表加 `source = 'qd_<name>_v<N>'`；公式變動就 bump N | 跨版本回測能精確分辨 |

延伸：每張 silver / gold 表都附 `source` + `ingestion_ts` 欄。Lineage 不用單獨表，靠 `source` + manifest 即可回溯。

---

## 1. raw → bronze criteria

**目的**：把 RAW_SOURCES 內的 zip / csv / parquet / sqlite「凝固」成不可變層，供下游隨時重跑。

### 規則

1. **絕不修改 byte 內容**
    - 解 zip 直接寫到 `bronze/<source>/<filename>`
    - 不轉碼、不重命名欄位、不 cast 型別、不調整時區
    - 如果原檔是 sqlite，**整檔** copy；不 attach 改動

2. **檔名帶 snapshot 日期**
    - 格式 `bronze/<source>/<dataset>_<YYYY-MM-DD>.<ext>`
    - 範例：`bronze/finmind/finmind_2026-05-18.sqlite`
    - 同來源新版本不 overwrite，落新檔；rolling retention 由 cron / 人類管

3. **SHA256 sidecar**
    - 每個 bronze 檔配一個 `<filename>.sha256`
    - 內容是 `sha256sum` 標準輸出格式：`<hex>  <basename>`
    - sidecar 可重新算（idempotent），但**不要**手改

4. **路徑慣例**
    ```
    bronze/
    ├── tej/              # TEJ CSV / API dump
    ├── taifex/           # TAIFEX 公開頁面爬
    ├── twse/             # TWSE 公開爬（預留）
    ├── yahoo/            # yfinance（預留）
    ├── histdata/         # NQ/ES/GC 1min parquet
    └── finmind/          # FinMind sqlite snapshot
    ```

5. **manifest entry**（追加進 `meta/audit/ingest_<YYYY-MM-DD>.jsonl`）
    ```json
    {"task": "bronze_extract", "source": "finmind", "file": "bronze/finmind/finmind_2026-05-18.sqlite",
     "sha256": "...", "size_bytes": 2518978560, "extracted_at_utc": "2026-05-18T15:21:00Z"}
    ```

### Bronze 不應該做

- ❌ 解 csv 並寫 parquet — 那是 silver 的事
- ❌ 把 cp950 轉 UTF-8 — 那是 silver 的事
- ❌ 合併多個檔 — 那是 silver 的事
- ❌ 刪除舊版 — bronze 是 immutable，舊版留著做 reproducibility

### Checklist（PR review 用）

- [ ] bronze 檔名含 snapshot 日期
- [ ] `.sha256` sidecar 存在且能驗證
- [ ] `meta/audit/ingest_*.jsonl` 留下一行 manifest
- [ ] 沒有「改了 bronze 既有檔」這種 diff
- [ ] 大檔（> 100 MB）`.gitignore` 已蓋掉，不會誤 commit

---

## 2. bronze → silver criteria

**目的**：把多種來源的同類資料**統一成一份 schema**，下游不需要關心是 TEJ 還是 FinMind 餵的。

### 規則

1. **Canonical schema**（[完整 schema 表](../db/schema.md)）
    - 命名：snake_case lowercase，**沒有中文欄名**、沒有空白、沒有 `Unnamed:*`
    - 時間：兩個欄位
      - `ts_utc TIMESTAMP WITH TIME ZONE` — UTC，bar 結束時刻
      - `trading_date DATE` — partition key
    - Symbol：`symbol VARCHAR`（本地短碼，TWSE 4 碼、futures `TXFD4`、選擇權 `TXO20250620C12000`）
    - Audit：`source VARCHAR`（哪個 bronze 來的）、`ingestion_ts TIMESTAMP WITH TIME ZONE`

2. **Type cast 全部都要做**

    | 從 | 到 |
    |---|---|
    | `'20100104'` (int / str) | `DATE` (parse `%Y%m%d`) |
    | `'2024/03/02'` | `DATE` (parse `%Y/%m/%d`) |
    | `Asia/Taipei` naive timestamp | UTC tz-aware (`localize → tz_convert`) |
    | `成交量(千股)` | `BIGINT` shares (× 1000) — 單位**寫進欄名** `volume_shares` |
    | `cp950` 中文 | UTF-8 |
    | `''` / `'NA'` / `'-'` / `'—'` | `NULL` |

3. **Symbol normalization**
    - vendor symbol → 本地短碼，透過 `reference/symbol_map.parquet`
    - 範例：TEJ `coid='2330'` → silver `symbol='2330'`；FinMind `stock_id='2330'` → 同樣 `'2330'`

4. **Deduplicate**
    - Primary key per table（見 schema）：
      - bars：`(asset_class, symbol, contract_id, trading_date, session)`
      - flows：`(stock_id, trading_date)`
      - fundamentals_q：`(stock_id, fiscal_period, period_type, consolidated)`
    - 重複時保留 **最新 `ingestion_ts`**

5. **Validation（pandera schema）**
    必跑的檢查：
    - `open / high / low / close > 0`（除權息調整除外）
    - `high >= max(open, close)`、`low <= min(open, close)`
    - `volume >= 0`，期 / 選 `open_interest >= 0`
    - `trading_date BETWEEN '2000-01-01' AND today + 1 day`（reject 未來日期）
    - 月營收：`publish_date >= fiscal_month`（不能在月份結束前公告）

6. **Quarantine 而非 drop**
    - 任何 validate 失敗的 row 寫到 `_quarantine/<dataset>/<YYYY-MM-DD>.parquet` + reason column
    - **不要靜默丟掉**；長時間累積 = signal 有 bug

7. **Hive partitioning**
    - `silver/bars/asset_class=tw_stock/year=2024/<hash>-0.parquet`
    - DuckDB / Spark / Polars 都會自動 partition-pruning

8. **Charset**
    - 所有 silver parquet UTF-8
    - cp950 / Big5 上游檔在 silver writer 用 `encoding='cp950', errors='replace'` decode

9. **Compression**
    - parquet zstd level 3（時間 / 空間平衡）
    - row group size 128 MB（小檔可降到 default 64 MB）

### Silver 不應該做

- ❌ 衍生計算（return、RSI、ADX）— 那是 gold
- ❌ 跨表 JOIN（除非是 lookup 性質如 symbol → exchange）— gold 才做大 JOIN
- ❌ Cross-sectional rank / percentile — gold 才做
- ❌ Drop 任何整段資料 — 失敗 row 一定要進 quarantine

### Checklist

- [ ] 所有 timestamp 有時區（不是 naive）
- [ ] `trading_date` 跟 `ts_utc` 都存在且一致
- [ ] 主鍵欄位都不為 NULL
- [ ] `source` + `ingestion_ts` 兩欄都帶
- [ ] pandera schema 跑 PASS
- [ ] Quarantine 列數 < 上游 1%；否則回頭審
- [ ] Hive partition key（`year=YYYY`）在路徑裡
- [ ] zstd 壓縮、row group 合理

---

## 3. silver → gold criteria

**目的**：把 silver 衍生出**可直接被策略 / backtest / dashboard 吃**的 features 或 derived datasets。

### 規則

1. **Determinism（唯一最重要的鐵律）**
    - 同一份 silver → 永遠同一份 gold
    - 不接 live API
    - 不用 `now()` / `random.random()` / 機器資訊；只能用 `ingestion_ts` 當 audit 欄
    - 結果可被任何人重跑驗證

2. **Point-in-Time correctness**
    - 用 `publish_date` 對齊財報、月營收，**不是** `fiscal_period`
    - 任何 cross-sectional rank 都要鎖定在「該 as_of 當時 universe 可見的 stocks」
    - 例：`gold/rs_rating_daily` 對 `as_of='2024-03-05'` 排名，universe 是 2024-03-05 當時有 silver 資料的 stocks，**不是**用 2024 整年 universe

3. **Source versioning**
    - 每張 gold 表都帶 `source VARCHAR` = `qd_gold_<name>_v<N>`
    - 範例：`qd_gold_rs_rating_v1`、`qd_gold_stock_factor_v2`
    - 公式變動 → bump N；舊資料留著做 A/B compare

4. **NULL 有語意，不亂 fill**
    - back-adjustment chain 中斷時 `adj_factor=NULL`；**不要** silent 用 1.0 填
    - 缺資料應該 propagate 為 NULL，下游 explicit 過濾
    - 範例：`gold/continuous/tx_continuous_d.parquet` 後段 10 列 `adj_factor IS NULL` 因為從 bars_1d 衍生不知道前綴 adj chain

5. **Cross-sectional 計算的 universe 邊界**
    - 加 `universe_size` 欄記下排名當時 universe 大小
    - 樣本太少時 return 空（例：< 10 stocks 不算 RS rank）

6. **不要落 view 不必要的計算**
    - 變動頻率高的（每日 refresh 後重算）→ view
    - 變動低的 + 大資料量 → 落 parquet
    - 範例：`stock_factor_daily` 落 parquet（6.4M 列）；`qc_stock_price_diff` 走 view（每天變）

7. **Test fixtures（強烈建議）**
    - 對每個 gold 表寫 pytest：合成 silver → 手算 expected → assert
    - 例：`test_rs_rating.py` 餵 11 個 stocks 已知 1Y returns，驗證 floor(1 + 98 × pct_rank) ∈ [1, 99] 且 tie 規則正確

### Gold 不應該做

- ❌ 讀 bronze 直接 JOIN — 跳過 silver standardization 等於放棄了 canonical schema
- ❌ 任何 live API call
- ❌ 任何 `random()` / 跟系統時間有關的 logic（除了 audit `ingestion_ts`）
- ❌ silent fill NULL；缺資料就 propagate

### Checklist

- [ ] `source` 欄帶 `qd_gold_<name>_v<N>` 版本標記
- [ ] 重跑同 silver 兩次 → diff = 0
- [ ] PIT-correct：用 `publish_date` 而非 fiscal end
- [ ] Cross-sectional 計算的 universe size > min threshold（通常 10）
- [ ] NULL 不被 silent 填
- [ ] 有 pytest fixture 覆蓋

---

## 4. 失敗模式速查表

| 症狀 | 多半是哪段壞了 | 怎麼排查 |
|---|---|---|
| bronze 有 row，silver 沒有 | bronze→silver writer 漏 ingest 或 schema mismatch | 看 `meta/audit/ingest_*.jsonl` 跟 `_quarantine/` |
| Silver 跨年 schema 不同 | 上游 vendor 改了欄位、silver writer 沒更新 | 暫解 `read_parquet(..., union_by_name=True)`；永解 `qd-ingest <table> --rewrite` 全量 |
| gold 跨日結果不一致 | derive 公式 non-deterministic 或上游 silver 改了 | 抓兩次 gold output 比對；確認 source/version 標記 |
| Quarantine 越長越大 | validate rule 過嚴／資料品質下降 | 看 quarantine 內 reason 統計，回頭調 rule |
| `_duckdb.IOException: lock` | catalog write contention | `fuser catalog/quant.duckdb`；見 [troubleshooting](../ops/troubleshooting.md) |
| FinMind tick 卡 quota | sponsor 1500/hr per token；daily 跟 tick 同時跑會撞 | 序列化（先 daily → 再 tick），別開兩 process |

---

## 5. 加新 source / adapter 的 checklist

從 0 接入新 source（例：彭博 BBG 行情）走以下順序：

1. `bronze/bbg/` 目錄 + `<date>.csv.gz` 落地 + `.sha256` sidecar + manifest 一行
2. 寫 `src/qd_ingest/sources/bbg.py`：reader → schema cast → validate → quarantine → write silver parquet
3. 對應 `reference/symbol_map.parquet` 加 BBG → 本地 symbol 對應
4. 在 `src/qd_ingest/common/catalog.py` 加新 view DDL
5. `scripts/gap_report.py` `DATASETS` 加一行，含 `raw_paths / bronze_paths / silver_paths`
6. 寫 pytest：合成 BBG CSV → silver parquet → assert
7. 跑 `qd-ingest bbg-<table>` + `qd-ingest build-catalog` + `gap_report.py`
8. 確認新 view 出現在 `docs/gap_dashboard.html`

每一步對應上面 1-3 節的 criteria。

---

## 6. 進階：跨 source cross-check（QC）

當有兩個 source 可以覆蓋同一資料（如 TEJ stock_daily ∩ FinMind taiwan_stock_price），建：

- silver 兩條獨立 line（`source='tej'` / `source='finmind'`）
- view-layer JOIN 比較（如 `qc_stock_price_diff`）
- 自動 alert：若 `ABS(pct_diff) > 0.5%` 列數 / 總列數 > 1%，gap_report 標 STALE

實例參考：[FinMind 整合 / QC 結果](../db/finmind.md)。
