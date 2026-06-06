"""Tick history — 非交易日 / 盤後回看任一日的逐 tick 資料。

三層 fallback（由快到慢）：

1. 自收 ``meta/realtime/ticks_<date>.jsonl``（collector 盤中收的快照 tick）
2. FinMind sqlite ``taiwan_stock_price_tick``（FINMIND資料集 repo 的本地庫，
   交易所全量逐筆）
3. FinMind API ``TaiwanStockPriceTick``（1 檔 × 1 日/呼叫；token 讀 FINMIND
   repo ``.env``）→ 成功後 cache 成
   ``meta/realtime/finmind_ticks_<date>_<symbol>.jsonl``，重看不再打 API

FinMind tick 統一轉成 collector 的 tick schema（price/tick_vol/cum_vol/
tlong/...），前端同一套 renderer 直接吃。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

from ui.search.tick_collector import REALTIME_DIR, tick_file

ROOT = Path(__file__).resolve().parents[2]
FINMIND_REPO = Path(os.environ.get("FINMIND_REPO",
                                   "/home/kevin/gs-scraper/FINMIND資料集"))
FINMIND_SQLITE = FINMIND_REPO / "data" / "finmind.sqlite"
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SYM_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")
_TZ_TAIPEI = dt.timezone(dt.timedelta(hours=8))

MAX_TICKS = 30_000   # 單日單檔回傳上限（2330 一天約 8k，留餘裕）


def _valid_date(date: str) -> bool:
    return bool(_DATE_RE.match(date))


def last_trading_day() -> str | None:
    """catalog bars_1d 的 max(trading_date) 即最後一個交易日。"""
    try:
        from ui.search.catalog_inspector import get_connection
        con = get_connection()
        try:
            row = con.execute("SELECT max(trading_date) FROM bars_1d").fetchone()
        finally:
            con.close()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


def _finmind_token() -> str | None:
    env = os.environ.get("FINMIND_TOKEN")
    if env:
        return env
    fp = FINMIND_REPO / ".env"
    if fp.is_file():
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("FINMIND_TOKEN=") :
                val = line.split("=", 1)[1].strip()
                if val:
                    return val
    return None


def available_dates(limit: int = 30) -> dict:
    """可回看的日期清單（新→舊）+ 最後交易日。

    含：自收 JSONL 日期、FinMind sqlite 日期、FinMind cache 日期、
    最後交易日（有 token 即可即抓，標 fetchable）。
    """
    dates: set[str] = set()
    if REALTIME_DIR.is_dir():
        for fp in REALTIME_DIR.glob("ticks_*.jsonl"):
            d = fp.stem.removeprefix("ticks_")
            if _valid_date(d):
                dates.add(d)
        for fp in REALTIME_DIR.glob("finmind_ticks_*.jsonl"):
            m = re.match(r"finmind_ticks_(\d{4}-\d{2}-\d{2})_", fp.name)
            if m:
                dates.add(m.group(1))
    if FINMIND_SQLITE.is_file():
        try:
            con = sqlite3.connect(f"file:{FINMIND_SQLITE}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT DISTINCT date FROM taiwan_stock_price_tick").fetchall()
            finally:
                con.close()
            dates.update(r[0] for r in rows if r[0] and _valid_date(str(r[0])))
        except sqlite3.Error:
            pass
    ltd = last_trading_day()
    fetchable = bool(_finmind_token())
    if ltd and fetchable:
        dates.add(ltd)
    return {
        "dates": sorted(dates, reverse=True)[:limit],
        "last_trading_day": ltd,
        "finmind_fetchable": fetchable,
    }


# ── tick 轉換 ───────────────────────────────────────────────────────────────

def _finmind_row_to_tick(symbol: str, date: str, time_s: str, price, vol,
                         tick_type, cum_vol: float) -> dict:
    """FinMind (date, Time, deal_price, volume, TickType) → collector tick schema。"""
    tlong = 0
    try:
        hh, mm, rest = time_s.split(":")
        ss = float(rest)
        base = dt.datetime.fromisoformat(date).replace(
            hour=int(hh), minute=int(mm), tzinfo=_TZ_TAIPEI)
        tlong = int((base.timestamp() + ss) * 1000)
    except (ValueError, AttributeError):
        pass
    return {
        "symbol": symbol.upper(),
        "name": "",
        "ex": "finmind",
        "date": date.replace("-", ""),
        "time": time_s.split(".")[0] if time_s else "",
        "tlong": tlong,
        "price": None if price is None else float(price),
        "tick_vol": None if vol is None else float(vol),
        "cum_vol": cum_vol,
        "bid": None, "ask": None,
        "open": None, "high": None, "low": None,
        "prev_close": None,
        "tick_type": str(tick_type or ""),
    }


def _prev_close_for(symbol: str, date: str) -> float | None:
    """bars_1d 取 date 前一交易日收盤（歷史模式漲跌基準）。"""
    try:
        from ui.search.catalog_inspector import get_connection
        con = get_connection()
        try:
            row = con.execute(
                "SELECT close FROM bars_1d WHERE symbol = ? AND trading_date < ? "
                "ORDER BY trading_date DESC LIMIT 1", [symbol, date]).fetchone()
        finally:
            con.close()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


# ── 三層讀取 ────────────────────────────────────────────────────────────────

def _from_collected(date: str, symbol: str) -> list[dict]:
    fp = tick_file(date)
    if not fp.is_file():
        return []
    out = []
    sym = symbol.upper()
    with open(fp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            if t.get("symbol") == sym:
                out.append(t)
    return out[:MAX_TICKS]


def _finmind_cache_file(date: str, symbol: str) -> Path:
    return REALTIME_DIR / f"finmind_ticks_{date}_{symbol.upper()}.jsonl"


def _from_finmind_cache(date: str, symbol: str) -> list[dict]:
    fp = _finmind_cache_file(date, symbol)
    if not fp.is_file():
        return []
    out = []
    with open(fp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out[:MAX_TICKS]


def _from_finmind_sqlite(date: str, symbol: str) -> list[dict]:
    if not FINMIND_SQLITE.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{FINMIND_SQLITE}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT date, Time, deal_price, volume, TickType "
                "FROM taiwan_stock_price_tick WHERE date = ? AND stock_id = ? "
                "ORDER BY Time", [date, symbol]).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    out, cum = [], 0.0
    for d, time_s, price, vol, tt in rows[:MAX_TICKS]:
        cum += float(vol or 0)
        out.append(_finmind_row_to_tick(symbol, str(d), str(time_s), price, vol, tt, cum))
    return out


def _from_finmind_api(date: str, symbol: str, timeout: float = 30.0) -> list[dict]:
    token = _finmind_token()
    if not token:
        return []
    qs = urllib.parse.urlencode({
        "dataset": "TaiwanStockPriceTick", "data_id": symbol,
        "start_date": date, "token": token,
    })
    try:
        with urllib.request.urlopen(f"{FINMIND_API}?{qs}", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    if data.get("status") != 200:
        return []
    rows = data.get("data") or []
    out, cum = [], 0.0
    for r in rows[:MAX_TICKS]:
        cum += float(r.get("volume") or 0)
        out.append(_finmind_row_to_tick(symbol, r.get("date") or date,
                                        r.get("Time") or "", r.get("deal_price"),
                                        r.get("volume"), r.get("TickType"), cum))
    if out:  # cache 落地，重看不再打 API
        try:
            REALTIME_DIR.mkdir(parents=True, exist_ok=True)
            with open(_finmind_cache_file(date, symbol), "w", encoding="utf-8") as f:
                for t in out:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return out


def get_history_ticks(date: str, symbol: str) -> dict:
    """三層 fallback 取 (date, symbol) 的逐 tick。回 {ticks, source, ...}。"""
    if not _valid_date(date):
        raise ValueError("date 必須是 YYYY-MM-DD")
    symbol = symbol.strip().upper()
    if not _SYM_RE.match(symbol):
        raise ValueError("symbol 格式不合法")

    for source, fn in (("collected", _from_collected),
                       ("finmind_cache", _from_finmind_cache),
                       ("finmind_sqlite", _from_finmind_sqlite),
                       ("finmind_api", _from_finmind_api)):
        ticks = fn(date, symbol)
        if ticks:
            # 歷史 tick 沒有昨收 → 從 bars_1d 補（漲跌著色基準）
            if ticks and ticks[0].get("prev_close") is None:
                pc = _prev_close_for(symbol, date)
                if pc is not None:
                    for t in ticks:
                        t["prev_close"] = pc
            return {"date": date, "symbol": symbol, "source": source,
                    "ticks": ticks, "count": len(ticks)}
    return {"date": date, "symbol": symbol, "source": None,
            "ticks": [], "count": 0}
