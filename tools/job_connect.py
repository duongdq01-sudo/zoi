#!/usr/bin/env python3
"""In cau `connect` + `@script` ra stdout de pipe vao `sqlplus -S /nolog`.

    job_connect.py <ten_khoi_secrets> <duong_dan_script.sql>

VI SAO TON TAI FILE NAY
`sqlplus user/pw@dsn @script` de mat khau lo ra trong `ps` cho MOI user tren
may. Viet mat khau vao mot file tam thi lai de no nam tren dia. Cach o day:
doc conf/secrets.ini (0600) roi in cau `connect` ra STDOUT, pipe thang vao
sqlplus. Mat khau chi ton tai trong mot pipe giua hai tien trinh.

KHONG in gi khac ra stdout: moi dong o stdout deu di vao sqlplus nhu lenh.
Thong bao loi di ra STDERR.
"""

from __future__ import annotations

import configparser
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) != 3:
        print(f"cách dùng: {sys.argv[0]} <khối> <script.sql>", file=sys.stderr)
        return 2
    khoi, sql = sys.argv[1], sys.argv[2]

    p = ROOT / "conf" / "secrets.ini"
    if not p.exists():
        print(f"không thấy {p}", file=sys.stderr)
        return 3
    mode = p.stat().st_mode & 0o777
    if mode & 0o077:
        print(f"{p} có quyền {mode:o}, phải là 600", file=sys.stderr)
        return 4

    cp = configparser.ConfigParser()
    cp.read(p, encoding="utf-8")
    if khoi not in cp:
        print(f"không có khối [{khoi}] trong secrets.ini", file=sys.stderr)
        return 5
    s = cp[khoi]
    user, pw, dsn = s.get("user", ""), s.get("password", ""), s.get("dsn", "")
    if not (user and pw and dsn):
        print(f"khối [{khoi}] thiếu user, password hoặc dsn", file=sys.stderr)
        return 6

    if not Path(sql).exists():
        print(f"không thấy script {sql}", file=sys.stderr)
        return 7

    # WHENEVER SQLERROR EXIT FAILURE de ma thoat cua sqlplus phan anh dung
    # ket qua - neu khong thi script loi van tra 0 va job bao SUCCEEDED.
    out = [
        "WHENEVER SQLERROR EXIT FAILURE",
        "WHENEVER OSERROR EXIT FAILURE",
        "SET ECHO ON",
        "SET SERVEROUTPUT ON SIZE UNLIMITED",
        f"connect {user}/{pw}@{dsn}",
        f"@{sql}",
        "EXIT",
    ]
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
