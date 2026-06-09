# 對外即時行情 API v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 QUANTDATA Search UI 新增穩定、只讀、版本化的 `/api/v1/*` 對外契約，讓另一台機器上的風控系統能拉取當日即時行情快照、逐 tick 增量、日線 OHLCV 與 collector 健康狀態。

**Architecture:** 新增 `ui/search/api_v1.py` 為 Flask Blueprint（prefix `/api/v1`），在 `ui/search/app.py` 註冊。Blueprint 只讀，複用既有 `tick_collector` / `tick_history` / `live_timeseries` 模組；唯一對既有碼的修改是在 `TickCollector` 補一個 `latest_snapshot()` 讀取方法。無認證（靠 Tailnet 邊界），對外不暴露任何 start/stop 寫入端點。

**Tech Stack:** Python 3.11/3.12、Flask、pytest。時區固定 `+08:00`（台灣無 DST）。

設計來源：`docs/superpowers/specs/2026-06-09-realtime-api-v1-design.md`

---

## File Structure

| 檔案 | 動作 | 責任 |
|---|---|---|
| `ui/search/tick_collector.py` | Modify | `TickCollector` 補 `latest_snapshot()`（從 ring 取各 symbol 最新 tick）|
| `ui/search/api_v1.py` | Create | v1 Blueprint：5 個只讀端點 + `_now()` 時間源 + CORS after_request + 共通 envelope |
| `ui/search/app.py` | Modify | import + `register_blueprint(api_v1_bp)`（2 行）|
| `tests/test_tick_collector.py` | Modify | `latest_snapshot()` 單元測試 |
| `tests/test_api_v1.py` | Create | 5 端點 + envelope + CORS 的端點測試（Flask test client + fake collector / monkeypatch）|
| `docs/api-v1.md` | Create | 對外 API 契約文件（參數 / 回應 schema / curl / staleness 建議）|
| `README.md` | Modify | 補一行指向 `docs/api-v1.md` |

每個 Task 自成可獨立 commit 的單元，依 TDD：先寫失敗測試 → 跑驗證失敗 → 最小實作 → 跑驗證通過 → commit。

---

## Task 1: `TickCollector.latest_snapshot()`

從 ring buffer 取每個 symbol 的最新一筆 tick，供 `/snapshot` 端點使用。

**Files:**
- Modify: `ui/search/tick_collector.py`（在 `get_ticks` 之後，`_backfill_from_file` 之前的「讀取」區塊）
- Test: `tests/test_tick_collector.py`

- [ ] **Step 1: Write the failing test**

加到 `tests/test_tick_collector.py` 末尾（檔案頂部已 `from ui.search import tick_collector as tc`）：

```python
# ── latest_snapshot ───────────────────────────────────────────────────────

def _fake_collector_with_ring(ticks):
    """建一個不啟動 thread 的 collector，直接灌 ring（seq 遞增）。"""
    c = tc.TickCollector(fetcher=lambda ex_chs: [])
    for t in ticks:
        c._append_tick(t, persist=False)
    return c


def test_latest_snapshot_returns_newest_per_symbol():
    c = _fake_collector_with_ring([
        {"symbol": "2330", "price": 100.0, "tlong": 1, "cum_vol": 10.0},
        {"symbol": "0050", "price": 50.0, "tlong": 2, "cum_vol": 20.0},
        {"symbol": "2330", "price": 101.0, "tlong": 3, "cum_vol": 11.0},  # 較新
    ])
    snap = c.latest_snapshot()
    assert snap["2330"]["price"] == 101.0
    assert snap["2330"]["tlong"] == 3
    assert snap["0050"]["price"] == 50.0


def test_latest_snapshot_filters_to_requested_symbols():
    c = _fake_collector_with_ring([
        {"symbol": "2330", "price": 100.0, "tlong": 1, "cum_vol": 10.0},
        {"symbol": "0050", "price": 50.0, "tlong": 2, "cum_vol": 20.0},
    ])
    snap = c.latest_snapshot(["2330", "TAIEX"])
    assert set(snap) == {"2330"}          # TAIEX 不在 ring → 不出現
    assert snap["2330"]["price"] == 100.0


def test_latest_snapshot_empty_ring():
    c = tc.TickCollector(fetcher=lambda ex_chs: [])
    assert c.latest_snapshot() == {}
    assert c.latest_snapshot(["2330"]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tick_collector.py -k latest_snapshot -v`
Expected: FAIL — `AttributeError: 'TickCollector' object has no attribute 'latest_snapshot'`

- [ ] **Step 3: Write minimal implementation**

在 `ui/search/tick_collector.py` 的 `get_ticks` 方法之後（約 line 213 後）新增：

```python
    def latest_snapshot(self, symbols: list[str] | None = None) -> dict[str, dict]:
        """回 {symbol: 最新 tick dict}。從 ring 由新到舊掃，每 symbol 取第一筆命中。

        symbols 為 None → 回所有 symbol 的最新；給定清單則只回其中已在 ring 的。
        """
        want = {s.strip().upper() for s in symbols} if symbols else None
        out: dict[str, dict] = {}
        with self._lock:
            for _seq, t in reversed(self._ring):
                sym = t.get("symbol")
                if sym in out:
                    continue
                if want is not None and sym not in want:
                    continue
                out[sym] = t
                if want is not None and len(out) == len(want):
                    break
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tick_collector.py -k latest_snapshot -v`
Expected: PASS（3 個案例綠）

- [ ] **Step 5: Commit**

```bash
git add ui/search/tick_collector.py tests/test_tick_collector.py
git commit -m "feat: TickCollector 補 latest_snapshot — 從 ring 取各 symbol 最新 tick

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: api_v1 Blueprint 骨架 + `/health` + CORS

建立 Blueprint 模組、時間源 `_now()`、共通 envelope、CORS after_request，並實作最簡單的 `/health`，最後在 app.py 註冊。

**Files:**
- Create: `ui/search/api_v1.py`
- Modify: `ui/search/app.py:38-39`（app 建立後加註冊）
- Test: `tests/test_api_v1.py`

- [ ] **Step 1: Write the failing test**

建立 `tests/test_api_v1.py`：

```python
"""Endpoint tests for ui.search.api_v1 — 對外即時行情 API v1。"""

from __future__ import annotations

import datetime as dt

import pytest

from ui.search import api_v1


TPE = dt.timezone(dt.timedelta(hours=8))
FIXED_NOW = dt.datetime(2026, 6, 9, 13, 25, 7, tzinfo=TPE)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_v1, "_now", lambda: FIXED_NOW)
    from ui.search.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class FakeCollector:
    """測試用假 collector（不起 thread）。"""

    def __init__(self, snap=None, symbols=None, running=True,
                 last_poll_at="2026-06-09T13:25:05", ticks=None):
        self._snap = snap or {}
        self._symbols = list(symbols or [])
        self._running = running
        self._last_poll = last_poll_at
        self._ticks = ticks or []
        self.started_with = []

    def status(self):
        return {"running": self._running, "symbols": sorted(self._symbols),
                "poll_sec": 3.0, "started_at": "2026-06-09T09:00:01",
                "last_poll_at": self._last_poll, "poll_count": 5204,
                "ticks_in_ring": len(self._snap), "seq": 48213,
                "last_error": None}

    def start(self, syms):
        self.started_with.append(list(syms))
        self._symbols = list(syms)[:20]
        self._running = True
        return {"running": True, "symbols": sorted(self._symbols), "unknown": []}

    def latest_snapshot(self, symbols=None):
        if symbols is None:
            return dict(self._snap)
        return {s.upper(): self._snap[s.upper()] for s in symbols
                if s.upper() in self._snap}

    def get_ticks(self, symbol=None, since_seq=0, limit=5000):
        return list(self._ticks), 48213


def _patch_collector(monkeypatch, fake):
    from ui.search import tick_collector
    monkeypatch.setattr(tick_collector, "get_collector", lambda: fake)


def test_health_shape_and_envelope(client, monkeypatch):
    _patch_collector(monkeypatch, FakeCollector(symbols=["2330", "TAIEX"]))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["server_time"] == "2026-06-09T13:25:07+08:00"
    c = body["collector"]
    assert c["running"] is True
    assert c["collected_symbols"] == ["2330", "TAIEX"]
    assert c["seconds_since_poll"] == 2.0      # 13:25:07 - 13:25:05
    assert c["seq"] == 48213
    # CORS header（只讀對外）
    assert r.headers["Access-Control-Allow-Origin"] == "*"


def test_health_null_last_poll(client, monkeypatch):
    _patch_collector(monkeypatch, FakeCollector(last_poll_at=None, running=False))
    body = client.get("/api/v1/health").get_json()
    assert body["collector"]["seconds_since_poll"] is None
    assert body["collector"]["running"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.search.api_v1'`

- [ ] **Step 3: Write minimal implementation**

建立 `ui/search/api_v1.py`：

```python
"""對外即時行情 API v1 — 給風控系統等跨機器消費者的只讀穩定契約。

設計：docs/superpowers/specs/2026-06-09-realtime-api-v1-design.md
- 只讀（不含任何 collector start/stop 寫入端點）
- 無認證，靠 Tailnet / 內網防火牆做邊界
- 複用 tick_collector / tick_history / live_timeseries
- 回應一律含 server_time（+08:00），供消費者自算 staleness；GET 加 CORS allow-all
"""

from __future__ import annotations

import datetime as dt

from flask import Blueprint, jsonify, request

from ui.search import live_timeseries as lt
from ui.search import tick_collector
from ui.search import tick_history as th

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

TPE = dt.timezone(dt.timedelta(hours=8))
MAX_SYMBOLS = tick_collector.MAX_SYMBOLS   # 20


def _now() -> dt.datetime:
    """可注入的「現在」（aware, +08:00）。測試 monkeypatch 此函式取得確定值。"""
    return dt.datetime.now(TPE)


def _server_time() -> str:
    return _now().isoformat(timespec="seconds")


def _seconds_since(iso_naive: str | None) -> float | None:
    """collector 的 last_poll_at / started_at 是 naive local ISO 字串。"""
    if not iso_naive:
        return None
    try:
        then = dt.datetime.fromisoformat(iso_naive)
    except ValueError:
        return None
    now_naive = _now().replace(tzinfo=None)
    return round((now_naive - then).total_seconds(), 1)


@bp.after_request
def _add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/health")
def health():
    st = tick_collector.get_collector().status()
    return jsonify({
        "server_time": _server_time(),
        "collector": {
            "running": st["running"],
            "collected_symbols": st["symbols"],
            "poll_sec": st["poll_sec"],
            "started_at": st["started_at"],
            "last_poll_at": st["last_poll_at"],
            "seconds_since_poll": _seconds_since(st["last_poll_at"]),
            "poll_count": st["poll_count"],
            "ticks_in_ring": st["ticks_in_ring"],
            "seq": st["seq"],
            "last_error": st["last_error"],
        },
    })
```

在 `ui/search/app.py` 第 39 行（`app.config["JSON_AS_ASCII"] = False` 之後）新增註冊：

```python
app.config["JSON_AS_ASCII"] = False

from ui.search.api_v1 import bp as api_v1_bp  # noqa: E402

app.register_blueprint(api_v1_bp)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py -v`
Expected: PASS（`test_health_shape_and_envelope`, `test_health_null_last_poll` 綠）

- [ ] **Step 5: Commit**

```bash
git add ui/search/api_v1.py ui/search/app.py tests/test_api_v1.py
git commit -m "feat: api_v1 Blueprint 骨架 + /health + CORS — 對外即時行情 API

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `/api/v1/snapshot`

風控主力端點：每個 symbol 當下最新快照，含 change / age_sec / 懶啟動。

**Files:**
- Modify: `ui/search/api_v1.py`（新增 helper + route）
- Test: `tests/test_api_v1.py`

- [ ] **Step 1: Write the failing test**

加到 `tests/test_api_v1.py`（沿用上方 `client` / `FakeCollector` / `_patch_collector`）：

```python
# ── /snapshot ────────────────────────────────────────────────────────────

_TICK_2330 = {"symbol": "2330", "name": "台積電", "price": 1085.0,
              "bid": 1085.0, "ask": 1090.0, "open": 1080.0, "high": 1095.0,
              "low": 1078.0, "prev_close": 1075.0, "cum_vol": 18234.0,
              "tick_vol": 3.0, "time": "13:24:58",
              "tlong": 1780982698000}  # FIXED_NOW(=epoch 1780982707) 之前約 9 秒


def test_snapshot_happy(client, monkeypatch):
    fake = FakeCollector(snap={"2330": _TICK_2330}, symbols=["2330"])
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols=2330").get_json()
    s = body["snapshots"]["2330"]
    assert s["price"] == 1085.0
    assert s["change"] == 10.0                      # 1085 - 1075
    assert s["change_pct"] == pytest.approx(0.93, abs=0.01)
    assert s["live"] is True and s["warming"] is False
    assert s["age_sec"] == pytest.approx(9.0, abs=0.5)
    assert body["not_collected"] == [] and body["dropped"] == []


def test_snapshot_missing_symbols_param(client, monkeypatch):
    _patch_collector(monkeypatch, FakeCollector())
    r = client.get("/api/v1/snapshot")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_snapshot_symbol_parsing_comma_space_case(client, monkeypatch):
    fake = FakeCollector(snap={"2330": _TICK_2330}, symbols=["2330", "0050", "TAIEX"])
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols= 2330 , 0050;taiex").get_json()
    # 三檔都在 watchlist，但只有 2330 有 tick → 另兩檔 warming
    assert set(body["snapshots"]) == {"2330", "0050", "TAIEX"}
    assert body["snapshots"]["0050"]["warming"] is True
    assert body["snapshots"]["0050"]["live"] is False


def test_snapshot_ensure_lazy_start(client, monkeypatch):
    fake = FakeCollector(snap={}, symbols=[], running=True)
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols=2330&ensure=1").get_json()
    assert fake.started_with               # start() 被呼叫（懶啟動）
    assert "2330" in fake.started_with[-1]
    assert body["snapshots"]["2330"]["warming"] is True


def test_snapshot_ensure_off_lists_not_collected(client, monkeypatch):
    fake = FakeCollector(snap={}, symbols=[], running=True)
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols=2330&ensure=0").get_json()
    assert not fake.started_with           # 純讀，不啟動
    assert body["not_collected"] == ["2330"]
    assert "2330" not in body["snapshots"]


def test_snapshot_over_20_reports_dropped(client, monkeypatch):
    syms = [f"{1000 + i}" for i in range(25)]
    fake = FakeCollector(snap={}, symbols=[], running=True)
    _patch_collector(monkeypatch, fake)
    body = client.get("/api/v1/snapshot?symbols=" + ",".join(syms)
                      + "&ensure=1").get_json()
    assert len(body["dropped"]) == 5       # 25 - 20


def test_snapshot_503_when_cannot_start(client, monkeypatch):
    fake = FakeCollector(snap={}, symbols=[], running=False)
    # start() 模擬 MIS 掛掉：不改 running、symbols 維持空
    fake.start = lambda syms: {"running": False, "symbols": [], "unknown": syms}
    _patch_collector(monkeypatch, fake)
    r = client.get("/api/v1/snapshot?symbols=2330&ensure=1")
    assert r.status_code == 503
    assert "error" in r.get_json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py -k snapshot -v`
Expected: FAIL — 404（route 不存在）

- [ ] **Step 3: Write minimal implementation**

在 `ui/search/api_v1.py` 加入 symbol 解析、enrich helper 與 `/snapshot` route（接在 `health` 之後）：

```python
def _parse_symbols(raw: str) -> list[str]:
    """逗號/空白/分號分隔 → 去空白、大寫、去重保序。"""
    import re
    parts = re.split(r"[,\s;]+", raw.strip())
    out: list[str] = []
    for p in parts:
        s = p.strip().upper()
        if s and s not in out:
            out.append(s)
    return out


def _age_sec(tlong) -> float | None:
    if not tlong:
        return None
    return round(_now().timestamp() - tlong / 1000.0, 1)


def _enrich(t: dict) -> dict:
    price, prev = t.get("price"), t.get("prev_close")
    change = change_pct = None
    if price is not None and prev not in (None, 0):
        change = round(price - prev, 4)
        change_pct = round(change / prev * 100, 4)
    return {
        "symbol": t.get("symbol"), "name": t.get("name"),
        "price": price, "bid": t.get("bid"), "ask": t.get("ask"),
        "open": t.get("open"), "high": t.get("high"), "low": t.get("low"),
        "prev_close": prev, "cum_vol": t.get("cum_vol"),
        "tick_vol": t.get("tick_vol"), "change": change,
        "change_pct": change_pct, "time": t.get("time"),
        "tlong": t.get("tlong"), "age_sec": _age_sec(t.get("tlong")),
        "live": True, "warming": False,
    }


def _warming_stub(sym: str) -> dict:
    return {"symbol": sym, "name": None, "price": None, "bid": None,
            "ask": None, "open": None, "high": None, "low": None,
            "prev_close": None, "cum_vol": None, "tick_vol": None,
            "change": None, "change_pct": None, "time": None, "tlong": None,
            "age_sec": None, "live": False, "warming": True}


@bp.route("/snapshot")
def snapshot():
    raw = request.args.get("symbols") or ""
    symbols = _parse_symbols(raw)
    if not symbols:
        return jsonify({"error": "需要 symbols 參數（逗號分隔）"}), 400
    ensure = (request.args.get("ensure") or "1") != "0"

    collector = tick_collector.get_collector()
    collected = set(collector.status()["symbols"])
    dropped: list[str] = []

    if ensure and any(s not in collected for s in symbols):
        merged = list(dict.fromkeys(sorted(collected) + symbols))
        if len(merged) > MAX_SYMBOLS:
            dropped = merged[MAX_SYMBOLS:]
        collector.start(merged[:MAX_SYMBOLS])
        st = collector.status()
        collected = set(st["symbols"])
        snaps = collector.latest_snapshot(symbols)
        if not st["running"] and not snaps:
            return jsonify({"error": "collector 無法啟動（MIS 來源可能無回應）",
                            "not_collected": symbols}), 503
    snaps = collector.latest_snapshot(symbols)

    out: dict[str, dict] = {}
    not_collected: list[str] = []
    for s in symbols:
        if s in snaps:
            out[s] = _enrich(snaps[s])
        elif s in collected:
            out[s] = _warming_stub(s)
        else:
            not_collected.append(s)

    return jsonify({"server_time": _server_time(), "snapshots": out,
                    "not_collected": not_collected, "dropped": dropped})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py -k snapshot -v`
Expected: PASS（7 個 snapshot 案例綠）

- [ ] **Step 5: Commit**

```bash
git add ui/search/api_v1.py tests/test_api_v1.py
git commit -m "feat: /api/v1/snapshot — 各 symbol 最新快照 + 懶啟動 + staleness

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `/api/v1/ticks`

逐 tick 增量流，薄包 `collector.get_ticks()`。

**Files:**
- Modify: `ui/search/api_v1.py`
- Test: `tests/test_api_v1.py`

- [ ] **Step 1: Write the failing test**

加到 `tests/test_api_v1.py`：

```python
# ── /ticks ───────────────────────────────────────────────────────────────

def test_ticks_happy(client, monkeypatch):
    ticks = [{"symbol": "2330", "price": 100.0, "tlong": 1, "cum_vol": 10.0}]
    _patch_collector(monkeypatch, FakeCollector(ticks=ticks, symbols=["2330"]))
    body = client.get("/api/v1/ticks?symbol=2330&since_seq=5").get_json()
    assert body["symbol"] == "2330"
    assert body["seq"] == 48213
    assert body["ticks"][0]["price"] == 100.0
    assert body["server_time"] == "2026-06-09T13:25:07+08:00"


def test_ticks_bad_since_seq(client, monkeypatch):
    _patch_collector(monkeypatch, FakeCollector())
    r = client.get("/api/v1/ticks?since_seq=abc")
    assert r.status_code == 400


def test_ticks_limit_clamped(client, monkeypatch):
    captured = {}

    class Cap(FakeCollector):
        def get_ticks(self, symbol=None, since_seq=0, limit=5000):
            captured["limit"] = limit
            captured["since_seq"] = since_seq
            return [], 0

    _patch_collector(monkeypatch, Cap())
    client.get("/api/v1/ticks?limit=99999&since_seq=3")
    assert captured["limit"] == 20000        # 上限 clamp
    assert captured["since_seq"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py -k "ticks and not history" -v`
Expected: FAIL — 404

- [ ] **Step 3: Write minimal implementation**

在 `ui/search/api_v1.py` 加 `/ticks` route：

```python
@bp.route("/ticks")
def ticks():
    symbol = (request.args.get("symbol") or "").strip() or None
    try:
        since_seq = max(0, int(request.args.get("since_seq") or 0))
        limit = min(20000, max(1, int(request.args.get("limit") or 5000)))
    except ValueError:
        return jsonify({"error": "since_seq / limit 必須是整數"}), 400
    rows, seq = tick_collector.get_collector().get_ticks(
        symbol=symbol, since_seq=since_seq, limit=limit)
    return jsonify({"server_time": _server_time(),
                    "symbol": symbol, "ticks": rows, "seq": seq})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py -k "ticks and not history" -v`
Expected: PASS（3 案例綠）

- [ ] **Step 5: Commit**

```bash
git add ui/search/api_v1.py tests/test_api_v1.py
git commit -m "feat: /api/v1/ticks — 逐 tick 增量流（since_seq 游標）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `/api/v1/ticks/history`

任一日某標的逐 tick（三層 fallback），薄包 `tick_history.get_history_ticks()`。

**Files:**
- Modify: `ui/search/api_v1.py`
- Test: `tests/test_api_v1.py`

- [ ] **Step 1: Write the failing test**

加到 `tests/test_api_v1.py`：

```python
# ── /ticks/history ─────────────────────────────────────────────────────────

def test_ticks_history_happy(client, monkeypatch):
    from ui.search import tick_history
    monkeypatch.setattr(tick_history, "get_history_ticks",
                        lambda date, symbol: {"date": date, "symbol": symbol,
                                              "source": "self_jsonl", "count": 2,
                                              "ticks": [{"price": 1.0}, {"price": 2.0}]})
    body = client.get("/api/v1/ticks/history?date=2026-06-06&symbol=2330").get_json()
    assert body["source"] == "self_jsonl"
    assert body["count"] == 2
    assert body["server_time"] == "2026-06-09T13:25:07+08:00"


def test_ticks_history_missing_params(client, monkeypatch):
    r = client.get("/api/v1/ticks/history?date=2026-06-06")
    assert r.status_code == 400


def test_ticks_history_bad_date_propagates_400(client, monkeypatch):
    from ui.search import tick_history

    def boom(date, symbol):
        raise ValueError("date 必須是 YYYY-MM-DD")

    monkeypatch.setattr(tick_history, "get_history_ticks", boom)
    r = client.get("/api/v1/ticks/history?date=xx&symbol=2330")
    assert r.status_code == 400
    assert "error" in r.get_json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py -k history -v`
Expected: FAIL — 404

- [ ] **Step 3: Write minimal implementation**

在 `ui/search/api_v1.py` 加 `/ticks/history` route：

```python
@bp.route("/ticks/history")
def ticks_history():
    date = (request.args.get("date") or "").strip()
    symbol = (request.args.get("symbol") or "").strip()
    if not date or not symbol:
        return jsonify({"error": "需要 date 與 symbol"}), 400
    try:
        data = th.get_history_ticks(date, symbol)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    data["server_time"] = _server_time()
    return jsonify(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py -k history -v`
Expected: PASS（3 案例綠）

- [ ] **Step 5: Commit**

```bash
git add ui/search/api_v1.py tests/test_api_v1.py
git commit -m "feat: /api/v1/ticks/history — 任一日逐 tick 三層 fallback

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: `/api/v1/bars`

當日 + 歷史日線 OHLCV，薄包 `live_timeseries.get_timeseries()`。

**Files:**
- Modify: `ui/search/api_v1.py`
- Test: `tests/test_api_v1.py`

- [ ] **Step 1: Write the failing test**

加到 `tests/test_api_v1.py`：

```python
# ── /bars ──────────────────────────────────────────────────────────────────

def test_bars_happy(client, monkeypatch):
    monkeypatch.setattr(lt, "get_timeseries",
                        lambda symbol, days: {"symbol": symbol, "asset_class": "tw_stock",
                                              "days": days,
                                              "series": {"dates": ["2026-06-08"],
                                                         "open": [1.0], "high": [2.0],
                                                         "low": [0.5], "close": [1.5],
                                                         "volume": [100]},
                                              "latest": {"close": 1.5}})
    body = client.get("/api/v1/bars?symbol=2330&days=30").get_json()
    assert body["symbol"] == "2330"
    assert body["days"] == 30
    assert body["latest"]["close"] == 1.5
    assert body["server_time"] == "2026-06-09T13:25:07+08:00"


def test_bars_missing_symbol(client, monkeypatch):
    r = client.get("/api/v1/bars")
    assert r.status_code == 400


def test_bars_not_found_404(client, monkeypatch):
    monkeypatch.setattr(lt, "get_timeseries", lambda symbol, days: None)
    r = client.get("/api/v1/bars?symbol=ZZZ")
    assert r.status_code == 404


def test_bars_days_clamped(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(lt, "get_timeseries",
                        lambda symbol, days: captured.update(days=days) or
                        {"symbol": symbol, "asset_class": "x", "days": days,
                         "series": {}, "latest": {}})
    client.get("/api/v1/bars?symbol=2330&days=9999")
    assert captured["days"] == 365          # MAX_DAYS clamp
```

注意：`from ui.search import live_timeseries as lt` 已在 `api_v1.py` import；測試頂部需 `from ui.search import live_timeseries as lt`（與 module 內同名，monkeypatch 對 module attribute 生效）。

- [ ] **Step 2: Run test to verify it fails**

先在 `tests/test_api_v1.py` 頂部 import 區加 `from ui.search import live_timeseries as lt`。
Run: `.venv/bin/python -m pytest tests/test_api_v1.py -k bars -v`
Expected: FAIL — 404

- [ ] **Step 3: Write minimal implementation**

在 `ui/search/api_v1.py` 加 `/bars` route：

```python
@bp.route("/bars")
def bars():
    symbol = (request.args.get("symbol") or "").strip()
    if not symbol:
        return jsonify({"error": "需要 symbol"}), 400
    try:
        days = min(lt.MAX_DAYS, max(1, int(request.args.get("days") or lt.DEFAULT_DAYS)))
    except ValueError:
        return jsonify({"error": "days 必須是整數"}), 400
    data = lt.get_timeseries(symbol, days)
    if data is None:
        return jsonify({"error": f"查無標的: {symbol}"}), 404
    data["server_time"] = _server_time()
    return jsonify(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py -k bars -v`
Expected: PASS（4 案例綠）

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `.venv/bin/python -m pytest tests/test_api_v1.py tests/test_tick_collector.py -q`
Expected: 全綠（v1 端點 + collector）

- [ ] **Step 6: Commit**

```bash
git add ui/search/api_v1.py tests/test_api_v1.py
git commit -m "feat: /api/v1/bars — 日線 OHLCV（days clamp 365）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: 文件 `docs/api-v1.md` + README 指向

**Files:**
- Create: `docs/api-v1.md`
- Modify: `README.md`（補一行連結）

- [ ] **Step 1: 撰寫 `docs/api-v1.md`**

```markdown
# QUANTDATA 對外即時行情 API v1

給另一台機器上的系統（例如風控系統）拉取當日即時行情的只讀 HTTP API。

- Base URL：`http://<host>:5050/api/v1`（內網 / Tailscale，例如 `http://100.104.1.39:5050/api/v1`）
- 無認證：靠 Tailnet ACL / 內網防火牆做邊界，請勿暴露到公網
- 只讀：v1 不含任何 collector 啟停寫入端點
- 回應一律 JSON，含 `server_time`（ISO8601 `+08:00`）；GET 回應帶
  `Access-Control-Allow-Origin: *`
- 錯誤：`{"error": "..."}` + HTTP 400 / 404 / 503

## 建議用法（staleness guard）

風控在信任快照前，先打 `/health` 看 `seconds_since_poll`（collector 多久沒輪詢），
再看 `/snapshot` 每檔的 `age_sec`（該檔最新 tick 距現在幾秒）。兩者皆大 → 資料不新鮮，
應觸發降級或告警，勿據以下單。

## GET /health

collector 健康 + 資料新鮮度。

```bash
curl http://100.104.1.39:5050/api/v1/health
```
回應：`{server_time, collector:{running, collected_symbols, poll_sec, started_at,
last_poll_at, seconds_since_poll, poll_count, ticks_in_ring, seq, last_error}}`

## GET /snapshot

每個 symbol 當下最新快照（mark-to-market / kill-switch 主力）。

| 參數 | 必填 | 說明 |
|---|---|---|
| `symbols` | ✓ | 逗號/空白/分號分隔，如 `2330,TAIEX,0050`（大小寫不敏感）|
| `ensure` | | 預設 `1`：未採集的 symbol 自動加入 watchlist 開始採集（上限 20）；`0`=純讀 |

```bash
curl "http://100.104.1.39:5050/api/v1/snapshot?symbols=2330,TAIEX,0050"
```
回應：`{server_time, snapshots:{<sym>:{symbol,name,price,bid,ask,open,high,low,
prev_close,cum_vol,tick_vol,change,change_pct,time,tlong,age_sec,live,warming}},
not_collected:[...], dropped:[...]}`
- `change = price - prev_close`；`change_pct = change/prev_close*100`（prev_close 缺 → null）
- `age_sec`：該 tick 距 server_time 秒數
- `live=false, warming=true`：剛開始採集、ring 還沒資料
- `not_collected`：`ensure=0` 時未採集的 symbol（snapshots 不含）
- `dropped`：因超過 20 檔上限被丟掉的 symbol

## GET /ticks

逐 tick 增量流（ring buffer）。

| 參數 | 必填 | 說明 |
|---|---|---|
| `symbol` | | 省略 = 所有採集中 symbol 合併流 |
| `since_seq` | | 上次回傳的 `seq`，只拿之後的新 tick（預設 0）|
| `limit` | | 預設 5000，上限 20000 |

```bash
curl "http://100.104.1.39:5050/api/v1/ticks?symbol=2330&since_seq=48000"
```
回應：`{server_time, symbol, ticks:[{symbol,time,price,tick_vol,cum_vol,bid,ask,tlong}],
seq}`。下次帶 `since_seq=<回應的 seq>`。

## GET /ticks/history

任一日某標的逐 tick（三層 fallback：自收 JSONL → FinMind cache/sqlite → FinMind API）。

| 參數 | 必填 | 說明 |
|---|---|---|
| `date` | ✓ | `YYYY-MM-DD` |
| `symbol` | ✓ | 標的代碼 |

```bash
curl "http://100.104.1.39:5050/api/v1/ticks/history?date=2026-06-06&symbol=2330"
```
回應：`{server_time, date, symbol, source, count, ticks:[...]}`（`source` 標明命中層級）

## GET /bars

當日 + 歷史日線 OHLCV（算波動率 / ATR / 回撤基準）。

| 參數 | 必填 | 說明 |
|---|---|---|
| `symbol` | ✓ | 標的代碼 |
| `days` | | 預設 60，上限 365 |

```bash
curl "http://100.104.1.39:5050/api/v1/bars?symbol=2330&days=60"
```
回應：`{server_time, symbol, asset_class, days, series:{dates,open,high,low,close,volume},
latest:{trading_date,open,high,low,close,volume,prev_close,change,change_pct}}`

## 運維註記

- snapshot 懶啟動與 dashboard 共用同一個 process 級 collector 單例與 20 檔上限；
  兩個消費者同時要求超過 20 檔時會互相擠掉。
- collector 資料源為 TWSE MIS（約 5 秒快照），非逐筆撮合等級；歷史逐 tick 才走 FinMind。
```

- [ ] **Step 2: README 補連結**

在 `README.md` 找到 Search UI / dashboard 相關段落，加一行：

```markdown
- 對外即時行情 API（給風控系統等跨機器消費者）：見 [`docs/api-v1.md`](docs/api-v1.md)
```

- [ ] **Step 3: Commit**

```bash
git add docs/api-v1.md README.md
git commit -m "docs: 對外即時行情 API v1 契約文件 + README 指向

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 收尾驗證

- [ ] **跑完整測試套件**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全綠（CI 跑 ubuntu × {3.11, 3.12}，本機先確認）

- [ ] **手動 smoke（server 起著時）**

```bash
curl -s http://127.0.0.1:5050/api/v1/health | python -m json.tool
curl -s "http://127.0.0.1:5050/api/v1/snapshot?symbols=2330,TAIEX" | python -m json.tool
```
Expected: 200，含 `server_time` 與 `+08:00`；header 有 `Access-Control-Allow-Origin: *`。

- [ ] **commit message 把關**

Run: `python scripts/check_commit_messages.py --range origin/main..HEAD`
Expected: 無違規（每個 subject 都含中文）。
