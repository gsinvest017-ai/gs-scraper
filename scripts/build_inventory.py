"""Write meta/audit/asset_inventory.csv: baseline of every parquet/csv file's path, size, mtime."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "meta" / "audit" / "asset_inventory.csv"
EXTS = {".parquet", ".csv", ".json", ".rar", ".zip", ".7z"}
SKIP_DIRS = {"_quarantine", "_staging", ".git", ".venv", "__pycache__"}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        if p.suffix.lower() not in EXTS:
            continue
        st = p.stat()
        rows.append({
            "path": str(rel),
            "size_bytes": st.st_size,
            "mtime": dt.datetime.fromtimestamp(st.st_mtime).isoformat(),
            "ext": p.suffix.lower(),
            "top_dir": rel.parts[0] if len(rel.parts) > 1 else "",
        })

    rows.sort(key=lambda r: (-r["size_bytes"], r["path"]))
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["path", "size_bytes", "mtime", "ext", "top_dir"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT}")
    print(f"Total size: {sum(r['size_bytes'] for r in rows) / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
