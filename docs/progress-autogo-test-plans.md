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

## 進度日誌

### M2 — 5 支 fuzzy plan  `(M2 commit)`

全部走 fuzzy 風格（沒寫 traced-script）。對應 QUANTDATA 五個面向：

| id | title | url 起點 |
|---|---|---|
| `001-search-ui-smoke` | Search UI 是否起來、首頁列出 view | `/` |
| `002-view-detail-stock-factor` | 點開 stock_factor_daily 看 metadata | `/view/stock_factor_daily` |
| `003-query-chart-period-switch` | chart 切日/周/月 resample | `/` → 選 view → run query |
| `004-downloads-bulk-csv` | Downloads 分桶按鈕 + 單檔 CSV | `/downloads` |
| `005-gap-dashboard-health` | catalog 健康度 STALE/EMPTY | `/gap_dashboard.html` |

每支 plan ≤ 25 行 body，符合「自然語言、不列具體步驟、agent 自己決定」的
fuzzy 規範。

### M3 — `scripts/validate_test_plans.py`  `(M3 commit)`

實作 spec 的 9 條規則（BOM 偵測、fence 解析、key:value 解析、required +
either-or 檢查、runner whitelist、inline-array 語法、kebab-case id、
filename==stem convention）。

- `--strict` 把 warning 也當錯
- `--json` 給機器讀
- exit code：0 pass / 1 fail / 2 nothing found

本地跑：`python scripts/validate_test_plans.py --strict` → **5/5 pass strict**。

### M4 — pytest 測 validator + 全 plans  `(M4 commit)`

`tests/test_test_plans.py` 11 個 case：
- 3 case 確保 `test-plans/` 存在 + 至少一支 plan + parametrize 對每支 plan strict-pass
- 4 case 用 tmp_path 餵刻意壞的 plan，確認 validator 真的會抓（缺 id / 缺 title+runner / 缺 frontmatter / unknown runner）

過程發現 1 個 validator bug：`validate_file()` 對 tmp_path 餵進來的檔
`Path.relative_to(REPO)` 會 ValueError。修為 try/except fallback 印絕對路徑。

全測試套件：**113 passed in 1.17s**（102 既有 + 11 新增）。

## 後續

- 若 autogo 端登錄此 repo 後按 `↻ refresh` 看到 `<label>:001-search-ui-smoke` 等 5 條 → 接續按 `▶ Run now` 跑單一支驗證 wiring。
- 將來加 traced 風格 plan（含 `js traced-script` 區塊）需更新 validator
  跳過 fenced code block 的 frontmatter scan（目前 2 KB 範圍內前段 frontmatter
  解析未受影響，但 strict 模式可能要新增「至少 1 個 traced-script fence」檢查）。

## Fallback

```bash
git revert HEAD~3..HEAD
rm -rf test-plans/ scripts/validate_test_plans.py tests/test_test_plans.py
```
