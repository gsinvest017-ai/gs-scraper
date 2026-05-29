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

## Fallback

```bash
git revert HEAD~3..HEAD
rm -f tests/conftest.py tests/test_integration_io.py tests/test_integration_shell.py
rm -f tests/test_integration_env.py tests/test_e2e_search_ui.py
```
