# 2026-06-01 — Commit message 中文化 spec

## 目標

把「git commit message 用繁體中文寫」訂成本 repo 的可執行規則：
（1）寫進 `CLAUDE.md` 讓 Claude 與人類都看得到，
（2）給一支驗證腳本可重複跑，
（3）給一個 commit-msg hook 在提交時自動把關，
（4）pytest 守住規則本身（避免之後規則文字被誤刪）。

> 規則只管 **subject line（標題行）**。body 不強制；
> `Co-Authored-By:` 等 trailer 仍英文（GitHub 機器解析依賴）。

## 計畫 milestone

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + `CLAUDE.md`（spec 本體） |
| **M2** | `scripts/check_commit_messages.py`（CLI + hook 用同一支）+ `scripts/git-hooks/commit-msg`（hook 樣板） |
| **M3** | `tests/test_commit_msg_check.py` + 全 suite 全綠 + 收尾 |

## 規則摘要（細節見 `CLAUDE.md` § Commit message 規約）

1. **Subject 必須含至少 1 個中日韓統一表意文字**（U+4E00..U+9FFF 或 U+3400..U+4DBF）
2. 保留 `Mn:` / conventional commits（feat/fix/docs/chore/...） 前綴與技術 token
3. 標題長度 ≤ 72 字（中文字也算）
4. 例外（自動產生、不擋）：`Merge ...`、`Revert "..."`、`Initial commit`

## 進度日誌

### M1 — spec 落地  `488e98f`

- `CLAUDE.md` 寫死規約：subject 至少含 1 個 CJK 字、長度 ≤ 72 字、保留
  `Mn:` / conventional commits 前綴、列出 4 種自動產生例外
- 此 commit 本身就是第一支符合規約的 commit（標題全中文）

### M2 — validator + hook  `(M2 commit)`

- `scripts/check_commit_messages.py`：CJK regex `[一-鿿㐀-䶿]`、空白檢查、
  長度檢查、Merge/Revert/Initial commit/fixup!/squash! 例外
  - 雙模式：CLI（`--range` 對 git log）與 hook（`--file` 讀單一 message file）
  - 支援 `--json` 與 `--strict`
- `scripts/git-hooks/commit-msg`：bash 樣板；安裝指令寫在 CLAUDE.md
- 自我測試 last 6 commits：1/6 通過（僅今日 M1 中文 commit）

### M3 — pytest 鎖 + hook 安裝  `(M3 commit)`

- `tests/test_commit_msg_check.py` 25 case：7 正面、6 負面、5 自動例外、
  2 長度邊界、2 CLAUDE.md 文字存在性、3 `--file` 模式（含 git 註解行）
- 安裝 hook：`ln -sf ../../scripts/git-hooks/commit-msg .git/hooks/commit-msg` ✓
- 全 suite：**138 passed in 1.26s**（既有 113 + 新 25）
- 不追溯歷史：歷史 commit 不會被刪改，CI 只擋新 commit（建議用 `--range
  origin/main..HEAD` 限縮到 PR 變動）

## 後續

- **CI 串接**：`.github/workflows/pytest.yml` 已涵蓋（pytest 跑就會擋），如要 PR
  獨立 job 可加：

  ```yaml
  - name: commit-msg check
    run: python scripts/check_commit_messages.py --range ${{ github.event.pull_request.base.sha }}..HEAD
  ```

- **歷史不追溯**：規約 2026-06-01 起生效。早於此 sha 的 commit 維持原狀，
  不 force-push 也不 rebase 改寫。
- **緊急時略過**：`git commit --no-verify -m "..."` 一次性繞過 hook（限緊急
  修補；事後仍要看 PR review 是否擋下）。

## Fallback

```bash
git revert HEAD~2..HEAD
rm -f CLAUDE.md scripts/check_commit_messages.py scripts/git-hooks/commit-msg tests/test_commit_msg_check.py
rm -f .git/hooks/commit-msg
```
