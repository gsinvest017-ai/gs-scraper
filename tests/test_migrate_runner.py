"""Unit tests for ui.search.migrate_runner — migration dashboard 後端。

重點在 **安全**：輸入白名單驗證擋掉 shell injection / 壞格式，且 password
不會出現在組出來的指令列（只走 env）。
"""
from __future__ import annotations

import pytest

from ui.search.migrate_runner import (
    ValidationError,
    _bash_for,
    build_command,
    validate,
)


# ── validate: 合法輸入 ────────────────────────────────────────────────────

def test_validate_minimal_dryrun():
    p = validate({"user": "kevin", "ip": "192.168.0.50"})
    assert p["host"] == "192.168.0.50"
    assert p["user"] == "kevin"
    assert p["port"] == 22
    assert p["apply"] is False


def test_validate_hostname_when_no_ip():
    p = validate({"user": "kevin", "hostname": "quant-node-2"})
    assert p["host"] == "quant-node-2"


def test_validate_ip_preferred_over_hostname():
    p = validate({"user": "kevin", "ip": "10.0.0.5", "hostname": "h"})
    assert p["host"] == "10.0.0.5"


def test_validate_ipv6_ok():
    p = validate({"user": "kevin", "ip": "::1"})
    assert p["host"] == "::1"


# ── validate: 應該被擋下的輸入 ────────────────────────────────────────────

@pytest.mark.parametrize("payload, frag", [
    ({"user": "a;rm -rf /", "ip": "1.2.3.4"}, "user"),
    ({"user": "a b", "ip": "1.2.3.4"}, "user"),
    ({"user": "kevin", "ip": "999.1.1.1"}, "ip"),
    ({"user": "kevin", "ip": "not-an-ip"}, "ip"),
    ({"user": "kevin", "ip": "1.2.3.4", "port": "70000"}, "port"),
    ({"user": "kevin", "ip": "1.2.3.4", "port": "0"}, "port"),
    ({"user": "kevin", "ip": "1.2.3.4", "port": "abc"}, "port"),
    ({"user": "kevin", "ip": "1.2.3.4", "target_path": "/x'/y"}, "單引號"),
    ({"user": "kevin"}, "host"),
    ({"user": "kevin", "ip": "1.2.3.4", "os_type": "solaris"}, "os_type"),
    ({"user": "kevin", "hostname": "bad host!", "ip": ""}, "hostname"),
    ({"user": "kevin\ninjected", "ip": "1.2.3.4"}, "非法字元"),
])
def test_validate_rejects_bad_input(payload, frag):
    with pytest.raises(ValidationError) as ei:
        validate(payload)
    assert frag in str(ei.value)


def test_validate_relative_path_rejected_for_linux():
    with pytest.raises(ValidationError):
        validate({"user": "kevin", "ip": "1.2.3.4", "target_path": "relative/path"})


# ── build_command: 旗標組裝 ───────────────────────────────────────────────

def test_build_command_dryrun_has_no_apply():
    cmd = build_command(validate({"user": "kevin", "ip": "1.2.3.4"}))
    assert "--apply" not in cmd
    assert "--host" in cmd and "kevin@1.2.3.4" in cmd
    assert cmd[:2] == ["bash"] + [cmd[1]]  # bash <script>


def test_build_command_apply_verify():
    cmd = build_command(validate({
        "user": "kevin", "ip": "1.2.3.4", "apply": True, "verify": True,
        "target_path": "/home/kevin/QD",
    }))
    assert "--apply" in cmd
    assert "--verify" in cmd
    assert "--path" in cmd and "/home/kevin/QD" in cmd


def test_build_command_verify_only_when_apply():
    # verify 沒有 apply 時不應加 --verify（dry-run 模式無意義）
    cmd = build_command(validate({"user": "kevin", "ip": "1.2.3.4", "verify": True}))
    assert "--verify" not in cmd


def test_build_command_no_delete():
    cmd = build_command(validate({"user": "kevin", "ip": "1.2.3.4", "no_delete": True}))
    assert "--no-delete" in cmd


def test_build_command_bwlimit_numeric_ok():
    cmd = build_command(validate({"user": "kevin", "ip": "1.2.3.4", "bwlimit": "20000"}))
    assert "--bwlimit" in cmd and "20000" in cmd


def test_build_command_bwlimit_nonnumeric_rejected():
    with pytest.raises(ValidationError):
        build_command(validate({"user": "kevin", "ip": "1.2.3.4", "bwlimit": "fast"}))


# ── os_type 轉發：linux→rsync / windows→robocopy ──────────────────────────

def test_build_command_forwards_os_type_default_linux():
    cmd = build_command(validate({"user": "kevin", "ip": "1.2.3.4"}))
    i = cmd.index("--os-type")
    assert cmd[i + 1] == "linux"


def test_build_command_forwards_os_type_windows():
    cmd = build_command(validate({
        "user": "kevin", "ip": "1.2.3.4", "os_type": "windows",
    }))
    i = cmd.index("--os-type")
    assert cmd[i + 1] == "windows"


def test_bash_for_linux_uses_path_bash():
    # linux/wsl 用 PATH 上的 bash（Windows 上落到 WSL，有 rsync）
    assert _bash_for("linux") == "bash"
    assert _bash_for("wsl") == "bash"


def test_bash_for_windows_prefers_git_bash(monkeypatch):
    # windows 找得到 Git Bash 時要用它（有 cygpath）；找不到才退回 "bash"
    monkeypatch.setattr("ui.search.migrate_runner.os.path.isfile", lambda p: True)
    assert _bash_for("windows").lower().endswith("bash.exe")
    monkeypatch.setattr("ui.search.migrate_runner.os.path.isfile", lambda p: False)
    assert _bash_for("windows") == "bash"


def test_validate_windows_allows_drive_path():
    # windows 目標可填 X:\ 或 \\UNC（非 / 開頭不應被擋）
    p = validate({
        "user": "kevin", "ip": "1.2.3.4", "os_type": "windows",
        "target_path": r"C:\QUANTDATA",
    })
    assert p["os_type"] == "windows"
    assert p["target_path"] == r"C:\QUANTDATA"


# ── 安全：password 不進指令列 ─────────────────────────────────────────────

def test_password_never_in_command():
    """password 只走 env；組出來的 arg list 不可含密碼明碼。"""
    payload = {"user": "kevin", "ip": "1.2.3.4", "password": "SUPERSECRET", "apply": True}
    p = validate(payload)        # validate 不該回傳 password
    assert "password" not in p
    assert "SUPERSECRET" not in " ".join(build_command(p))
