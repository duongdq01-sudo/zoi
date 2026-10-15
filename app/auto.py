"""Thao tac nhanh - tu dong chay het mot quy trinh, dung mot lan xac nhan.

NO LAM GI
Nguoi dung go TEN BANG. Bo lai nay mo mot phien quy trinh roi di tung buoc
tu dau den cuoi: do pre-flight, chay buoc, cho viec nen, kiem chung, roi moi
sang buoc sau. Buoc nao KHONG DAT la DUNG NGAY tai do.

NO KHONG LAM GI - doc truoc khi sua file nay
  * KHONG bo mot lop kiem nao. Moi buoc van di qua DUNG `Wizard.chay_buoc`
    voi dieu kien vao cua engine, van do pre-flight truoc khi chay, van chay
    `_xn_<buoc>` de kiem chung tren DB sau viec nen. Thu tu buoc van la hang
    so trong code.
  * KHONG tu quyet cai gi can nguoi quyet. Bang khong co khoa chinh thi
    buoc 2 dung lai va doi nguoi nhap cot khoa - chon khoa la mot phan doan
    nghiep vu, khong phai viec cua may.
  * KHONG tu chay khi bang nguon va bang dich KHAC TEN (yeu cau cua nguoi
    dung), va khong tu chay khi luong dang co mot phien khac mo.

CAI DUOC DANH DOI, NOI RO
Khung an toan goc doi NGHI THUC L2 cho TUNG buoc khong hoan tac duoc: go tay
ten bang, ly do, token 60 giay. Che do nhanh thu gon thanh MOT nghi thuc L2
cho CA LUOT: go tay ten bang mot lan, mot ly do, mot token. Nghia la nguoi
dung khong doc lai man hinh truoc tung buoc L2 nua.
Cai KHONG mat: moi chot ky thuat van chay va van chan. Cai MAT: su chu y cua
con nguoi o tung buoc. Day la danh doi co y, do nguoi dung yeu cau, va duoc
ghi vet day du - moi buoc mot dong audit `AUTO_STEP`.

RIENG `RESYNC_TABLE` CO MOT CHOT KHONG THE BO
Buoc 1 `XAC_NHAN_LECH` chay doi chieu truoc va chi mo cac buoc sau khi bang
THUC SU lech. Nho no, mot cu bam khong the xoa sach mot bang dang dung dung.
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import cho

log = logging.getLogger("ggadmin.auto")

# Toi da cho mot viec nen (giay). Buoc nap mot bang lon co the lau; tran nay
# chi de khong treo mai neu viec nen chet ma khong ai cap nhat trang thai.
CHO_JOB_TOI_DA = 3 * 3600
# Tran tong thoi gian mot luot. CHO_BAT_KIP va CHO_LOW_WM da tu cho ben
# trong (10 va 20 phut), nen tran nay phai rong hon tong cac cho do.
TONG_TOI_DA = 6 * 3600


class AutoError(RuntimeError):
    pass


def kiem_dieu_kien(con, kind: str, bang: str) -> dict:
    """Kiem truoc khi mo phien. Tra dict thong tin, hoac raise AutoError.

    Lam o day chu khong trong bo lai: nguoi dung phai biet LY DO TU CHOI
    ngay luc bam, khong phai sau khi mo mot phien roi no chet o buoc 1.
    """
    r = con.execute(
        "SELECT src_owner, src_table, tgt_owner, tgt_table, stream,"
        " prepared, in_extract, has_map FROM managed_table WHERE src_table=?",
        (bang,)).fetchone()
    if r is None:
        raise AutoError(
            "%s không nằm trong danh sách quản lý. Đăng ký bảng trước (trang "
            "Quy trình), rồi quay lại đây. Công cụ không được chạm vào bảng "
            "ngoài phạm vi." % bang)

    # YEU CAU CUA NGUOI DUNG: che do nhanh chi ap cho bang nguon va dich
    # CUNG TEN. Khac ten thi anh xa cot co the lech theo kieu kho thay, nen
    # de nguoi dung di tung buoc va doc tung man hinh.
    if (r["tgt_table"] or "").upper() != (r["src_table"] or "").upper():
        raise AutoError(
            "Bảng nguồn %s và bảng đích %s KHÁC TÊN. Thao tác nhanh chỉ áp "
            "cho bảng cùng tên. Dùng quy trình từng bước ở trang Quy trình."
            % (r["src_table"], r["tgt_table"]))

    if kind == "ADD_TABLE":
        if r["has_map"] and r["in_extract"]:
            raise AutoError(
                "%s ĐÃ có cả dòng TABLE lẫn dòng MAP trong luồng. Nếu dữ liệu "
                "lệch thì dùng «Đồng bộ lại bảng», không dùng «Thêm bảng»."
                % bang)
    elif kind == "RESYNC_TABLE":
        if not r["has_map"]:
            raise AutoError(
                "%s chưa có dòng MAP trong luồng nào, tức là chưa được đồng "
                "bộ. Dùng «Thêm bảng vào luồng» trước." % bang)
    else:
        raise AutoError("chế độ nhanh chỉ hỗ trợ thêm bảng và đồng bộ lại")

    return dict(r)


class TuDong:
    """Bo lai: chay het mot phien quy trinh, dung lai ngay khi khong dat."""

    def __init__(self, cfg, con, wizard, pre, auth, pf_cua):
        self.cfg = cfg
        self.con = con
        self.wizard = wizard
        self.pre = pre
        self.auth = auth
        self._pf_cua = pf_cua          # ham cua main.py: op -> (pf_op, target)
        self.dang_chay: set[int] = set()

    # --------------------------------------------------------------- mo phien
    def mo_phien(self, kind: str, bang: str, *, stream: str, who: str) -> int:
        """Mo phien quy trinh. Raise AutoError neu khong du dieu kien."""
        r = kiem_dieu_kien(self.con, kind, bang)
        lu = (r["stream"] or stream or "").strip()
        if not lu:
            raise AutoError(
                "%s chưa gắn luồng nào trong danh sách quản lý, nên công cụ "
                "không biết đưa vào Extract/Replicat nào. Chọn luồng ở ô bên "
                "cạnh. Công cụ CỐ Ý không tự đoán luồng: đoán sai là thêm "
                "bảng vào sai Extract." % bang)
        try:
            return self.wizard.tao(kind, stream=lu, src_table=bang, who=who)
        except Exception as e:                                # noqa: BLE001
            raise AutoError(str(e)) from e

    # ----------------------------------------------------------------- bo lai
    async def chay(self, rid: int, *, who: str, ly_do: str) -> None:
        """Chay het phien `rid`. Goi trong task nen, khong trong request."""
        if rid in self.dang_chay:
            return
        self.dang_chay.add(rid)
        t0 = time.monotonic()
        try:
            await self._chay(rid, who, ly_do, t0)
        except Exception as e:                                # noqa: BLE001
            log.exception("thao tác nhanh #%s lỗi", rid)
            self._ghi_loi(rid, "lỗi không lường trước: %s" % e)
        finally:
            self.dang_chay.discard(rid)

    async def _chay(self, rid: int, who: str, ly_do: str, t0: float) -> None:
        from .wizard import BO_BUOC, buoc_cua

        run = self.wizard.lay(rid)
        tong = max(x.no for x in BO_BUOC[run["kind"]])
        log.info("thao tác nhanh #%s %s trên %s: bắt đầu",
                 rid, run["kind"], run["src_table"])

        while True:
            if time.monotonic() - t0 > TONG_TOI_DA:
                self._ghi_loi(rid, "vượt trần %d giờ cho một lượt - dừng lại. "
                                   "Mở trang quy trình để đi tiếp bằng tay."
                              % (TONG_TOI_DA // 3600))
                return

            run = self.wizard.lay(rid)
            if run["state"] != "OPEN":
                log.info("thao tác nhanh #%s: phiên đã %s", rid, run["state"])
                return
            no = run["cur_step"]
            st = buoc_cua(run["kind"], no)

            ok_vao, vi_sao = self.wizard.dieu_kien_vao(rid, no)
            if not ok_vao:
                self._ghi_loi(rid, "bước %d (%s) chưa đủ điều kiện: %s"
                              % (no, st.nhan, vi_sao))
                return

            # PRE-FLIGHT DO LAI TRUOC TUNG BUOC, y nhu duong bam tay. Khong
            # dat la DUNG - khong co duong vuot qua trong che do nhanh.
            pf_op, pf_target = self._pf_cua("WIZARD_STEP", "%d:%d" % (rid, no))
            if pf_op and pf_op != "KHONG_CO":
                pf = await self.pre.run(pf_op, pf_target)
                if not pf.ok:
                    self._ghi_loi(
                        rid, "bước %d (%s): kiểm tra trước KHÔNG đạt - %s"
                        % (no, st.nhan, pf.note))
                    return

            # LUU LENH SE CHAY vao `wizard_step.preview` TRUOC khi chay.
            #
            # Duong bam tay hien ban xem truoc ngay tren man hinh, nguoi dung
            # doc roi moi bam. Che do nhanh khong co ai doc, nen neu khong
            # luu thi khong con dau vet nao ve LENH da chay - chi con ket
            # qua. Luu lai de o kha nang theo doi va truy nguoc ve sau con
            # thay duoc "no da chay cai gi", khong chi "no bao gi".
            try:
                xt = await self.wizard.xem_truoc(rid, no)
            except Exception as e:                            # noqa: BLE001
                xt = "(không dựng được bản xem trước: %s)" % e
            self.con.execute(
                "UPDATE wizard_step SET preview=? WHERE run_id=? AND step_no=?",
                ((xt or "")[:20_000], rid, no))

            # Chay buoc. Moi buoc mot dong audit rieng.
            tt0 = time.monotonic()
            try:
                r = await self.wizard.chay_buoc(
                    rid, no, who=who,
                    reason="[thao tác nhanh] " + ly_do, tham_so={"who": who})
            except Exception as e:                            # noqa: BLE001
                self._audit(who, st, rid, no, False, str(e))
                self._ghi_loi(rid, "bước %d (%s) lỗi: %s"
                              % (no, st.nhan, str(e).splitlines()[0]))
                return
            self._audit(who, st, rid, no, True,
                        r.get("msg", ""), int((time.monotonic() - tt0) * 1000))

            # Buoc cho viec nen: hoi den khi xong, roi kiem chung tren DB.
            if r.get("cho_job"):
                xong = await self._cho_job(rid, no, st)
                if not xong:
                    return
                continue                      # kiem_job da day cur_step

            if no >= tong:
                log.info("thao tác nhanh #%s: HOÀN TẤT %d/%d bước",
                         rid, tong, tong)
                return

    async def _cho_job(self, rid: int, no: int, st) -> bool:
        """Hoi trang thai viec nen den khi PASS/FAIL. True = da PASS.

        Nhip THICH UNG (`app/cho.py`): hoi moi giay trong 10 giay dau, thua
        dan ve sau. Buoc nap mot bang nho xong trong 2-3 giay; voi nhip co
        dinh 5 giay thi buoc sau phai doi vo ich, ma day la luc Replicat
        dang DUNG - thoi gian dat nhat cua ca quy trinh.
        """
        hong: dict = {}

        async def do() -> tuple[bool, str]:
            try:
                r = await self.wizard.kiem_job(rid, no)
            except Exception as e:                            # noqa: BLE001
                hong["msg"] = "lỗi khi kiểm việc chạy: %s" % e
                return True, str(hong["msg"])
            tt = r.get("state")
            if tt == "PASS":
                return True, "việc chạy nền đạt"
            if tt == "FAIL":
                j = r.get("job") or {}
                hong["msg"] = "việc chạy nền KHÔNG đạt - %s" % (
                    (j.get("err_msg") or "; ".join(
                        r.get("ghi_chu") or []))[:300])
                return True, str(hong["msg"])
            if tt == "KHONG_CO_JOB":
                hong["msg"] = "mất dấu việc chạy nền"
                return True, "mất dấu việc chạy nền"
            j = r.get("job") or {}
            return False, "việc nền #%s %s" % (j.get("id", "?"),
                                               j.get("state", tt or "?"))

        r = await cho.cho_den(do, tran_s=CHO_JOB_TOI_DA,
                              nhan="việc nền bước %d" % no)
        if not r["xong"]:
            self._ghi_loi(rid, "bước %d (%s): việc chạy nền quá %s chưa kết "
                               "thúc - dừng lại." % (no, st.nhan,
                                                     cho.gio_gon(r["giay"])))
            return False
        if hong:
            self._ghi_loi(rid, "bước %d (%s): %s"
                          % (no, st.nhan, hong["msg"]))
            return False
        return True

    # ----------------------------------------------------------------- phu tro
    def _ghi_loi(self, rid: int, msg: str) -> None:
        """Ghi ly do dung vao `note` cua phien, de UI hien duoc.

        KHONG dong phien va KHONG hoan tac gi: cac buoc da PASS van co hieu
        luc that tren he thong. Nguoi dung mo trang quy trinh di tiep bang
        tay tu dung buoc dang dung.
        """
        log.warning("thao tác nhanh #%s dừng: %s", rid, msg)
        self.con.execute(
            "UPDATE wizard_run SET note=?, updated_at=? WHERE id=?",
            ("[thao tác nhanh dừng] " + msg, int(time.time()), rid))

    def _audit(self, who, st, rid, no, ok, msg, ms=None) -> None:
        self.auth.audit(
            who, "auto", st.level, "AUTO_STEP",
            target="%d:%d" % (rid, no),
            reason="%s - %s" % (st.name, (msg or "")[:160]),
            exit_code=0 if ok else 1, duration_ms=ms)
