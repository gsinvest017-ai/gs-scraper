"""對外即時行情 API v1 的 OpenAPI 3.0 規格（手寫，不依賴任何產生器套件）。

`build_spec()` 回一個 dict，由 `api_v1` 的 `/openapi.json` 端點 jsonify 後給
Swagger UI（`/api/v1/docs`，前端資產 vendored 在 static/swagger/）讀取，
供其他專案開發者參考如何接這組 API。

維護原則：這份 spec 是「人讀 + Swagger UI 用」的契約描述，與 `api_v1.py` 的
實際回應保持一致；改端點時記得同步這裡與 `docs/api-v1.md`。
"""

from __future__ import annotations

# 共用 tick 物件 schema（snapshot enrich 後的單檔形狀）
_SNAPSHOT_ITEM = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "example": "2330"},
        "name": {"type": "string", "nullable": True, "example": "台積電"},
        "price": {"type": "number", "nullable": True, "example": 1085.0},
        "bid": {"type": "number", "nullable": True},
        "ask": {"type": "number", "nullable": True},
        "open": {"type": "number", "nullable": True},
        "high": {"type": "number", "nullable": True},
        "low": {"type": "number", "nullable": True},
        "prev_close": {"type": "number", "nullable": True},
        "cum_vol": {"type": "number", "nullable": True},
        "tick_vol": {"type": "number", "nullable": True},
        "change": {"type": "number", "nullable": True,
                   "description": "price - prev_close；prev_close 缺或為 0 → null"},
        "change_pct": {"type": "number", "nullable": True,
                       "description": "change / prev_close * 100"},
        "time": {"type": "string", "nullable": True, "example": "13:24:58"},
        "tlong": {"type": "integer", "nullable": True,
                  "description": "成交時間 epoch 毫秒"},
        "age_sec": {"type": "number", "nullable": True,
                    "description": "該 tick 距 server_time 的秒數（staleness guard）"},
        "live": {"type": "boolean"},
        "warming": {"type": "boolean",
                    "description": "true = 剛開始採集、ring 還沒資料"},
    },
}

_ERROR = {
    "type": "object",
    "properties": {"error": {"type": "string"}},
}


def _symbol_param(required: bool, desc: str) -> dict:
    return {"name": "symbol", "in": "query", "required": required,
            "schema": {"type": "string"}, "description": desc}


def build_spec() -> dict:
    """組出完整 OpenAPI 3.0 dict。"""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "QUANTDATA 對外即時行情 API",
            "version": "1.0.0",
            "description": (
                "給另一台機器上的系統（例如風控系統）拉取當日即時行情的只讀 HTTP API。\n\n"
                "- 無認證：靠 Tailnet ACL / 內網防火牆做邊界，請勿暴露到公網\n"
                "- 只讀：不含任何 collector 啟停寫入端點\n"
                "- 回應一律含 `server_time`（ISO8601 +08:00）；GET 帶 "
                "`Access-Control-Allow-Origin: *`\n"
                "- 建議 staleness guard：先看 `/health` 的 `seconds_since_poll`，"
                "再看 `/snapshot` 每檔的 `age_sec`"
            ),
        },
        "servers": [{"url": "/api/v1"}],
        "paths": {
            "/health": {
                "get": {
                    "summary": "collector 健康 + 資料新鮮度",
                    "description": "風控在信任 snapshot 前先打這支判斷資料是否新鮮。",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "server_time": {"type": "string"},
                                    "collector": {
                                        "type": "object",
                                        "properties": {
                                            "running": {"type": "boolean"},
                                            "collected_symbols": {
                                                "type": "array",
                                                "items": {"type": "string"}},
                                            "poll_sec": {"type": "number"},
                                            "started_at": {"type": "string", "nullable": True},
                                            "last_poll_at": {"type": "string", "nullable": True},
                                            "seconds_since_poll": {"type": "number", "nullable": True},
                                            "poll_count": {"type": "integer"},
                                            "ticks_in_ring": {"type": "integer"},
                                            "seq": {"type": "integer"},
                                            "last_error": {"type": "string", "nullable": True},
                                        },
                                    },
                                },
                            }}},
                        },
                    },
                },
            },
            "/snapshot": {
                "get": {
                    "summary": "各 symbol 當下最新快照（mark-to-market / kill-switch 主力）",
                    "parameters": [
                        {"name": "symbols", "in": "query", "required": True,
                         "schema": {"type": "string"},
                         "description": "逗號/空白/分號分隔，如 2330,TAIEX,0050（大小寫不敏感）"},
                        {"name": "ensure", "in": "query", "required": False,
                         "schema": {"type": "string", "enum": ["0", "1"], "default": "1"},
                         "description": "1=未採集的 symbol 自動加入 watchlist（上限 20）；0=純讀"},
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "server_time": {"type": "string"},
                                    "snapshots": {
                                        "type": "object",
                                        "additionalProperties": _SNAPSHOT_ITEM},
                                    "not_collected": {"type": "array",
                                                      "items": {"type": "string"}},
                                    "dropped": {"type": "array",
                                                "items": {"type": "string"},
                                                "description": "超過 20 檔上限被丟掉的 symbol"},
                                },
                            }}},
                        },
                        "400": {"description": "缺 symbols",
                                "content": {"application/json": {"schema": _ERROR}}},
                        "503": {"description": "collector 無法啟動且無任何 tick",
                                "content": {"application/json": {"schema": _ERROR}}},
                    },
                },
            },
            "/ticks": {
                "get": {
                    "summary": "逐 tick 增量流（ring buffer）",
                    "parameters": [
                        _symbol_param(False, "省略 = 所有採集中 symbol 合併流"),
                        {"name": "since_seq", "in": "query", "required": False,
                         "schema": {"type": "integer", "default": 0},
                         "description": "上次回傳的 seq，只拿之後的新 tick"},
                        {"name": "limit", "in": "query", "required": False,
                         "schema": {"type": "integer", "default": 5000, "maximum": 20000}},
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "server_time": {"type": "string"},
                                    "symbol": {"type": "string", "nullable": True},
                                    "ticks": {"type": "array",
                                              "items": {"type": "object"}},
                                    "seq": {"type": "integer"},
                                },
                            }}},
                        },
                        "400": {"description": "since_seq / limit 非整數",
                                "content": {"application/json": {"schema": _ERROR}}},
                    },
                },
            },
            "/ticks/history": {
                "get": {
                    "summary": "任一日某標的逐 tick（三層 fallback）",
                    "description": "自收 JSONL → FinMind cache/sqlite → FinMind API。",
                    "parameters": [
                        {"name": "date", "in": "query", "required": True,
                         "schema": {"type": "string", "format": "date"},
                         "description": "YYYY-MM-DD"},
                        _symbol_param(True, "標的代碼"),
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "server_time": {"type": "string"},
                                    "date": {"type": "string"},
                                    "symbol": {"type": "string"},
                                    "source": {"type": "string", "nullable": True,
                                               "description": "命中層級"},
                                    "count": {"type": "integer"},
                                    "ticks": {"type": "array",
                                              "items": {"type": "object"}},
                                },
                            }}},
                        },
                        "400": {"description": "缺 date/symbol 或日期格式錯",
                                "content": {"application/json": {"schema": _ERROR}}},
                    },
                },
            },
            "/bars": {
                "get": {
                    "summary": "當日 + 歷史日線 OHLCV（算波動率 / ATR / 回撤基準）",
                    "parameters": [
                        _symbol_param(True, "標的代碼"),
                        {"name": "days", "in": "query", "required": False,
                         "schema": {"type": "integer", "default": 60, "maximum": 365}},
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "server_time": {"type": "string"},
                                    "symbol": {"type": "string"},
                                    "asset_class": {"type": "string"},
                                    "days": {"type": "integer"},
                                    "series": {
                                        "type": "object",
                                        "properties": {
                                            "dates": {"type": "array", "items": {"type": "string"}},
                                            "open": {"type": "array", "items": {"type": "number"}},
                                            "high": {"type": "array", "items": {"type": "number"}},
                                            "low": {"type": "array", "items": {"type": "number"}},
                                            "close": {"type": "array", "items": {"type": "number"}},
                                            "volume": {"type": "array", "items": {"type": "number"}},
                                        },
                                    },
                                    "latest": {"type": "object"},
                                },
                            }}},
                        },
                        "400": {"description": "缺 symbol 或 days 非整數",
                                "content": {"application/json": {"schema": _ERROR}}},
                        "404": {"description": "查無標的",
                                "content": {"application/json": {"schema": _ERROR}}},
                    },
                },
            },
        },
    }
