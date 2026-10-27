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
    buoc xac nhan cot khoa dung lai doi nguoi nhap cot khoa - chon khoa la mot phan doan
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
from .adapters.ora import SCHEMA_SOURCE, SCHEMA_TARGET, check_ident

# Ba loai quy trinh cua LUONG LOG (giu dong bo voi wizard.KIND_LOG; khong
# import tu wizard de auto.py khong keo theo ca engine).
KIND_LOG = ("LUONG_LOG", "THEM_LOG", "GO_LOG")

log = logging.getLogger("ggadmin.auto")

# Toi da cho mot viec nen (giay). Buoc nap mot bang lon co the lau; tran nay
# chi de khong treo mai neu viec nen chet ma khong ai cap nhat trang thai.
CHO_JOB_TOI_DA = 3 * 3600
# Tran tong thoi gian mot luot. CHO_BAT_KIP va CHO_LOW_WM da tu cho ben
# trong (10 va 20 phut), nen tran nay phai rong hon tong cac cho do.
TONG_TOI_DA = 6 * 3600
# LUONG_MOI dai hon han: 30 buoc, co cho Extract bat kip (toi da 20 phut),
# cho Low WM, va mot dot nap day du.
TONG_TOI_DA_LUONG_MOI = 10 * 3600


class AutoError(RuntimeError):
    pass


def kiem_dieu_kien(con, kind: str, bang: str) -> dict:
    """Kiem truoc khi mo phien. Tra dict thong tin, hoac raise AutoError.

    Lam o day chu khong trong bo lai: nguoi dung phai biet LY DO TU CHOI
    ngay luc bam, khong phai sau khi mo mot phien roi no chet o buoc 1.
    """
    la_log = kind in KIND_LOG
    bang_qly = "managed_log" if la_log else "managed_table"
    r = con.execute(
        "SELECT src_owner, src_table, tgt_owner, tgt_table, stream,"
        f" prepared, in_extract, has_map FROM {bang_qly} WHERE src_table=?",
        (bang,)).fetchone()
    if r is None and kind in ("LUONG_LOG", "THEM_LOG"):
        # Quy trinh log tu dang ky o buoc dau, giong LUONG_MOI / ADD_TABLE.
        check_ident(bang)
        if len(bang) > 26:
            raise AutoError("Tên bảng log %s_LOG vượt 30 ký tự." % bang)
        r = {"src_owner": SCHEMA_SOURCE, "src_table": bang,
             "tgt_owner": SCHEMA_TARGET, "tgt_table": bang + "_LOG",
             "stream": None, "prepared": 0, "in_extract": 0, "has_map": 0}
    elif r is None and kind in ("LUONG_MOI", "ADD_TABLE"):
        # Quy trinh tao luong moi TU DANG KY bang o buoc 2, nen o day khong
        # doi bang phai co san trong danh sach quan ly. Doi la mot vong
        # khong the di: o dang ky bang bat chon mot luong DA CO trong
        # conf/app.toml, ma luong nay chi ton tai tu buoc 15.
        #
        # Khong phai bo chot: buoc 2 kiem bang co THAT o nguon KT_BSH truoc
        # khi ghi dong nao, va moi buoc cham vao GoldenGate deu o SAU no.
        # Van kiem cu phap ten NGAY O DAY de mot chuoi rac khong di xa hon.
        check_ident(bang)
        r = {"src_owner": SCHEMA_SOURCE, "src_table": bang,
             "tgt_owner": SCHEMA_TARGET, "tgt_table": bang, "stream": None,
             "prepared": 0, "in_extract": 0, "has_map": 0}
    elif r is None:
        raise AutoError(
            "%s không nằm trong danh sách %s. Công cụ không được chạm vào bảng "
            "ngoài phạm vi." % (bang, "log" if la_log else
                                "quản lý. Đăng ký bảng trước (trang Quy trình), rồi "
                                "quay lại đây"))

    # YEU CAU CUA NGUOI DUNG: che do nhanh chi ap cho bang nguon va dich
    # CUNG TEN. Khac ten thi anh xa cot co the lech theo kieu kho thay, nen
    # de nguoi dung di tung buoc va doc tung man hinh.
    # Gỡ bảng không nạp dữ liệu nên luật cùng tên (chống lệch ánh xạ cột khi
    # nạp) không áp cho nó.
    if kind not in ("GO_BANG",) + KIND_LOG and \
            (r["tgt_table"] or "").upper() != (r["src_table"] or "").upper():
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
    elif kind == "LUONG_MOI":
        # Bang dau tien cua mot luong CHUA TON TAI: phai chua thuoc luong
        # nao ca. Ba dieu kien, khong gop lam mot vi ly do tu choi khac nhau
        # thi viec phai lam cung khac nhau.
        if r["has_map"]:
            raise AutoError(
                "%s đã có dòng MAP trong một luồng. Bảng đầu tiên của luồng mới "
                "phải chưa thuộc luồng nào." % bang)
        if r["in_extract"]:
            raise AutoError(
                "%s đã có dòng TABLE trong một Extract đang chạy. Gỡ khỏi luồng cũ "
                "trước, hoặc chọn bảng khác làm bảng đầu tiên." % bang)
        if r["stream"]:
            raise AutoError(
                "Danh sách quản lý ghi %s thuộc luồng %s. Bảng đầu tiên của luồng "
                "mới phải chưa gắn luồng nào." % (bang, r["stream"]))
        dang_mo = con.execute(
            "SELECT id, src_table FROM wizard_run"
            " WHERE kind IN ('NEW_STREAM','LUONG_MOI') AND state='OPEN'").fetchone()
        if dang_mo:
            raise AutoError(
                "Đang có phiên tạo luồng mới #%s mở (trên %s). Tối đa MỘT phiên "
                "loại này cùng lúc - nó chạm vào DB nguồn production."
                % (dang_mo["id"], dang_mo["src_table"]))
    elif kind == "LUONG_LOG":
        if r["has_map"] or r["in_extract"] or r["stream"]:
            raise AutoError(
                "%s đã thuộc luồng log %s. Bảng đầu tiên của luồng log mới phải chưa "
                "thuộc luồng log nào." % (bang, r["stream"] or "?"))
        dang_mo = con.execute(
            "SELECT id, src_table FROM wizard_run WHERE kind IN"
            " ('NEW_STREAM','LUONG_MOI','LUONG_LOG') AND state='OPEN'").fetchone()
        if dang_mo:
            raise AutoError(
                "Đang có phiên tạo luồng mới #%s mở (trên %s). Tối đa MỘT phiên loại "
                "này cùng lúc - nó chạm vào DB nguồn production."
                % (dang_mo["id"], dang_mo["src_table"]))
    elif kind == "THEM_LOG":
        if r["has_map"] and r["in_extract"]:
            raise AutoError("%s ĐÃ ghi log ở luồng %s (đủ TABLE và MAP)." % (bang, r["stream"]))
    elif kind in ("GO_BANG", "GO_LOG"):
        # Kiem tren danh sach quan ly NGAY LUC BAM; buoc 1 cua quy trinh kiem
        # lai tren file tham so THAT (danh sach quan ly co the cham hon).
        if not r["stream"] or not (r["in_extract"] or r["has_map"]):
            raise AutoError(
                "%s không có dòng TABLE/MAP trong luồng nào (theo danh sách quản "
                "lý) - không có gì để gỡ." % bang)
        n_tab = con.execute(
            f"SELECT COUNT(*) FROM {bang_qly} WHERE stream=? AND in_extract=1",
            (r["stream"],)).fetchone()[0]
        if r["in_extract"] and n_tab <= 1:
            raise AutoError(
                "%s là bảng CUỐI CÙNG của luồng %s. Bỏ nó thì Extract không còn bảng "
                "nào - đó là gỡ cả luồng, không phải gỡ một bảng." % (bang, r["stream"]))
        dang_mo = con.execute(
            "SELECT id, kind, src_table FROM wizard_run WHERE stream=? AND state='OPEN'",
            (r["stream"],)).fetchone()
        if dang_mo:
            raise AutoError(
                "Luồng %s đang có quy trình #%s (%s trên %s) mở. Một luồng chỉ một quy "
                "trình cùng lúc." % (r["stream"], dang_mo["id"], dang_mo["kind"],
                                    dang_mo["src_table"]))
    else:
        # NEW_STREAM (ban thu cong 13 buoc) CO Y khong phai che do nhanh:
        # ban tron bo la LUONG_MOI.
        raise AutoError("chế độ nhanh chỉ hỗ trợ thêm/gỡ bảng, đồng bộ lại, tạo luồng "
                        "mới và các quy trình luồng log - không có %s" % kind)

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
    def mo_phien(self, kind: str, bang: str, *, stream: str, who: str,
                 keycols: str = "", ten_moi: dict | None = None) -> int:
        """Mo phien quy trinh. Raise AutoError neu khong du dieu kien."""
        r = kiem_dieu_kien(self.con, kind, bang)
        if kind in ("LUONG_MOI", "LUONG_LOG"):
            # Luong CHUA ton tai -> khong doi `stream`, va `Wizard.tao` ep
            # `stream=None`. Ten cua 4 doi tuong moi dat truoc o day de buoc
            # KIEM_TEN khong phai dung lai doi nguoi go.
            try:
                rid = self.wizard.tao(kind, stream=None, src_table=bang, who=who)
            except Exception as e:                            # noqa: BLE001
                raise AutoError(str(e)) from e
            self.dat_ten_moi(rid, ten_moi or {})
            if keycols.strip():
                self.dat_keycols(rid, keycols)
            return rid
        lu = (r["stream"] or stream or "").strip()
        if not lu:
            raise AutoError(
                "%s chưa gắn luồng nào trong danh sách quản lý, nên công cụ "
                "không biết đưa vào Extract/Replicat nào. Chọn luồng ở ô bên "
                "cạnh. Công cụ CỐ Ý không tự đoán luồng: đoán sai là thêm "
                "bảng vào sai Extract." % bang)
        try:
            rid = self.wizard.tao(kind, stream=lu, src_table=bang, who=who)
        except Exception as e:                                # noqa: BLE001
            raise AutoError(str(e)) from e
        if keycols.strip():
            self.dat_keycols(rid, keycols)
        return rid

    def dat_keycols(self, rid: int, keycols: str) -> None:
        """Dat truoc KEYCOLS cho phien, de buoc xac nhan cot khoa khong phai dung lai doi nguoi.

        KHONG phai duong vong qua kiem chung. Buoc XAC_NHAN_KEYCOLS van chay
        day du: cot co that o nguon khong, co DUY NHAT tren DU LIEU THAT
        khong, va tung cot khoa co bao nhieu dong NULL. Dat truoc chi thay
        cho viec go vao o cua buoc do - cot khoa sai thi buoc do van chan.
        """
        kc = ", ".join(c.strip().upper() for c in keycols.split(",") if c.strip())
        self.con.execute(
            "UPDATE wizard_run SET keycols=?, updated_at=? WHERE id=?",
            (kc, int(time.time()), rid))

    def dat_ten_moi(self, rid: int, ten: dict) -> None:
        """Dat truoc 4 ten + ma luong cho phien LUONG_MOI.

        KHONG phai duong vong qua kiem chung - giong het `dat_keycols`.
        Buoc KIEM_TEN van chay day du va chay TAI THOI DIEM DO: dinh dang
        ten, danh sach bat kha xam pham, trung voi `info all`, dirprm/<ten>
        .prm chua ton tai, dirdat/<tien to>* chua co file. Dat truoc chi
        thay cho viec go vao o cua buoc do.

        Kiem ngay o day mot lan nua la de nguoi dung biet LY DO TU CHOI tu
        luc bam nut, khong phai sau khi da mo mot phien roi no chet.
        """
        import json

        from .adapters import gg

        et = gg.kiem_ten_moi(ten.get("extract", ""), "extract")
        rep = gg.kiem_ten_moi(ten.get("replicat", ""), "replicat")
        pre = gg.kiem_ten_moi(ten.get("trail_prefix", ""), "trail")
        if et == rep:
            raise AutoError("Tên Extract và Replicat không được giống nhau")
        ck = (ten.get("ckpt_table") or f"OGG_BSH.{rep}").strip().upper()
        if "." not in ck:
            ck = f"OGG_BSH.{ck}"
        gg.kiem_ten_moi(ck.split(".")[-1], "checkpointtable")
        ctx = {"luong_moi": {"sid": (ten.get("stream_id") or pre).strip().upper(),
                             "ten": (ten.get("stream_ten") or "").strip()}}
        self.con.execute(
            "UPDATE wizard_run SET created_extract=?, created_replicat=?,"
            " created_trail_prefix=?, created_ckpt_table=?, ctx_json=?,"
            " updated_at=? WHERE id=?",
            (et, rep, pre, ck, json.dumps(ctx, ensure_ascii=False),
             int(time.time()), rid))

    def kiem_tiep(self, rid: int) -> dict:
        """Kiem truoc khi chay tiep mot phien dang dung. Tra run, hoac raise.

        Chay tiep KHONG phai chay lai: bo lai doc `cur_step` cua phien va di
        tu dung buoc dang dung, cac buoc da PASS khong dung lai lan nua.
        """
        try:
            run = self.wizard.lay(rid)
        except Exception as e:                                # noqa: BLE001
            raise AutoError(str(e)) from e
        if run["state"] != "OPEN":
            raise AutoError(
                "Phiên #%s đã %s nên không chạy tiếp được." % (rid, run["state"]))
        if rid in self.dang_chay:
            raise AutoError(
                "Phiên #%s đang chạy tự động. Đợi nó dừng rồi hẵng mở lượt "
                "mới - hai bộ lái cùng đi trên một phiên là chồng bước." % rid)
        return run

    # ----------------------------------------------------------------- bo lai
    async def chay(self, rid: int, *, who: str, ly_do: str) -> None:
        """Chay het phien `rid`. Goi trong task nen, khong trong request."""
        if rid in self.dang_chay:
            return
        self.dang_chay.add(rid)
        self.con.execute(
            "UPDATE wizard_run SET note=NULL, updated_at=? WHERE id=?",
            (int(time.time()), rid))
        t0 = time.monotonic()
        try:
            await self._chay(rid, who, ly_do, t0)
        except Exception as e:                                # noqa: BLE001
            log.exception("thao tác nhanh #%s lỗi", rid)
            self._ghi_loi(rid, "lỗi không lường trước: %s" % e)
        finally:
            self.dang_chay.discard(rid)
        await self._don_neu_hong(rid, who)

    # ------------------------------------------------- tu don khi hong
    async def _don_neu_hong(self, rid: int, who: str) -> None:
        """LUONG_MOI hong giua chung -> TU DON SACH ngay.

        YEU CAU CUA NGUOI DUNG 2026-10-15: "hỏng đâu dọn đó". Ly do chinh
        dang: tu buoc REGISTER_EXTRACT, phien da dang ky mot tien trinh doc
        redo vao DB NGUON production; de lai la no giu archive log va dao
        redo vo ich MAI MAI.

        CAI DANH DOI, noi ro vi no co that: lenh don chay
        `UNREGISTER EXTRACT ... DATABASE` len chinh DB nguon - nguy hiem hon
        chinh buoc vua hong, va khong hoan tac duoc. No cung xoa mat hien
        truong o GoldenGate de chan doan. Vi vay:
          1. HIEN TRUONG trong DB cua cong cu duoc giu NGUYEN truoc khi don:
             `note` ly do dung, `wizard_step.preview` va `output` cua buoc
             hong khong bi dong vao.
          2. Ghi mot dong audit AUTO_ROLLBACK noi ro don vi buoc nao.
          3. KHONG BAO GIO tu don lan hai: don ma loi thi dung han, bao do,
             de nguoi doc 2 cau SQL kiem tay.
        """
        try:
            run = self.wizard.lay(rid)
        except Exception:                                     # noqa: BLE001
            return
        if run["kind"] not in ("LUONG_MOI", "LUONG_LOG") or run["state"] != "OPEN":
            return
        note = run["note"] or ""
        if not note.startswith("[thao tác nhanh dừng]"):
            return                      # khong phai dung vi loi
        if "tự dọn" in note:
            return                      # da tu don mot lan roi - khong lam lai
        if not run["created_extract"]:
            return                      # chua chot ten -> chua tao gi de don

        ly_do = "tự dọn sau khi %s" % note[len("[thao tác nhanh dừng] "):][:200]
        log.warning("thao tác nhanh #%s: TU DON SACH - %s", rid, ly_do)

        # DANH DAU "DANG DON" TRUOC KHI DON, khong phai sau.
        #
        # Su co 2026-10-20: ggadmin bi khoi dong lai trong luc dang don;
        # tac vu chet giua duong va KHONG con ai ghi ket qua, nen man hinh
        # hien y nguyen "[thao tác nhanh dừng] bước 24..." - nhin vao thi
        # tuong luot chay chi dung lai, trong khi thuc te cau hinh da bi go
        # va mot Extract da dang ky vao DB nguon van con. Dau nay ton tai
        # de cho truong hop do: con thay "[đang tự dọn]" tuc la don CHUA
        # chac xong, phai kiem tay.
        #
        # Cung la chot "khong bao gio tu don lan hai": dau co chua chu
        # "tự dọn" nen lan sau ham nay thoat ngay o kiem tra ben tren.
        self.con.execute(
            "UPDATE wizard_run SET note=?, updated_at=? WHERE id=?",
            ("[đang tự dọn - nếu dấu này còn thì dọn CHƯA xong, kiểm tay] "
             + note[len("[thao tác nhanh dừng] "):][:250],
             int(time.time()), rid))
        try:
            self.auth.audit(who, "auto", "L2", "AUTO_ROLLBACK",
                            target="%d:%s" % (rid, run["created_extract"]),
                            reason=ly_do)
        except Exception:                                     # noqa: BLE001
            log.exception("khong ghi duoc audit AUTO_ROLLBACK #%s", rid)
        try:
            r = await self.wizard.rollback_new_stream(rid, who, ly_do)
            self.con.execute(
                "UPDATE wizard_run SET note=?, updated_at=? WHERE id=?",
                ("[tự dọn] " + note[len("[thao tác nhanh dừng] "):][:300]
                 + " || " + str(r.get("msg", ""))[:200], int(time.time()), rid))
            log.info("thao tác nhanh #%s: tu don XONG - %s", rid,
                     str(r.get("msg", ""))[:200])
        except Exception as e:                                # noqa: BLE001
            log.exception("thao tác nhanh #%s: TU DON KHONG XONG", rid)
            self.con.execute(
                "UPDATE wizard_run SET note=?, updated_at=? WHERE id=?",
                ("[tự dọn KHÔNG XONG] " + note[len("[thao tác nhanh dừng] "):][:250]
                 + " || dọn lỗi: " + str(e)[:250], int(time.time()), rid))

    async def _chay(self, rid: int, who: str, ly_do: str, t0: float) -> None:
        from .wizard import BO_BUOC, buoc_cua

        run = self.wizard.lay(rid)
        tong = max(x.no for x in BO_BUOC[run["kind"]])
        tran = (TONG_TOI_DA_LUONG_MOI if run["kind"] == "LUONG_MOI"
                else TONG_TOI_DA)
        log.info("thao tác nhanh #%s %s trên %s: bắt đầu",
                 rid, run["kind"], run["src_table"])

        while True:
            if time.monotonic() - t0 > tran:
                self._ghi_loi(rid, "vượt trần %d giờ cho một lượt - dừng lại. "
                                   "Mở trang quy trình để đi tiếp bằng tay."
                              % (tran // 3600))
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
