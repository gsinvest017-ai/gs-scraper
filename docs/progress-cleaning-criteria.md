# Recap data engineering / cleaning criteria

> 啟動：2026-05-25
> 觸發：`/safe-yolo recap data engineering/cleaning critetion, 將raw -> bronze, bronze -> silver, silver-> gold`

## 目標

寫一份明確、可被照著做的「三段 transitions 標準」doc，作為新 source adapter 開發、code review、migration audit 的參考。涵蓋：

- **raw → bronze**：immutability、provenance、unpack
- **bronze → silver**：canonical schema、type cast、UTC、normalize、validate、quarantine
- **silver → gold**：deterministic、PIT、aggregate、versioning

寫在 `docs-site/architecture/cleaning-criteria.md`（與 `medallion.md` 並列），對外 live。

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + outline | ✅ |
| **M2** | `docs-site/architecture/cleaning-criteria.md` 完整內容 | ⏳ |
| **M3** | nav 加進去 + medallion.md 連結 + mkdocs strict + push | ⏳ |

## Outline（M2 內容預計）

1. **整體原則**（cross-cutting）
   - Idempotency / 重跑無副作用
   - 可審計：manifest jsonl + sha256
   - Atomic write（`.tmp` → rename）
   - Schema versioning（`source = 'qd_xxx_vN'`）
   - Lineage（哪個 silver/gold row 來自哪個 bronze file）

2. **raw → bronze** criteria
   - 不可變性（once written, never modified）
   - SHA256 sidecar
   - 不做 schema normalize / type cast / 命名轉換
   - 路徑慣例 `bronze/<source>/<dataset>/<YYYY-MM-DD>/...`
   - manifest entry：`(source, table, ingestion_ts, file_path, sha256, byte_size, row_count_unverified)`
   - 解壓 zip / 7z 落地 bronze；壓縮原檔留 RAW_SOURCES
   - **檢核 checklist**

3. **bronze → silver** criteria
   - Canonical schema lookup（symbol_map / contract_specs）
   - Column rename：vendor → snake_case lowercase；中文 → 英文
   - Type cast：時區（TPE → UTC）、`TIMESTAMP WITH TIME ZONE`、`DATE`、`DECIMAL`/`DOUBLE`、`BIGINT`
   - Charset：UTF-8（cp950 入 silver 前要 decode）
   - 移除垃圾欄位 / `Unnamed:*`
   - Deduplicate：primary key + 最新 `ingestion_ts`
   - Validate (pandera)：OHLC consistency、price > 0、volume ≥ 0、`high ≥ max(open, close)`、`low ≤ min(open, close)`
   - 失敗列 → `_quarantine/<dataset>/<date>.parquet`，不靜默 drop
   - 加 audit columns：`source` (bronze 來源)、`ingestion_ts`
   - Partition：Hive `asset_class=*/symbol=*/year=*`
   - **檢核 checklist**

4. **silver → gold** criteria
   - 純 deterministic（不接 live API；no `now()` outside `ingestion_ts`）
   - Re-runable：同 silver → 同 gold
   - Point-in-Time correctness：用 `publish_date` 對齊財報，不用 `fiscal_period`
   - Cross-sectional 計算（rank, percentile, normalize）只在當 as_of 的 universe 內
   - Window functions（returns、momentum、IC）正確 lookback
   - 多 source JOIN（bars × calendar × symbol_map）
   - 命名 `source = 'qd_gold_<name>_v<N>'` 帶版本
   - 派生有缺資料時：`adj_factor=NULL` 標記，**不要** silent fill
   - **檢核 checklist**

5. **失敗模式速查表**
   - bronze 內已有但 silver 沒有 → 漏 ingest／schema mismatch
   - silver schema 漂移（跨年欄位數不同）→ `union_by_name=True` 暫解、`--rewrite` 永解
   - gold 結果跨日不一致 → 上游 silver 改了（罕見）或 derive 公式 non-deterministic
   - quarantine 越長越大 → 標 P0，回頭修 validate rule

## Fallback

- 寫壞了：`git revert <M2-commit>`，cleaning-criteria.md 從 nav 拿掉
- 標準描述太嚴／太鬆 → 直接編輯該頁，版本標 v0.1 → v0.2

### M1 — outline

見上面。
