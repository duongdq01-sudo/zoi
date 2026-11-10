"""Danh dau "da xu ly" file discard tu trang Log.

VI SAO KHONG CAT/XOA FILE .dsc

Replicat dang giu file .dsc mo de ghi. Cat ngan file trong luc do: neu GG
khong mo file voi O_APPEND, lan ghi sau se nhay ve offset cu va de lai mot lo
NUL o dau file; neu cat nham luc GG dang ghi thi mat chinh bang chung can
doc. Va tool nay co nguyen tac khong ghi vao $GG_HOME.

Nen thao tac o day CHI ghi vao CSDL cua ggadmin mot moc: "da doc va xu ly
den byte N cua file co dong dau X". Dem dong la chi tinh phan sau byte N.
File GoldenGate giu nguyen tung byte. Ban sao noi dung luc danh dau duoc luu
o data/discard_luu/ de doi chieu ve sau.

Moc tu het hieu luc khi file bi tao moi (restart Replicat voi PURGE lam dong
dau doi) - khong bao gio giau mot file moi.

MUC AN TOAN
- Chi co lenh DDL bi bo -> L1 (khong mat dong du lieu nao).
- Co ban ghi DML bi bo  -> L2, go tay ten Replicat: danh dau la tat mot canh
  bao mat du lieu, nguoi bam phai chac da doi chieu/va xong.
"""

from __future__ import annotations

import shutil
import time

from .adapters import osfs


class DiscardError(ValueError):
    pass


def kiem_replicat(cfg, rep: str) -> str:
    rep = (rep or "").strip().upper()
    reps = {v["replicat"].upper() for v in cfg.streams.values()}
    if rep not in reps:
        raise DiscardError(f"{rep!r} không phải Replicat của luồng nào")
    return rep


def lay_ack(con, rep: str) -> dict | None:
    r = con.execute(
        "SELECT replicat, offset, dau, ts, username, reason, so_ddl, so_dml,"
        " ban_luu FROM discard_ack WHERE replicat=?", (rep,)).fetchone()
    return dict(r) if r else None


def xem_truoc(cfg, con, rep: str) -> dict:
    """Trang thai hien tai + cac khoi se duoc danh dau. Goi luc ARM."""
    rep = kiem_replicat(cfg, rep)
    ack = lay_ack(con, rep)
    info = osfs.discard_info(cfg, rep, ack)
    if info.get("exists") != 1:
        raise DiscardError(f"Không đọc được file {rep}.dsc")
    khoi = osfs.discard_khoi(cfg, rep, ack, toi_da=1000)
    van_ban = []
    for i, k in enumerate(khoi, 1):
        van_ban.append(f"--- khối {i}: {k['loai']} "
                       + ("(lệnh cấu trúc, không mất dòng dữ liệu)" if k["loai"] == "DDL"
                          else "(BẢN GHI DỮ LIỆU bị bỏ)"))
        van_ban.extend(k["dong"][:12])
        if len(k["dong"]) > 12:
            van_ban.append(f"... (+{len(k['dong']) - 12} dòng)")
    return {"rep": rep, "info": info, "khoi": khoi,
            "offset": int(info["bytes"]), "dau": info["dau"],
            "so_ddl": int(info["so_lenh_ddl"]), "so_dml": int(info["so_ban_ghi_dml"]),
            "van_ban": "\n".join(van_ban)}


def danh_dau(cfg, con, rep: str, *, offset: int, dau: str, who: str,
             reason: str, ddl: int = 0, dml: int = 0) -> dict:
    """Ghi moc da xu ly = `offset` (do LUC ARM, khong phai luc bam).

    Dung offset luc arm: ban ghi moi roi vao file trong 60 giay giua arm va
    fire VAN nam sau moc, van duoc dem - nguoi dung chi xac nhan dung nhung
    gi ho da nhin thay.
    """
    rep = kiem_replicat(cfg, rep)
    p = cfg.discard_path(rep)
    ack_cu = lay_ack(con, rep)
    info = osfs.discard_info(cfg, rep, ack_cu)
    if info.get("exists") != 1:
        raise DiscardError(f"Không đọc được file {rep}.dsc")
    if info["dau"] != dau or info["bytes"] < offset:
        raise DiscardError(
            f"File {rep}.dsc đã đổi từ lúc xem trước (bị tạo mới hoặc nhỏ đi). "
            "Không đánh dấu - mở lại trang Log để xem nội dung mới.")
    tam = dict(ack_cu or {})
    ts = int(time.time())
    thu_muc = cfg.data_dir / "discard_luu"
    thu_muc.mkdir(parents=True, exist_ok=True)
    ban_luu = thu_muc / f"{rep}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(ts))}.dsc"
    with p.open("rb") as src, ban_luu.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    con.execute(
        "INSERT INTO discard_ack(replicat,offset,dau,ts,username,reason,so_ddl,"
        "so_dml,ban_luu) VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(replicat) DO UPDATE SET offset=excluded.offset,"
        " dau=excluded.dau, ts=excluded.ts, username=excluded.username,"
        " reason=excluded.reason, so_ddl=excluded.so_ddl, so_dml=excluded.so_dml,"
        " ban_luu=excluded.ban_luu",
        (rep, int(offset), dau, ts, who, reason, ddl, dml, str(ban_luu)))
    return {"rep": rep, "offset": offset, "so_ddl": ddl, "so_dml": dml,
            "ban_luu": str(ban_luu), "moc_cu": tam.get("offset")}


def bo_danh_dau(cfg, con, rep: str) -> dict:
    rep = kiem_replicat(cfg, rep)
    ack = lay_ack(con, rep)
    if not ack:
        raise DiscardError(f"{rep}.dsc chưa có mốc đã xử lý nào")
    con.execute("DELETE FROM discard_ack WHERE replicat=?", (rep,))
    return {"rep": rep, "moc_cu": ack["offset"], "ban_luu": ack.get("ban_luu")}
