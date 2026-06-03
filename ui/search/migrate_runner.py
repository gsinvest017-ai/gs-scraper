"""Migration dashboard 後端：把表單參數轉成 migrate_to_host.sh 呼叫並串流 log。

安全要點：
  - password 只塞進 subprocess 的 SSHPASS env，**絕不**進指令列 / log / 磁碟 / 回傳。
  - 一律用 arg list（subprocess 不開 shell），杜絕 shell injection。
  - 對 os_type / user / host / port / path 做白名單格式驗證。
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "migrate_to_host.sh"

OS_TYPES = {"linux", "wsl", "windows"}
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")  # hostname label chars

# 同時只允許一個遷移在跑（避免兩個 rsync 互相打架）
_run_lock = threading.Lock()


class ValidationError(ValueError):
    """表單輸入不合法。"""


def _clean(s, name: str) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        raise ValidationError(f"{name} 必須是字串")
    s = s.strip()
    if "\n" in s or "\r" in s or "\x00" in s:
        raise ValidationError(f"{name} 含非法字元")
    return s


def validate(payload: dict) -> dict:
    """驗證並正規化表單輸入；回傳乾淨參數 dict（不含 password 的明碼以外用途）。"""
    os_type = _clean(payload.get("os_type"), "os_type").lower() or "linux"
    if os_type not in OS_TYPES:
        raise ValidationError(f"os_type 必須是 {sorted(OS_TYPES)} 之一")

    ip = _clean(payload.get("ip"), "ip")
    hostname = _clean(payload.get("hostname"), "hostname")
    user = _clean(payload.get("user"), "user")
    target_path = _clean(payload.get("target_path"), "target_path")

    if not user or not _USER_RE.match(user):
        raise ValidationError("user 必填，且只允許英數與 . _ -（≤64 字）")

    if ip:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise ValidationError(f"ip 格式不正確：{ip}")
    if hostname and not _HOST_RE.match(hostname):
        raise ValidationError("hostname 只允許英數與 . _ -")
    host = ip or hostname
    if not host:
        raise ValidationError("ip 與 hostname 至少要填一個")

    # port
    raw_port = payload.get("port") or 22
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise ValidationError("port 必須是整數")
    if not (1 <= port <= 65535):
        raise ValidationError("port 必須介於 1..65535")

    # target_path：可空（沿用來源路徑）。給值時必須是絕對路徑、且不含單引號
    # （腳本內以 'path' 單引號包進 mkdir/cd，含單引號會破壞）。
    if target_path:
        if "'" in target_path:
            raise ValidationError("target_path 不可含單引號")
        if not target_path.startswith("/") and os_type != "windows":
            raise ValidationError("target_path 應為絕對路徑（/ 開頭）")

    return {
        "os_type": os_type,
        "host": host,
        "user": user,
        "port": port,
        "target_path": target_path,
        "apply": bool(payload.get("apply")),
        "verify": bool(payload.get("verify")),
        "no_delete": bool(payload.get("no_delete")),
        "bwlimit": _clean(payload.get("bwlimit"), "bwlimit"),
    }


def build_command(p: dict) -> list[str]:
    """組 migrate_to_host.sh 的 arg list（不含 password）。"""
    cmd = ["bash", str(SCRIPT), "--host", f"{p['user']}@{p['host']}", "--port", str(p["port"])]
    if p["target_path"]:
        cmd += ["--path", p["target_path"]]
    if p["apply"]:
        cmd.append("--apply")
        if p["verify"]:
            cmd.append("--verify")
    if p["no_delete"]:
        cmd.append("--no-delete")
    if p["bwlimit"]:
        if not p["bwlimit"].isdigit():
            raise ValidationError("bwlimit 必須是數字（KB/s）")
        cmd += ["--bwlimit", p["bwlimit"]]
    return cmd


def stream_migration(p: dict, password: str | None) -> Iterator[str]:
    """spawn migrate_to_host.sh，逐行 yield log。password 只進 env。

    若已有遷移在跑則 yield 一行錯誤後結束。
    """
    if not _run_lock.acquire(blocking=False):
        yield "[dashboard] 已有遷移在執行中，請等它完成。\n"
        return

    try:
        cmd = build_command(p)
        env = os.environ.copy()
        if password:
            env["SSHPASS"] = password  # 只在這個 subprocess env 裡，跑完即丟
        else:
            env.pop("SSHPASS", None)

        masked = " ".join(cmd)  # cmd 本身無 password，可安全顯示
        mode = "APPLY（真的搬）" if p["apply"] else "DRY-RUN（預覽）"
        auth = "password" if password else "ssh key"
        yield f"[dashboard] 模式：{mode}　認證：{auth}\n"
        yield f"[dashboard] $ {masked}\n"
        yield "[dashboard] " + "-" * 60 + "\n"

        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                yield line
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.terminate()
        yield f"\n[dashboard] === 結束，exit code = {proc.returncode} ===\n"
    except ValidationError as e:
        yield f"[dashboard] 輸入錯誤：{e}\n"
    except Exception as e:  # noqa: BLE001
        yield f"[dashboard] 執行失敗：{e}\n"
    finally:
        _run_lock.release()
