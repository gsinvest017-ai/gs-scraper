"""Live crawl monitor — 當日增量爬蟲審計事件的讀取與彙整。

資料來源是 ``meta/audit/ingest_<YYYY-MM-DD>.jsonl``（append-only，由
``qd_ingest.common.audit.write_audit`` 寫入）。本模組純讀，不碰 DuckDB
catalog，避免與 ingest / duckdb -ui 的鎖衝突。

增量讀取協定：caller 帶上一次讀到的 byte offset，``read_events`` 只讀檔案
新增的部分並回傳 (events, new_offset)。檔案被輪替/截斷（offset > size）時
從頭重讀。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

# repo root = ui/search/live_monitor.py 往上兩層
ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "meta" / "audit"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# status → 嚴重度排序（dashboard 排序 / 顏色用）
_STATUS_RANK = {"ok": 0, "skip": 1, "validation_fail": 2, "transform_fail": 2}


def today_str() -> str:
    return dt.date.today().isoformat()


def audit_path(date: str) -> Path:
    """date 必須是 YYYY-MM-DD；非法輸入直接 ValueError（防 path traversal）。"""
    if not _DATE_RE.match(date):
        raise ValueError(f"invalid date: {date!r}")
    return AUDIT_DIR / f"ingest_{date}.jsonl"


def read_events(date: str, offset: int = 0) -> tuple[list[dict], int]:
    """讀取 audit JSONL 自 byte ``offset`` 起的新事件。

    回傳 (events, new_offset)。new_offset 是「已完整 parse 的最後一行」的
    結尾位置 — 寫入方若正在 append 半行，殘行不會被吃掉，下次再讀。
    """
    fp = audit_path(date)
    if not fp.is_file():
        return [], 0
    size = fp.stat().st_size
    if offset > size:  # 檔案被截斷/重建 → 從頭
        offset = 0
    if offset == size:
        return [], offset

    with open(fp, "rb") as f:
        f.seek(offset)
        buf = f.read()
    # 只消費到最後一個換行 — 寫入方正在 append 的半行留到下次
    end = buf.rfind(b"\n")
    if end == -1:
        return [], offset
    consumed = end + 1

    events: list[dict] = []
    for raw in buf[:consumed].split(b"\n"):
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 壞行跳過但 offset 照推進，避免卡死
    return events, offset + consumed


def summarize(events: list[dict]) -> dict:
    """把整天的事件彙整成 dashboard summary。

    - tables: 每個 (source, table) 取「最後一筆」為當前狀態
    - sources: 每個 source 的 ok/fail/rows 統計
    - totals: 全日總計 + 最後事件時間
    """
    tables: dict[tuple[str, str], dict] = {}
    sources: dict[str, dict] = {}
    n_ok = n_fail = 0
    total_rows = 0
    last_ended = None

    for ev in events:
        src = ev.get("source") or "?"
        tbl = ev.get("table") or "?"
        status = ev.get("status") or "?"
        rows = ev.get("rows_out") or 0
        ended = ev.get("ended_at") or ev.get("started_at") or ""
        extra = ev.get("extra") or {}

        is_ok = status == "ok"
        n_ok += is_ok
        n_fail += (not is_ok)
        total_rows += rows if is_ok else 0
        if ended and (last_ended is None or ended > last_ended):
            last_ended = ended

        key = (src, tbl)
        prev = tables.get(key)
        runs = (prev["runs"] + 1) if prev else 1
        tables[key] = {
            "source": src,
            "table": tbl,
            "status": status,
            "rows_out": rows,
            "ended_at": ended,
            "elapsed_sec": extra.get("elapsed_sec"),
            "max_date": extra.get("max_date") or _max_date_from_extra(extra),
            "error": ev.get("error"),
            "runs": runs,
        }

        s = sources.setdefault(src, {"source": src, "ok": 0, "fail": 0,
                                     "rows_out": 0, "last_ended_at": ""})
        s["ok" if is_ok else "fail"] += 1
        s["rows_out"] += rows if is_ok else 0
        if ended > s["last_ended_at"]:
            s["last_ended_at"] = ended

    # 失敗排最前，再按時間新→舊（兩段 stable sort）
    table_list = sorted(tables.values(), key=lambda t: t["ended_at"], reverse=True)
    table_list.sort(key=lambda t: _STATUS_RANK.get(t["status"], 3), reverse=True)
    return {
        "tables": table_list,
        "sources": sorted(sources.values(), key=lambda s: -s["rows_out"]),
        "totals": {
            "events": len(events),
            "ok": n_ok,
            "fail": n_fail,
            "tables": len(tables),
            "rows_out": total_rows,
            "last_ended_at": last_ended,
        },
    }


def _max_date_from_extra(extra: dict) -> str | None:
    """extra.range = [start, end] 的 fallback。"""
    rng = extra.get("range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        return str(rng[1])
    return None


def available_dates(limit: int = 30) -> list[str]:
    """列出 meta/audit/ 既有的 ingest 日期（新→舊）。"""
    if not AUDIT_DIR.is_dir():
        return []
    dates = []
    for fp in AUDIT_DIR.glob("ingest_*.jsonl"):
        d = fp.stem.removeprefix("ingest_")
        if _DATE_RE.match(d):
            dates.append(d)
    return sorted(dates, reverse=True)[:limit]
