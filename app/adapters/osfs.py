"""Doc tang he dieu hanh: dia, trail, discard file, ggserr.log.

Tat ca chi DOC. Khong ghi gi vao $GG_HOME - sua trail la pha checkpoint.
"""

from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
from datetime import date
from pathlib import Path

# `QU_ERROR` CO CHUA chuoi `ERROR` -> grep ERROR thuan cho duong tinh gia.
# Phai loai no ra, dung nhu cam bay da ghi.
ABEND = re.compile(r"\b(ABEND|FATAL)\b")
QU_ERROR = re.compile(r"QU_ERROR", re.I)
# Chan mat khau lot vao log cua app, du *.prm da chuyen sang USERIDALIAS
REDACT = re.compile(r"(PASSWORD|IDENTIFIED\s+BY)(\s+)(\S+)", re.I)
# Dong "banner" cua chinh GoldenGate trong discard file - KHONG phai ban ghi
# bi bo. GG ghi mot dong khi Replicat START va mot dong nua khi no STOP.
# Khi ABEND, GG ghi them `Process Abending : <thoi diem>` - cung khong phai
# ban ghi bi bo (abend da co luat process_khong_running bao rieng). Khong
# nhan dong nay thi canh bao discard mo oan va KHONG BAO GIO dong: RLOG
# 2026-10-27 abend 24 lan vi loi COLMAP, file .dsc chi toan banner.
RE_BANNER = re.compile(
    r"Oracle GoldenGate .*process (started|stopped), group \w+"
    r"|^\s*Process Abending\s*:\s*\d{4}-\d\d-\d\d", re.I)


def redact(s: str) -> str:
    return REDACT.sub(r"\1\2***", s)


def df(path: str = "/") -> dict[str, float]:
    t, u, f = shutil.disk_usage(path)
    return {
        "total_bytes": float(t),
        "used_bytes": float(u),
        "avail_bytes": float(f),
        "avail_pct": round(f / t * 100, 2) if t else 0.0,
        "avail_gb": round(f / 1024**3, 1),
    }


def trail_info(cfg, prefix: str) -> dict[str, float | str]:
    """Tong dung luong trail + file moi nhat."""
    files = sorted(glob.glob(cfg.trail_glob(prefix)))
    if not files:
        return {"files": 0, "total_bytes": 0.0, "newest": "", "newest_bytes": 0.0}
    total = 0.0
    for f in files:
        try:
            total += os.path.getsize(f)
        except OSError:
            pass
    newest = files[-1]
    try:
        nb = float(os.path.getsize(newest))
    except OSError:
        nb = 0.0
    return {"files": len(files), "total_bytes": total,
            "newest": os.path.basename(newest), "newest_bytes": nb}


def discard_info(cfg, replicat: str) -> dict[str, float | int]:
    """Dem SO DONG KHONG PHAI BANNER. KHONG so kich thuoc voi moc rong.

    VI SAO KHONG DUNG KICH THUOC - loi da gap that 2026-10-14

    Ban dau ham nay so `os.path.getsize()` voi mot "moc rong" do san
    (118/113/115 byte, khac nhau theo do dai ten file). Moc do CHI dung khi
    Replicat chay LIEN TUC tu luc file duoc tao: khi Replicat STOP, GG ghi
    them mot dong banner `process stopped, group RHT : <thoi diem>` va file
    to len 94 byte MA KHONG CO BAN GHI NAO BI BO.

    Hau qua that: moi lan stop/start Replicat binh thuong deu lam canh bao
    `discard_lech_moc_rong` no, va pre-flight LOAD bi chan - no da chan oan
    dung buoc NAP cua wizard ADD_TABLE o lan chay dau tien. Mot canh bao no
    sai moi lan bao tri la cach chac chan nhat de DBA hoc cach bo qua no, ma
    day lai la canh bao DUY NHAT bat duoc viec Replicat bo ban ghi IM LANG.

    Cach dung: file discard RONG chi chua cac dong BANNER cua GG. Bat ky dong
    nao KHAC banner deu la dau hieu co chuyen. Dem DONG, khong dem BYTE.
    """
    base = cfg.discard_baseline.get(replicat, 0)
    p = cfg.discard_path(replicat)
    try:
        size = os.path.getsize(p)
    except OSError:
        return {"bytes": -1, "baseline": base, "delta_bytes": -1,
                "so_dong_la": -1, "exists": 0}
    la = 0
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip() and not RE_BANNER.search(line):
                    la += 1
    except OSError:
        la = -1
    return {"bytes": size, "baseline": base,
            # `delta_bytes` giu lai DE HIEN THI, khong con dung de bao dong.
            "delta_bytes": max(0, size - base),
            "so_dong_la": la, "exists": 1}


def discard_tail(cfg, replicat: str, lines: int = 40) -> list[str]:
    p = cfg.discard_path(replicat)
    if not p.exists():
        return []
    return [redact(l) for l in _tail(p, lines)]


def _tail(path: Path, n: int, max_bytes: int = 256 * 1024) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()          # bo dong bi cat doi
            data = fh.read()
    except OSError:
        return []
    out = data.decode("utf-8", errors="replace").splitlines()
    return out[-n:]


def ggserr_tail(cfg, lines: int = 120, only_errors: bool = False) -> list[str]:
    out = _tail(cfg.ggserr_log, lines if not only_errors else lines * 10)
    if only_errors:
        out = [l for l in out if ABEND.search(l) and not QU_ERROR.search(l)]
    return [redact(l) for l in out[-lines:]]


def abend_count_today(cfg) -> int:
    """Dem ABEND/FATAL cua HOM NAY, da bo QU_ERROR."""
    today = date.today().isoformat()
    n = 0
    for l in _tail(cfg.ggserr_log, 4000, max_bytes=4 * 1024 * 1024):
        if today in l and ABEND.search(l) and not QU_ERROR.search(l):
            n += 1
    return n


def param_plaintext_password_count(cfg) -> int:
    """Phai la 0 tu 2026-10-13 (da chuyen sang USERIDALIAS)."""
    n = 0
    for p in sorted((cfg.gg_home / "dirprm").glob("*.prm")):
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                u = line.upper().lstrip()
                if u.startswith("USERID ") and "PASSWORD" in u:
                    n += 1
                    break
        except OSError:
            pass
    return n


def credential_store_ok(cfg) -> bool:
    """cwallet.sso mat la CA 6 PROCESS khong start duoc - diem chet don le."""
    return (cfg.gg_home / "dircrd" / "cwallet.sso").exists()


async def a_snapshot(cfg) -> dict:
    """Chay cac phep doc filesystem trong thread de khong chan event loop."""
    def _work() -> dict:
        streams = cfg.streams
        return {
            "fs": df("/"),
            "trails": {k: trail_info(cfg, v["trail"]) for k, v in streams.items()},
            "discards": {v["replicat"]: discard_info(cfg, v["replicat"])
                         for v in streams.values()},
            "abend_today": abend_count_today(cfg),
            "plaintext_pw": param_plaintext_password_count(cfg),
            "credstore": credential_store_ok(cfg),
            "param_counts": {
                p: _count_kw(cfg.param_file(p), kw)
                for p, kw in (("etpmnv", "TABLE"), ("etht", "TABLE"),
                              ("etdthu", "TABLE"), ("rpmnvods", "MAP"),
                              ("rht", "MAP"), ("rdthu", "MAP"))
            },
        }
    return await asyncio.to_thread(_work)


def _count_kw(path: Path, kw: str) -> int:
    if not path.exists():
        return -1
    n = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.upper().startswith(kw + " "):
                n += 1
    except OSError:
        return -1
    return n
