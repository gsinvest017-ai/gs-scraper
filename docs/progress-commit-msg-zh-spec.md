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

## Fallback

```bash
git revert HEAD~2..HEAD
rm -f CLAUDE.md scripts/check_commit_messages.py scripts/git-hooks/commit-msg tests/test_commit_msg_check.py
```
