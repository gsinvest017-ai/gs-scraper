"""P0 integration — environment variable guards.

Covers I-017..018: scripts that need an env var must fail loud (not silently)
when it's missing.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_fetch_tej_check_env():
    """Import scripts/fetch_tej.py as a module to access _check_env directly.

    scripts/ is not a package, so we use importlib.util.spec_from_file_location.
    """
    src = REPO / "scripts" / "fetch_tej.py"
    spec = importlib.util.spec_from_file_location("fetch_tej_under_test", src)
    mod = importlib.util.module_from_spec(spec)
    # Don't execute module body (it imports heavy deps + has top-level constants
    # that need TEJ env); pluck out just _check_env via source parsing fallback.
    # Easier: actually exec it but allow the env probe to run.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_I017_fetch_tej_check_env_exits_when_key_missing(monkeypatch, capsys):
    """`_check_env()` must `sys.exit(...)` with non-zero when TEJAPI_KEY missing."""
    monkeypatch.delenv("TEJAPI_KEY", raising=False)
    monkeypatch.delenv("TEJAPI_BASE", raising=False)
    mod = _load_fetch_tej_check_env()
    with pytest.raises(SystemExit) as exc:
        mod._check_env()
    # sys.exit(msg_string) → SystemExit.code is the string (not 0)
    assert exc.value.code != 0
    assert "TEJAPI_KEY" in str(exc.value.code)


def test_I017_fetch_tej_check_env_passes_with_key(monkeypatch):
    """With TEJAPI_KEY set, _check_env() returns None and sets TEJAPI_BASE default."""
    monkeypatch.setenv("TEJAPI_KEY", "test-key-not-real")
    monkeypatch.delenv("TEJAPI_BASE", raising=False)
    mod = _load_fetch_tej_check_env()
    # Should NOT raise SystemExit
    mod._check_env()
    # Default base must be set
    import os
    assert os.environ.get("TEJAPI_BASE") == "https://api.tej.com.tw"


def test_I018_paths_raw_root_respects_env(monkeypatch, tmp_path):
    """qd_ingest.common.paths.RAW_ROOT picks up QUANTDATA_RAW env."""
    custom = tmp_path / "raw_sources"
    custom.mkdir()
    monkeypatch.setenv("QUANTDATA_RAW", str(custom))

    # Force re-import so the module-level computation runs with new env
    import importlib
    import qd_ingest.common.paths as paths_mod
    importlib.reload(paths_mod)
    try:
        assert paths_mod.RAW_ROOT == custom
    finally:
        importlib.reload(paths_mod)  # restore default for other tests
