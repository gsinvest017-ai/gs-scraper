"""Unit tests for ui.search.live_monitor — 增量 tail + 彙整邏輯。"""

from __future__ import annotations

import json

import pytest

from ui.search import live_monitor as lm


def _ev(source="tej", table="bars_1d", status="ok", rows=100,
        ended="2026-06-06T01:00:00+00:00", error=None, extra=None):
    return {
        "source": source, "table": table, "bronze_file": "x.csv",
        "rows_in": rows, "rows_out": rows, "sha256": "",
        "status": status, "started_at": ended, "ended_at": ended,
        "error": error, "extra": extra or {},
    }


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "AUDIT_DIR", tmp_path)
    return tmp_path


def _write(audit_dir, date, events, *, partial_tail=None):
    fp = audit_dir / f"ingest_{date}.jsonl"
    with open(fp, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        if partial_tail is not None:
            f.write(partial_tail)  # 模擬寫到一半的行（無換行）
    return fp


# ── audit_path ──────────────────────────────────────────────────────────────

def test_audit_path_rejects_traversal():
    with pytest.raises(ValueError):
        lm.audit_path("../etc/passwd")
    with pytest.raises(ValueError):
        lm.audit_path("2026-06-06x")


def test_audit_path_ok():
    assert lm.audit_path("2026-06-06").name == "ingest_2026-06-06.jsonl"


# ── read_events ─────────────────────────────────────────────────────────────

def test_read_events_missing_file(audit_dir):
    events, off = lm.read_events("2026-06-06")
    assert events == [] and off == 0


def test_read_events_full_then_incremental(audit_dir):
    d = "2026-06-06"
    _write(audit_dir, d, [_ev(table="a"), _ev(table="b")])
    events, off = lm.read_events(d)
    assert [e["table"] for e in events] == ["a", "b"]
    assert off > 0

    # 沒新資料 → 空 + offset 不變
    events2, off2 = lm.read_events(d, off)
    assert events2 == [] and off2 == off

    # append 一筆 → 只回新事件
    _write(audit_dir, d, [_ev(table="c")])
    events3, off3 = lm.read_events(d, off)
    assert [e["table"] for e in events3] == ["c"]
    assert off3 > off


def test_read_events_skips_partial_tail(audit_dir):
    d = "2026-06-06"
    _write(audit_dir, d, [_ev(table="a")], partial_tail='{"source": "tej", "tab')
    events, off = lm.read_events(d)
    assert [e["table"] for e in events] == ["a"]

    # 半行補完後可讀到
    fp = audit_dir / f"ingest_{d}.jsonl"
    with open(fp, "a", encoding="utf-8") as f:
        f.write('le": "b"}\n')
    events2, off2 = lm.read_events(d, off)
    assert [e.get("table") for e in events2] == ["b"]
    assert off2 == fp.stat().st_size


def test_read_events_truncated_file_resets(audit_dir):
    d = "2026-06-06"
    fp = _write(audit_dir, d, [_ev(table="a"), _ev(table="b")])
    _, off = lm.read_events(d)
    # 檔案被重建（變小）→ 從頭重讀
    fp.write_text(json.dumps(_ev(table="z")) + "\n", encoding="utf-8")
    events, off2 = lm.read_events(d, off)
    assert [e["table"] for e in events] == ["z"]
    assert off2 == fp.stat().st_size


def test_read_events_skips_bad_lines(audit_dir):
    d = "2026-06-06"
    fp = audit_dir / f"ingest_{d}.jsonl"
    fp.write_text("not json\n" + json.dumps(_ev(table="ok")) + "\n", encoding="utf-8")
    events, off = lm.read_events(d)
    assert [e["table"] for e in events] == ["ok"]
    assert off == fp.stat().st_size


# ── summarize ───────────────────────────────────────────────────────────────

def test_summarize_empty():
    s = lm.summarize([])
    assert s["totals"]["events"] == 0
    assert s["tables"] == [] and s["sources"] == []


def test_summarize_last_event_wins_per_table():
    evs = [
        _ev(table="bars_1d", status="transform_fail", rows=0,
            ended="2026-06-06T01:00:00+00:00", error="boom"),
        _ev(table="bars_1d", status="ok", rows=500,
            ended="2026-06-06T02:00:00+00:00"),
    ]
    s = lm.summarize(evs)
    assert len(s["tables"]) == 1
    t = s["tables"][0]
    assert t["status"] == "ok" and t["rows_out"] == 500 and t["runs"] == 2
    assert s["totals"]["ok"] == 1 and s["totals"]["fail"] == 1


def test_summarize_fail_sorted_first():
    evs = [
        _ev(table="good", status="ok", ended="2026-06-06T03:00:00+00:00"),
        _ev(table="bad", status="transform_fail", rows=0,
            ended="2026-06-06T01:00:00+00:00", error="x"),
    ]
    s = lm.summarize(evs)
    assert s["tables"][0]["table"] == "bad"


def test_summarize_source_stats_and_max_date():
    evs = [
        _ev(source="tej", table="a", rows=10),
        _ev(source="finmind", table="b", rows=30,
            extra={"max_date": "2026-06-05"}),
        _ev(source="finmind", table="c", rows=5,
            extra={"range": ["2020-01-01", "2026-06-04"]}),
    ]
    s = lm.summarize(evs)
    by_src = {x["source"]: x for x in s["sources"]}
    assert by_src["finmind"]["rows_out"] == 35
    assert by_src["finmind"]["ok"] == 2
    by_tbl = {t["table"]: t for t in s["tables"]}
    assert by_tbl["b"]["max_date"] == "2026-06-05"
    assert by_tbl["c"]["max_date"] == "2026-06-04"  # range fallback
    assert s["totals"]["rows_out"] == 45


def test_summarize_fail_rows_not_counted():
    evs = [_ev(table="a", status="validation_fail", rows=999, error="schema")]
    s = lm.summarize(evs)
    assert s["totals"]["rows_out"] == 0
    assert s["totals"]["fail"] == 1


# ── available_dates ─────────────────────────────────────────────────────────

def test_available_dates_sorted_desc(audit_dir):
    for d in ["2026-06-01", "2026-06-03", "2026-06-02"]:
        _write(audit_dir, d, [_ev()])
    (audit_dir / "ingest_garbage.jsonl").write_text("", encoding="utf-8")
    assert lm.available_dates() == ["2026-06-03", "2026-06-02", "2026-06-01"]
