"""Audit log for every ingest run -> meta/audit/ingest_<YYYY-MM-DD>.jsonl."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import META_AUDIT


@dataclass
class IngestRecord:
    source: str            # 'tej','taifex','twse','yahoo','histdata'
    table: str             # silver target table, e.g. 'bars_1d', 'tw_inst_futures_daily'
    bronze_file: str       # relative path to bronze input
    rows_in: int
    rows_out: int
    sha256: str
    status: str            # 'ok','validation_fail','transform_fail'
    started_at: str
    ended_at: str
    error: str | None = None
    extra: dict = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_audit(rec: IngestRecord) -> Path:
    META_AUDIT.mkdir(parents=True, exist_ok=True)
    day = dt.date.today().isoformat()
    fp = META_AUDIT / f"ingest_{day}.jsonl"
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
    return fp
