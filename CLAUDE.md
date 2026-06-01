# CLAUDE.md — QUANTDATA agent spec

本檔是給 Claude（及人類維護者）讀的 repo 規約。Claude Code 開啟此 repo 時會自動把
本檔載入 context。

---

## Commit message 規約（強制）

從 2026-06-01 起，本 repo 的 **git commit subject（標題行）必須包含至少 1 個繁體中
文字**。body 可用中英混寫，技術細節用英文沒問題；trailer（`Co-Authored-By:` /
`Signed-off-by:` 等）保持英文，GitHub / 工具鏈靠它做機器解析。

### Subject 規則

1. **至少 1 個 CJK 字**（Unicode `U+4E00..U+9FFF` 或 `U+3400..U+4DBF`）
2. 標題長度 **≤ 72 字**（中文字算 1 字，非位元組數）
3. 仍保留以下前綴慣例（與中文並存）：
   - safe-yolo milestone：`M1: <中文描述>`、`M2-iter3: <中文描述>` 等
   - Conventional Commits：`feat: ...` / `fix: ...` / `docs: ...` / `chore: ...` /
     `refactor: ...` / `test: ...` / `build: ...` / `ci: ...` / `perf: ...` /
     `style: ...` / `revert: ...`
4. 允許在中文敘述裡保留技術 token（檔名、函式名、SQL 關鍵字、log 原文、變數、
   英文縮寫如 `CSV` / `CI` / `DuckDB` / `Flask` / `pytest`）

### Body 規則（不強制中文）

- 中英混寫可以；複雜實作細節 / 錯誤訊息用英文最自然
- 不限字數，但每行 ≤ 100 字較容易在 `git log --oneline` 與 GitHub PR 內閱讀

### Trailer（保持英文）

```
Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
Signed-off-by: ...
```

### 例外（不擋）

- `Merge ...`（`git merge` 自動生成）
- `Revert "<subject>"`（`git revert` 自動生成）
- `Initial commit`（首次提交慣例）
- `fixup!` / `squash!`（git rebase --autosquash 暫存）

### 範例

✅ 合格

```
M2: 寫 P0 unit 測試 — query_builder + BS-IV + CSV escape 共 66 個案例
fix: TXO build_txo_daily_features 在 Timestamp vs date 比較炸 TypeError
docs: 補 autogo /plans import contract 與本地 validator
chore: 升級 pytest 7.4 → 8.0
M4-iter3: dashboard 重生 — OK 38 → 39（新增 macro_daily gold）
```

❌ 不合格（無中文）

```
M2: P0 unit tests for query_builder
fix: txo TypeError on Timestamp vs date
chore: bump pytest
```

### 自動把關

兩道閘門已就位（任一即可）：

1. **commit-msg hook**（推薦，本機提交時擋下）：

   ```bash
   ln -sf ../../scripts/git-hooks/commit-msg .git/hooks/commit-msg
   ```

2. **CI 驗證**：`scripts/check_commit_messages.py` 在 pytest 與 GitHub Actions
   都會跑，PR 上有違規 commit 會擋合併。

人工驗證：

```bash
python scripts/check_commit_messages.py            # HEAD~20..HEAD 預設
python scripts/check_commit_messages.py --range origin/main..HEAD
```

---

## 其他 repo 規約

- **目錄結構**：`bronze/`（不可變）/ `silver/`（標準化）/ `gold/`（research-ready
  features）/ `catalog/`（DuckDB views）/ `reference/`（symbol_map 等）/
  `meta/`（audit / schema / lineage）/ `ui/`（Search UI）/ `scripts/`（一次性
  與 cron 腳本）/ `tests/`（pytest）
- **永遠不動 bronze/**：原始檔不可變，重抓只能往 silver 寫
- **silver multi-ingest dedup**：用 `unique(subset=key, keep='last' by ingestion_ts)`
- **DuckDB 鎖**：`duckdb -ui` CLI 會鎖整個 catalog；audit / build 前要先確認 lock
  free（`ps -o pid -p <pid>` 或 `fuser catalog/quant.duckdb`）
- **不主動 push**：safe-yolo 與 goldify-100 都不會 `git push`；要 push 由人類決定
- **Search UI 預設 bind**：`0.0.0.0:5050`（透過 WSL2 / Windows portproxy 對外）
- **測試**：`pytest -q tests/` 須全綠；CI 跑 ubuntu × {3.11, 3.12} matrix
- **詳細測試計畫**：見 `docs/test-plan.md`
- **autogo test-plans**：放 `test-plans/*.md`，符合 frontmatter spec；本地用
  `scripts/validate_test_plans.py --strict` 預先驗
