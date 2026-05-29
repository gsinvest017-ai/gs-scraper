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

## Fallback

要 rollback：

```bash
git revert HEAD~3..HEAD              # 撤 M1..M3
rm -f tests/test_query_builder.py tests/test_derived_math.py tests/test_csv_escape.py tests/test_io.py
rm -f .github/workflows/pytest.yml
```
