"""Bo CHO dung chung - nhip thich ung, tran tinh theo DONG HO THAT.

Vi sao can mot cho rieng thay vi `for i in range(60): sleep(10)`:

1. NHIP CO DINH lam mat thoi gian vo ich. Buoc truoc xong o giay thu 2 thi
   voi nhip 10 giay, buoc sau van phai doi den giay thu 10. Tren mot quy
   trinh 23 buoc co 3-4 cho nhu the, cong lai thanh vai phut CUA SO BAO TRI
   bi tieu khong vi ly do gi. Ma cua so bao tri la luc Replicat dang DUNG -
   do la thoi gian dat nhat trong ca quy trinh.

2. TRAN TINH THEO SO VONG LA SAI. `range(60)` voi `sleep(10)` duoc goi la
   "10 phut", nhung moi vong con ton them thoi gian GOI `ggsci` (1-3 giay
   that do), nen tran that la 13-15 phut. Thong bao noi sai voi nguoi dung,
   va nguoi dung lay so do de quyet dinh cua so bao tri.

3. NHAN THOI GIAN `i*10s` CUNG SAI theo dung ly do tren.

Nhip thich ung giai quyet ca ba: hoi day o dau (bat duoc ngay khi xong),
thua dan ve sau (cho lau thi khong can hoi day), va moc thoi gian lay tu
`time.monotonic()` nen luon la thoi gian THAT.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

log = logging.getLogger("ggadmin.cho")

# (troi_qua_toi, nhip) - tra nhip dau tien co troi_qua < troi_qua_toi.
#
# Dai y: 1 giay trong 10 giay dau (phan lon cho ngan ket thuc o day), roi
# thua dan. Mot cho 20 phut chi ton khoang 90 lan hoi thay vi 120, ma van
# bat duoc trong 1 giay neu no xong som.
BAC = ((10.0, 1.0), (30.0, 2.0), (120.0, 5.0), (600.0, 10.0))
NHIP_TRAN = 20.0


def nhip_cua(troi_qua: float) -> float:
    """Nhip hoi tai thoi diem da cho doi `troi_qua` giay."""
    for moc, nhip in BAC:
        if troi_qua < moc:
            return nhip
    return NHIP_TRAN


def gio_gon(giay: float) -> str:
    """Mo ta thoi luong ngan gon, tieng Viet."""
    g = int(giay)
    if g < 60:
        return "%d giây" % g
    if g < 3600:
        return "%d phút %02d giây" % (g // 60, g % 60)
    return "%d giờ %02d phút" % (g // 3600, (g % 3600) // 60)


async def cho_den(
    do: Callable[[], Awaitable[tuple[bool, str]]],
    *,
    tran_s: float,
    nhan: str,
    ghi: list[str] | None = None,
    toi_da_dong: int = 14,
) -> dict:
    """Hoi `do()` den khi no bao XONG, hoac het `tran_s` theo dong ho that.

    `do()` tra `(xong, mo_ta)`. Moi lan hoi ghi mot dong `mo_ta` kem moc
    thoi gian THAT. Tra ve:
        {"xong": bool, "giay": float, "so_lan": int, "output": [...]}

    Khong raise khi het tran - de ben goi quyet dinh, vi moi buoc co cach
    noi loi khac nhau. Ngoai le tu `do()` thi KHONG bat: mot loi khi do
    trang thai la tin hieu that, khong duoc im lang nuot roi cho tiep.
    """
    t0 = time.monotonic()
    out: list[str] = ghi if ghi is not None else []
    lan = 0
    cuoi = ""
    while True:
        lan += 1
        troi = time.monotonic() - t0
        xong, mo_ta = await do()
        cuoi = mo_ta
        out.append("%s: %s" % (gio_gon(time.monotonic() - t0), mo_ta))
        if len(out) > toi_da_dong:
            # Giu dong DAU (moc xuat phat) va cac dong CUOI (dien bien gan
            # nhat). Khuc giua cua mot cho 20 phut khong ai doc.
            out[:] = out[:1] + ["..."] + out[-(toi_da_dong - 2):]
        if xong:
            giay = time.monotonic() - t0
            log.info("cho %s: xong sau %.1fs, %d lan hoi", nhan, giay, lan)
            return {"xong": True, "giay": giay, "so_lan": lan, "output": out,
                    "cuoi": cuoi}
        troi = time.monotonic() - t0
        if troi >= tran_s:
            log.warning("cho %s: HET TRAN sau %.1fs, %d lan hoi", nhan, troi,
                        lan)
            return {"xong": False, "giay": troi, "so_lan": lan, "output": out,
                    "cuoi": cuoi}
        # Khong ngu qua moc tran - ngu lo ra ngoai thi tran mat y nghia.
        await asyncio.sleep(min(nhip_cua(troi), tran_s - troi))
