"""Ensure every test-plans/*.md file passes autogo's import contract.

Mirrors `scripts/validate_test_plans.py`. Run via `pytest -q` — if you add a
new plan with a typo'd frontmatter, this fails BEFORE you push and discover
`cached 0 plans` on the dashboard.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLANS_DIR = REPO / "test-plans"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_test_plans_under_test",
        REPO / "scripts" / "validate_test_plans.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


VAL = _load_validator()
ALL_PLANS = sorted(PLANS_DIR.glob("*.md")) if PLANS_DIR.is_dir() else []


def test_plans_dir_exists():
    assert PLANS_DIR.is_dir(), f"missing {PLANS_DIR}"


def test_at_least_one_plan_exists():
    assert ALL_PLANS, "no .md under test-plans/ — autogo will report `cached 0 plans`"


@pytest.mark.parametrize("plan", ALL_PLANS, ids=[p.name for p in ALL_PLANS])
def test_plan_passes_validator(plan: Path):
    """Each plan must pass strict validation (no errors AND no warnings)."""
    result = VAL.validate_file(plan)
    assert result["ok"], f"{plan.name} errors: {result['errors']}"
    # Strict: no warnings either — keeps frontmatter quality high
    assert not result["warnings"], \
        f"{plan.name} warnings: {result['warnings']}"


def test_validator_rejects_missing_id(tmp_path: Path):
    """Smoke: validator catches a bad plan (no id key)."""
    bad = tmp_path / "999-bad.md"
    bad.write_text("---\ntitle: no id here\nrunner: playwright-mcp\n---\nbody", encoding="utf-8")
    result = VAL.validate_file(bad)
    assert not result["ok"]
    assert any("id" in e for e in result["errors"])


def test_validator_rejects_missing_title_and_runner(tmp_path: Path):
    """Smoke: validator catches missing title AND runner."""
    bad = tmp_path / "998-no-title.md"
    bad.write_text("---\nid: nine-eight\n---\nbody", encoding="utf-8")
    result = VAL.validate_file(bad)
    assert not result["ok"]
    assert any("title" in e or "runner" in e for e in result["errors"])


def test_validator_rejects_missing_frontmatter(tmp_path: Path):
    """Smoke: a plain markdown file (no frontmatter) is correctly rejected."""
    bad = tmp_path / "997-plain.md"
    bad.write_text("# just a heading\n\nno frontmatter here", encoding="utf-8")
    result = VAL.validate_file(bad)
    assert not result["ok"]
    assert any("frontmatter" in e for e in result["errors"])


def test_validator_warns_on_unknown_runner(tmp_path: Path):
    """A plan with an unrecognised runner passes errors but raises a warning."""
    bad = tmp_path / "996-weird-runner.md"
    bad.write_text(
        "---\nid: 996-weird-runner\ntitle: weird\nrunner: selenium-grid\ncreated: 2026-05-29\ntags: [x]\nestimated_seconds: 10\n---\nbody",
        encoding="utf-8",
    )
    result = VAL.validate_file(bad)
    assert result["ok"], "errors should be empty"
    assert any("unknown runner" in w for w in result["warnings"])
