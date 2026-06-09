"""Tick collector — 盤中輪詢 TWSE MIS 即時行情，產生當日逐 tick 流。

資料源：``https://mis.twse.com.tw/stock/api/getStockInfo.jsp``（免費、無需
key、約 5 秒更新的最新成交快照）。輪詢間隔 ``poll_sec``（預設 3s），以
``(tlong, cum_vol)`` 變化判定新 tick；同一快照重複輪詢不會重複記錄。

- 即時讀取走 in-memory ring buffer（global seq，client 帶 ``since_seq`` 增量拉）
- 持久化 append 到 ``meta/realtime/ticks_<YYYY-MM-DD>.jsonl``（meta/** 已 gitignore）
- server 重啟時自動從當日 JSONL backfill ring buffer
- 上市/上櫃自動偵測：先試 ``tse_<sym>.tw`` 再 fallback ``otc_<sym>.tw``，結果 cache
- 指數別名：``TAIEX`` → ``tse_t00.tw``、``OTC`` → ``otc_o00.tw``

設計上 collector 是 process 級單例（Flask dev server 單 process OK）；
``start()`` / ``stop()`` 冪等。
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REALTIME_DIR = ROOT / "meta" / "realtime"

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_HEADERS = {"User-Agent": "Mozilla/5.0 (QUANTDATA live monitor)"}

# 指數與常用別名 → MIS ex_ch
ALIASES = {
    "TAIEX": "tse_t00.tw",
    "OTC": "otc_o00.tw",
}
# MIS 回傳的 c 欄位 → 對外顯示名（與 ALIASES 對應）
REVERSE_ALIASES = {"T00": "TAIEX", "O00": "OTC"}

RING_MAX = 50_000          # 全 symbol 共用 ring 上限（一天 tick 量級夠用）
MAX_SYMBOLS = 20           # 單次輪詢 ex_ch 數上限（MIS 一次 query 的合理上限）
DEFAULT_POLL_SEC = 3.0


def today_str() -> str:
    return dt.date.today().isoformat()


def tick_file(date: str | None = None) -> Path:
    return REALTIME_DIR / f"ticks_{date or today_str()}.jsonl"


# ── MIS client ──────────────────────────────────────────────────────────────

def fetch_quotes(ex_chs: list[str], timeout: float = 8.0) -> list[dict]:
    """打 MIS API 回原始 msgArray（list of dict）。網路錯誤丟 exception。"""
    if not ex_chs:
        return []
    qs = urllib.parse.urlencode({"ex_ch": "|".join(ex_chs), "json": "1", "delay": "0"})
    req = urllib.request.Request(f"{MIS_URL}?{qs}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("msgArray") or []


def _f(v) -> float | None:
    """MIS 數值欄位轉 float；'-' / '' / None → None。"""
    if v in (None, "", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_tick(msg: dict) -> dict | None:
    """把 MIS msgArray 元素轉成 tick dict；沒有成交價的快照回 None。"""
    price = _f(msg.get("z"))
    if price is None:
        return None
    # 五檔字串："2365.0000_2370.0000_..." → 取第一檔
    def first(s):
        if not s:
            return None
        return _f(s.split("_")[0])
    sym = (msg.get("c") or "").upper()
    return {
        "symbol": REVERSE_ALIASES.get(sym, sym),
        "name": msg.get("n") or "",
        "ex": msg.get("ex") or "",
        "date": msg.get("d") or "",
        "time": msg.get("%") or msg.get("t") or "",
        "tlong": int(msg.get("tlong") or 0),
        "price": price,
        "tick_vol": _f(msg.get("tv")),
        "cum_vol": _f(msg.get("v")),
        "bid": first(msg.get("b")),
        "ask": first(msg.get("a")),
        "open": _f(msg.get("o")),
        "high": _f(msg.get("h")),
        "low": _f(msg.get("l")),
        "prev_close": _f(msg.get("y")),
    }


# ── collector ───────────────────────────────────────────────────────────────

class TickCollector:
    """背景 thread 輪詢 MIS，新 tick 進 ring buffer + 落地 JSONL。"""

    def __init__(self, poll_sec: float = DEFAULT_POLL_SEC, fetcher=None):
        self.poll_sec = poll_sec
        self._fetch = fetcher or fetch_quotes      # 測試可注入 fake
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._symbols: dict[str, str] = {}          # symbol → ex_ch
        self._last_key: dict[str, tuple] = {}       # symbol → (tlong, cum_vol)
        self._ring: deque = deque(maxlen=RING_MAX)  # [(seq, tick), ...]
        self._seq = 0
        self._started_at: str | None = None
        self._last_poll_at: str | None = None
        self._last_error: str | None = None
        self._poll_count = 0
        self._backfilled = False

    # -- ex_ch 解析 ---------------------------------------------------------

    def resolve_ex_ch(self, symbol: str) -> str | None:
        """symbol → MIS ex_ch。別名直查；其他 probe tse 再 otc。"""
        sym = symbol.strip().upper()
        if not sym:
            return None
        if sym in ALIASES:
            return ALIASES[sym]
        with self._lock:
            if sym in self._symbols:
                return self._symbols[sym]
        for ex_ch in (f"tse_{sym.lower()}.tw", f"otc_{sym.lower()}.tw"):
            try:
                msgs = self._fetch([ex_ch])
            except Exception:
                return None
            # MIS 對不存在的代碼回空 msgArray 或缺 key 欄位
            if msgs and any(m.get("c") for m in msgs):
                return ex_ch
        return None

    # -- 控制 ---------------------------------------------------------------

    def start(self, symbols: list[str]) -> dict:
        """設定 watchlist 並啟動輪詢 thread（冪等；重複 start 走更新清單）。"""
        resolved: dict[str, str] = {}
        unknown: list[str] = []
        for s in symbols[:MAX_SYMBOLS]:
            ex_ch = self.resolve_ex_ch(s)
            if ex_ch:
                resolved[s.strip().upper()] = ex_ch
            else:
                unknown.append(s)
        with self._lock:
            self._symbols = resolved
        if not self._backfilled:
            self._backfill_from_file()
        if resolved and (self._thread is None or not self._thread.is_alive()):
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="tick-collector")
            self._started_at = dt.datetime.now().isoformat(timespec="seconds")
            self._thread.start()
        return {"running": self.running, "symbols": sorted(resolved),
                "unknown": unknown}

    def stop(self) -> dict:
        self._stop_evt.set()
        return {"running": False}

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop_evt.is_set())

    def status(self) -> dict:
        with self._lock:
            n_ticks = len(self._ring)
            symbols = sorted(self._symbols)
            seq = self._seq
        return {
            "running": self.running,
            "symbols": symbols,
            "poll_sec": self.poll_sec,
            "started_at": self._started_at,
            "last_poll_at": self._last_poll_at,
            "poll_count": self._poll_count,
            "ticks_in_ring": n_ticks,
            "seq": seq,
            "last_error": self._last_error,
            "tick_file": str(tick_file()),
        }

    # -- 讀取 ---------------------------------------------------------------

    def get_ticks(self, symbol: str | None = None, since_seq: int = 0,
                  limit: int = 5000) -> tuple[list[dict], int]:
        """回 (ticks, max_seq)。ticks 依 seq 升冪；可依 symbol 過濾。"""
        sym = symbol.strip().upper() if symbol else None
        with self._lock:
            out = [t for q, t in self._ring
                   if q > since_seq and (sym is None or t["symbol"] == sym)]
            max_seq = self._seq
        return out[-limit:], max_seq

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

    # -- 內部 ---------------------------------------------------------------

    def _backfill_from_file(self):
        """server 重啟後把當日 JSONL 灌回 ring（保留 dedup 基準）。"""
        self._backfilled = True
        fp = tick_file()
        if not fp.is_file():
            return
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        t = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._append_tick(t, persist=False)
        except OSError:
            pass

    def _append_tick(self, tick: dict, persist: bool = True):
        with self._lock:
            self._seq += 1
            self._ring.append((self._seq, tick))
            self._last_key[tick["symbol"]] = (tick.get("tlong"), tick.get("cum_vol"))
        if persist:
            try:
                REALTIME_DIR.mkdir(parents=True, exist_ok=True)
                with open(tick_file(), "a", encoding="utf-8") as f:
                    f.write(json.dumps(tick, ensure_ascii=False) + "\n")
            except OSError as e:
                self._last_error = f"persist: {e}"

    def poll_once(self):
        """單次輪詢（thread loop 用；測試也可直接呼叫）。"""
        with self._lock:
            ex_chs = list(self._symbols.values())
        if not ex_chs:
            return
        try:
            msgs = self._fetch(ex_chs)
            self._last_error = None
        except Exception as e:
            self._last_error = str(e)
            return
        finally:
            self._last_poll_at = dt.datetime.now().isoformat(timespec="seconds")
            self._poll_count += 1
        for msg in msgs:
            tick = parse_tick(msg)
            if tick is None:
                continue
            key = (tick.get("tlong"), tick.get("cum_vol"))
            if self._last_key.get(tick["symbol"]) == key:
                continue  # 同一筆快照 → 不是新 tick
            self._append_tick(tick)

    def _loop(self):
        while not self._stop_evt.wait(self.poll_sec):
            self.poll_once()


# process 級單例
_collector: TickCollector | None = None
_collector_lock = threading.Lock()


def get_collector() -> TickCollector:
    global _collector
    with _collector_lock:
        if _collector is None:
            _collector = TickCollector()
        return _collector
