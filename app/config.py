"""Cau hinh ggadmin.

Hai nguon tach biet, co y:
  conf/app.toml     - cau hinh khong bi mat, commit duoc
  conf/secrets.ini  - mat khau DB, chmod 0600, KHONG commit

Mat khau nap MOT LAN luc khoi dong vao bo nho, khong log, khong tra qua API,
khong ghi vao SQLite. Tu 2026-10-13 moi mat khau DB nam o secrets.ini vi
dirprm/*.prm da chuyen sang USERIDALIAS (0/7 file con mat khau dang ro).
"""

from __future__ import annotations

import configparser
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class DbCred:
    user: str
    dsn: str
    password: str = field(repr=False)  # repr=False: khong lot ra log/traceback

    def __str__(self) -> str:  # phong khi ai do f-string ca object
        return f"{self.user}@{self.dsn}"


@dataclass(frozen=True)
class Config:
    gg_home: Path
    oracle_home: Path
    oracle_sid: str
    tns_admin: Path
    host: str
    port: int
    poll_seconds: int
    data_dir: Path
    log_dir: Path
    scripts_dir: Path
    ggserr_log: Path
    # moc rong cua tung discard file: khac nhau theo DO DAI TEN FILE,
    # nen phai nho rieng tung cai (RPMNVODS 118 / RHT 113 / RDTHU 115)
    discard_baseline: dict[str, int]
    streams: dict[str, dict[str, str]]
    creds: dict[str, DbCred]
    retain_raw_days: int
    notify: dict          # khoi [notify] trong app.toml
    # Duong dan file cau hinh goc. Buoc DANG_KY_CAU_HINH cua quy trinh
    # LUONG_MOI ghi THEM khoi [streams.<ma>] vao dung file nay (co luu ban
    # cu, ghi nguyen tu, doc lai doi chieu) - xem app/toml_ghi.py.
    app_toml: Path
    # khoi [log_tran]: han muc (GB) tung nhom log + nguong_pct cho trang
    # Bieu do log. Trong = dung mac dinh trong app/logsize.py.
    log_tran: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ggadmin.db"

    def discard_path(self, replicat: str) -> Path:
        return self.gg_home / "dirrpt" / f"{replicat}.dsc"

    def trail_glob(self, prefix: str) -> str:
        return str(self.gg_home / "dirdat" / f"{prefix}*")

    def param_file(self, process: str) -> Path:
        return self.gg_home / "dirprm" / f"{process.lower()}.prm"

    def subprocess_env(self) -> dict[str, str]:
        """env DUNG RIENG cho ggsci/sqlplus.

        KHONG ke thua env cua tien trinh web. Hai ly do:
        - `ssh host 'cmd'` khong source .bash_profile nen ggsci khong tim
          duoc thu vien Oracle (cam bay da ghi trong runbook);
        - tien trinh web dung oracledb THIN mode nen co y chay voi
          LD_LIBRARY_PATH sach, khong tro vao $ORACLE_HOME/lib.
        """
        return {
            "ORACLE_HOME": str(self.oracle_home),
            "ORACLE_SID": self.oracle_sid,
            "TNS_ADMIN": str(self.tns_admin),
            "LD_LIBRARY_PATH": str(self.oracle_home / "lib"),
            "NLS_LANG": "AMERICAN_AMERICA.AL32UTF8",
            "PATH": f"{self.oracle_home / 'bin'}:/usr/bin:/bin",
            "HOME": os.environ.get("HOME", "/home/oracle"),
        }


def _read_secrets(path: Path) -> dict[str, DbCred]:
    if not path.exists():
        raise SystemExit(f"Không thấy {path}. Xem conf/secrets.ini.example")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise SystemExit(f"{path} có quyền {mode:o}, phải là 600. Chạy: chmod 600 {path}")
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    out: dict[str, DbCred] = {}
    for name in cp.sections():
        s = cp[name]
        # Nhan ca 'password' lan 'token': khoi [zalo_bot] dung khoa 'token'.
        secret = s.get("password") or s.get("token")
        if not secret:
            continue
        out[name] = DbCred(
            user=s.get("user", ""),
            dsn=s.get("dsn", ""),
            password=secret,
        )
    return out


def load(app_toml: Path | None = None, secrets_ini: Path | None = None) -> Config:
    app_toml = app_toml or ROOT / "conf" / "app.toml"
    secrets_ini = secrets_ini or ROOT / "conf" / "secrets.ini"
    with app_toml.open("rb") as fh:
        raw = tomllib.load(fh)

    paths = raw["paths"]
    srv = raw["server"]
    oracle = raw["oracle"]
    gg_home = Path(paths["gg_home"])

    return Config(
        app_toml=app_toml,
        gg_home=gg_home,
        oracle_home=Path(oracle["oracle_home"]),
        oracle_sid=oracle["oracle_sid"],
        tns_admin=Path(oracle.get("tns_admin") or Path(oracle["oracle_home"]) / "network" / "admin"),
        host=srv["host"],
        port=int(srv["port"]),
        poll_seconds=int(srv.get("poll_seconds", 30)),
        data_dir=Path(paths["data_dir"]),
        log_dir=Path(paths["log_dir"]),
        scripts_dir=Path(paths["scripts_dir"]),
        ggserr_log=gg_home / "ggserr.log",
        discard_baseline={k: int(v) for k, v in raw["discard_baseline"].items()},
        streams=raw["streams"],
        creds=_read_secrets(secrets_ini),
        retain_raw_days=int(raw.get("retention", {}).get("raw_days", 90)),
        notify=raw.get("notify", {}),
        log_tran=raw.get("log_tran", {}),
    )
