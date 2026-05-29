"""P0 integration — shell script lint.

Covers I-014..015: every .sh under scripts/ must pass `bash -n` (syntax check).
Skipped on Windows (no bash).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((REPO / "scripts").glob("*.sh"))
TOP_LEVEL = [REPO / "run.sh"] if (REPO / "run.sh").exists() else []
ALL_SH = SCRIPTS + TOP_LEVEL


@pytest.mark.skipif(sys.platform == "win32", reason="bash not on PATH for Windows runners")
@pytest.mark.skipif(not shutil.which("bash"), reason="bash binary missing")
@pytest.mark.parametrize("script", ALL_SH, ids=[p.name for p in ALL_SH])
def test_I014_bash_syntax_check(script: Path):
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n {script.name} failed:\n{r.stderr}"


@pytest.mark.skipif(sys.platform == "win32", reason="bash not on PATH for Windows runners")
def test_I015_run_sh_help_is_zero_exit():
    """run.sh --help must succeed (no flag → would print menu + read stdin, so we use --help)."""
    if not (REPO / "run.sh").exists():
        pytest.skip("no top-level run.sh")
    r = subprocess.run(
        ["bash", str(REPO / "run.sh"), "--help"],
        capture_output=True, text=True, cwd=REPO, timeout=10,
    )
    assert r.returncode == 0, r.stderr
    assert "QUANTDATA launcher" in r.stdout
    assert "subcommand" in r.stdout.lower() or "setup" in r.stdout.lower()
