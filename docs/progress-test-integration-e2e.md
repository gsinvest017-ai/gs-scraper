# 2026-05-29 — P0 integration + e2e 實作

## 目標

接續 `progress-test-plan-implementation.md`：unit tier 已 71 pass，接下來補
integration（DuckDB+parquet 真打、shell-lint、env-var guards）與 e2e（Flask
test_client 對迷你 catalog）。VCR cassette 對外部 HTTP 留作第三輪。

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + `tests/conftest.py`（mini_catalog session fixture：tmp duckdb + 1 parquet）+ `app_client` fixture（monkeypatch `_temp_catalog`） |
| **M2** | P0 integration ×4：DuckDB+parquet 真實 round-trip、`bash -n` 七支 shell script、`_check_env` 缺 `TEJAPI_KEY` 時 sys.exit、mini catalog 可被 `list_views` 看到 |
| **M3** | P0 e2e ×5：Flask `app.test_client()` 對 mini_catalog 跑 `/` / `/view/<v>` / `/api/query` happy + injection 400 / `/downloads` |
| **M4** | pytest 全綠 + 進度檔收尾 |

## 設計重點

### `mini_catalog` fixture

純 duckdb + pyarrow，不靠 `catalog.build()`（後者依賴 ~30 個 silver
parquet，太重）。直接寫 1 個 `calendar_xtai` view 指向 inline parquet。

```python
@pytest.fixture(scope="session")
def mini_catalog(tmp_path_factory):
    root = tmp_path_factory.mktemp("mini_catalog")
    pq.write_table(..., root / "calendar_xtai.parquet")
    con = duckdb.connect(str(root / "mini.duckdb"))
    con.execute("CREATE OR REPLACE VIEW calendar_xtai AS SELECT * FROM read_parquet(...);")
    return root / "mini.duckdb"
```

### `app_client` fixture（monkeypatch UI 看 mini catalog）

`ui.search.catalog_inspector` 用 module-level `_temp_catalog` 變數 +
`_views_cache` / `_meta_cache`。在 fixture 內 monkeypatch 三者，Flask 一啟動
即看到 mini_catalog。

注意：避開 `/api/refresh`（會觸發 `_ensure_temp_catalog` 把真 CATALOG copy 進去，覆蓋 mini）。

## 範圍（本輪不做）

- TEJ / yfinance / FinMind VCR cassette（需先 `pip install vcrpy`，本輪不擴依賴）
- `catalog.build()` 完整 mini-rebuild（需 ~30 個 silver parquet fixture）
- Windows-side e2e（CI 內 windows job 已 dispatch-only）

## 進度日誌

### M2 — integration 4 檔  `(M2 commit)`

| 檔 | cases | 對應 |
|---|---|---|
| `tests/test_integration_io.py` | 3 | I-001/002/007 — 真 DuckDB+parquet round-trip + delete_matching upsert |
| `tests/test_integration_shell.py` | 9 | I-014/015 — 7 個 scripts/*.sh + run.sh + `./run.sh --help` 全部 `bash -n` |
| `tests/test_integration_env.py` | 3 | I-017/018 — `fetch_tej._check_env()` 缺 key 必爆、補 key 必過、`paths.RAW_ROOT` 讀 env |
| **新增 case 數** | **15** | |

直接針對 mini_catalog fixture 驗一條（`test_I002_mini_catalog_has_expected_views`）也算自我測試。

### M3 — e2e Flask 1 檔  `6fcb490`

`tests/test_e2e_search_ui.py` 16 case，全靠 `app_client` fixture。`app_client` 改三個 module-level 變數 + 1 個函式：

```python
monkeypatch.setattr(ci, "_temp_catalog", mini_catalog)
monkeypatch.setattr(ci, "_views_cache", [])
monkeypatch.setattr(ci, "_meta_cache", {})
monkeypatch.setattr(ci, "_ensure_temp_catalog", lambda: mini_catalog)  # avoid /api/refresh stomping mini
```

| Group | 對應 | 範圍 |
|---|---|---|
| E-004 | `/` + `/view/<v>` + 404 | 3 |
| E-005 | `/api/query` empty / eq / in-list | 3 |
| E-006 | injection / unknown view / bad op / missing view → 400 | 4 |
| E-008 | `/download/view/<v>.csv` 標頭 + 內容 | 2 |
| E-009 | `/download/bundle.zip` ZIP archive 完整 | 1 |
| E-010 | bundle.zip bad/empty inputs → 400 | 2 |
| E-011 | `/downloads` 列出 views | 1 |

`/api/query` 走的 `build_sql` 已有 unit 層 21 個案例覆蓋 → e2e 只驗 HTTP wiring 與 status code，非重複測 SQL logic。

### M4 — 收尾

全測試套件：**102 passed in 0.99s**（71 unit + 15 integration + 16 e2e）。
CI 在下次 push 後即跑 ubuntu × {3.11, 3.12}。

## 後續

下一輪建議：

1. **VCR cassette**：`pip install vcrpy`，錄一輪 TEJ stock_daily / yfinance macro / FinMind happy；對應 I-008..013（會把外部 HTTP 也納入回歸測試）。
2. **catalog.build full mini-rebuild**（I-002 完整版）：需建 ~30 個 silver parquet fixture；可以做但 fixture 體積大；用 `--fixture-only` 子命令在 conftest 內 lazy 生成。
3. **Coverage 量測**：加 `pytest --cov=src --cov=ui` + `coverage-badge` → README 一個 badge。
4. **Open questions 收斂**：跟 spec 作者對齊 `daily_refresh.sh --dry-run`、`download_bundle_zip` 錯誤契約等。

## Fallback

```bash
git revert HEAD~3..HEAD
rm -f tests/conftest.py tests/test_integration_io.py tests/test_integration_shell.py
rm -f tests/test_integration_env.py tests/test_e2e_search_ui.py
```
