# 2026-05-29 — 實作 test-plan 建議的下一步

## 目標

`docs/test-plan.md` 規劃 76 條測試（P0 共 ~26 條）。本輪實作 P0 unit
最便宜回報最高的部分，並把 pytest 串到 GitHub Actions 上跑 ubuntu + windows
matrix（呼應 platform-compatible 留下的 CI TODO）。

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔；目標：P0 unit 4 檔（query_builder / derived 數學 / csv_escape / io） |
| **M2** | 4 個 unit 測試檔，覆蓋 ~22 條 P0 unit case |
| **M3** | `.github/workflows/pytest.yml`（ubuntu-latest, python 3.11/3.12） |
| **M4** | 本地 `pytest -q` 全綠 + 進度檔收尾 |

## 範圍（本輪不做）

- P0 integration / e2e（需 fixture + VCR cassette + tmp catalog；下一輪）
- Windows matrix（pytest.yml 內以 `runs-on: ubuntu-latest` 起步；matrix 配置留作 P1 後續）
- Open questions 1–6 的 spec 補完（需與作者討論）

## 進度日誌

### M2 — 4 個 unit 測試檔  `(M2 commit)`

| 檔 | cases | 對應 test-plan 編號 |
|---|---|---|
| `tests/test_derived_math.py` | 25 | U-020..029（含 parametrize 展開） |
| `tests/test_csv_escape.py` | 14 | U-042..046 |
| `tests/test_query_builder.py` | 21 | U-032..041 |
| `tests/test_io.py` | 4 | U-004..006 |
| **合計** | **64 新增 + 5 既有 + 2 parametrize 額外** = **71 個 pytest case** | |

**過程中發現的 2 個誤判**（都是 test 認知不足，code 沒問題）：

1. `_bs_iv` 對 deep-OTM put（S=120, K=80, price=0.0001）並不返 NaN —— price ≥ intrinsic(=0)，bisection 收斂到 0.36，是合理數值。改測「price 大於 σ=5.0 可建模上限」這條真正會返 NaN 的 guard。
2. `build_sql` 的 `order_dir` fallback 邏輯是 `"DESC" if upper()=="DESC" else "ASC"` —— 也就是**只有確切是 DESC 才 DESC，其他全部 ASC**（不是 DESC）。改 assertion。

執行：`.venv/bin/python -m pytest tests/ -q` → **71 passed in 0.56s** ✓。

### M3 — pytest CI workflow  `4da5219`

`.github/workflows/pytest.yml`：

- **main job** `runs-on: ubuntu-latest`，matrix `python-version: ['3.11', '3.12']`
- 觸發：push/PR 改到 `src/**` `ui/**` `tests/**` `pyproject.toml` 或 workflow 本身
- 步驟：`pip install -e ".[ingest,dev]"` → `ruff check`（non-blocking）→ `pytest -q tests/`
- **windows-latest job** 用 `workflow_dispatch`（手動觸發）：ingest pipeline 是 Linux-only（`/tmp` lock + bash），自動跑 windows 會浪費 CI 分鐘；保留手動 smoke 機制以防 src/ui pure-logic 中混入 Linux-only 寫法

YAML 語法已 `yaml.safe_load` 驗證 ✓。

### M4 — 進度檔收尾

71 個 unit case 已通過、CI 配置 commit、本文件更新。push 後 GitHub Actions 會跑第一輪驗證。

## 後續

下一輪建議：

1. **P0 integration（~6 條）**：建 `tests/conftest.py` + `tests/fixtures/`，含一個迷你 catalog builder（3 個小 parquet → 1 個 tmp duckdb）；先寫 `I-002 catalog.build mini-rebuild` + `I-006 io round-trip extended` + `I-014 bash -n daily_refresh`。
2. **P0 e2e（~5 條）**：Flask `app.test_client()` + 上述 tmp catalog；E-004 `/`、E-005 `/api/query`、E-006 injection 400 是最高價值。
3. **VCR cassette**：等到 `tests/conftest.py` 有 catalog fixture 後再加 TEJ/yfinance/FinMind 三套 cassette（需 `pip install vcrpy`）。
4. **Open questions 1–6**：跟 spec 作者對齊；尤其 #3 `daily_refresh.sh --dry-run` flag 不存在，e2e 跑不過。

## Fallback

要 rollback：

```bash
git revert HEAD~3..HEAD              # 撤 M1..M3
rm -f tests/test_query_builder.py tests/test_derived_math.py tests/test_csv_escape.py tests/test_io.py
rm -f .github/workflows/pytest.yml
```
