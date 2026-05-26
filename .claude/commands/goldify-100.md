---
description: Goldify EVERY catalog view that has non-gold data (silver/bronze/raw) and no gold_paths — regardless of completeness. Loop until 0 candidates or stuck.
---

You are running the **`/goldify-100` repo routine** on QUANTDATA. The goal: **100% catalog coverage of gold artifacts** — every view whose silver/bronze/raw contains at least one row must have an entry in `gold_paths` (a new gold parquet derived from its source, or a backlink to an existing gold). Completeness % is NOT a filter: STALE views with old silver data are equally valid goldification targets. Gold reflects "as of silver max_date" snapshot; refresh upstream separately if you want fresher gold. When you finish one pass, re-audit; if new ripe candidates emerged, do another pass. Stop when **0 candidates** or **stuck** (no progress for 2 consecutive iterations).

> **Note on the name**: "goldify-100" originally implied "100% per-view completeness" but is reinterpreted as "100% catalog coverage of gold". This wider scope was the intent; the legacy narrower behavior is still accessible via `scripts/goldify_audit.py --complete-only`.

**User extra instructions**: $ARGUMENTS

---

## Hard invariants (apply every iteration)

1. **Audit-first**: never edit `derived.py` / `gap_report.py` until `goldify_audit.py` confirms ≥1 candidate
2. **Milestone-based commits**: each pass writes its own progress doc + at least 4 commits (M1 plan / M2 builders / M3 registry / M4 dashboard); never bundle
3. **Silver multi-ingest dedup** in every builder: `unique(subset=key, keep='last' by ingestion_ts)`
4. **Dashboard regen at end of every iteration**: even if 0 candidates this round
5. **Push only after final convergence** — not after each iteration

---

## Loop algorithm

```
iteration ← 1
prev_count ← +∞
while iteration ≤ 5:
    cands ← run scripts/goldify_audit.py --json meta/audit/goldify_audit.json
    n ← len(cands)
    if n == 0:
        say "✅ converged after K iterations"
        regen dashboard one last time (sanity)
        push origin main
        exit
    if n == prev_count and iteration > 1:
        say "⚠ stuck — no progress between iter $(iteration-1) and $iteration. n=$n. Stopping."
        print candidates table; exit
    do_one_iteration(iteration, cands)  ← see below
    prev_count ← n
    iteration += 1

if iteration > 5:
    say "⚠ 5-iteration cap hit; stopping for human review"
    push? no — let user inspect first
```

### `do_one_iteration(iteration, cands)` — 4 milestones

Match exactly the `.claude/agents/goldify-100pct.md` workflow:

**M1 — plan** (`docs/progress-goldify-100-2026-MM-DD-iter<N>.md`):
- list cands with view / template / model-after gold
- one-paragraph factor design per cand (use template name → look at the existing model gold's columns)
- commit: `M1-iter<N>: plan — goldify <view list>`

**M2 — builders** (`src/qd_ingest/sources/derived.py`):
- add `build_<view_stub>()` per cand modeled on `build_<template_example>`
- include silver dedup
- include `build_<...>` calls in `build_all()`
- run each new builder individually to verify row count & elapsed
- commit: `M2-iter<N>: <new view list> builders + run`

**M3 — registry + catalog** (`scripts/gap_report.py`, `src/qd_ingest/common/catalog.py`):
- in `scripts/gap_report.py` DATASETS: backlink each cand's silver Dataset `gold_paths` to new parquet, **and** add a fresh Dataset entry for the new gold view itself (with `derived` or appropriate category, P-tier)
- in `src/qd_ingest/common/catalog.py`: register each new parquet in the gold `for name, fp in [...]` loop
- commit: `M3-iter<N>: registry+catalog — <new view list>`

**M4 — rebuild + dashboard**:
- `cp catalog/quant.duckdb catalog/quant.duckdb.bak_pre_goldify100_iter<N>_$(date +%s)`
- if DuckDB UI lock holding (PID via `fuser catalog/quant.duckdb`), kill the idle session
- `.venv/bin/python -m qd_ingest.common.catalog`
- `.venv/bin/python scripts/restore_finmind_views.py` (**always** — `build()` doesn't preserve finmind views)
- spot-check new views queryable: `duckdb catalog/quant.duckdb -c "SELECT count(*) FROM <new_view>;"`
- `.venv/bin/python scripts/gap_report.py --format all` (writes `docs/gap_dashboard.html` + `docs-site/gap_dashboard.html`)
- `.venv/bin/mkdocs build --strict`
- commit: `M4-iter<N>: dashboard regen — OK=<before>→<after>` (include the progress doc with logs)
- **no push yet** — wait until convergence

### After loop exits

- Report total iterations, new gold views added, before→after OK count
- Final progress doc summarizes all iterations
- One final push: `git push origin main` (use `Monitor` with retry-on-500 if GitHub flakes)

---

## Factor template → model-after lookup (from audit)

| audit template | model-after builder | model-after gold |
|---|---|---|
| `time_series_bar` | `build_stock_factor_daily` | `stock_factor_daily` |
| `flow_rolling` | `build_inst_flow_factors` | `inst_flow_factors` |
| `balance_zscore` | `build_margin_factors` | `margin_factors` |
| `per_entity_oi` | `build_futures_inst_factors` | `futures_inst_factors` |
| `event_panel` | `build_dividend_calendar` | `dividend_calendar` |
| `boolean_panel` | `build_stock_attrs_status` | `stock_attrs_status` |
| `pit_fundamentals` | `build_fundamentals_pit` | `fundamentals_pit` |
| `view_materialize` | `materialize_qc_snapshot` | `qc_stock_price_diff_snapshot` |
| `left_join_merge` | `materialize_finmind_canonical` | `finmind_price_canonical` |

---

## Stop conditions (any one → halt loop)

| condition | action |
|---|---|
| `goldify_audit` returns 0 candidates | ✅ converged → final push |
| candidate count same as last iteration (≥ 2 iterations) | ⚠ stuck → print stuck candidates + STOP, no push |
| iteration > 5 | ⚠ cap hit → print state + STOP, no push |
| Any milestone fails 3 times | ⚠ stuck → commit WIP, log in progress doc, STOP |

---

## Don'ts

- ❌ Don't push after each iteration — wait until loop exits cleanly
- ❌ Don't skip M1 progress doc even on iteration 2+ (every iteration gets a fresh doc with iter suffix)
- ❌ Don't auto-create new silver views — this command only goldifies *existing* 100%-complete views
- ❌ Don't `--force` anything; if a builder errors, fix root cause and commit a new attempt
- ❌ Don't bundle iter1 + iter2 in same commit
- ❌ Don't touch `bronze/` — bronze is immutable

---

## Typical fast-path (if audit reports 0 on first run)

```
$ /goldify-100
> Audit: ✅ no views with non-gold data found.
> Catalog is fully goldified. No work to do.
> exit 0
```

This is expected most days. The routine does real work when:
- A new silver view appears (e.g. a fresh `fetch_tej.py --table foo` writes silver parquet for a previously-untracked table)
- An existing silver view has data but no `gold_paths` backlink (registry oversight)
- A previously empty view gains its first row of data

## Why both agent and command?

- **`.claude/agents/goldify-100pct.md`** is the *agent definition* — Claude routes to it when conversation context implies goldification (natural language trigger).
- **`/goldify-100`** is the *deterministic command* — the user types it explicitly, expects loop semantics, doesn't want to negotiate scope. The command embeds the agent's workflow and **adds** the loop wrapper.

Use the command when you want guaranteed convergence; the agent is fine for single ad-hoc passes.
