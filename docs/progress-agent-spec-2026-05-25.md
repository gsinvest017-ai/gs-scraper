# 2026-05-25 — Agent / Skill specs

> 啟動：2026-05-25
> 觸發：`/safe-yolo 寫一個repo agent spec讓每次增量爬蟲在爬取新資料之後 都自動更新gap-dashboard資訊 寫另一個system global agent command讓電腦上的的所有repo可以在執行"/update-doc"之後根據最新commits時的repo狀態來更新doc website`

---

## 目標

兩個獨立的自動化資產：

1. **QUANTDATA repo-scoped agent** `incremental-crawler` — 任何時候做增量爬蟲，**強制**在最後跑 gap_report 並把 dashboard 寫進 docs/ 與 docs-site/
2. **Global slash command** `/update-doc` — 在任何 repo（不只 QUANTDATA）執行 `/update-doc` 後，根據該 repo 最新 commit 的狀態自動更新文檔網站（如果有 MkDocs 結構），含 mermaid 圖、index、各 page 內容 refresh

---

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | `.claude/agents/incremental-crawler.md`（QUANTDATA-scoped agent spec）+ 本進度檔 | ✅ |
| **M2** | `~/.claude/skills/update-doc/SKILL.md` + `~/.claude/commands/update-doc.md`（global） | ✅ |
| **M3** | docs-site/ops/ 補一頁說明這兩個自動化 + 在 README 連到；commit + push | ⏳ |

---

## 進度日誌

### M1 — incremental-crawler agent

`.claude/agents/incremental-crawler.md` 落地，frontmatter 含 `name` + 強 `description`（中文觸發詞涵蓋「跑增量爬蟲 / 抓最新 TEJ / refresh FinMind / append-since-silver / 更新 silver / 補洞」），tool 限制 `Bash, Read, Edit, Write, Grep`（不需 NotebookEdit / Web*）。

核心約束：**爬完必跑 gap_report + 必 commit**。其他流程（fetch → ingest → build-catalog → restore_finmind_views → regen → commit）作為標準工作流文件化。明確列出不可省略 / 不要做的事，包含「不主動 push」「不動 bronze」「不省 dashboard regen」。

設計思路：補上人類常忘記的「regen gap_dashboard」這個尾段，把「爬 → regen → commit」變成原子單元。

### M2 — global update-doc skill

落兩個檔到 `~/.claude/`：

- `~/.claude/skills/update-doc/SKILL.md`（~150 行）— 完整工作流：偵測框架（MkDocs/Docusaurus/VitePress/Jekyll）→ 找上次 doc commit → strict build → 提案要改的頁 → regen dashboard → changelog → commit
- `~/.claude/commands/update-doc.md`（~40 行）— slim 入口，frontmatter `description`，body `$ARGUMENTS`，重點摘要 + 安全規則

驗證：harness 自動 register skill；下一輪 system-reminder 已列出 `update-doc` 在 available skill list 第一條，trigger 描述完整。

設計重點：
- **框架不可知**：四種主流 doc framework 都支援，build command 不同但流程一致
- **看 git diff 提案**：不亂改頁面，先列哪幾頁該改 + 為什麼
- **不主動 push**：除非使用者明確要求
- **尊重 repo-scoped agent**：若 repo 有 `.claude/agents/<doc-*>.md`，本 skill 是 fallback
- **scaffold 路線**：repo 沒 doc-site 也能起手

### M3 — pending

---

## Fallback

- **M1 rollback**：`rm .claude/agents/incremental-crawler.md`，agent 從此不再被自動 routing
- **M2 rollback**：`rm -rf ~/.claude/skills/update-doc/ ~/.claude/commands/update-doc.md`
- **若 incremental-crawler 觸發了不該觸發的 task**：在 prompt 加 "別用 incremental-crawler"，或暫時把 agent file 改副檔名
