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
| **M2** | 在 `docs-site/ui/gap-dashboard.md` + `docs-site/index.md` 加 live URL 連結；加 nav 條目；strict build PASS | ⏳ |
| **M3** | push + verify https://...github.io/gs-scraper/gap_dashboard.html HTTP 200 | ⏳ |

## 進度日誌

### M1 — gap_report.py 雙寫

加 `--out-html-mirror` arg（預設 `docs-site/gap_dashboard.html`），讀取一次 `render_html()` 後寫兩份。Regen 後兩檔 md5 一致（`6f5c90511ba08f6debb3a7272970ccff`，13,102 bytes）。

向後相容：`--out-html-mirror=""` 可以關掉鏡像（給 systemd / cron 不需要更新 docs-site 時用）。

### M2 — pending

### M3 — pending

## Fallback

- Rollback M1：`git revert <commit>` + 刪 `docs-site/gap_dashboard.html`
- 若 docs-site/ HTML 過時：手動跑 `.venv/bin/python scripts/gap_report.py --format all` 重生
- 若覺得每天都要 push 文件累：未來改成 docs.yml 內加 step `cd /tmp/...` 跑 gap_report，避免 dashboard 進版控
