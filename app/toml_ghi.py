"""Ghi them cau hinh vao conf/app.toml. CHI THEM, khong bao gio viet lai ca file.

VI SAO MODULE NAY TON TAI
Quy trinh LUONG_MOI tao ra mot Extract/Replicat/trail moi. Cong cu chi biet
mot luong khi no nam trong `[streams]` cua conf/app.toml. Truoc 2026-10-15
buoc cuoi chi IN RA khoi do va bat nguoi tu dan vao file tren server roi
`run.sh restart` - nghia la quy trinh "tu dong" van dut doan o dung cho cuoi.
Nguoi dung yeu cau dang ky tu dong het.

VI SAO CHI THEM VAN BAN, KHONG SERIALISE LAI
1. Day la cau hinh cua mot he dang chay. Ghi them van ban thi byte mo ta
   PM/HT/DT KHONG BI CHAM - va buoc doc lai chung minh duoc dieu do.
2. Python khong co bo ghi TOML trong thu vien chuan. Serialise lai bang tay
   la mat sach hon 30 dong chu thich van hanh trong file.

BA CHOT CUA MOI LAN GHI, khong bo cai nao:
  luu ban cu (co dong dau thoi gian)  ->  ghi NGUYEN TU (tmp + os.replace)
  ->  DOC LAI va doi chieu tung gia tri cu; lech mot chu thi tra file ve
      ban luu va raise.

CAI BAY DA THAY TRUOC KHI VIET: them mot khoa vao `[discard_baseline]` ma
append cuoi file thi khoa do roi vao BANG CUOI CUNG cua file (hien la
`[notify.zalo_bot]`), khong phai bang minh dinh. Phai chen ngay sau dong
tieu de. Xem `them_discard_baseline`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import time
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger("ggadmin.toml")

# Cac bang goc PHAI y nguyen sau moi lan ghi. Day la danh sach doi chieu
# cua buoc "doc lai": thieu mot ten o day la mot vung cau hinh khong ai
# kiem - nen khi them bang moi vao app.toml thi them ten vao day luon.
KHOI_PHAI_GIU = ("server", "paths", "oracle", "retention",
                 "discard_baseline", "notify")


class TomlGhiError(RuntimeError):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _doc(p: Path) -> dict[str, Any]:
    with p.open("rb") as fh:
        return tomllib.load(fh)


def khoi_streams(sid: str, extract: str, replicat: str, trail: str,
                 ten: str = "", *, ghi_chu: str = "") -> str:
    """Dung khoi [streams.<SID>] dang van ban.

    Thuan, test duoc, va CHINH NO la ban xem truoc hien tren khung xac nhan
    - nguoi doc dung thu se duoc ghi, khong phai mot ban mo ta gan dung.
    """
    sid = sid.strip().upper()
    ten = (ten or f"Luồng {sid}").replace('"', "'").strip()
    dong = [f"\n[streams.{sid}]"]
    if ghi_chu:
        dong.insert(0, f"\n# {ghi_chu}")
    dong += [f'extract  = "{extract.strip().upper()}"',
             f'replicat = "{replicat.strip().upper()}"',
             f'trail    = "{trail.strip().lower()}"',
             f'ten      = "{ten}"']
    return "\n".join(dong) + "\n"


def luu_ban_cu(path: Path) -> Path:
    """Luu ban cu co dong dau thoi gian. Tra duong dan ban luu."""
    bak = path.with_name(f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(path, bak)
    return bak


def _ghi_nguyen_tu(path: Path, noi_dung: str) -> None:
    """Ghi nguyen tu: tmp CUNG THU MUC -> fsync -> os.replace.

    Cung thu muc moi bao dam `os.replace` la mot thao tac nguyen tu cua he
    tap tin. Khong fsync thi mat dien giua chung co the de lai file rong.
    """
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(noi_dung)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            shutil.copymode(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _doi_chieu(cu: dict, moi: dict, *, sid: str | None = None,
               cho_doi_discard: bool = False) -> None:
    """Moi khoi goc phai y nguyen; moi luong CU phai y nguyen tung gia tri."""
    for khoi in KHOI_PHAI_GIU:
        if khoi == "discard_baseline" and cho_doi_discard:
            # Them mot khoa moi thi duoc, nhung khoa CU phai y nguyen.
            for k, v in (cu.get(khoi) or {}).items():
                if (moi.get(khoi) or {}).get(k) != v:
                    raise TomlGhiError(
                        f"[{khoi}] khoá {k} đã đổi giá trị ({v} -> "
                        f"{(moi.get(khoi) or {}).get(k)}) sau khi ghi")
            continue
        if cu.get(khoi) != moi.get(khoi):
            raise TomlGhiError(f"khối [{khoi}] đã đổi sau khi ghi - không được phép")
    for ten, gt in (cu.get("streams") or {}).items():
        if (moi.get("streams") or {}).get(ten) != gt:
            raise TomlGhiError(
                f"luồng ĐANG CHẠY {ten} đã đổi sau khi ghi: {gt} -> "
                f"{(moi.get('streams') or {}).get(ten)}")
    if sid and sid not in (moi.get("streams") or {}):
        raise TomlGhiError(f"ghi xong mà đọc lại không thấy [streams.{sid}]")


def them_khoi_streams(path: Path, sid: str, extract: str, replicat: str,
                      trail: str, ten: str = "") -> dict:
    """Them khoi [streams.<SID>] vao CUOI file.

    Tra {"da_co", "backup", "khoi", "sha_truoc", "sha_sau"}. `da_co=True`
    nghia la khoi da co san va TRUNG Y HET -> khong ghi gi (chay lai duoc
    sau khi app chet giua chung).
    """
    sid = sid.strip().upper()
    et, rep, tr = (extract.strip().upper(), replicat.strip().upper(),
                   trail.strip().lower())
    cu = _doc(path)
    co = (cu.get("streams") or {}).get(sid)
    if co is not None:
        if (str(co.get("extract", "")).upper() == et
                and str(co.get("replicat", "")).upper() == rep
                and str(co.get("trail", "")).lower() == tr):
            return {"da_co": True, "backup": None, "khoi": "",
                    "sha_truoc": _sha(path), "sha_sau": _sha(path)}
        raise TomlGhiError(
            f"[streams.{sid}] đã có trong {path.name} nhưng KHÁC: đang là {co}. "
            "Không ghi đè cấu hình của một luồng khác.")

    sha_truoc = _sha(path)
    goc = path.read_text(encoding="utf-8")
    bak = luu_ban_cu(path)
    khoi = khoi_streams(sid, et, rep, tr, ten)
    # Dong cuoi khong co ky tu xuong dong (ai do sua tay) thi khoi moi se bi
    # noi vao dong do. Chuan hoa truoc khi noi.
    than = goc if goc.endswith("\n") else goc + "\n"
    try:
        _ghi_nguyen_tu(path, than + khoi)
        _doi_chieu(cu, _doc(path), sid=sid)
    except Exception as e:
        os.replace(bak, path)
        raise TomlGhiError(
            f"ghi {path.name} không đạt ({e}) - đã trả file về bản cũ") from e
    return {"da_co": False, "backup": str(bak), "khoi": khoi,
            "sha_truoc": sha_truoc, "sha_sau": _sha(path)}


def them_discard_baseline(path: Path, replicat: str, so_byte: int) -> dict:
    """Chen `<REP> = <n>` NGAY SAU dong tieu de [discard_baseline].

    KHONG duoc append cuoi file: khoa se roi vao bang cuoi cung cua file
    (hien la [notify.zalo_bot]) va `cfg.discard_baseline` khong thay no ->
    moc rong = 0 -> canh bao discard GIA, vinh vien.
    """
    rep = replicat.strip().upper()
    cu = _doc(path)
    if rep in (cu.get("discard_baseline") or {}):
        return {"da_co": True, "backup": None}
    goc = path.read_text(encoding="utf-8")
    m = re.search(r"^\[discard_baseline\][ \t]*$", goc, re.M)
    if not m:
        raise TomlGhiError(
            f"không tìm thấy khối [discard_baseline] trong {path.name} - "
            "không chèn mò vào chỗ khác")
    bak = luu_ban_cu(path)
    cho = goc.index("\n", m.end()) + 1
    moi = goc[:cho] + f"{rep:<8} = {int(so_byte)}\n" + goc[cho:]
    try:
        _ghi_nguyen_tu(path, moi)
        doc = _doc(path)
        _doi_chieu(cu, doc, cho_doi_discard=True)
        if int((doc.get("discard_baseline") or {}).get(rep, -1)) != int(so_byte):
            raise TomlGhiError(f"đọc lại không thấy discard_baseline.{rep}")
    except Exception as e:
        os.replace(bak, path)
        raise TomlGhiError(
            f"ghi mốc discard cho {rep} không đạt ({e}) - đã trả file về bản cũ") from e
    return {"da_co": False, "backup": str(bak)}


def bo_khoi_streams(path: Path, sid: str) -> dict:
    """Xoa khoi [streams.<SID>] - dung cho duong don sach.

    Xoa tu dong tieu de den TRUOC tieu de ke tiep. Cung luu ban cu, ghi
    nguyen tu va doc lai nhu luc them.
    """
    sid = sid.strip().upper()
    cu = _doc(path)
    if sid not in (cu.get("streams") or {}):
        return {"da_bo": False, "backup": None}
    goc = path.read_text(encoding="utf-8")
    m = re.search(rf"^\[streams\.{re.escape(sid)}\][ \t]*$", goc, re.M)
    if not m:
        raise TomlGhiError(
            f"[streams.{sid}] có trong cấu hình đã đọc nhưng không tìm thấy dòng tiêu "
            f"đề trong {path.name} - không xoá mò")
    dau = m.start()
    ke = re.search(r"^\[", goc[m.end():], re.M)
    cuoi = m.end() + ke.start() if ke else len(goc)
    bak = luu_ban_cu(path)
    moi = (goc[:dau].rstrip("\n") + "\n") + goc[cuoi:]
    try:
        _ghi_nguyen_tu(path, moi)
        doc = _doc(path)
        if sid in (doc.get("streams") or {}):
            raise TomlGhiError(f"xoá xong mà đọc lại vẫn thấy [streams.{sid}]")
        con_lai = dict(cu.get("streams") or {})
        con_lai.pop(sid, None)
        _doi_chieu({**cu, "streams": con_lai}, doc)
    except Exception as e:
        os.replace(bak, path)
        raise TomlGhiError(
            f"gỡ [streams.{sid}] không đạt ({e}) - đã trả file về bản cũ") from e
    return {"da_bo": True, "backup": str(bak)}


def dang_ky_luong(cfg, sid: str, extract: str, replicat: str, trail: str,
                  ten: str = "") -> dict:
    """Ghi luong moi vao app.toml ROI nap nong vao cau hinh dang chay.

    Thu tu KHONG duoc dao: ghi dia truoc, doc lai doi chieu, CHI KHI DAT moi
    dung vao bo nho. Nguoc lai thi app se dieu khien mot luong ma file cau
    hinh khong co - khoi dong lai la mat.
    """
    from .adapters import gg

    sid = sid.strip().upper()
    et, rep, tr = (extract.strip().upper(), replicat.strip().upper(),
                   trail.strip().lower())
    path = Path(cfg.app_toml)

    for ten_cu, st in (cfg.streams or {}).items():
        if ten_cu.upper() == sid:
            continue
        if str(st.get("extract", "")).upper() == et:
            raise TomlGhiError(f"Extract {et} đã thuộc luồng {ten_cu}")
        if str(st.get("replicat", "")).upper() == rep:
            raise TomlGhiError(f"Replicat {rep} đã thuộc luồng {ten_cu}")
        if str(st.get("trail", "")).lower() == tr:
            raise TomlGhiError(f"tiền tố trail {tr!r} đã thuộc luồng {ten_cu}")

    r = them_khoi_streams(path, sid, et, rep, tr, ten)

    # Chi dung vao bo nho SAU khi dia da dat.
    cfg.streams[sid] = {"extract": et, "replicat": rep, "trail": tr,
                        "ten": ten or f"Luồng {sid}"}
    gg.dang_ky_process(et, rep)
    log.info("đã đăng ký luồng %s (%s/%s, trail %s) vào %s", sid, et, rep, tr, path)
    r["sid"] = sid
    return r


def bo_dang_ky_luong(cfg, sid: str) -> dict:
    """Nguoc cua `dang_ky_luong`, cho duong don sach."""
    sid = sid.strip().upper()
    r = bo_khoi_streams(Path(cfg.app_toml), sid)
    cfg.streams.pop(sid, None)
    log.info("đã gỡ luồng %s khỏi %s", sid, cfg.app_toml)
    return r
