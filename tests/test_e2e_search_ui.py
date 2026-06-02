"""P0 e2e — Flask Search UI against mini_catalog.

Covers E-004 (GET /), E-005 (POST /api/query happy), E-006 (POST /api/query
injection 400), E-008 (CSV download), E-011 (GET /downloads).
Uses the `app_client` fixture from conftest.py.
"""
from __future__ import annotations

import json
import zipfile
from io import BytesIO


# ── E-004: index page ───────────────────────────────────────────────────────

def test_E004_index_returns_200_with_view_list(app_client):
    r = app_client.get("/")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    # mini_catalog exposes calendar_xtai + symbol_map
    assert "calendar_xtai" in body
    assert "symbol_map" in body


def test_E004_view_page_returns_200(app_client):
    r = app_client.get("/view/calendar_xtai")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    # column header should appear
    assert "date" in body
    assert "is_trading" in body


def test_E004_view_page_unknown_view_404(app_client):
    r = app_client.get("/view/i_do_not_exist")
    assert r.status_code == 404


# ── E-005: /api/query happy paths ───────────────────────────────────────────

def test_E005_api_query_empty_filters_returns_rows(app_client):
    payload = {"view": "calendar_xtai", "filters": []}
    r = app_client.post("/api/query", data=json.dumps(payload),
                        content_type="application/json")
    assert r.status_code == 200
    data = r.get_json()
    assert set(data.keys()) >= {"columns", "rows", "row_count"}
    assert data["row_count"] == 3
    assert "date" in data["columns"]


def test_E005_api_query_filter_eq(app_client):
    payload = {
        "view": "symbol_map",
        "filters": [{"column": "symbol", "op": "eq", "value": "2330"}],
    }
    r = app_client.post("/api/query", data=json.dumps(payload),
                        content_type="application/json")
    assert r.status_code == 200
    data = r.get_json()
    assert data["row_count"] == 1
    # Find the 'name' column index, value should be 台積電
    name_idx = data["columns"].index("name")
    assert data["rows"][0][name_idx] == "台積電"


def test_E005_api_query_filter_in_list(app_client):
    payload = {
        "view": "symbol_map",
        "filters": [{"column": "symbol", "op": "in", "value": ["2330", "1101"]}],
    }
    r = app_client.post("/api/query", data=json.dumps(payload),
                        content_type="application/json")
    assert r.status_code == 200
    assert r.get_json()["row_count"] == 2


# ── E-006: security — injection / whitelist failures map to 400 ─────────────

def test_E006_api_query_unknown_view_returns_400(app_client):
    payload = {"view": "no_such_view", "filters": []}
    r = app_client.post("/api/query", data=json.dumps(payload),
                        content_type="application/json")
    assert r.status_code == 400
    assert "Unknown view" in r.get_json().get("error", "")


def test_E006_api_query_injection_via_column_400(app_client):
    payload = {
        "view": "symbol_map",
        "filters": [{"column": '"; DROP TABLE symbol_map; --', "op": "eq", "value": 1}],
    }
    r = app_client.post("/api/query", data=json.dumps(payload),
                        content_type="application/json")
    assert r.status_code == 400
    assert "Unknown column" in r.get_json().get("error", "")


def test_E006_api_query_bad_op_400(app_client):
    payload = {
        "view": "symbol_map",
        "filters": [{"column": "symbol", "op": "DELETE FROM symbol_map", "value": 1}],
    }
    r = app_client.post("/api/query", data=json.dumps(payload),
                        content_type="application/json")
    assert r.status_code == 400


def test_E006_api_query_missing_view_400(app_client):
    r = app_client.post("/api/query", data=json.dumps({}),
                        content_type="application/json")
    assert r.status_code == 400


# ── E-011: downloads page ───────────────────────────────────────────────────

def test_E011_downloads_page_lists_views(app_client):
    r = app_client.get("/downloads")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    # Bucket buttons + view name in table
    assert "calendar_xtai" in body
    assert "symbol_map" in body
    assert "Download selected as .zip" in body or "Download" in body


# ── E-008: single CSV stream ────────────────────────────────────────────────

def test_E008_download_view_csv(app_client):
    r = app_client.get("/download/view/calendar_xtai.csv")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/csv")
    assert 'attachment; filename="calendar_xtai.csv"' in r.headers["Content-Disposition"]
    text = r.data.decode("utf-8")
    lines = text.strip().split("\n")
    assert lines[0] == "date,is_trading,session"
    assert len(lines) == 1 + 3  # header + 3 rows


def test_E008_download_unknown_view_404(app_client):
    r = app_client.get("/download/view/nope.csv")
    assert r.status_code == 404


# ── E-009: bundle.zip ───────────────────────────────────────────────────────

def test_E009_download_bundle_zip_two_views(app_client):
    r = app_client.get("/download/bundle.zip?v=calendar_xtai&v=symbol_map")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/zip"

    zf = zipfile.ZipFile(BytesIO(r.data))
    names = sorted(zf.namelist())
    assert names == ["calendar_xtai.csv", "symbol_map.csv"]
    # Each csv has header + N rows
    cal = zf.read("calendar_xtai.csv").decode("utf-8").strip().split("\n")
    assert cal[0].startswith("date,")
    assert len(cal) == 1 + 3


def test_E010_download_bundle_unknown_view_400(app_client):
    r = app_client.get("/download/bundle.zip?v=calendar_xtai&v=no_such")
    assert r.status_code == 400
    assert "unknown views" in r.get_json().get("error", "").lower()


def test_E010_download_bundle_no_views_400(app_client):
    r = app_client.get("/download/bundle.zip")
    assert r.status_code == 400


# ── E-012: /gap_dashboard.html static serve ─────────────────────────────────

def test_E012_gap_dashboard_html_served(app_client):
    """nav 列那條連結要能真的開到 docs/gap_dashboard.html。"""
    r = app_client.get("/gap_dashboard.html")
    # 真實 catalog 通常已有此檔；測試環境可能沒有 → 接受 200 或 404 但兩者
    # 都不該是「URL not found on the server」這種 Flask 預設沒 route 的 404。
    # 用「200 OK」當 happy path，並驗 content-type；404 需來自我們的 abort。
    if r.status_code == 200:
        assert "text/html" in r.headers.get("Content-Type", "")
        # 內容應是真的 dashboard（含某種 summary 字眼）
        body = r.data.decode("utf-8", errors="replace")
        assert "gap_dashboard" in body.lower() or "OK" in body or "STALE" in body
    else:
        # 404 路徑：我們的 route 該回繁體中文提示訊息
        assert r.status_code == 404
        assert "gap_report" in r.data.decode("utf-8", errors="replace")
