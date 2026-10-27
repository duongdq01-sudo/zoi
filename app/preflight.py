"""Pre-flight - noi cong cu tao ra gia tri lon nhat.

VI SAO MODULE NAY PHAI CO TRUOC MOI TINH NANG GHI KHAC CUA PHASE 2

Cong cu nay LAM TANG TAN SUAT thao tac nguy hiem - do chinh la muc dich,
bien quy trinh 16 phut thanh vai click - ma KHONG tu dong lam tang dung
luong dia. Du an tao ra gia toc ma khong tao ra phanh.

Lam tay thi moi lan TRUNCATE la mot quyet dinh co y thuc, co nguoi doc
`df -hP /` truoc. Dat no sau mot nut trong browser thi ba lop an toan tu
nhien - ma sat, su chu y, va viec chi mot nguoi biet cach lam - bien mat
CUNG MOT LUC.

Kich ban vo cu the: mot DBA mo wizard re-sync cho bang 6 GB nguon, luc do
trail dang phinh vi Replicat luong khac abend tu dem truoc. Initial load
chay, `/` day. Oracle khong ghi duoc datafile -> ca DB BSHC chet. Va vi `/`
la MOT LV duy nhat chua dong thoi datafile Oracle, OGG home va trail file,
khong co cho nao de xoay.

FAIL -> nut KHONG MO DUOC, chi co "xem chi tiet". KHONG co checkbox "toi
hieu rui ro". Muon vuot thi lam tay theo runbook, co chu y.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .adapters import gg, osfs

log = logging.getLogger("ggadmin.preflight")

# Chu so trung gian de doi dinh dang so sang kieu Viet. Doi truc tiep
# `,` -> `.` roi `.` -> `,` se doi LAI tat ca ve dau phay.
_TAM = "\x00"


def so_vn(t: str) -> str:
    """So kieu Viet: thap phan DAU PHAY, hang nghin DAU CHAM.

    Ban nho dat o day thay vi import tu `app.main`: main da import
    preflight, nen import nguoc lai la vong tron.
    """
    return t.replace(",", _TAM).replace(".", ",").replace(_TAM, ".")

# Tran cung cua mot smallfile datafile, block 8K: 4.194.303 block.
# Dat MAXSIZE 32G la ORA-03206 vi 32G = 4.194.304 block, vuot dung 1 block.
SMALLFILE_CAP_MB = 32768
# Duoi muc nay thi coi tablespace la het cho, du datafile con autoextend.
TS_ROOM_CANH_BAO_MB = 20480
# Trail vuot muc nay la dau hieu Replicat dang khong apply kip.
TRAIL_CANH_BAO_MB = 2048


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True

    @property
    def icon(self) -> str:
        return "ĐẠT" if self.ok else ("KHÔNG ĐẠT" if self.blocking
                                      else "CẢNH BÁO")


# Ten op la ma noi bo (`KHONG_CO`, `PARAM_EDIT`...). No duoc hien NGUYEN VAN
# cho nguoi dung trong khung xac nhan, nen phai co nhan doc duoc.
NHAN_OP = {
    "START": "chạy tiến trình",
    "STOP": "dừng tiến trình",
    "PARAM_EDIT": "sửa file tham số",
    "ADD_TRANDATA": "bật TRANDATA ở nguồn",
    "VERIFY_SNAPSHOT": "đối chiếu chế độ chuẩn",
    "RESTART_ADD_TABLE": "chạy lại Extract sau khi thêm bảng",
    "LOAD": "nạp dữ liệu",
    "WIZARD": "bước trong quy trình",
    "KHONG_CO": "thao tác chỉ đọc",
    "AUTO": "chạy tự động cả quy trình",
    "KHOI_PHUC": "khôi phục an toàn - chỉ chạy/dừng tiến trình",
}


@dataclass(frozen=True)
class Result:
    op: str
    target: str
    checks: list[Check]

    @property
    def nhan(self) -> str:
        """Nhan doc duoc cua op, de hien trong khung xac nhan."""
        return NHAN_OP.get(self.op, self.op)

    @property
    def ok(self) -> bool:
        """Chi check blocking moi quyet dinh. Canh bao khong chan."""
        return all(c.ok for c in self.checks if c.blocking)

    @property
    def note(self) -> str:
        bad = [c.name for c in self.checks if c.blocking and not c.ok]
        return ", ".join(bad) if bad else "tất cả đều đạt"

    def as_dict(self) -> dict:
        return {"op": self.op, "target": self.target, "ok": self.ok,
                "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail,
                            "blocking": c.blocking} for c in self.checks]}


class Preflight:
    def __init__(self, cfg, con, ora, collector):
        self.cfg = cfg
        self.con = con
        self.ora = ora
        self.collector = collector

    # =============================================================== dieu phoi
    async def run(self, op: str, target: str = "") -> Result:
        fn = {
            "START": self._start,
            "STOP": self._stop,
            "PARAM_EDIT": self._param_edit,
            "ADD_TRANDATA": self._add_trandata,
            "VERIFY_SNAPSHOT": self._verify_snapshot,
            "LOAD": self._load,
            "KHONG_CO": self._khong_co,
            "AUTO": self._auto,
            "KHOI_PHUC": self._khoi_phuc,
            "LUONG_MAT": self._luong_mat,
        }.get(op)
        if fn is None:
            return Result(op, target, [Check("thao tác không rõ", False,
                                             f"Không có mục kiểm nào cho {op!r}")])
        base = self._chung()
        try:
            rieng = await fn(target)
        except Exception as e:                       # noqa: BLE001
            log.exception("kiểm tra trước khi chạy %s thất bại", op)
            rieng = [Check("không đo được", False,
                           f"Không đo được: {str(e).splitlines()[0][:200]}")]
        return Result(op, target, base + rieng)

    async def _khoi_phuc(self, target: str) -> list[Check]:
        """Muc kiem cho khoi phuc an toan.

        CO Y khong co muc kiem nao CHAN. Day la thao tac dung khi he DANG
        co su co: mot muc kiem chan se chan dung luc can chay nhat. Va moi
        viec no lam chi la start/stop - dao nguoc duoc trong mot cu bam, nen
        khong co gi de can.

        Ba muc kiem CHUNG van chay (so lieu giam sat con moi, dia, Manager),
        vi neu so lieu giam sat da cu thi chan doan cung dua tren so cu.
        """
        return [Check("mức an toàn", True,
                      "chỉ chạy hoặc dừng tiến trình - không sửa tham số, "
                      "không TRUNCATE, không chạm mốc checkpoint",
                      blocking=False),
                Check("kế hoạch", True,
                      "xem phần dưới: lệnh nào sẽ chạy, theo thứ tự nào, và "
                      "vì sao - đo lại ngay trước khi chạy",
                      blocking=False)]

    async def _auto(self, target: str) -> list[Check]:
        """Thao tac nhanh: 3 muc kiem CHUNG + mot dong noi ro ban chat.

        KHONG dung lai `_khong_co`: nhan cua no la "thao tac chi doc", va
        hien dong chu do ngay truoc mot luot co the XOA SACH BANG la gay
        nham theo huong nguy hiem nhat. Muc kiem rieng cua tung buoc van
        duoc DO LAI truoc tung buoc trong luc chay, nen o day khong lap.
        """
        return [Check("từng bước tự đo riêng", True,
                      "mỗi bước trong lượt vẫn đo mục kiểm của riêng nó "
                      "trước khi chạy, và không đạt là dừng ngay tại đó",
                      blocking=False)]

    async def _luong_mat(self, target: str) -> list[Check]:
        """Phien ghi mot luong ma conf/app.toml KHONG con. Luon CHAN."""
        return [Check("luồng còn trong cấu hình", False,
                      f"luồng {target!r} không còn trong conf/app.toml - không bước nào "
                      "của phiên này chạy tiếp được; phiên tạo luồng mới thì chỉ còn "
                      "đường dọn sạch")]

    async def _khong_co(self, target: str) -> list[Check]:
        """Buoc chi doc / khong co pre-flight rieng. Van giu 3 check chung."""
        return [Check("bước chỉ đọc", True,
                      "bước này không có mục kiểm riêng", blocking=False)]

    # ================================================================== chung
    def _chung(self) -> list[Check]:
        """Ba check ap cho MOI thao tac ghi."""
        s = self.collector.snap
        out: list[Check] = []

        # 1. So lieu giam sat phai moi. Khong chay thao tac ghi khi khong
        #    biet trang thai that cua he thong.
        if not s.ts:
            out.append(Check("số liệu giám sát", False,
                             "Chưa đo được lần nào - không biết trạng thái hệ thống"))
        elif s.age_s > self.cfg.poll_seconds * 4:
            out.append(Check("số liệu giám sát", False,
                             f"Số liệu cũ {s.age_s}s (đo lại mỗi {self.cfg.poll_seconds}s)"))
        else:
            out.append(Check("số liệu giám sát", True, f"mới {s.age_s}s"))

        # 2. Dia `/` - do LAI tai day, khong dung so cua tick truoc.
        #    Ly do: con so dung luong HET HAN RAT NHANH (cam bay 29).
        try:
            d = osfs.df("/")
            gb = d["avail_gb"]
            from .collector import READ_ONLY_GB
            out.append(Check("đĩa /", gb >= READ_ONLY_GB,
                             so_vn(f"còn {gb:.1f} GB "
                                   f"(ngưỡng {READ_ONLY_GB:.0f} GB)")))
        except OSError as e:
            out.append(Check("đĩa /", False, f"không đọc được dung lượng đĩa: {e}"))

        # 3. Manager phai RUNNING, neu khong moi lenh ggsci se treo hoac loi
        mgr = next((p for p in s.procs if p.name == "MGR"), None)
        out.append(Check("Manager", bool(mgr and mgr.is_running),
                         mgr.status if mgr else "không thấy Manager"))
        return out

    # ------------------------------------------------- datafile + tablespace
    def _datafile(self) -> list[Check]:
        """Tung datafile phai con cho DUOI tran 32 GB.

        Khong chi xem `% free` cua tablespace: tablespace bao "con cho" trong
        khi datafile da cung tran la cai bay da gap that (ORA-03206).
        """
        s = self.collector.snap
        if not s.datafiles:
            return [Check("datafile", False, "chưa đọc được danh sách datafile")]
        room: dict[str, float] = {}
        for d in s.datafiles:
            room[d["tablespace_name"]] = room.get(d["tablespace_name"], 0.0) \
                + float(d["room_mb"] or 0)
        xau: list[str] = []
        for d in s.datafiles:
            if float(d["size_mb"] or 0) >= SMALLFILE_CAP_MB \
                    and room.get(d["tablespace_name"], 0.0) < TS_ROOM_CANH_BAO_MB:
                xau.append(f"{d['file_name'].rsplit('/', 1)[-1]} đã cụng trần "
                           f"mà {d['tablespace_name']} chỉ còn "
                           f"{room[d['tablespace_name']]:,.0f} MB")
        if xau:
            return [Check("datafile", False, "; ".join(xau))]
        thap = [f"{k} {v:,.0f} MB" for k, v in sorted(room.items())
                if v < TS_ROOM_CANH_BAO_MB]
        return [Check("datafile", True,
                      "không file nào cụng trần mà hết chỗ nới"
                      + (f"; còn ít chỗ nới: {', '.join(thap)}"
                         if thap else ""))]

    def _trail(self) -> list[Check]:
        s = self.collector.snap
        if not s.trails:
            return [Check("trail", True, "chưa đo được", blocking=False)]
        to = [(k, float(v.get("bytes") or 0) / 1048576)
              for k, v in s.trails.items()]
        qua = [f"{k} {mb:,.0f} MB" for k, mb in to if mb > TRAIL_CANH_BAO_MB]
        if qua:
            return [Check("trail", False,
                          "trail đang phình: " + ", ".join(qua)
                          + " -> Replicat không áp kịp, đừng nạp thêm")]
        return [Check("trail", True,
                      ", ".join(f"{k} {mb:,.0f} MB" for k, mb in sorted(to)))]

    def _discard(self) -> list[Check]:
        s = self.collector.snap
        # Dem DONG KHONG PHAI BANNER, khong so kich thuoc voi moc rong:
        # stop/start Replicat lam file to len 94 byte ma khong bo ban ghi nao
        # (loi nay da chan oan dung buoc NAP cua wizard lan chay dau tien).
        la = [f"{r}: {int(d['so_dong_la'])} dòng"
              for r, d in s.discards.items() if (d.get("so_dong_la") or 0) > 0]
        if la:
            return [Check("dữ liệu bị bỏ", False, "; ".join(la)
                          + " -> có dòng khác dòng mở đầu trong file discard, tức là "
                            "có bản ghi bị bỏ. Đọc file đó trước khi đi tiếp.")]
        return [Check("dữ liệu bị bỏ", True,
                      "cả 3 file chỉ có dòng mở đầu, 0 bản ghi bị bỏ")]

    # ---------------------------------------------------------------- process
    def _proc(self, name: str) -> gg.ProcState | None:
        return next((p for p in self.collector.snap.procs if p.name == name), None)

    async def _proc_that(self, name: str) -> gg.ProcState | None:
        """Tim tien trinh; KHONG thay trong anh so lieu thi doc `info all` THAT.

        VI SAO CAN - su co 2026-10-20, da lam mat ca mot luong vua dung:
        quy trinh LUONG_MOI tao Replicat o buoc ADD_REPLICAT roi chay no o
        buoc START_REPLICAT_2. Voi mot bang nho (70k dong, nap 2 giay) hai
        buoc do cach nhau BAY GIAY - ngan hon chu ky do cua bo thu thap, nen
        anh so lieu luc do chua co tien trinh vua tao. Pre-flight ket luan
        "khong thay RPMNV2 trong danh sach tien trinh" -> luot chay dung ->
        duong tu don thao sach ca luong (gỡ cấu hình, xoá Extract đã đăng ký
        vào DB nguồn production).

        Anh so lieu la TIEN NGHI de khong goi `info all` cho moi request.
        Mot tien trinh VUA DUOC TAO thi anh do CHUA BIET, va "chua biet"
        khong duoc phep bien thanh "khong ton tai". Doc that o day: chi mot
        lan ggsci, va chi khi anh so lieu khong co ten do.
        """
        p = self._proc(name)
        if p is not None:
            return p
        try:
            ds = await gg.info_all(self.cfg)
        except Exception as e:                           # noqa: BLE001
            log.warning("doc info all de tim %s that bai: %s", name, e)
            return None
        return next((x for x in ds if x.name == name), None)

    def _cap_process(self, process: str) -> tuple[str | None, str | None]:
        """Tra (extract, replicat) cua luong chua process nay."""
        for st in self.cfg.streams.values():
            if process in (st.get("extract"), st.get("replicat")):
                return st.get("extract"), st.get("replicat")
        return None, None

    # ================================================================== START
    async def _start(self, target: str) -> list[Check]:
        out: list[Check] = []
        p = await self._proc_that(target)
        if p is None:
            return [Check("tiến trình", False,
                          f"không thấy {target} trong `info all` - đã đọc trực tiếp từ "
                          "GoldenGate, không phải từ số liệu đo cũ")]
        out.append(Check("process_dang_stopped", not p.is_running,
                         f"đang {p.status}"
                         + (" -> không cần chạy lại" if p.is_running else "")))

        et, rep = self._cap_process(target)
        # CHOT QUAN TRONG NHAT CUA START.
        # Start mot Extract dang co nhieu dong TABLE hon so dong MAP cua
        # Replicat cap doi = MAT DU LIEU IM LANG: Extract dao redo cua bang
        # thieu MAP va ghi vao trail, Replicat bo qua ban ghi do VA VAN DOI
        # CHECKPOINT. Khong discard, khong log, `info all` van RUNNING lag 0.
        if et and rep and target == et:
            n_tab = gg.count_param_entries(self.cfg, et, "TABLE")
            n_map = gg.count_param_entries(self.cfg, rep, "MAP")
            ok = not (n_tab > 0 and n_map >= 0 and n_tab > n_map)
            out.append(Check(
                "table_khop_map", ok,
                f"{et} có {n_tab} dòng TABLE, {rep} có {n_map} dòng MAP"
                + ("" if ok else
                   f" -> chạy {et} lúc này sẽ làm {n_tab - n_map} bảng bị Replicat BỎ QUA "
                   "trong khi mốc checkpoint VẪN DỜI: mất dữ liệu im lặng. Thêm dòng MAP "
                   f"vào {rep}.prm trước.")))
            if rep and (rp := self._proc(rep)) is not None:
                out.append(Check("replicat_san_sang", rp.is_running,
                                 f"{rep} đang {rp.status}", blocking=False))
        return out

    # =================================================================== STOP
    async def _stop(self, target: str) -> list[Check]:
        out: list[Check] = []
        p = await self._proc_that(target)
        if p is None:
            return [Check("tiến trình", False,
                          f"không thấy {target} trong `info all` - đã đọc trực tiếp từ "
                          "GoldenGate, không phải từ số liệu đo cũ")]
        out.append(Check("process_dang_running", p.is_running, f"đang {p.status}"))
        if target == "MGR":
            out.append(Check("không dừng Manager", False,
                             "STOP MGR bi chan: Manager dung thi moi Extract/"
                             "Replicat mat nguoi giam sat va khong tu khoi dong lai"))
        # Dung Replicat thi trail se phinh. Kiem con bao nhieu cho.
        if p.program == "REPLICAT":
            out += self._trail()
        return out

    # ============================================================ PARAM_EDIT
    async def _param_edit(self, target: str) -> list[Check]:
        """KHONG BAO GIO ghi param cua process dang RUNNING.

        Extract/Replicat chi doc param file LUC KHOI DONG (cam bay 34). Sua
        file cua process dang chay thi thay doi khong co hieu luc ngay, ma
        lan restart bat ky ve sau - ke ca restart ngoai y muon - se nap ban
        moi. Do la thay doi co hieu luc o thoi diem khong ai doan duoc.
        """
        out: list[Check] = []
        p = self._proc(target)
        path = self.cfg.param_file(target)
        out.append(Check("param_ton_tai", path.exists(), str(path)))
        if p is None:
            out.append(Check("tiến trình", False, f"không thấy {target}"))
            return out
        out.append(Check("process_dang_stopped", not p.is_running,
                         f"đang {p.status}"
                         + ("" if not p.is_running else
                            " -> phải DỪNG trước khi sửa tham số. Tiến trình chỉ đọc tham số lúc "
                            "khởi động, nên sửa lúc đang chạy là tạo ra một thay đổi có hiệu lực ở "
                            "thời điểm không ai đoán được.")))
        try:
            ok, _ = await gg.checkprm(
                self.cfg, path.name,
                "EXTRACT" if p.program == "EXTRACT" else "REPLICAT")
            out.append(Check("cú pháp bản hiện tại", ok,
                             "bản hiện tại qua được kiểm cú pháp" if ok else
                             "bản hiện tại KHÔNG qua được kiểm cú pháp - sửa tay trước"))
        except Exception as e:                       # noqa: BLE001
            out.append(Check("cú pháp bản hiện tại", False, str(e)[:160]))
        return out

    # ========================================================== ADD_TRANDATA
    async def _add_trandata(self, target: str) -> list[Check]:
        out: list[Check] = []
        row = self.con.execute(
            "SELECT src_table, keycols, prepared, in_extract, has_map"
            " FROM managed_table WHERE src_table=?", (target,)).fetchone()
        if row is None:
            # Bang chi nam trong danh sach LOG (luong log): pham vi hop le,
            # va TRANDATA o day la ALLCOLS - buoc tu kiem bang dba_log_groups.
            lg = self.con.execute(
                "SELECT src_table, prepared FROM managed_log WHERE src_table=?",
                (target,)).fetchone()
            if lg is not None:
                return [Check("trong_registry", True, "có trong danh sách log"),
                        Check("allcols", not lg["prepared"],
                              "đã bật TRANDATA ALLCOLS -> bước này sẽ bỏ qua"
                              if lg["prepared"] else "chưa bật TRANDATA ALLCOLS",
                              blocking=False)]
        # Lop 5 chong injection: ten bang phai nam trong registry.
        out.append(Check("trong_registry", row is not None,
                         "có trong danh sách quản lý" if row else
                         f"{target} khong nam trong registry -> tu choi. "
                         "Bảng ngoài phạm vi GoldenGate thì công cụ không được chạm vào."))
        if row is None:
            return out
        # KHONG CHAN khi da bat: handler `_b_add_trandata` tu kiem lai bang
        # `info trandata` va bo qua neu da bat. De chot nay CHAN thi moi
        # luot chay LAI (vi du LUONG_MOI sau mot lan don sach - lan don co y
        # KHONG tat TRANDATA) deu chet o buoc nay vi mot dieu VO HAI.
        out.append(Check("chua_prepared", not row["prepared"],
                         "đã bật TRANDATA rồi -> bước này sẽ bỏ qua, không làm lại"
                         if row["prepared"] else "chưa bật TRANDATA",
                         blocking=False))
        # Bang khong co khoa that: phai co KEYCOLS tuong minh TRUOC.
        out.append(Check("co_keycols", bool(row["keycols"]),
                         row["keycols"] or
                         "chưa xác nhận cột khoá. Bảng không có khoá chính thật thì phải khảo "
                         "sát và xác nhận khoá trước - GoldenGate không dùng được index theo "
                         "biểu thức làm khoá.",
                         blocking=False))
        return out

    # ======================================================= VERIFY_SNAPSHOT
    async def _verify_snapshot(self, target: str) -> list[Check]:
        """Che do chuan phai DUNG ca Extract va Replicat cua luong."""
        out: list[Check] = []
        st = self.cfg.streams.get(target)
        if st is None:
            return [Check("luồng", False, f"không có luồng {target!r}")]
        et, rep = st.get("extract"), st.get("replicat")
        pe, pr = self._proc(et), self._proc(rep)
        out.append(Check("extract_dang_running", bool(pe and pe.is_running),
                         f"{et} {pe.status if pe else '?'}"))
        out.append(Check("replicat_dang_running", bool(pr and pr.is_running),
                         f"{rep} {pr.status if pr else '?'}"))
        out.append(Check("lag_thap", bool(pr and (pr.lag_s or 0) <= 60),
                         f"{rep} lag {pr.lag_s if pr else '?'}s"
                         + ("" if pr and (pr.lag_s or 0) <= 60 else
                            " -> chờ bắt kịp trước, nếu không thì cửa sổ dừng luồng sẽ kéo rất dài")))
        out += self._discard()

        # Khong co job verify nao dang chay (toi da 1 tren toan he)
        n = self.con.execute(
            "SELECT COUNT(*) FROM job WHERE state IN ('QUEUED','RUNNING')"
        ).fetchone()[0]
        out.append(Check("khong_co_job_khac", n == 0,
                         f"{n} việc đang chạy" if n else "không có việc nào đang chạy"))

        # Flashback con hieu luc du lau cho ca lo khong? tuned_undoretention
        # o NGUON quyet dinh, va day la cho da an ORA-01555 that.
        try:
            r = await self.ora.undo_retention_source()
            n_bang = self.con.execute(
                "SELECT COUNT(*) FROM managed_table WHERE stream=? AND has_map=1",
                (target,)).fetchone()[0]
            out.append(Check("thời gian giữ ảnh dữ liệu cũ", r >= 900,
                             f"nguồn giữ ảnh dữ liệu cũ {r}s, cho {n_bang} bảng"
                             + ("" if r >= 900 else
                                " -> quá ngắn, ảnh dữ liệu cũ sẽ hết hiệu lực giữa lô (ORA-01555)"),
                             blocking=False))
        except Exception as e:                       # noqa: BLE001
            out.append(Check("thời gian giữ ảnh dữ liệu cũ", False, str(e)[:160], blocking=False))
        return out

    # =================================================================== LOAD
    async def _load(self, target: str) -> list[Check]:
        """Danh cho Phase 3 (initial load / re-sync). Chat nhat trong ca module.

        Yeu cau `/` con > 2x kich thuoc NGUON cua bang: nap direct-path sinh
        them segment o dich, cong voi trail va undo trong luc nap.
        """
        out = self._datafile() + self._trail() + self._discard()
        row = self.con.execute(
            "SELECT src_table, tgt_table FROM managed_table WHERE src_table=?",
            (target,)).fetchone()
        out.append(Check("trong_registry", row is not None,
                         "có trong danh sách quản lý" if row else
                         f"{target} ngoài danh sách quản lý -> từ chối"))
        if row is None:
            return out
        try:
            mb = await self.ora.scalar(
                "SELECT ROUND(SUM(bytes)/1048576) FROM dba_segments"
                " WHERE owner=:o AND segment_name=:t",
                o="ODS", t=row["tgt_table"]) or 0
            free_gb = osfs.df("/")["avail_gb"]
            can_gb = float(mb) * 2 / 1024
            out.append(Check("đĩa gấp đôi kích thước bảng", free_gb > can_gb,
                             f"bảng khoảng {float(mb):,.0f} MB, cần hơn {can_gb:.1f} GB, đĩa còn "
                             f"{free_gb:.1f} GB"))
        except Exception as e:                       # noqa: BLE001
            out.append(Check("đĩa gấp đôi kích thước bảng", False, str(e)[:160]))

        # Toi da 1 job load tren toan he, va 1 lan/ngay - chan kich ban
        # "thay de qua, re-sync lien 5 bang".
        n = self.con.execute(
            "SELECT COUNT(*) FROM job WHERE kind LIKE 'LOAD%'"
            " AND created_at > strftime('%s','now','-1 day')").fetchone()[0]
        out.append(Check("gioi_han_tan_suat", n < 1,
                         f"{n} job load trong 24h qua"
                         + ("" if n < 1 else " -> đã đạt giới hạn 1 lần mỗi ngày")))
        return out
