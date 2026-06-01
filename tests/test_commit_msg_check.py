"""鎖住 commit-msg 驗證器與 CLAUDE.md spec 文字。

不檢查歷史 commit（規約 2026-06-01 起生效，歷史不追溯）；只測 validator
的判斷邏輯本身與 spec 文字是否完整。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "check_commit_messages_under_test",
        REPO / "scripts" / "check_commit_messages.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


CHK = _load_validator()


# ── _check_subject 正面案例（必須通過）────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "M2: 寫 P0 unit 測試 — query_builder + BS-IV + CSV escape 共 66 個案例",
    "fix: TXO build_txo_daily_features 在 Timestamp vs date 比較炸 TypeError",
    "docs: 補 autogo /plans import contract 與本地 validator",
    "chore: 升級 pytest 7.4 → 8.0",
    "M4-iter3: dashboard 重生 — OK 38 → 39（新增 macro_daily gold）",
    "中",  # 邊界：單字 + 沒前綴也合格（雖然不建議）
    "feat: 新增 Search UI 的「期/週/月」chart 切換",
])
def test_valid_subjects_pass(subject):
    ok, errs = CHK._check_subject(subject)
    assert ok, f"應通過但被擋：{subject!r} → {errs}"
    assert errs == []


# ── _check_subject 負面案例（必須擋下）────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "M2: P0 unit tests for query_builder",
    "fix: txo TypeError on Timestamp vs date",
    "chore: bump pytest",
    "M4: progress doc — 102 pytest pass",
    "WIP",
    "",  # 空標題
])
def test_invalid_subjects_blocked(subject):
    ok, errs = CHK._check_subject(subject)
    assert not ok, f"應擋下但通過：{subject!r}"
    assert errs


# ── 自動產生例外 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "Merge branch 'feature/x' into main",
    'Revert "fix: bad commit"',
    "Initial commit",
    "fixup! M2: 補測試",
    "squash! 修 typo",
])
def test_auto_generated_subjects_exempt(subject):
    ok, _ = CHK._check_subject(subject)
    assert ok, f"自動產生的 commit 不該被擋：{subject!r}"


# ── 長度限制 ───────────────────────────────────────────────────────────────

def test_subject_over_72_chars_fails():
    long_subject = "改: " + "中" * 80
    ok, errs = CHK._check_subject(long_subject)
    assert not ok
    assert any("長度" in e for e in errs)


def test_subject_exactly_72_chars_passes():
    # 「改:」(2 chars) + 70 chars CJK = 72
    subject = "改:" + "中" * 70
    assert len(subject) == 72
    ok, errs = CHK._check_subject(subject)
    assert ok, errs


# ── CLAUDE.md spec 文字存在性鎖住 ─────────────────────────────────────────

def test_claude_md_exists_and_contains_spec():
    p = REPO / "CLAUDE.md"
    assert p.is_file(), "CLAUDE.md 必須存在於 repo root"
    text = p.read_text(encoding="utf-8")
    assert "Commit message 規約" in text
    assert "CJK" in text or "U+4E00" in text
    # safe-yolo 前綴慣例必須保留說明
    assert "milestone" in text.lower() or "M1:" in text or "M2:" in text


def test_hook_template_executable():
    hook = REPO / "scripts" / "git-hooks" / "commit-msg"
    assert hook.is_file()
    # bash shebang 在第一行
    first_line = hook.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!")


# ── --file 模式（hook 入口）───────────────────────────────────────────────

def test_file_mode_pass(tmp_path):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("M2: 補一些測試案例\n\n# 這行是 git 註解\n", encoding="utf-8")
    rc = CHK.main(["--file", str(msg)])
    assert rc == 0


def test_file_mode_fail(tmp_path):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("M2: english only subject\n", encoding="utf-8")
    rc = CHK.main(["--file", str(msg)])
    assert rc == 1


def test_file_mode_skips_comment_lines(tmp_path):
    """git 在 COMMIT_EDITMSG 加 `# ...` 註解；驗證器要跳過，讀第一非註解行。"""
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(
        "# Please enter the commit message for your changes...\n"
        "#\n"
        "M3: 真正的標題在這\n",
        encoding="utf-8",
    )
    rc = CHK.main(["--file", str(msg)])
    assert rc == 0
