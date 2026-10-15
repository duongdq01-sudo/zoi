"""Khung an toan L0/L1/L2 - cong vao moi thao tac GHI cua Phase 2.

BA MUC, khong phai hai, vi muc giua la noi hau het thao tac that nam:

  L0  doc            info all, stats, verify LIVE, xem log        -> da login
  L1  hoan tac duoc  start/stop, add trandata, them dong TABLE    -> diff + ly do
  L2  khong hoan tac TRUNCATE+nap lai, bo HANDLECOLLISIONS,       -> toan bo
                     delete trandata, verify SNAPSHOT_SCN            nghi thuc

NGHI THUC L2 - nam yeu cau, thieu mot la khong mo duoc nut:
  1. GO TAY ten doi tuong (o nhap chan onpaste o phia UI)
  2. pre-flight PASS - FAIL thi nut khong mo duoc, KHONG co checkbox
     "toi hieu rui ro". Muon vuot thi lam tay theo runbook, co chu y.
  3. ly do bat buoc, >= 10 ky tu
  4. token het han sau 60 GIAY
  5. ghi audit TRUOC khi thuc thi

VI SAO TOKEN HET HAN NHANH
Khong phai de chong tan cong - ke tan cong da vao duoc UI thi co SSH roi.
Ma de chong CHINH NGUOI DUNG: mo tab xac nhan TRUNCATE luc 9h, di hop, 11h
quay lai bam tiep trong khi trang thai he thong da khac han. Token 60 giay
buoc pre-flight phai duoc do LAI ngay truoc khi chay.

VI SAO L1/L2 DEU CHI CAN ROLE OPERATOR
Ca 2-5 DBA dung cong cu nay deu co SSH va root tren server, tuc la deu lam
tay duoc moi thu cong cu lam. Dung role de chan ho se la nghi thuc rong.
Chot an toan that o day la NGHI THUC + AUDIT, khong phai phan quyen. ADMIN
chi giu nhung gi thuoc ve chinh cong cu: xem audit, quan ly tai khoan.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

L0, L1, L2 = "L0", "L1", "L2"

TOKEN_TTL = 60.0          # giay - xem ghi chu dau file
MIN_REASON = 10           # ky tu
MAX_PENDING = 64          # chong phinh bo nho neu ai do spam nut

# Role toi thieu cho tung muc. Xem ghi chu dau file ve ly do L1 = L2.
NEED_ROLE = {L0: "VIEWER", L1: "OPERATOR", L2: "OPERATOR"}


class Denied(RuntimeError):
    """Tu choi vi chinh sach - KHONG phai loi ky thuat.

    Moi lan raise deu phai keo theo mot dong audit SECURITY_BLOCK hoac
    DENIED o phia goi. Khong bao gio bo qua am tham.
    """


@dataclass
class Pending:
    """Mot thao tac da duoc nap dan, cho ban co.

    Luu ca ket qua pre-flight tai thoi diem nap: `fire()` se do LAI, va neu
    ket qua doi thi tu choi. Pre-flight do mot lan roi tin mai la cach de
    chay mot thao tac dua tren tinh trang dia cua 10 phut truoc.
    """
    token: str
    user: str
    action: str
    target: str
    level: str
    reason: str
    created: float
    expires: float
    preflight_ok: bool
    preflight_note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def con_lai_s(self) -> int:
        return max(0, int(self.expires - time.monotonic()))


class Guard:
    def __init__(self, cfg, con, collector, auth):
        self.cfg = cfg
        self.con = con
        self.collector = collector
        self.auth = auth
        self._pending: dict[str, Pending] = {}

    # ------------------------------------------------------------ chot cung
    def read_only_reason(self) -> str | None:
        """Chot an toan toan cuc. Tra ly do neu app dang read-only.

        Nguong nay la HANG SO trong collector.READ_ONLY_GB, co y khong doc
        tu DB: moi nguong khac deu cau hinh duoc tu UI, rieng cai nay thi
        khong. Mot chot an toan ma noi long duoc tu UI thi khong phai chot.
        """
        s = self.collector.snap
        if s.read_only:
            gb = (s.fs or {}).get("avail_gb")
            return (f"Công cụ đang ở chế độ chỉ đọc toàn cục: đĩa / chỉ còn {gb:.1f} GB. Mọi "
                    "thao tác ghi bị tắt cho đến khi đĩa thoáng lại." if gb is not None else
                    "Công cụ đang ở chế độ chỉ đọc toàn cục vì đĩa / xuống dưới ngưỡng.")
        if not s.ts:
            return "Chưa đo được lần nào - không biết trạng thái hệ thống."
        if s.age_s > self.cfg.poll_seconds * 4:
            return (f"Số liệu giám sát đã cũ {s.age_s}s - phần đo có vấn đề. Không chạy thao "
                    "tác ghi khi không biết trạng thái thật.")
        return None

    # --------------------------------------------------------------- kiem tra
    def require(self, user: dict | None, level: str, *, peer: str = "",
                action: str = "") -> None:
        """Cong vao MOI thao tac. Raise Denied neu khong duoc phep."""
        if user is None:
            raise Denied("Chưa đăng nhập")
        need = NEED_ROLE[level]
        if not self.auth.has_role(user, need):
            self._deny(user, peer, level, action, f"can role {need}")
            raise Denied(f"Thao tác mức {level} cần vai {need}")
        if level == L0:
            return
        if (why := self.read_only_reason()) is not None:
            self._deny(user, peer, level, action, why)
            raise Denied(why)

    @staticmethod
    def check_reason(reason: str) -> str:
        r = (reason or "").strip()
        if len(r) < MIN_REASON:
            raise Denied(f"Lý do là bắt buộc, ít nhất {MIN_REASON} ký tự (đang có {len(r)})")
        return r

    def _deny(self, user: dict | None, peer: str, level: str, action: str,
              why: str) -> None:
        self.auth.audit(user["username"] if user else "(khong ro)", peer,
                        "SECURITY_BLOCK", action or "DENIED", reason=why)

    # ------------------------------------------------------------------- nap
    def arm(self, user: dict, *, action: str, target: str, level: str,
            reason: str, preflight_ok: bool, preflight_note: str = "",
            peer: str = "", extra: dict | None = None) -> Pending:
        """Nap dan mot thao tac. Tra Pending co token song 60 giay.

        Ghi audit NGAY o buoc nay, truoc khi co bat cu thay doi nao - de du
        thao tac that bai hay nguoi dung bo giua duong thi van con dau vet
        la da co y dinh lam gi.
        """
        self.require(user, level, peer=peer, action=action)
        reason = self.check_reason(reason)
        if level == L2 and not preflight_ok:
            self._deny(user, peer, level, action,
                       f"pre-flight FAIL: {preflight_note}")
            raise Denied("Kiểm tra trước không đạt - nút không mở được. Không có đường vượt qua "
                         "từ giao diện.")

        self._purge()
        if len(self._pending) >= MAX_PENDING:
            raise Denied("Quá nhiều thao tác đang chờ xác nhận")

        now = time.monotonic()
        p = Pending(token=secrets.token_urlsafe(24), user=user["username"],
                    action=action, target=target, level=level, reason=reason,
                    created=now, expires=now + TOKEN_TTL,
                    preflight_ok=preflight_ok, preflight_note=preflight_note,
                    extra=extra or {})
        self._pending[p.token] = p
        self.auth.audit(p.user, peer, level, f"ARM_{action}", target=target,
                        reason=reason)
        return p

    def fire(self, token: str, user: dict, *, typed: str | None = None,
             preflight_now: bool | None = None, peer: str = "") -> Pending:
        """Ban co. Dung MOT LAN duy nhat - pop khoi dict ngay.

        `preflight_now` la ket qua do LAI ngay truoc khi chay. Doi so voi
        luc nap -> tu choi.
        """
        self._purge()
        p = self._pending.pop(token, None)
        if p is None:
            raise Denied("Lần xác nhận không hợp lệ hoặc đã hết hạn 60 giây. Làm lại từ đầu để "
                         "phần kiểm tra trước được đo lại.")
        if p.user != user["username"]:
            self._deny(user, peer, p.level, p.action,
                       f"lần xác nhận của {p.user} nhưng {user['username']} bấm")
            raise Denied("Lần xác nhận này không thuộc về tài khoản đang đăng nhập")
        if time.monotonic() > p.expires:
            raise Denied("Lần xác nhận đã hết hạn 60 giây")

        # Muc L2: phai go tay dung ten doi tuong.
        # `extra["go_tay"]` cho phep doi tuong go khac `target`: buoc wizard co
        # target dang "<rid>:<buoc>", go chuoi do thi vo nghia - phai go TEN
        # BANG, vi do moi la thu nguoi dung can doc lai mot lan nua.
        if p.level == L2:
            can_go = (p.extra.get("go_tay") or p.target or "").strip()
            if (typed or "").strip().upper() != can_go.upper():
                self._deny(user, peer, p.level, p.action,
                           f"gõ sai tên: {typed!r} khác {can_go!r}")
                raise Denied(f"Gõ sai tên. Phải gõ đúng: {can_go}")
            if preflight_now is False:
                self._deny(user, peer, p.level, p.action,
                           "trạng thái hệ thống đổi giữa lúc xác nhận")
                raise Denied("Phần kiểm tra trước không còn đạt - trạng thái hệ thống đã đổi trong "
                             "60 giây vừa rồi. Làm lại từ đầu.")

        if (why := self.read_only_reason()) is not None:
            self._deny(user, peer, p.level, p.action, why)
            raise Denied(why)
        return p

    # ------------------------------------------------------------------ phu
    def pending_of(self, token: str) -> Pending | None:
        self._purge()
        return self._pending.get(token)

    def _purge(self) -> None:
        now = time.monotonic()
        for t in [t for t, p in self._pending.items() if p.expires < now]:
            self._pending.pop(t, None)
