"""Dang nhap + phien + audit.

Phien: cookie da ky bang itsdangerous, HttpOnly + SameSite=Strict.
Khong luu session trong DB - 2-5 nguoi dung thi khong can.

Luu y ve mo hinh bao mat: SSH tunnel la LOP THU NHAT (muon mo duoc UI thi
truoc het phai SSH duoc vao server bang user oracle). Login trong app la
LOP THU HAI, de phan biet AI trong so cac DBA cho muc dich audit.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE = "ggadmin_session"
MAX_AGE = 12 * 3600          # 12 gio
LOCK_AFTER = 10              # giong FAILED_LOGIN_ATTEMPTS cua profile DB
LOCK_SECONDS = 300

ROLE_RANK = {"VIEWER": 0, "OPERATOR": 1, "ADMIN": 2}


def _secret(data_dir: Path) -> bytes:
    p = data_dir / ".session_key"
    if p.exists():
        return p.read_bytes()
    key = os.urandom(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    p.write_bytes(key)
    os.chmod(p, 0o600)
    return key


class Auth:
    def __init__(self, cfg, con: sqlite3.Connection):
        self.cfg = cfg
        self.con = con
        self._ser = URLSafeTimedSerializer(_secret(cfg.data_dir), salt="ggadmin")

    # ------------------------------------------------------------- dang nhap
    def login(self, username: str, password: str, peer: str) -> tuple[str | None, str]:
        from . import db as dbmod
        row = self.con.execute(
            "SELECT * FROM app_user WHERE username=?", (username,)).fetchone()
        now = int(time.time())
        if row is None:
            self.audit("(khong ro)", peer, "L0", "LOGIN_FAIL",
                       target=username, reason="tài khoản không tồn tại")
            return None, "Sai tai khoan hoac mat khau"
        if not row["is_active"]:
            return None, "Tài khoản đã bị vô hiệu hoá"
        if row["locked_until"] and row["locked_until"] > now:
            return None, f"Tài khoản tạm khoá, thử lại sau {row['locked_until'] - now}s"

        if not dbmod.verify_pwd(password, row["pwd_hash"]):
            n = row["failed_count"] + 1
            lock = now + LOCK_SECONDS if n >= LOCK_AFTER else None
            self.con.execute(
                "UPDATE app_user SET failed_count=?, locked_until=? WHERE username=?",
                (n, lock, username))
            self.audit(username, peer, "L0", "LOGIN_FAIL",
                       reason=f"sai mật khẩu lần {n}")
            return None, "Sai tai khoan hoac mat khau"

        self.con.execute(
            "UPDATE app_user SET failed_count=0, locked_until=NULL, last_login_at=?"
            " WHERE username=?", (now, username))
        self.audit(username, peer, "L0", "LOGIN_OK")
        return self._ser.dumps({"u": username, "r": row["role"]}), ""

    def identify(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        try:
            data = self._ser.loads(token, max_age=MAX_AGE)
        except BadSignature:
            return None
        except Exception:
            return None
        row = self.con.execute(
            "SELECT username, role, is_active, must_change_pwd, full_name"
            " FROM app_user WHERE username=?", (data.get("u"),)).fetchone()
        if row is None or not row["is_active"]:
            return None
        return dict(row)

    @staticmethod
    def has_role(user: dict | None, need: str) -> bool:
        if not user:
            return False
        return ROLE_RANK.get(user["role"], -1) >= ROLE_RANK[need]

    def change_password(self, username: str, new_pw: str, peer: str) -> None:
        from . import db as dbmod
        self.con.execute(
            "UPDATE app_user SET pwd_hash=?, must_change_pwd=0 WHERE username=?",
            (dbmod.hash_pwd(new_pw), username))
        self.audit(username, peer, "L1", "CHANGE_PASSWORD")

    # ----------------------------------------------------------------- audit
    def audit(self, username: str, peer: str | None, level: str, action: str,
              *, target: str | None = None, reason: str | None = None,
              payload_before: str | None = None, payload_after: str | None = None,
              cmd_text: str | None = None, exit_code: int | None = None,
              stdout_tail: str | None = None, duration_ms: int | None = None) -> None:
        """Ghi 2 NOI, append-only.

        1. bang audit_log  - de query/bao cao
        2. logs/audit-YYYYMM.jsonl - vi mot he audit ma ke tan cong xoa duoc
           bang CUNG dac quyen thi khong phai audit.

        prev_hash = SHA-256 cua record truoc -> hash chain chong sua.
        Redact mat khau TRUOC khi ghi: day la cai bay ma `grep PW_SRC` trong
        script cu da mac, khong mang sang app.
        """
        from .adapters.osfs import redact

        ts = int(time.time())
        prev = self.con.execute(
            "SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = prev["row_hash"] if prev else ""

        rec = {
            "ts": ts, "username": username, "peer": peer, "level": level,
            "action": action, "target": target, "reason": reason,
            "payload_before": redact(payload_before) if payload_before else None,
            "payload_after": redact(payload_after) if payload_after else None,
            "cmd_text": redact(cmd_text) if cmd_text else None,
            "exit_code": exit_code,
            "stdout_tail": redact(stdout_tail[:4096]) if stdout_tail else None,
            "duration_ms": duration_ms,
            "prev_hash": prev_hash,
        }
        rec["row_hash"] = hashlib.sha256(
            json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        self.con.execute(
            "INSERT INTO audit_log(ts,username,peer,level,action,target,reason,"
            "payload_before,payload_after,cmd_text,exit_code,stdout_tail,"
            "duration_ms,prev_hash,row_hash) VALUES"
            "(:ts,:username,:peer,:level,:action,:target,:reason,:payload_before,"
            ":payload_after,:cmd_text,:exit_code,:stdout_tail,:duration_ms,"
            ":prev_hash,:row_hash)", rec)

        try:
            self.cfg.log_dir.mkdir(parents=True, exist_ok=True)
            p = self.cfg.log_dir / f"audit-{time.strftime('%Y%m', time.localtime(ts))}.jsonl"
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def verify_chain(self) -> tuple[bool, int, str]:
        """Kiem hash chain lien mach. Tra (ok, so_record, thong_bao)."""
        prev_hash = ""
        n = 0
        for row in self.con.execute("SELECT * FROM audit_log ORDER BY id"):
            n += 1
            rec = {k: row[k] for k in row.keys() if k not in ("id", "row_hash")}
            want = hashlib.sha256(
                json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if row["prev_hash"] != prev_hash:
                return False, n, f"bản ghi #{row['id']}: prev_hash không khớp"
            if row["row_hash"] != want:
                return False, n, f"bản ghi #{row['id']}: row_hash không khớp"
            prev_hash = row["row_hash"]
        return True, n, f"chuoi lien mach, {n} record"
