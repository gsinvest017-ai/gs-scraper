"""W1 dedup: move CONFIRMED-duplicate dirs to _quarantine/ with a manifest.

NEVER rm — only mv. The dedup is reversible until _quarantine/ is purged.

Confirmed duplicates (MD5-identical, see DATA_ARCHITECTURE.md §1.2):
- MXF_1m_clean_all/   (keep MXF_1m_clean_all.parquet at root)
- GC_1min_2010-2024/  (keep GC/)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUARANTINE = ROOT / "_quarantine"

DUP_PAIRS = [
    # (move_this, kept_canonical)
    ("MXF_1m_clean_all", "MXF_1m_clean_all.parquet"),
    ("GC_1min_2010-2024", "GC"),
]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_pair(dup_dir: Path, kept: Path) -> list[dict]:
    """Walk both trees, match files by leaf name, verify SHA256 match."""
    out: list[dict] = []
    dup_files = {p.name: p for p in dup_dir.rglob("*") if p.is_file()}
    for name, dp in dup_files.items():
        # find matching file in kept (could be at any depth)
        candidates = list(kept.rglob(name)) if kept.is_dir() else ([kept] if kept.name == name else [])
        if not candidates:
            out.append({"file": str(dp.relative_to(ROOT)), "status": "no_match_in_kept", "kept_sha": None})
            continue
        kp = candidates[0]
        h_d = sha256_file(dp)
        h_k = sha256_file(kp)
        out.append({
            "file": str(dp.relative_to(ROOT)),
            "kept_path": str(kp.relative_to(ROOT)),
            "dup_sha256": h_d,
            "kept_sha256": h_k,
            "status": "match" if h_d == h_k else "MISMATCH",
        })
    return out


def main(dry_run: bool = False) -> int:
    QUARANTINE.mkdir(exist_ok=True)
    today = dt.date.today().isoformat()
    manifest_path = QUARANTINE / f"manifest_{today}.jsonl"
    summary = {"moved": 0, "verified_match": 0, "mismatch": 0}

    with open(manifest_path, "a", encoding="utf-8") as mf:
        for dup_name, kept_name in DUP_PAIRS:
            dup = ROOT / dup_name
            kept = ROOT / kept_name
            if not dup.exists():
                print(f"[skip] {dup_name}: not present")
                continue
            if not kept.exists():
                print(f"[skip] {dup_name}: kept canonical {kept_name} missing -- aborting move")
                continue
            print(f"[verify] {dup_name} vs {kept_name}")
            records = verify_pair(dup, kept)
            all_match = all(r["status"] == "match" for r in records)
            for r in records:
                if r["status"] == "MISMATCH":
                    summary["mismatch"] += 1
                elif r["status"] == "match":
                    summary["verified_match"] += 1
                mf.write(json.dumps({**r, "dup_dir": dup_name, "kept_dir": kept_name, "verified_at": today}) + "\n")

            if not all_match:
                print(f"[ABORT] {dup_name}: some files don't match — leaving in place. Check manifest.")
                continue

            target = QUARANTINE / dup_name
            if dry_run:
                print(f"[dry-run] would mv {dup} -> {target}")
            else:
                shutil.move(str(dup), str(target))
                summary["moved"] += 1
                print(f"[moved] {dup_name} -> _quarantine/")

    print()
    print(f"Summary: {summary}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    sys.exit(main(dry_run=dry))
