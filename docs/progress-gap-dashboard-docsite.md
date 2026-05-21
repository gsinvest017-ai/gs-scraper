# gap_dashboard.html 接入 docs-site 進度

> 建立日期：2026-05-21
> 範圍：讓 `docs/gap_dashboard.html`（gap_report.py 產出的新鮮度 dashboard）能透過 docs-site 在公開 URL 看到。
> 前置：docs-site 已上線（見 `progress-docsite.md`、`progress-docsite-deploy.md`）。

## 目標

讓使用者可以從 https://gsinvest017-ai.github.io/gs-scraper/gap_dashboard.html 直接看到最新的 dashboard，而不是只能本地 `explorer.exe docs/gap_dashboard.html`。

## 設計選擇

3 種路線比較：

| 路線 | 描述 | 評價 |
|---|---|---|
| **A. gap_report.py 雙寫** | 寫 `docs/gap_dashboard.html` AND `docs-site/gap_dashboard.html` | ✅ 最簡單；本地 mkdocs serve / CI mkdocs build 都自動拿到最新 |
| B. CI-only 複製 | docs.yml 增加 `cp docs/... docs-site/...` step | 本地 `mkdocs serve` 看不到，違反 SSOT |
| C. Symlink | `docs-site/gap_dashboard.html → ../docs/gap_dashboard.html` | symlink 在 git 跨平台 fragile，mkdocs 對 symlink 行為不一致 |

選 **A**。新增 `--out-html-mirror` flag，預設 `docs-site/gap_dashboard.html`，set to `""` 可關掉。

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 改 `scripts/gap_report.py` 加 mirror 寫入，regen 兩份 HTML | ✅ |
| **M2** | 在 `docs-site/ui/gap-dashboard.md` + `docs-site/index.md` 加 live URL 連結；加 nav 條目；strict build PASS | ✅ |
| **M3** | push + verify https://...github.io/gs-scraper/gap_dashboard.html HTTP 200 | ✅ |

## 進度日誌

### M1 — gap_report.py 雙寫

加 `--out-html-mirror` arg（預設 `docs-site/gap_dashboard.html`），讀取一次 `render_html()` 後寫兩份。Regen 後兩檔 md5 一致（`6f5c90511ba08f6debb3a7272970ccff`，13,102 bytes）。

向後相容：`--out-html-mirror=""` 可以關掉鏡像（給 systemd / cron 不需要更新 docs-site 時用）。

### M2 — 連結 + nav 條目 + strict build

- `docs-site/ui/gap-dashboard.md` 開頭加 Material admonition `!!! tip "📊 看當前 live dashboard"` 含 `[→ 開啟 gap_dashboard.html](../gap_dashboard.html){target=_blank}`
- `mkdocs.yml` nav 加頂層條目 `- Live Dashboard ↗: gap_dashboard.html`（MkDocs 1.6.1 nav 接受 HTML 檔；不會 strict-fail）
- `.venv/bin/mkdocs build --strict` PASS（0.36s，加碼 `gap_dashboard.html` 進到 site/）

Verify：
- `site/gap_dashboard.html` 出現
- `site/index.html` 含 3 個 `gap_dashboard.html` reference（nav）
- `site/ui/gap-dashboard/index.html` 含 8 個 reference（admonition + nav + 內文）

### M3 — push + live verify

`git push origin main` (`62f36db..9b44d12`) 觸發 `docs.yml` workflow run `26215198819`，~15s 完成 success。Pages 因為已啟用，重新 deploy 後立即可訪：

| URL | HTTP |
|---|---:|
| <https://gsinvest017-ai.github.io/gs-scraper/gap_dashboard.html> | **200** |
| <https://gsinvest017-ai.github.io/gs-scraper/ui/gap-dashboard/> | **200** |

內容驗證：`curl` 結果含 3 個 FinMind/QC INFO 列（finmind_stock_price_norm / finmind_stock_price_adj_norm / qc_stock_price_diff），跟本地完全一致。

進入方式：
- 從 docs-site 任何頁面點頂層 nav 的「Live Dashboard ↗」
- 從 `ui/gap-dashboard` 頁開頭的 tip box `→ 開啟 gap_dashboard.html`
- 直接 URL：上面那條

## 後續

每次 `.venv/bin/python scripts/gap_report.py --format html`（含 cron daily_refresh）會 mirror 到 docs-site/。`git push` 觸發 docs.yml 後 live URL 自動更新。

兩個小擔憂：

1. **dashboard 進版控有 churn**：每天 commit 一次 13 KB 的 HTML。可以接受（300/year × 13KB ≈ 4MB/年）。要改：未來把 docs.yml 改成 build 時自己跑 gap_report.py（需要把 DuckDB catalog 也 stage 上 CI，工作量大，先不做）。
2. **本地 cron 沒 push，dashboard live URL 滯後**：要看最新一定要手動 `git push`。可以接受。

## Fallback

- Rollback M1：`git revert <commit>` + 刪 `docs-site/gap_dashboard.html`
- 若 docs-site/ HTML 過時：手動跑 `.venv/bin/python scripts/gap_report.py --format all` 重生
- 若覺得每天都要 push 文件累：未來改成 docs.yml 內加 step `cd /tmp/...` 跑 gap_report，避免 dashboard 進版控
