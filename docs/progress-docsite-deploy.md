# docs-site 上線部署進度

> 建立日期：2026-05-21
> 範圍：把已建好的 `docs-site/` MkDocs 站台推到 GitHub、設定 Pages、確認 live URL 可用。
> 前置：見 `docs/progress-docsite.md`（站台本身的 build & strict 已通過）。

---

## 目標

把當前 main branch 上 5 個 docs-site commit + 1 個 gitignore commit 推到 `gsinvest017-ai/gs-scraper`，讓 GitHub Actions `docs.yml` 第一次跑、產生 `gh-pages` branch、然後把 Pages source 設成 `gh-pages` → 拿到 live URL。

---

## 起始狀態（2026-05-21 pre-deploy）

- Remote: `origin = https://github.com/gsinvest017-ai/gs-scraper.git`
- 本地 branch: `main`，ahead 20 commits 於 `origin/main`
- repo visibility: **public**（personal accounts 用 GitHub Pages 必須）
- repo default branch: `main`
- gh auth: `gsinvest017-ai` token scopes 含 `repo` + `workflow`（夠用）
- `gh-pages` branch: **不存在**（會由 docs.yml 首次跑時自動建）
- Pages config: **未啟用**（`gh api .../pages` 回 404）
- 本地 `mkdocs build --strict` 通過（M5 已驗）

---

## Milestone 計畫

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 寫此進度檔 + 確認 pre-flight（remote / branch / gh auth / repo public） | ✅ |
| **M2** | `git push origin main`：把 20 個本地 commit 推上去；觸發第一次 `docs.yml` workflow run | ⏳ |
| **M3** | 等 docs.yml 完成（建出 `gh-pages` branch）→ `gh api ... /pages -F source.branch=gh-pages` 設 Pages source | ⏳ |
| **M4** | 確認 live URL 200，把 URL 寫進 README / index.md | ⏳ |

---

## 進度日誌

### M1 — pre-flight clean

Confirmed `gh repo view`: public + main + 不啟用 Pages（404）；`gh-pages` branch 不存在。本地 20 commits ahead，無 dirty changes（meta/audit/ingest jsonl 被 .gitignore 蓋掉）。

### M2 — pending

### M3 — pending

### M4 — pending

---

## Fallback 指引

| 卡關 | 怎麼接手 |
|---|---|
| Push 拒絕（force needed） | `git fetch origin && git log origin/main..HEAD` 看差異；正常前進就 `git push`，不要 force |
| docs.yml workflow 失敗 | `gh run list -w docs.yml --limit 3` + `gh run view <id> --log-failed` |
| `mkdocs build --strict` CI 失敗但本地過 | 多半是 Python 版本差；CI 用 3.12，本地 venv 也是 3.12 應該一致 |
| `gh api -X POST /pages` 回 409 (already configured) | Pages 已設好；用 `gh api repos/.../pages` PATCH 改 source |
| Live URL 404 | gh-pages branch 還沒 deploy 完；等 1-3 分鐘再 curl |
| Force rollback | `git revert <commit>` 整段；刪 gh-pages branch：`git push origin :gh-pages` |

未來重新部署：只要 `git push`，docs.yml 自動跑。手動觸發：`gh workflow run docs.yml --ref main`。
