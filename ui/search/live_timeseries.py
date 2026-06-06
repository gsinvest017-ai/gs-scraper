"""Live timeseries — 最新交易日錨點的標的價量序列查詢。

資料源（read-only catalog 快照，經 ``catalog_inspector.get_connection``）：

- ``bars_1d``     台股 / 台期 / 股期 日線（symbol + asset_class）
- ``macro_daily`` 總經標的（TAIEX / SPX / VIX / USDTWD …，以 category 分類）

對外兩個函式：

- ``list_symbols()``  → 全部可查標的（含 asset_class、最新交易日），給前端
  autocomplete；process 內 cache，`refresh` 參數可強制重抓。
- ``get_timeseries(symbol, days)`` → 近 N 個交易日的 OHLCV 序列 + 最新交易日
  統計（漲跌、漲跌幅 vs 前一交易日收盤）。
"""

from __future__ import annotations

from ui.search.catalog_inspector import get_connection

MAX_DAYS = 365
DEFAULT_DAYS = 60

# (view, symbol 欄位, 類別欄位或常值, 有無 volume)
_SYMBOL_CACHE: list[dict] | None = None


def list_symbols(refresh: bool = False) -> list[dict]:
    """回傳 [{symbol, asset_class, latest_date}, ...]（兩個資料源聯集）。

    bars_1d 與 macro_daily 重名時（如 0050）優先 bars_1d（台股日線較完整），
    macro 版本以 ``macro:<symbol>`` 形式保留可查。
    """
    global _SYMBOL_CACHE
    if _SYMBOL_CACHE is not None and not refresh:
        return _SYMBOL_CACHE

    out: list[dict] = []
    seen: set[str] = set()
    con = get_connection()
    try:
        try:
            rows = con.execute(
                "SELECT symbol, asset_class, max(trading_date) FROM bars_1d "
                "GROUP BY 1, 2 ORDER BY 1"
            ).fetchall()
            for sym, ac, latest in rows:
                if not sym:
                    continue
                out.append({"symbol": sym, "asset_class": ac,
                            "latest_date": str(latest)})
                seen.add(sym)
        except Exception:
            pass  # mini catalog / 測試環境沒有 bars_1d
        try:
            rows = con.execute(
                "SELECT symbol, category, max(trading_date) FROM macro_daily "
                "GROUP BY 1, 2 ORDER BY 1"
            ).fetchall()
            for sym, cat, latest in rows:
                if not sym:
                    continue
                name = sym if sym not in seen else f"macro:{sym}"
                out.append({"symbol": name, "asset_class": f"macro/{cat}",
                            "latest_date": str(latest)})
        except Exception:
            pass
    finally:
        con.close()
    _SYMBOL_CACHE = out
    return out


def _query_series(con, symbol: str, days: int) -> tuple[list, str] | None:
    """依 symbol 決定資料源查近 N 個交易日。回傳 (rows, asset_class) 或 None。

    rows 欄位固定：trading_date, open, high, low, close, volume（舊→新）。
    """
    if symbol.startswith("macro:"):
        candidates = [("macro", symbol.split(":", 1)[1])]
    else:
        candidates = [("bars", symbol), ("macro", symbol)]

    for kind, sym in candidates:
        try:
            if kind == "bars":
                rows = con.execute(
                    "SELECT trading_date, open, high, low, close, volume, asset_class "
                    "FROM bars_1d WHERE symbol = ? "
                    "ORDER BY trading_date DESC LIMIT ?", [sym, days],
                ).fetchall()
                if rows:
                    return [r[:6] for r in reversed(rows)], rows[0][6]
            else:
                rows = con.execute(
                    "SELECT trading_date, open, high, low, close, volume, category "
                    "FROM macro_daily WHERE symbol = ? "
                    "ORDER BY trading_date DESC LIMIT ?", [sym, days],
                ).fetchall()
                if rows:
                    return [r[:6] for r in reversed(rows)], f"macro/{rows[0][6]}"
        except Exception:
            continue  # view 不存在（測試 mini catalog）→ 試下一個
    return None


def get_timeseries(symbol: str, days: int = DEFAULT_DAYS) -> dict | None:
    """回傳標的近 N 個交易日序列 + 最新交易日統計；查無此標的回 None。"""
    days = max(5, min(int(days), MAX_DAYS))
    symbol = symbol.strip()
    if not symbol:
        return None

    con = get_connection()
    try:
        hit = _query_series(con, symbol, days)
    finally:
        con.close()
    if hit is None:
        return None
    rows, asset_class = hit

    def f(v):
        return None if v is None else float(v)

    series = {
        "dates": [str(r[0]) for r in rows],
        "open":  [f(r[1]) for r in rows],
        "high":  [f(r[2]) for r in rows],
        "low":   [f(r[3]) for r in rows],
        "close": [f(r[4]) for r in rows],
        "volume": [None if r[5] is None else float(r[5]) for r in rows],
    }

    last = rows[-1]
    prev_close = f(rows[-2][4]) if len(rows) >= 2 else None
    last_close = f(last[4])
    change = change_pct = None
    if last_close is not None and prev_close not in (None, 0):
        change = round(last_close - prev_close, 6)
        change_pct = round(change / prev_close * 100, 4)

    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "days": len(rows),
        "series": series,
        "latest": {
            "trading_date": str(last[0]),
            "open": f(last[1]), "high": f(last[2]),
            "low": f(last[3]), "close": last_close,
            "volume": None if last[5] is None else float(last[5]),
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
        },
    }
