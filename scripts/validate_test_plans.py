#!/usr/bin/env python3
"""Validate autogo test-plan markdown files against the import contract.

Mirrors autogo's `_is_plan_file()` rules (see docs/progress-autogo-test-plans.md
or autogo's `/plans` source). Use this locally before pushing so the dashboard
side won't silently `cached 0 plans` on you.

Rules enforced:
  1. File is under `test-plans/<id>.md` (or repo root .md fallback)
  2. Filename is not README*.md and not a dotfile
  3. First 2 KB starts with `---` (alone on a line) and contains a closing `---`
  4. Frontmatter parses line-by-line (key: value) — no nested YAML, inline arrays only
  5. Required: `id`; AND at least one of `title` or `runner`
  6. Optional warnings: missing recommended keys (`runner`, `created`, `tags`)
  7. `runner` must be in the known set if present

Usage:
    python scripts/validate_test_plans.py                 # validate all under test-plans/
    python scripts/validate_test_plans.py path/to/foo.md  # validate one file
    python scripts/validate_test_plans.py --strict        # treat warnings as errors
    python scripts/validate_test_plans.py --json          # machine-readable output

Exit codes:
    0 = all files pass
    1 = at least one file fails
    2 = no files found (probably ran from wrong directory)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLANS_DIR = REPO / "test-plans"

FRONTMATTER_MAX_BYTES = 2048  # autogo scans first 2 KB
REQUIRED_KEYS = ("id",)
EITHER_OR = ("title", "runner")  # at least one
KNOWN_RUNNERS = {"playwright-mcp", "playwright-traced", "chrome-devtools-mcp"}
RECOMMENDED = ("runner", "created", "tags", "estimated_seconds")

# Line pattern: `key: value` (no leading whitespace beyond the simple parser)
_KV_RE = re.compile(r"^(?P<key>[a-z][a-z0-9_]*)\s*:\s*(?P<value>.*?)\s*$")


def _read_head(p: Path) -> str:
    with p.open("rb") as fh:
        raw = fh.read(FRONTMATTER_MAX_BYTES)
    # Strip UTF-8 BOM if present (a common silent fail in autogo's parser)
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def parse_frontmatter(p: Path) -> tuple[dict[str, str], list[str], list[str]]:
    """Return (kv, errors, warnings). kv is empty if no frontmatter."""
    errors: list[str] = []
    warnings: list[str] = []
    head = _read_head(p)
    lines = head.split("\n")

    if not lines or lines[0].rstrip("\r") != "---":
        errors.append("no frontmatter open: first line must be exactly `---`")
        return {}, errors, warnings

    close_idx = None
    for i, ln in enumerate(lines[1:], start=1):
        if ln.rstrip("\r") == "---":
            close_idx = i
            break
    if close_idx is None:
        errors.append(f"no frontmatter close `---` within first {FRONTMATTER_MAX_BYTES} bytes")
        return {}, errors, warnings

    kv: dict[str, str] = {}
    for ln in lines[1:close_idx]:
        s = ln.rstrip("\r").rstrip()
        if not s or s.lstrip().startswith("#"):
            continue
        m = _KV_RE.match(s)
        if not m:
            warnings.append(f"unparseable line (autogo will skip): {s!r}")
            continue
        kv[m["key"]] = m["value"].strip()

    # Required
    for k in REQUIRED_KEYS:
        if k not in kv or not kv[k]:
            errors.append(f"missing required key: {k!r}")
    if not any(k in kv and kv[k] for k in EITHER_OR):
        errors.append(f"need at least one of {EITHER_OR}")

    # Runner whitelist
    if (runner := kv.get("runner")) and runner not in KNOWN_RUNNERS:
        warnings.append(f"unknown runner {runner!r}; known: {sorted(KNOWN_RUNNERS)}")

    # Inline-array sanity: `tags: [a, b, c]`
    if (tags := kv.get("tags")) and tags:
        if not (tags.startswith("[") and tags.endswith("]")):
            warnings.append(f"tags should be inline array like [a, b], got {tags!r}")

    # Recommended
    for k in RECOMMENDED:
        if k not in kv:
            warnings.append(f"recommended key missing: {k!r}")

    # id should be url-slug-ish
    if (pid := kv.get("id")):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", pid):
            warnings.append(f"id {pid!r} not lowercase-kebab; autogo URL-uses it raw")
        # Filename should match id (recommended convention)
        if p.stem != pid:
            warnings.append(f"filename stem {p.stem!r} != id {pid!r} (convention only)")

    return kv, errors, warnings


def _is_eligible_file(p: Path) -> bool:
    """Mirror autogo skip rules: dotfile, README*."""
    name = p.name
    if name.startswith("."):
        return False
    if name.lower().startswith("readme"):
        return False
    if p.suffix.lower() != ".md":
        return False
    return True


def discover(root: Path | None = None) -> list[Path]:
    root = root or PLANS_DIR
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and _is_eligible_file(p))


def validate_file(p: Path) -> dict:
    kv, errors, warnings = parse_frontmatter(p)
    return {
        "path": str(p.relative_to(REPO)),
        "id": kv.get("id"),
        "title": kv.get("title"),
        "runner": kv.get("runner"),
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="specific files to check (default: all under test-plans/)")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.paths:
        files = [Path(p).resolve() for p in args.paths]
    else:
        files = discover()

    if not files:
        print(f"no plan files under {PLANS_DIR.relative_to(REPO)}/", file=sys.stderr)
        return 2

    results = [validate_file(f) for f in files]

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        n_ok = sum(1 for r in results if r["ok"] and (not args.strict or not r["warnings"]))
        n_total = len(results)
        for r in results:
            mark = "✅" if r["ok"] and (not args.strict or not r["warnings"]) else "❌"
            warn = "⚠" if r["warnings"] else ""
            title = r["title"] or "(no title)"
            print(f"{mark}{warn} {r['path']:42}  {r['id'] or '(no id)':30}  {title}")
            for e in r["errors"]:
                print(f"    ERROR: {e}")
            for w in r["warnings"]:
                print(f"    warn : {w}")
        print(f"\n{n_ok}/{n_total} plans pass{' strict' if args.strict else ''}")

    has_error = any(not r["ok"] for r in results)
    has_warn  = any(r["warnings"] for r in results)
    if has_error or (args.strict and has_warn):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
