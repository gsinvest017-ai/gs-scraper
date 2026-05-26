"""goldify_audit.py — find catalog views with 100% completeness that lack gold.

Reads:
  - catalog/quant.duckdb   — for actual freshness (completeness)
  - scripts/gap_report.py  — for the DATASETS registry (gold_paths annotations)
  - silver column schemas  — to suggest factor templates

Output (default: text to stdout, JSON via --json):
  - List of "ripe" candidate views: 100% complete, has silver_paths, gold_paths empty
  - For each: silver row count, key columns, suggested factor template name
  - Suggested next steps (add a builder in derived.py, register, regen dashboard)

Usage:
  .venv/bin/python scripts/goldify_audit.py
  .venv/bin/python scripts/goldify_audit.py --json meta/audit/goldify_audit.json
  .venv/bin/python scripts/goldify_audit.py --markdown reports/goldify_audit.md

Why this exists:
  Every time silver gains a new view, eventually it hits 100% completeness and
  becomes a candidate for goldification. Manually scanning the dashboard is
  error-prone. This script makes the goldify-routine deterministic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog" / "quant.duckdb"


# --- Factor template heuristics --------------------------------------------

@dataclass
class FactorTemplate:
    name: str
    description: str
    typical_outputs: tuple[str, ...]
    example_existing_gold: str  # an existing gold parquet to model after


TEMPLATES: dict[str, FactorTemplate] = {
    "time_series_bar": FactorTemplate(
        name="time_series_bar",
        description="time-series factors on (date, symbol) bar/price data",
        typical_outputs=("ret_5d/20d/60d", "vol_20d/60d", "atr_14", "turnover_20d"),
        example_existing_gold="stock_factor_daily",
    ),
    "flow_rolling": FactorTemplate(
        name="flow_rolling",
        description="rolling flow factors on (date, stock_id) net positions",
        typical_outputs=("net_5d/20d/60d", "persistence_20d", "hold_pct_chg_20d"),
        example_existing_gold="inst_flow_factors",
    ),
    "balance_zscore": FactorTemplate(
        name="balance_zscore",
        description="balance/utilization factors with z-score normalization",
        typical_outputs=("balance_chg_5d/20d", "util_zscore_60d", "ratio_chg_20d"),
        example_existing_gold="margin_factors",
    ),
    "per_entity_oi": FactorTemplate(
        name="per_entity_oi",
        description="per-entity open-interest factors (institutional or large-trader)",
        typical_outputs=("net_oi_chg_5d/20d", "net_volume_zscore_60d", "long_short_oi_ratio"),
        example_existing_gold="futures_inst_factors",
    ),
    "event_panel": FactorTemplate(
        name="event_panel",
        description="forward/backward looking event panel with cumulative + YoY",
        typical_outputs=("cum_<value>", "ttm_<value>", "yoy_growth_pct", "days_since/until"),
        example_existing_gold="dividend_calendar",
    ),
    "boolean_panel": FactorTemplate(
        name="boolean_panel",
        description="varchar Y/N flags converted to bool + rolling counts",
        typical_outputs=("is_*_bool", "<flag>_count_30d"),
        example_existing_gold="stock_attrs_status",
    ),
    "pit_fundamentals": FactorTemplate(
        name="pit_fundamentals",
        description="point-in-time fundamentals with TTM + YoY",
        typical_outputs=("*_ttm", "yoy_growth_pct", "rolling_4_avg"),
        example_existing_gold="fundamentals_pit",
    ),
    "view_materialize": FactorTemplate(
        name="view_materialize",
        description="materialize a pure SQL view as a parquet snapshot (no new factors)",
        typical_outputs=("(direct COPY of view)", "+ optional yearly aggregate"),
        example_existing_gold="qc_stock_price_diff_snapshot",
    ),
    "left_join_merge": FactorTemplate(
        name="left_join_merge",
        description="LEFT JOIN multiple views into a single canonical parquet",
        typical_outputs=("merged OHLCV", "+ adj_* columns"),
        example_existing_gold="finmind_price_canonical",
    ),
}


def suggest_template(columns: list[str], date_col: str, category: str) -> FactorTemplate:
    """Heuristic: pick a template based on silver schema and category."""
    colset = set(c.lower() for c in columns)

    # Pure view (no silver columns observable, e.g. computed QC)
    if not columns:
        return TEMPLATES["view_materialize"]

    # event_panel: ex_date / adjust_date / announce_date / pay_date
    if category == "event" or any(c in colset for c in ("ex_date", "adjust_date", "announce_date", "pay_date")):
        return TEMPLATES["event_panel"]

    # boolean_panel: >= 5 columns named is_*
    if sum(1 for c in colset if c.startswith("is_") or c.startswith("no_")) >= 5:
        return TEMPLATES["boolean_panel"]

    # PIT fundamentals: publish_date + eps/revenue/roe
    if "publish_date" in colset and any(c in colset for c in ("eps", "revenue", "roe_post", "net_income")):
        return TEMPLATES["pit_fundamentals"]

    # per_entity_oi: identity_code + open_interest variants
    if "identity_code" in colset and ("net_oi" in colset or "long_oi" in colset):
        return TEMPLATES["per_entity_oi"]

    # balance_zscore: margin / short balance
    if any("margin_balance" in c or "short_balance" in c for c in colset):
        return TEMPLATES["balance_zscore"]

    # flow_rolling: net_lot
    if any(c.endswith("_net_lot") for c in colset):
        return TEMPLATES["flow_rolling"]

    # time_series_bar: ohlc + volume
    if {"close", "volume"}.issubset(colset) or {"open", "high", "low", "close"}.issubset(colset):
        return TEMPLATES["time_series_bar"]

    # default: view_materialize (don't invent factors we can't justify)
    return TEMPLATES["view_materialize"]


# --- Registry + completeness loading ---------------------------------------

def load_registry() -> dict:
    """Load DATASETS from scripts/gap_report.py by import (not subprocess)."""
    spec = importlib.util.spec_from_file_location(
        "gap_report", REPO / "scripts" / "gap_report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # gap_report.py uses @dataclass; dataclass introspection requires the module
    # to be discoverable via sys.modules during exec.
    sys.modules["gap_report"] = mod
    spec.loader.exec_module(mod)
    return {d.view: d for d in mod.DATASETS}


def load_completeness() -> dict[str, dict]:
    """Run gap_report.py to refresh its JSON output, then read it.

    gap_report.py exits non-zero when any dataset is STALE/WARN, but the JSON
    is still written. We only care that the JSON file is fresh and parseable.
    """
    import subprocess
    out_path = REPO / "meta" / "audit" / "gap_report.json"
    subprocess.run(
        [".venv/bin/python", "scripts/gap_report.py", "--format", "json",
         "--out-json", str(out_path)],
        cwd=str(REPO), capture_output=True,
    )
    if not out_path.exists():
        raise RuntimeError(f"gap_report.py did not produce {out_path}")
    payload = json.loads(out_path.read_text())
    return {row["view"]: row for row in payload["datasets"]}


def view_columns(con: duckdb.DuckDBPyConnection, view: str) -> list[str]:
    try:
        rows = con.execute(f"DESCRIBE {view}").fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _proxy_completeness(comp: dict) -> float:
    """Mirror gap_report.html's completeness calc: clamp(1 - lag/90, 0, 1) * 100.

    Severity 'OK' means lag is within category tolerance, which on the dashboard
    typically renders 100%. For 'INFO' (derived/snapshot) we accept lag<=1 day.
    Anything else returns a real fractional percentage (so we never treat WARN
    or STALE as 100%).
    """
    sev = comp.get("severity")
    lag = comp.get("lag_days")
    if sev == "OK":
        return 100.0
    if sev == "INFO" and isinstance(lag, (int, float)) and abs(lag) <= 1:
        return 100.0
    if lag is None:
        return 0.0
    pct = max(0.0, min(1.0, 1.0 - abs(lag) / 90.0)) * 100.0
    return round(pct, 1)


# --- Audit ------------------------------------------------------------------

@dataclass
class Candidate:
    view: str
    tier: str
    category: str
    description: str
    row_count: int
    max_date: str
    completeness_pct: float
    severity: str
    columns: list[str]
    date_col: str
    silver_paths: tuple[str, ...]
    template: str
    template_outputs: tuple[str, ...]
    template_example: str
    suggested_steps: list[str] = field(default_factory=list)


def audit(complete_only: bool = False) -> list[Candidate]:
    """Find catalog views that should be goldified.

    Default (complete_only=False): any view with `row_count > 0` and empty
    `gold_paths` — regardless of dashboard severity. Gold reflects an "as of
    silver max_date" snapshot, so STALE silvers can still be goldified.

    complete_only=True restores the legacy filter (only severity=OK / 100%
    complete). Useful for CI where you only want to act on fresh data.
    """
    registry = load_registry()
    completeness = load_completeness()
    con = duckdb.connect(str(CATALOG), read_only=True)

    candidates: list[Candidate] = []
    for view, ds in registry.items():
        if ds.gold_paths:
            continue
        comp = completeness.get(view)
        if not comp:
            continue
        # Need at least one row to derive from. EMPTY views with no data at all
        # are skipped regardless of mode — there's literally nothing to goldify.
        if (comp.get("row_count") or 0) == 0:
            continue
        # Also need at least one upstream data path (silver/bronze/raw) in the
        # registry, otherwise the view is a pure computed view that may not
        # benefit from materialization (use view_materialize template if it does).
        # We don't enforce that here — `suggest_template` handles the no-silver case.
        pct = _proxy_completeness(comp)
        if complete_only and pct != 100.0:
            continue
        cols = view_columns(con, view)
        template = suggest_template(cols, ds.date_col, ds.category)
        steps = [
            f"1. Edit src/qd_ingest/sources/derived.py — add build_<{view.replace('_daily','')}>() using template '{template.name}' (model after build_{template.example_existing_gold})",
            f"2. Edit src/qd_ingest/common/catalog.py — register new gold parquet path in the gold-views loop",
            f"3. Edit scripts/gap_report.py — backlink {view}.gold_paths += '<new gold parquet>'; add new Dataset entry for the gold view itself",
            f"4. Run .venv/bin/python -m qd_ingest.sources.derived (or call the specific builder)",
            f"5. Run .venv/bin/python -m qd_ingest.common.catalog && .venv/bin/python scripts/restore_finmind_views.py",
            f"6. Run .venv/bin/python scripts/gap_report.py --format all to regen dashboard",
            f"7. git commit M2/M3/M4 separately; push at the end",
        ]
        candidates.append(Candidate(
            view=view,
            tier=ds.tier,
            category=ds.category,
            description=ds.description,
            row_count=comp.get("row_count", 0),
            max_date=str(comp.get("max_date", "")),
            completeness_pct=pct,
            severity=comp.get("severity", "?"),
            columns=cols,
            date_col=ds.date_col,
            silver_paths=ds.silver_paths,
            template=template.name,
            template_outputs=template.typical_outputs,
            template_example=template.example_existing_gold,
            suggested_steps=steps,
        ))
    con.close()
    return candidates


# --- Output formatters ------------------------------------------------------

def format_text(candidates: list[Candidate]) -> str:
    if not candidates:
        return "✅ goldify_audit: no views with non-gold data found. Catalog is fully goldified.\n"
    n100 = sum(1 for c in candidates if c.completeness_pct == 100.0)
    n_partial = len(candidates) - n100
    lines = [
        f"goldify_audit — {len(candidates)} candidate(s) found ({n100} at 100% / {n_partial} partial)",
        "=" * 100,
    ]
    for c in candidates:
        comp_tag = f"{c.completeness_pct:.0f}% {c.severity}"
        lines += [
            "",
            f"📌 {c.view}  (tier={c.tier}, category={c.category}, completeness={comp_tag})",
            f"   {c.description}",
            f"   silver rows: {c.row_count:,}  |  max_date: {c.max_date}  |  date_col: {c.date_col}",
            f"   suggested template: {c.template}  (model after gold/features/{c.template_example}.parquet)",
            f"   typical outputs: {', '.join(c.template_outputs)}",
            f"   columns ({len(c.columns)}): {', '.join(c.columns[:12])}{'...' if len(c.columns) > 12 else ''}",
        ]
    lines += [
        "",
        "=" * 100,
        "Standard workflow for each candidate (run as 4 milestones):",
    ]
    lines += [f"   {s}" for s in candidates[0].suggested_steps]
    if n_partial > 0:
        lines += [
            "",
            "ℹ️  Some candidates are not 100% complete (STALE/WARN). Gold will reflect a snapshot",
            "    of the silver as-of its current max_date. Refresh upstream separately for fresher gold.",
        ]
    return "\n".join(lines) + "\n"


def format_markdown(candidates: list[Candidate]) -> str:
    today = dt.date.today().isoformat()
    lines = [
        f"# Goldify Audit — {today}",
        "",
        f"Found **{len(candidates)} candidate view(s)** with non-gold data and no gold_paths backlink.",
        "",
    ]
    if not candidates:
        lines += ["✅ All views with non-gold data already have gold backlinks. Nothing to do."]
        return "\n".join(lines) + "\n"
    lines += [
        "| view | tier | category | completeness | severity | rows | template | model after |",
        "|---|---|---|---:|---|---:|---|---|",
    ]
    for c in candidates:
        lines.append(
            f"| `{c.view}` | {c.tier} | {c.category} | {c.completeness_pct:.0f}% | {c.severity} | {c.row_count:,} | `{c.template}` | `{c.template_example}` |"
        )
    lines += [
        "",
        "## Details",
        "",
    ]
    for c in candidates:
        lines += [
            f"### `{c.view}`",
            f"- **Description**: {c.description}",
            f"- **Silver rows**: {c.row_count:,}  |  **max_date**: {c.max_date}  |  **date_col**: `{c.date_col}`",
            f"- **Silver paths**: " + ", ".join(f"`{p}`" for p in c.silver_paths) if c.silver_paths else "- **Silver paths**: (none — materialize from view directly)",
            f"- **Suggested template**: `{c.template}` — {TEMPLATES[c.template].description}",
            f"- **Typical outputs**: {', '.join(c.template_outputs)}",
            f"- **Model after**: `gold/features/{c.template_example}.parquet`",
            f"- **Columns** ({len(c.columns)}): " + ", ".join(f"`{col}`" for col in c.columns),
            "",
        ]
    lines += [
        "## Standard 4-milestone workflow",
        "",
    ]
    for s in candidates[0].suggested_steps:
        lines.append(f"- {s}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Find catalog views with non-gold data (default: all severities; --complete-only restricts to 100%)")
    p.add_argument("--json", metavar="PATH", help="write JSON output to PATH")
    p.add_argument("--markdown", metavar="PATH", help="write Markdown report to PATH")
    p.add_argument("--quiet", action="store_true", help="suppress stdout text")
    p.add_argument("--complete-only", action="store_true",
                   help="legacy filter — restrict to views at 100%% completeness (severity=OK)")
    args = p.parse_args(argv)

    candidates = audit(complete_only=args.complete_only)
    if not args.quiet:
        print(format_text(candidates))

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(
            {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "candidates": [asdict(c) for c in candidates]},
            indent=2, ensure_ascii=False,
        ))
        print(f"[json] wrote {args.json}", file=sys.stderr)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(format_markdown(candidates))
        print(f"[markdown] wrote {args.markdown}", file=sys.stderr)

    return 0 if candidates else 0  # always 0 — caller checks count


if __name__ == "__main__":
    sys.exit(main())
