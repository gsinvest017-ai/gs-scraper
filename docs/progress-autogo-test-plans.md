# 2026-05-29 — autogo test-plan import contract

## 目標

讓本 repo 符合 autogo dashboard `/plans` 的 import contract：放足以被
`_is_plan_file()` 接受的 `.md` 在 `test-plans/`，並提供一支 formatter/validator
把 spec 規則寫死成可重複跑的檢查（避免將來新增 plan 又踩同樣的 frontmatter
typo）。

## Spec 摘要（autogo 那邊的 parser 行為）

| 規則 | 細節 |
|---|---|
| 位置 | `test-plans/<id>.md`（fallback 為 repo 根，避免混淆，採首選） |
| Frontmatter | 前 2 KB 開頭、`---` 一行起 + `---` 一行收 |
| 必填 | `id:` AND（`title:` OR `runner:`） |
| Parser | 手刻 `key: value`，**不是完整 YAML**；陣列 inline `[a, b]` only |
| 略過 | `README.md`（lowercase 比對）、`.` 開頭、無合格 frontmatter |
| Runner | `playwright-mcp`（fuzzy，預設） / `playwright-traced` / `chrome-devtools-mcp` |

## 計畫 milestone

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 |
| **M2** | `test-plans/` 5 支 plan：001 smoke、002 view list、003 query+chart、004 downloads、005 gap dashboard；皆 fuzzy 風格 |
| **M3** | `scripts/validate_test_plans.py` — formatter / validator；CLI 跑 `--check` 印 OK/FAIL 表 |
| **M4** | `tests/test_test_plans.py` 跑 validator 對所有 plans + pytest 全綠 |

## Fallback

```bash
git revert HEAD~3..HEAD
rm -rf test-plans/ scripts/validate_test_plans.py tests/test_test_plans.py
```
