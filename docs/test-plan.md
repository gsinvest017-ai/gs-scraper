# Test plan — QUANTDATA

> 生成於 2026-05-29；spec source: `README.md`, `DATA_ARCHITECTURE.md`, `docs/progress-*.md`, `docs-site/`；framework: **pytest**（auto-detected）

## Overview

- **Stack**：Python ≥ 3.11；DuckDB + Parquet medallion (bronze/silver/gold)；Flask Search UI；polars/pandas/pyarrow/pandera 做 transform/schema
- **Test framework**：pytest（`pyproject.toml [tool.pytest.ini_options] testpaths = ["tests"]`，dev-deps `pytest>=7.4`）
- **既有測試**：`tests/test_tej_stock.py`（5 個 unit case，全覆蓋 `qd_ingest.sources.tej` 的 3 個 pure helper）
- **覆蓋現況**：13 個 `src/qd_ingest` module + 10+ scripts + 3 個 ui module —— 整體 ~5% 覆蓋；極稀疏，計畫採 **foundation 版**
- **Coverage goal**：80%（用於 P0/P1/P2 排序）
- **建議 fixtures 位置**：`tests/fixtures/`（迷你 parquet + VCR cassette），`tests/conftest.py`（tmp catalog builder）

## Unit tests

### Module: `qd_ingest.common.paths`
**責任**：以 env var `QUANTDATA_RAW` 計算 `BRONZE/SILVER/GOLD/REFERENCE/CATALOG_DB` 路徑。
**純度**：pure
**公開介面**：`ROOT`, `BRONZE`, `SILVER`, `GOLD`, `REFERENCE`, `CATALOG_DB`, `RAW_ROOT`, `META`

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-001 | `RAW_ROOT` | env unset → 預設 `ROOT.parent / "RAW_SOURCES"` | happy | `monkeypatch.delenv("QUANTDATA_RAW")` + reimport | P1 |
| U-002 | `RAW_ROOT` | env set → 採用 env 路徑 | happy | `monkeypatch.setenv` + reimport | P1 |
| U-003 | `BRONZE/SILVER/GOLD` | 三條 path 是 `ROOT` subdir + 名稱固定 | invariant | — | P2 |

### Module: `qd_ingest.common.io`
**責任**：parquet zstd 寫入；dedup + ingest_ts 戳。
**純度**：side-effect（FS）
**公開介面**：`write_parquet`, `read_parquet`（包裝層）

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-004 | `write_parquet` | round-trip：寫入後讀回 schema + values 完全相等 | happy | `tmp_path` | **P0** |
| U-005 | `write_parquet` | 含 `ingestion_ts` 欄；連寫兩次同 key → keep last | invariant | tmp_path | **P0** |
| U-006 | `write_parquet` | zstd compression 啟用（file magic / size 比 uncompressed 小） | invariant | tmp_path | P2 |

### Module: `qd_ingest.common.audit`
**責任**：寫 `meta/audit/ingest_YYYY-MM-DD.jsonl`。
**純度**：side-effect（FS）
**公開介面**：`log_ingest(...)`

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-007 | `log_ingest` | 寫一條 → jsonl 多一行 + 欄位完整 | happy | tmp_path + freezegun | P1 |
| U-008 | `log_ingest` | 同日寫多條 → append（非覆寫） | invariant | tmp_path | P1 |

### Module: `qd_ingest.common.catalog`
**責任**：建 DuckDB views + macros。
**純度**：glue（DB + FS）
→ 主要 case 進 integration tier（I-002）；unit 只測純 helper：

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-009 | `_rel(p)` | `\` 路徑分隔 → `/`（Windows 友善） | happy | — | P1 |

### Module: `qd_ingest.sources.tej`
**責任**：TEJ API → bronze parquet。
**純度**：mixed（含 3 個 pure helper，已測 ✓）
**公開介面**：`_normalize_stock_id`, `_to_ts_utc`, `_transform_ewprcd_chunk`, `ingest_stock_daily`, `ingest_*`

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| ✓ U-010 | `_normalize_stock_id` | `"1101 台泥"` → `("1101", "台泥")` | happy | — | (已存在) |
| ✓ U-011 | `_normalize_stock_id` | 無中文名 → `(sid, "")` | happy | — | (已存在) |
| ✓ U-012 | `_to_ts_utc` | naive datetime → UTC tz-aware | happy | — | (已存在) |
| ✓ U-013 | `_transform_ewprcd_chunk` | 重命名 + 型別 | happy | — | (已存在) |
| U-014 | `_normalize_stock_id` | 全空 Series → 兩個空 Series（不 raise） | edge | — | P1 |
| U-015 | `_normalize_stock_id` | 含全形空白 / 多空白 → 正確切割 | edge | — | P2 |

### Module: `qd_ingest.sources.taifex`
**責任**：TAIFEX 期貨資料 + 三大法人衍生。
**純度**：mixed
**公開介面**：`SILVER_SCHEMA`, `_CODE_MAP`, `derive_inst_futures_daily`, `ingest_*`

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-016 | `_CODE_MAP` | 9 entries 完整、product × identity 唯一 | static | — | P2 |
| U-017 | `derive_inst_futures_daily` | dedup `keep='last'` by `ingestion_ts`（同 key 兩筆 → 留 newer） | invariant | tmp silver + 2 ingest | **P0** |
| U-018 | `derive_inst_futures_daily` | 60d net-OI z-score：常數序列 → 0；遞增 → > 0 | numeric | tiny fixture | P1 |
| U-019 | `SILVER_SCHEMA` | 包含 `(date, product, identity, ...)` 必要欄；ordering 穩定 | invariant | — | P1 |

### Module: `qd_ingest.sources.derived`
**責任**：gold factor builders（20+ 個 `build_*` / `materialize_*`）+ BS-IV helpers。
**純度**：mixed（含 3 個 pure 數學函式）
**公開介面（節錄）**：`build_all`, `build_txo_daily_features`, `_third_wednesday`, `_bs_price`, `_bs_iv`, `materialize_*`

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-020 | `_third_wednesday(2025, 1)` | == `date(2025, 1, 15)` | numeric | — | **P0** |
| U-021 | `_third_wednesday` | 12 個月 spot check（2024-01..2026-12） | numeric | — | P1 |
| U-022 | `_bs_price` | call ATM, σ=20%, T=30/365 → 已知值（±1e-2） | numeric | — | **P0** |
| U-023 | `_bs_price` | put ATM 同條件 → 已知值；put-call parity 滿足 | numeric | — | **P0** |
| U-024 | `_bs_iv` | 把 `_bs_price(σ_0)` 結果回灌 → 還原 σ_0（±1e-3） | numeric | — | **P0** |
| U-025 | `_bs_iv` | deep OTM（price ≈ 0） → 收斂到 lower bound 或回 NaN，不爆 | edge | — | P1 |
| U-026 | `_bs_iv` | bisection ≤60 iter | invariant | — | P2 |
| U-027 | `build_txo_daily_features` | Timestamp vs date 比對不丟 TypeError（regression） | regression | tmp silver fixture | **P0** |
| U-028 | `build_txo_daily_features` | mxf_close join key 一致（沒全 NaN） | regression | tmp silver fixture | **P0** |
| U-029 | `build_txo_daily_features` | 12 features 全寫出（pcr_vol/oi, max_pain(+dist), iv_skew_proxy, ...） | invariant | tmp fixture | P1 |

### Module: `qd_ingest.sources.macro`
**責任**：yfinance → `silver/macro/macro_daily.parquet`。
**純度**：side-effect
→ 主要 case 進 integration tier（I-005）。

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-030 | `ingest_macro_daily(dry_run=True)` | dry-run 不寫檔 | invariant | tmp_path + monkeypatch yf | P1 |

### Module: `qd_ingest.sources.twse` / `tw_futures` / `histdata`
**純度**：side-effect
→ unit tier 只列佔位；主要進 integration（I-007）。

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-031 | `tw_futures.build_continuous` | rollover 邏輯 happy | happy | tiny fixture | P1 |

### Module: `qd_ingest.sinks.zipline_bundle`
**責任**：gold → zipline bundle export。
**純度**：side-effect（外部 lib）
→ integration only。

### Module: `ui.search.query_builder`
**責任**：filter spec → parameterized SQL。
**純度**：**pure**（unit 黃金目標 — injection 防禦點）
**公開介面**：`build_sql`, `Filter`, `DEFAULT_LIMIT`, `MAX_LIMIT`

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-032 | `build_sql` | empty filters → `SELECT * FROM "v" LIMIT N` | happy | — | **P0** |
| U-033 | `build_sql` | 兩個 AND filter + order_by + limit | happy | — | **P0** |
| U-034 | `build_sql` | column 含 `"`/`;`/space → ValueError（injection 防禦） | **security** | — | **P0** |
| U-035 | `build_sql` | op `BETWEEN` → 兩個 param、`IN` → 多 param、`LIKE` → 字串 param、`IS NULL` → 0 param | happy | — | **P0** |
| U-036 | `build_sql` | op 非白名單字串 → ValueError | security | — | **P0** |
| U-037 | `build_sql` | limit > `MAX_LIMIT` → clipped 到 MAX_LIMIT | edge | — | P1 |
| U-038 | `build_sql` | limit ≤ 0 → 採用 `DEFAULT_LIMIT` | edge | — | P1 |
| U-039 | `build_sql` | order_dir 非 `ASC/DESC` → ValueError | security | — | **P0** |
| U-040 | `build_sql` | `select_cols=["a","b"]` → quoted、白名單外 → ValueError | security | — | **P0** |
| U-041 | `Filter` dataclass | 缺欄位 / None 處理 | edge | — | P2 |

### Module: `ui.search.catalog_inspector`
**責任**：列 view + metadata + read-only catalog copy。
**純度**：glue（DuckDB read）
→ integration tier（I-008..010）。

### Module: `ui.search.app` (Flask routes)
**責任**：8 routes（`/`, `/view/<v>`, `/api/query`, `/api/refresh`, `/downloads`, `/download/view/<v>.csv`, `/download/bundle.zip`）。
→ 主進 e2e tier（E-004..007）；unit 只測純 helper：

| # | Target | Case | 類型 | 前置 / Mock | 優先 |
|---|--------|------|------|------------|------|
| U-042 | `_csv_escape(None)` | == `""` | happy | — | **P0** |
| U-043 | `_csv_escape` | 含 `,` / `"` / `\n` / `\r` → 用 `"..."` 包；內部 `"` 倍化 | invariant | — | **P0** |
| U-044 | `_csv_escape` | 中文字串原樣輸出（utf-8） | happy | — | P1 |
| U-045 | `_jsonify_cell` | `pd.Timestamp` (00:00) → `'YYYY-MM-DD'`；有時分 → ISO | happy | — | P1 |
| U-046 | `_jsonify_cell` | `float('nan')` → None、`inf` → None | edge | — | P1 |

## Integration tests

### Boundary: DuckDB (read-only)
**Strategy**：tmpdir + 迷你 parquet fixture → `duckdb.connect(read_only=True)`，無 transaction（DuckDB single-writer）。

| # | Flow | Setup | Assert | 優先 |
|---|------|-------|--------|------|
| I-001 | `catalog_inspector.list_views()` 對 tmp catalog | tmp catalog with 2 views | 回 `[v1, v2]` | **P0** |
| I-002 | `catalog.build()` mini-rebuild | tmpdir + 3 個 silver parquet | views 數 ≥ 3、`SELECT count(*)` > 0 | **P0** |
| I-003 | `catalog_inspector.get_view_meta` 對不存在 view | tmp catalog | raises（ValueError or 404 layer） | P1 |
| I-004 | `catalog_inspector.refresh_catalog_copy` lock 行為 | 另一 conn open → ui copy 仍可 read | UI 不被擋 | P1 |
| I-005 | `catalog_inspector.view_summary` row_count + is_time_series 偵測 | view 含 `date` 欄 vs 無 | flag 正確 | P1 |

### Boundary: 檔案系統 / Parquet
**Strategy**：pytest `tmp_path` + 自製 fixture（pyarrow 寫小 parquet）。

| # | Flow | Setup | Assert | 優先 |
|---|------|-------|--------|------|
| I-006 | `io.write_parquet` round-trip | DataFrame n=10 | 讀回 == 原 frame | **P0** |
| I-007 | dedup invariant：寫兩次同 key | newer `ingestion_ts` | 留 newer | **P0** |

### Boundary: HTTP — TEJ API
**Strategy**：`vcrpy` cassette；首次錄製需 `TEJAPI_KEY`，CI 用 replay；key 進 `.env` 不入 cassette。

| # | Scenario | Cassette | Assert | 優先 |
|---|----------|---------|--------|------|
| I-008 | `ingest_stock_daily` happy（2 stocks × 5 days） | `tej_stock_daily_happy.yaml` | bronze parquet shape (~10 rows × 必要欄) | **P0** |
| I-009 | TEJ API 429 retry-backoff | `tej_429.yaml` | retry ≤ 3、最終 raise / OK | P1 |
| I-010 | TEJ schema drift（新欄位 / 缺欄位） | `tej_drift.yaml` | pandera warn 或 fail-fast | P1 |

### Boundary: HTTP — yfinance / FinMind / TWSE
**Strategy**：VCR；對應 module 各一個 cassette。

| # | Scenario | Cassette | Assert | 優先 |
|---|----------|---------|--------|------|
| I-011 | `macro.ingest_macro_daily` 跑 ^VIX/^TWII/USDTWD | `yf_macro.yaml` | `silver/macro/macro_daily.parquet` 寫出 + schema OK | P1 |
| I-012 | `scripts/fetch_finmind.py` per-day loop 3 days | `finmind_stock_3d.yaml` | 3 bronze partitions、universe filter 生效 | P1 |
| I-013 | `sources.twse.fetch_*` 一條 endpoint | `twse_one.yaml` | bronze 寫出 | P2 |

### Boundary: Subprocess / shell
**Strategy**：純 bash-level lint + dry-mode。

| # | Flow | Setup | Assert | 優先 |
|---|------|-------|--------|------|
| I-014 | `bash -n scripts/daily_refresh.sh` | — | exit 0（syntax OK） | **P0** |
| I-015 | `bash -n scripts/*.sh` | 全部 7 支 | 全部 exit 0 | P1 |
| I-016 | `shellcheck scripts/daily_refresh.sh` | shellcheck 已安裝 | 無 SC2086（unquoted）/ SC2046 等 high-sev | P2 |

### Boundary: 環境變數
**Strategy**：`monkeypatch.setenv` / `delenv`。

| # | Flow | Setup | Assert | 優先 |
|---|------|-------|--------|------|
| I-017 | `fetch_tej.py` 缺 `TEJAPI_KEY` | `delenv` | exit 2 + stderr 含說明 | **P0** |
| I-018 | `paths.RAW_ROOT` 受 `QUANTDATA_RAW` 控制 | setenv | `RAW_ROOT == 指定路徑` | P1 |
| I-019 | `fetch_finmind.py` 預設 `FINMIND_REPO` 不存在時的訊息 | delenv | 早期 fail + 提示 | P1 |

### Boundary: 時間
**Strategy**：`freezegun`。

| # | Flow | Setup | Assert | 優先 |
|---|------|-------|--------|------|
| I-020 | `audit.log_ingest` 時戳 frozen | `freeze_time('2026-05-29')` | 寫進 jsonl 的 ts 對應該日 | P1 |

### Boundary: Goldify lock
**Strategy**：tmpdir catalog；另開一條 write conn 模擬 duckdb-ui。

| # | Flow | Setup | Assert | 優先 |
|---|------|-------|--------|------|
| I-021 | `goldify_audit.py` 對有 lock 的 catalog | 另一 conn open | raises IOException with `lock` in msg | P1 |
| I-022 | `goldify_audit.py` 對乾淨 catalog（0 cands） | tmp catalog | stdout 含 `✅ goldify_audit: no views with non-gold data found` | **P0** |

## E2E tests

### Flow: catalog rebuild → audit → dashboard
**Entry**：`python -m qd_ingest.common.catalog`
**Setup**：`tmp_path/{silver,gold,reference}` + 3 個極小 parquet（calendar、stock_daily、taifex）
**Steps**：
1. `python -m qd_ingest.common.catalog`（建 tmp.duckdb）
2. `python scripts/restore_finmind_views.py`（best-effort，缺 finmind 跳過）
3. `python scripts/goldify_audit.py --json /tmp/out.json`
4. `python scripts/gap_report.py --format all --out-dir tmp_path/docs`

**Assert**：tmp.duckdb 含 ≥3 views queryable；audit json `candidates: []`；`docs/gap_dashboard.html` 含 `summary: ✅ OK=` 字串。

| # | Flow | 優先 |
|---|------|------|
| E-001 | catalog rebuild happy | **P0** |
| E-002 | goldify_audit converged (0 cands) | **P0** |
| E-003 | gap_dashboard regen happy | **P0** |

### Flow: Search UI happy path
**Entry**：Flask `app.test_client()`
**Setup**：tmp catalog（同 E-001）+ env `QUANTDATA_CATALOG=tmp/quant.duckdb`
**Steps**：
1. `GET /` → 200 + DOM 含 view list
2. `GET /view/<v>` → 200 + DOM 含 column header
3. `POST /api/query {view, filters: []}` → 200 + JSON `{columns, rows, row_count}`

| # | Flow | 優先 |
|---|------|------|
| E-004 | `/` + `/view/<v>` happy | **P0** |
| E-005 | `/api/query` empty filters → row_count > 0 | **P0** |
| E-006 | `/api/query` injection: `filters=[{column: '"; DROP", op: '=', value: 1}]` → 400 + error | **security P0** |
| E-007 | `/api/refresh` 後 `list_views` 含新增 view（mock） | P1 |

### Flow: Search UI bulk download
**Entry**：test client
**Setup**：同上

| # | Flow | 優先 |
|---|------|------|
| E-008 | `GET /download/view/calendar_xtai.csv` → 200, header + ≥1 row, Content-Disposition attachment | P1 |
| E-009 | `GET /download/bundle.zip?v=a&v=b` → 200, ZIP64, 2 entries 完整 | P1 |
| E-010 | `GET /download/bundle.zip?v=nonexistent` → 400 + error JSON | P1 |
| E-011 | `GET /downloads` → 200, DOM 含 bucket buttons + row_count | P2 |

### Flow: daily_refresh dry mode（若支援）
| # | Flow | 優先 |
|---|------|------|
| E-012 | `bash scripts/daily_refresh.sh --dry-run`（若有此 flag） | P1（先確認 flag 是否存在 → 見 Open questions） |

## Coverage matrix

| Module / Layer | Unit | Integration | E2E |
|---|---|---|---|
| `common.paths` | ✅ P1 (U-001..003) | — | — |
| `common.io` | ✅ P0 (U-004..006) | ✅ P0 (I-006..007) | — |
| `common.audit` | ✅ P1 (U-007..008) | ✅ P1 (I-020) | — |
| `common.catalog` | ⚠️ glue (U-009 only) | ✅ P0 (I-002) | ✅ E-001 |
| `sources.tej` | ✅ P0 (U-010..015) | ✅ P0 (I-008..010) | E-001 |
| `sources.taifex` | ✅ P0 (U-016..019) | — | E-001 |
| `sources.twse` | ⚠️ 主 side-effect | ✅ P2 (I-013) | — |
| `sources.tw_futures` | ✅ P1 (U-031) | — | — |
| `sources.macro` | ✅ P1 (U-030) | ✅ P1 (I-011) | — |
| `sources.histdata` | — | ⚠️ depends on big binary | — |
| `sources.derived` | ✅ P0 (U-020..029) | — | E-001 |
| `sinks.zipline_bundle` | ⚠️ partial | ⚠️ external bundle | — |
| `ui.search.query_builder` | ✅ P0 (U-032..041) | — | E-005, E-006 |
| `ui.search.catalog_inspector` | — | ✅ P0 (I-001..005) | E-004 |
| `ui.search.app` (routes) | ✅ P0 (U-042..046, helpers) | — | ✅ P0 (E-004..011) |
| `scripts/gap_report.py` | ⚠️ glue | ✅ implicit via E-003 | ✅ E-003 |
| `scripts/goldify_audit.py` | — | ✅ P0 (I-021..022) | ✅ E-002 |
| `scripts/fetch_finmind.py` | — | ✅ P1 (I-012, I-019) | — |
| `scripts/daily_refresh.sh` | shell-lint | ✅ P0 (I-014..016) | P1 (E-012) |

## Implementation order

1. **P0 unit ×~15**：`query_builder.build_sql` 6 條（含 4 security）+ BS-IV 5 條 + `third_wednesday` 1 + dedup invariants 1 + `io` round-trip 1 + `_csv_escape` 2
2. **P0 integration ×~6**：`catalog.build` mini-rebuild + parquet round-trip + TEJ happy cassette + `bash -n daily_refresh` + `TEJAPI_KEY` missing fail + goldify clean catalog
3. **P0 e2e ×~5**：catalog rebuild → audit → dashboard → Search UI `/` + `/view` → `/api/query` happy + injection 400
4. **P1**：retry / schema drift / 其他 fetch / download streams / 時間凍結
5. **P2**：perf / shellcheck / 大量資料 / zipline bundle / 中文邊界

最便宜回報最高：**先寫 P0 unit**（pure logic，無 fixture，可一晚完成 15 條）→ 立刻接 CI（GitHub Actions ubuntu-latest）→ 再補 P0 integration。

## Open questions

1. **`derived.py` 多處 `read_parquet(f"...")` f-string**：實務只用內部 path、無外部輸入，但 spec 未明寫 path injection 邊界。**doc 一下、加防禦註解即可。**
2. **`goldify_audit.py` 在 lock 時 exit code**：目前 raise `IOException`，process exit 1。test 是否該 assert exit code == 1 + stderr 包含 `Conflicting lock`？建議 yes。
3. **`daily_refresh.sh` 是否有 `--dry-run` flag**：grep 看不到；E-012 必須先補 `--dry-run` 模式（或 unset DAILY_REFRESH_DRY=1 等），否則 e2e 無法不副作用測；建議實作前先補。
4. **`download_bundle_zip` 部分 view 失敗**：當前寫 `_errors.txt` 進 zip；spec 未明列契約，test 該 assert 此檔存在 + 內容格式？建議 yes（E-010 變體）。
5. **`qd_ingest.cli` 內容近乎空**（只有 `main`）：`pyproject.toml` 已 register entry `qd-ingest`，但 cli 沒實作 subcommand。是要重設、放棄、還是補實作？建議**先標 deprecated**，否則 e2e 該驗 `qd-ingest --help` 不爆。
6. **macro_daily 與 cross_market_features**：兩者語義邊界？目前 macro 是純資料（VIX/SPY/USDTWD/TAIEX），cross_market 是衍生比率？test 該分兩條還是合一？需 spec。

## Out of scope

- Vendored / generated code（`docs-site/_build/`, MkDocs 編出來的 HTML）
- bronze/silver/gold 既有歷史資料的內容驗證（資料來源在 TEJ/FinMind，非 repo 內邏輯）
- DuckDB itself 的 SQL 行為（信任 upstream）
- `scripts/install_cron.sh` / `ngrok_tunnel.sh` / `tailscale_funnel.sh` / `backup_snapshot.sh` —— 部署層腳本，e2e 須真機；shell-lint 已包含

## 後續

寫測試實作不在本計畫範圍。建議：

```bash
# 1) 先填 P0 unit（最快）— 在 tests/ 下建 4 個檔
tests/test_query_builder.py    # U-032..041
tests/test_derived_math.py     # U-020..029
tests/test_csv_escape.py       # U-042..046
tests/test_io.py               # U-004..006

# 2) 接 CI（之前 platform-compatible 已標記為待辦）
.github/workflows/pytest.yml   # ubuntu-latest + python 3.11/3.12 matrix

# 3) 補 P0 integration（需 fixture）
tests/fixtures/                # 5 個迷你 parquet
tests/conftest.py              # tmp_catalog fixture

# 4) 最後 P0 e2e
tests/test_search_ui_e2e.py    # Flask test_client + tmp catalog
```

<!-- BEGIN test-plan: auto-generated section -->
（本標籤區內由 /test-plan 維護；下次重跑只更新此區）
<!-- END test-plan -->
