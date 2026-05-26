# 2026-05-26 — `/goldify-100` repo slash command（含 loop）

## 觸發

`/safe-yolo 寫一個 repo agent command "goldify-100" 把 complete rate 已經 100 percent 的 data catalog 的資料處理成 gold medal level 若是處理完還是有沒有 gold medal level 的資料則繼續 loop 進行`

## 目標

把 `.claude/agents/goldify-100pct.md`（agent definition）封裝成可用 `/goldify-100` 一鍵觸發的 **repo-scoped slash command**，並加上 **loop 語意**：跑完一輪後若 `goldify_audit` 仍報告 ripe candidates，就再跑下一輪，直到收斂（0 candidates）或卡 3 輪沒進展。

## 為什麼需要 loop

某些 view goldify 後會 **解鎖新的 ripe candidate**：
- 例如 `finmind_price_canonical` 變成 gold 後，下游若有「FinMind canonical + qc」的派生 view 又會冒出來
- 或者 cron 跑完之後馬上跑 `/goldify-100`，可能會在第一輪 build derived 後新衍生出一些 view 進入 100% 完整度

所以單次跑不夠；需要 audit → process → audit → process ... 直到穩態。

## 範圍

1. **`.claude/commands/goldify-100.md`** — repo slash command（不是 agent）
   - frontmatter `description` 給 `/help` 列表用
   - 內文指令 Claude 跑 loop：每輪內呼叫既有 `goldify-100pct` agent；audit `0 candidates` 就退出
   - 上限 5 輪（防護 infinite loop）；連續 2 輪 candidates 數沒下降就停下來人工 review

2. **`docs-site/ops/goldify-routine.md`** — 加一段「快速觸發：`/goldify-100`」

3. **`docs-site/ops/automation.md`** — 在 3-agent table 加上新的 command（變 4 個 entry，或把 command 列為「agent + slash command 互補組合」）

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 | ⏳ |
| **M2** | `.claude/commands/goldify-100.md`（slash command 全文）| ⏳ |
| **M3** | docs 更新 + strict build + commit + push | ⏳ |

## Slash command 設計（M2 細節）

```
---
description: Goldify every 100%-complete catalog view that lacks gold; loop until 0 candidates.
---

You are running the goldify routine on QUANTDATA repo. Follow this loop strictly:

1. Run audit:  .venv/bin/python scripts/goldify_audit.py --json meta/audit/goldify_audit.json
2. If 0 candidates → ✅ done, report and exit
3. Else: invoke the goldify-100pct workflow (see .claude/agents/goldify-100pct.md):
   - M1: write progress doc
   - M2: add builders in derived.py
   - M3: registry + catalog wiring
   - M4: rebuild catalog + dashboard + commit
4. After M4 commit, re-run audit
5. Repeat. Max 5 iterations. If iteration K and K+1 have same candidate count, stop and report stuck.

Hard rules:
- Each iteration gets its own progress doc (docs/progress-goldify-100-<YYYY-MM-DD>-iter<N>.md)
- Each milestone gets its own commit (Mn-iter<N>: ...)
- Never skip dashboard regen
- Push only at the very end (after all iterations converge)
```

## Fallback

- loop 卡住：手動 audit + 人工 review；無新 candidate 仍報 candidate 表示有 view 的 `gold_paths` registry 漏掛 → 修 `gap_report.py`
- 5 輪上限觸發：通常代表設計失誤（builder 沒實際移動 view，或 audit 邏輯不正確）

## 完成日誌

（M2-M3 後追加）
