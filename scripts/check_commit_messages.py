#!/usr/bin/env python3
"""驗證 git commit subject 是否符合本 repo「至少含 1 個 CJK 字」規約。

兩種用法：

1. CLI（CI / 人工檢查 git log 範圍）

       python scripts/check_commit_messages.py                   # 預設 HEAD~20..HEAD
       python scripts/check_commit_messages.py --range origin/main..HEAD
       python scripts/check_commit_messages.py --strict --json

2. commit-msg hook（本機提交時擋下不合格 subject）

       python scripts/check_commit_messages.py --file .git/COMMIT_EDITMSG

Exit codes:
    0 = 全部合格
    1 = 至少一筆違規
    2 = 無 commit / 無法解析

詳細規則見 `CLAUDE.md` § Commit message 規約。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# CJK Unified Ideographs (U+4E00..U+9FFF) + CJK Extension A (U+3400..U+4DBF)
_CJK = re.compile(r"[㐀-䶿一-鿿]")

# 自動產生的 commit 不擋
_EXEMPT_PATTERNS = [
    re.compile(r"^Merge\s"),
    re.compile(r"^Revert\s"),
    re.compile(r"^Initial commit$"),
    re.compile(r"^fixup!\s"),
    re.compile(r"^squash!\s"),
]

MAX_SUBJECT_LEN = 72


def _check_subject(subject: str) -> tuple[bool, list[str]]:
    """回傳 (ok, errors)。subject 是第一行，不含換行。"""
    errors: list[str] = []
    s = subject.rstrip()

    if not s:
        errors.append("空白標題")
        return False, errors

    # 例外規則：自動生成的 commit 一律放行
    for p in _EXEMPT_PATTERNS:
        if p.match(s):
            return True, []

    if not _CJK.search(s):
        errors.append("標題不含中文字（至少需 1 個 CJK 統一表意文字 U+4E00..U+9FFF）")

    # 長度（以字元計，中文字算 1 字）
    if len(s) > MAX_SUBJECT_LEN:
        errors.append(f"標題長度 {len(s)} 字 > {MAX_SUBJECT_LEN}")

    return not errors, errors


def _git_log_subjects(rev_range: str) -> list[tuple[str, str]]:
    """回 [(sha, subject), ...]，照時間升冪。"""
    out = subprocess.check_output(
        ["git", "log", "--reverse", "--pretty=format:%H%x09%s", rev_range],
        text=True, encoding="utf-8",
    )
    rows: list[tuple[str, str]] = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        sha, _, subject = ln.partition("\t")
        rows.append((sha, subject))
    return rows


def _read_msg_file(path: Path) -> str:
    """讀 commit-msg hook 收到的訊息檔，取第一行。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    # 過濾 `#` 註解行（git 自動加的）
    for ln in text.splitlines():
        if ln.startswith("#"):
            continue
        return ln
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--range", default="HEAD~20..HEAD",
                   help="git log range；預設 HEAD~20..HEAD")
    g.add_argument("--file", type=Path,
                   help="commit-msg hook 模式：讀單一檔的第一行")
    ap.add_argument("--strict", action="store_true",
                    help="目前等同預設；保留旗標供未來細分用")
    ap.add_argument("--json", action="store_true",
                    help="輸出 JSON")
    args = ap.parse_args(argv)

    if args.file:
        subject = _read_msg_file(args.file)
        ok, errs = _check_subject(subject)
        result = {"path": str(args.file), "subject": subject, "ok": ok, "errors": errs}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            mark = "✅" if ok else "❌"
            print(f"{mark} {subject}")
            for e in errs:
                print(f"   錯誤: {e}")
            if not ok:
                print("\n參見 CLAUDE.md § Commit message 規約。")
        return 0 if ok else 1

    # CLI / CI mode
    try:
        rows = _git_log_subjects(args.range)
    except subprocess.CalledProcessError as e:
        print(f"git log 失敗：{e}", file=sys.stderr)
        return 2

    if not rows:
        print(f"範圍 {args.range} 沒有 commit", file=sys.stderr)
        return 2

    results = []
    n_bad = 0
    for sha, subject in rows:
        ok, errs = _check_subject(subject)
        results.append({"sha": sha[:7], "subject": subject, "ok": ok, "errors": errs})
        if not ok:
            n_bad += 1

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "✅" if r["ok"] else "❌"
            print(f"{mark} {r['sha']}  {r['subject']}")
            for e in r["errors"]:
                print(f"        錯誤: {e}")
        print(f"\n{len(rows) - n_bad}/{len(rows)} 通過；{n_bad} 筆違規。")

    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
