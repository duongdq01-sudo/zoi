"""Test BO CHO thich ung + KHOI PHUC AN TOAN.

Hai phan nay deu co mot dac diem: hong thi hong IM LANG. Mot bo cho sai nhip
chi lam cham, khong ai thay. Mot ke hoach khoi phuc sai thu tu thi mat du
lieu ma khong discard, khong log - dung cai cam bay so mot cua du an. Nen o
day khoa lai BAN CHAT cua tung quyet dinh, khong chi khoa dau ra.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import cho, khoiphuc                                 # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# ==================================================== bo cho thich ung
@test
def nhip_day_o_dau_thua_dan_ve_sau():
    """Buoc ngan phai bat duoc NGAY; cho lau thi khong can hoi day."""
    assert cho.nhip_cua(0) <= 1.0, "nhip dau phai <= 1 giay"
    assert cho.nhip_cua(5) <= 1.0
    # Phai TANG don dieu
    truoc = 0.0
    for t in (0, 5, 15, 45, 200, 700, 5000):
        n = cho.nhip_cua(t)
        assert n >= truoc, "nhip phai khong giam theo thoi gian cho"
        truoc = n
    assert cho.nhip_cua(5000) == cho.NHIP_TRAN


@test
def tran_tinh_theo_DONG_HO_THAT_khong_theo_so_vong():
    """`range(60)` x `sleep(10)` KHONG phai 10 phut.

    Moi vong con ton thoi gian goi ggsci (1-3 giay that do), nen tran that
    la 13-15 phut. Thong bao noi sai voi nguoi dung, va nguoi dung lay so
    do de quyet dinh cua so bao tri.
    """
    async def chay():
        async def do():
            await asyncio.sleep(0.05)      # gia lap goi ggsci ton thoi gian
            return False, "chua xong"
        t0 = time.monotonic()
        r = await cho.cho_den(do, tran_s=1.0, nhan="thu tran")
        that = time.monotonic() - t0
        assert not r["xong"]
        # Khong duoc vuot tran qua 20% du moi lan do deu ton thoi gian
        assert that < 1.2, "vuot tran that: %.2fs / tran 1.0s" % that
        return r
    r = asyncio.run(chay())
    assert r["giay"] >= 1.0


@test
def bat_duoc_NGAY_khi_dieu_kien_dat():
    """Day la ly do ton tai cua bo cho: khong de thoi gian troi vo ich."""
    async def chay():
        async def do():
            return True, "xong ngay"
        t0 = time.monotonic()
        r = await cho.cho_den(do, tran_s=600, nhan="thu ngay")
        return r, time.monotonic() - t0
    r, that = asyncio.run(chay())
    assert r["xong"] and r["so_lan"] == 1
    assert that < 0.5, "mat %.2fs du dieu kien dat ngay lan hoi dau" % that


@test
def khong_ngu_vuot_qua_moc_tran():
    src = inspect.getsource(cho.cho_den)
    assert "min(" in src and "tran_s - troi" in src, \
        "co the ngu lo ra ngoai tran -> tran mat y nghia"


@test
def ngoai_le_khi_do_KHONG_bi_nuot():
    """Loi khi do trang thai la tin hieu that, khong duoc im lang cho tiep."""
    async def chay():
        async def do():
            raise RuntimeError("ggsci chet")
        try:
            await cho.cho_den(do, tran_s=5, nhan="thu loi")
        except RuntimeError as e:
            return str(e)
        raise AssertionError("ngoai le bi nuot")
    assert "ggsci chet" in asyncio.run(chay())


@test
def khong_con_vong_cho_nhip_co_dinh_nao():
    """Khoa lai: khong ai them mot `sleep(10)` moi vao wizard."""
    src = (ROOT / "app" / "wizard.py").read_text(encoding="utf-8")
    assert "asyncio.sleep" not in src, \
        "wizard.py co vong cho nhip co dinh - phai dung app/cho.py"
    src2 = (ROOT / "app" / "auto.py").read_text(encoding="utf-8")
    assert "asyncio.sleep" not in src2, \
        "auto.py co vong cho nhip co dinh - phai dung app/cho.py"


# ================================================= khoi phuc an toan
class _P:
    def __init__(self, status="RUNNING", lag=0):
        self.status = status
        self.lag_s = lag

    @property
    def is_running(self):
        return self.status == "RUNNING"


class _Cfg:
    streams = {"HT": {"extract": "ETHT", "replicat": "RHT"}}


def _con(nap=False):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE job (id INTEGER PRIMARY KEY, state TEXT,"
              " params_json TEXT)")
    c.execute("CREATE TABLE wizard_run (id INTEGER PRIMARY KEY, stream TEXT,"
              " src_table TEXT)")
    if nap:
        c.execute("INSERT INTO wizard_run VALUES (7,'HT','T_THU')")
        c.execute("INSERT INTO job VALUES (9,'RUNNING','{\"run_id\": 7}')")
    return c


def _kham(n_tab, n_map, et="RUNNING", rep="RUNNING", mgr="RUNNING",
          nap=False):
    """Goi `kham` voi gg bi thay the - khong can server that."""
    tt = {"ETHT": et, "RHT": rep, "MGR": mgr}
    goc_dem, goc_tt = khoiphuc.gg.count_param_entries, khoiphuc.gg.status_of

    def dem(cfg, proc, kw):
        return n_tab if kw.upper() == "TABLE" else n_map

    async def st(cfg, proc):
        return _P(tt[proc])

    khoiphuc.gg.count_param_entries = dem
    khoiphuc.gg.status_of = st
    try:
        return asyncio.run(khoiphuc.kham(_Cfg(), _con(nap), "HT"))
    finally:
        khoiphuc.gg.count_param_entries = goc_dem
        khoiphuc.gg.status_of = goc_tt


@test
def thieu_MAP_va_replicat_DANG_CHAY_thi_DUNG_NGAY():
    """Day la cam bay so mot cua du an, va la ca nguy hiem nhat.

    Replicat chay khi thieu dong MAP = bo qua ban ghi MA VAN DOI CHECKPOINT.
    Mat du lieu khong discard, khong log, khong dau vet. Phai dung NGAY, va
    `stop replicat` phai la lenh DAU TIEN.
    """
    k = _kham(n_tab=28, n_map=27, et="RUNNING", rep="RUNNING")
    assert k["mat_du_lieu_dang_xay_ra"] is True
    assert k["the_chay"] is True
    assert k["ke_hoach"], "phat hien mat du lieu ma khong co ke hoach gi"
    assert k["ke_hoach"][0]["lenh"].startswith("stop replicat"), \
        "lenh dau tien phai la dung Replicat: %r" % k["ke_hoach"][0]["lenh"]
    # KHONG duoc co lenh start nao trong ke hoach
    assert not any(b["lenh"].startswith("start") for b in k["ke_hoach"]), \
        "thieu MAP ma ke hoach co lenh start"


@test
def thieu_MAP_va_replicat_DA_DUNG_thi_KHONG_chay_lai():
    """Chua mat gi - ban ghi con trong trail. Chay lai la mat."""
    k = _kham(n_tab=28, n_map=27, et="RUNNING", rep="STOPPED")
    assert k["mat_du_lieu_dang_xay_ra"] is False
    assert not any(b["lenh"].startswith("start") for b in k["ke_hoach"])
    assert any(b["lenh"].startswith("stop extract") for b in k["ke_hoach"]), \
        "de Extract chay tiep la don them ban ghi se bi bo qua"
    assert "CHƯA mất" in k["ket_luan"]


@test
def thieu_MAP_ca_hai_da_dung_thi_KHONG_LAM_GI():
    k = _kham(n_tab=28, n_map=27, et="STOPPED", rep="STOPPED")
    assert k["ke_hoach"] == [], "ca hai da dung roi ma con chay lenh"
    assert k["the_chay"] is False
    assert "KHÔNG được chạy lại" in k["ket_luan"]


@test
def dang_NAP_thi_KHONG_chay_lai_GoldenGate():
    """HANDLECOLLISIONS khong bao ve INSERT APPEND - cam bay da gap."""
    k = _kham(n_tab=27, n_map=27, et="STOPPED", rep="STOPPED", nap=True)
    assert k["the_chay"] is False
    assert k["ke_hoach"] == []
    assert "ORA-00001" in k["ket_luan"]
    assert k["dang_nap"]["src_table"] == "T_THU"


@test
def mat_du_lieu_VUOT_moi_quan_tam_khac_ke_ca_dang_nap():
    """Mat du lieu im lang KHONG hoan tac duoc; mot dot nap thi nap lai duoc.

    Nen khi ca hai dieu kien cung dung, phai xu ly mat du lieu TRUOC.
    """
    k = _kham(n_tab=28, n_map=27, et="RUNNING", rep="RUNNING", nap=True)
    assert k["mat_du_lieu_dang_xay_ra"] is True
    assert k["the_chay"] is True
    assert k["ke_hoach"][0]["lenh"].startswith("stop replicat")


@test
def bat_bien_dat_thi_REPLICAT_CHAY_TRUOC_extract():
    """Dao thu tu la mat du lieu im lang - luat cung cua du an."""
    k = _kham(n_tab=27, n_map=27, et="STOPPED", rep="STOPPED")
    assert k["the_chay"] is True
    lenh = [b["lenh"] for b in k["ke_hoach"]]
    i_rep = next(i for i, l in enumerate(lenh) if l.startswith("start replicat"))
    i_et = next(i for i, l in enumerate(lenh) if l.startswith("start extract"))
    assert i_rep < i_et, "Extract chay truoc Replicat: %r" % lenh


@test
def manager_dung_thi_chay_manager_TRUOC_TIEN():
    k = _kham(n_tab=27, n_map=27, et="STOPPED", rep="STOPPED", mgr="STOPPED")
    assert k["ke_hoach"][0]["lenh"] == "start manager"


@test
def da_on_dinh_thi_khong_chay_gi():
    k = _kham(n_tab=27, n_map=27, et="RUNNING", rep="RUNNING")
    assert k["ke_hoach"] == []
    assert k["the_chay"] is False
    assert "đã ổn định" in k["ket_luan"]


@test
def nhieu_MAP_hon_TABLE_la_canh_bao_KHONG_phai_loi():
    """Replicat chi khong bao gio nhan ban ghi cho cac bang do - vo hai."""
    k = _kham(n_tab=26, n_map=27, et="RUNNING", rep="RUNNING")
    assert k["bat_bien_ok"] is True
    assert any("nhiều hơn" in c for c in k["canh_bao"])
    assert k["ke_hoach"] == []


@test
def luong_la_thi_tu_choi():
    goc = khoiphuc.gg.count_param_entries
    try:
        asyncio.run(khoiphuc.kham(_Cfg(), _con(), "KHONG_CO_LUONG_NAY"))
    except khoiphuc.KhoiPhucError as e:
        assert "Không có luồng" in str(e)
        return
    finally:
        khoiphuc.gg.count_param_entries = goc
    raise AssertionError("luong la ma khong tu choi")


def _ma_thuan(src: str) -> str:
    """Bo chu thich va moi chuoi, chi con MA THAT.

    Can thiet vi cac chot an toan o day duoc GIAI THICH bang chu ngay trong
    chu thich: docstring cua `khoiphuc` noi "khong TRUNCATE", chu thich noi
    "INSERT APPEND". Quet ca van ban thi test bao hong oan dung vi tai lieu
    viet ro - dung loai test te nhat, vi no day nguoi ta di XOA GIAI THICH
    cho test xanh lai.
    """
    import io as _io
    import tokenize
    ra = []
    try:
        for t in tokenize.generate_tokens(_io.StringIO(src).readline):
            if t.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            if t.type == tokenize.NAME or t.type == tokenize.OP:
                ra.append(t.string)
    except (tokenize.TokenError, IndentationError):
        return src
    return " ".join(ra)


@test
def kham_la_CHI_DOC():
    """Ban xem truoc phai goi duoc bao nhieu lan cung duoc."""
    src = _ma_thuan(inspect.getsource(khoiphuc.kham))
    for xau in ("control", ". start (", ". stop (", "INSERT", "UPDATE",
                "DELETE"):
        assert xau not in src, "kham() co thao tac ghi %r" % xau


@test
def chay_phai_DO_LAI_chan_doan_truoc_khi_chay():
    """Tu luc bam den luc chay co the vai chuc giay, ma he dang co su co."""
    src = inspect.getsource(khoiphuc.chay)
    assert "await kham(" in src, "chay() khong do lai chan doan"
    assert src.index("await kham(") < src.index("for b in"), \
        "do lai chan doan SAU khi bat dau chay thi vo nghia"


@test
def chay_DUNG_NGAY_khi_mot_lenh_loi():
    """Chay tiep khi chua biet vi sao lenh truoc loi la lam xau them."""
    src = inspect.getsource(khoiphuc.chay)
    assert src.count("return {\"ok\": False") >= 2, \
        "khong dung lai khi lenh loi / khong dat"
    assert "KHÔNG" in src


@test
def khoi_phuc_CHI_dung_start_va_stop():
    """Nho the no luon hoan tac duoc, va khong bao gio sinh su co moi."""
    src = _ma_thuan(inspect.getsource(khoiphuc))
    for xau in ("TRUNCATE", "ALTER", "TRANDATA", "EXTSEQNO", "EXTRBA",
                "paramsmod", "ora", "oracledb"):
        assert xau not in src, "khoi phuc co thao tac ngoai start/stop: %r" % xau
    # Chi duoc goi DUNG hai thao tac cua control
    assert ". stop (" in src and ". start (" in src
    import re as _re
    goi = set(_re.findall(r"control \. (\w+)", src))
    assert goi <= {"stop", "start"}, "goi thao tac khac cua control: %r" % goi


@test
def xem_truoc_noi_ro_SE_CHAY_LENH_NAO():
    k = _kham(n_tab=28, n_map=27, et="RUNNING", rep="RUNNING")
    t = khoiphuc.xem_truoc(k)
    assert "SẼ CHẠY" in t and "stop replicat RHT" in t
    assert "vì:" in t, "khong noi vi sao chay lenh do"
    assert "KẾT LUẬN" in t
    assert "CẢNH BÁO" in t
    k2 = _kham(n_tab=27, n_map=27, et="RUNNING", rep="RUNNING")
    assert "KHÔNG CHẠY LỆNH NÀO" in khoiphuc.xem_truoc(k2)


@test
def nut_khoi_phuc_la_MACRO_khong_phai_include():
    """`include with context` khong thay bien `{% set %}` trong vong for.

    No vap IM LANG: nut hien ra nhung `luong` rong -> khoi phuc SAI luong.
    """
    t = (ROOT / "web" / "templates" / "_nut_khoiphuc.html").read_text(
        encoding="utf-8")
    assert "{% macro nut_khoiphuc(" in t
    # Macro phai tu bao ve khi luong rong
    assert "{% if luong %}" in t, "luong rong van ve ra nut"
    for ten in ("_result.html", "_nhanh_song.html", "wizard_run.html",
                "control.html"):
        h = (ROOT / "web" / "templates" / ten).read_text(encoding="utf-8")
        assert "import nut_khoiphuc" in h, "%s khong import macro" % ten
        assert "_nut_khoiphuc.html\" with context" not in h, \
            "%s con dung include - se vap im lang" % ten


# =============================== duong THUC THI (control gia)
class _CtlGia:
    """Control gia - ghi lai da goi gi, theo thu tu nao."""

    def __init__(self, hong: set | None = None, no: set | None = None):
        self.goi: list[str] = []
        self.hong = hong or set()      # nem ngoai le
        self.no = no or set()          # tra ok=False

    async def start(self, proc):
        self.goi.append("start " + proc)
        if "start " + proc in self.hong:
            raise RuntimeError("ggsci chet khi start " + proc)
        return {"ok": "start " + proc not in self.no, "status": "STOPPED"}

    async def stop(self, proc):
        self.goi.append("stop " + proc)
        if "stop " + proc in self.hong:
            raise RuntimeError("ggsci chet khi stop " + proc)
        return {"ok": "stop " + proc not in self.no, "status": "RUNNING"}


def _chay(n_tab, n_map, et, rep, ctl, mgr="RUNNING"):
    tt = {"ETHT": et, "RHT": rep, "MGR": mgr}
    goc_dem, goc_tt = khoiphuc.gg.count_param_entries, khoiphuc.gg.status_of

    def dem(cfg, proc, kw):
        return n_tab if kw.upper() == "TABLE" else n_map

    async def st(cfg, proc):
        return _P(tt[proc])

    khoiphuc.gg.count_param_entries = dem
    khoiphuc.gg.status_of = st
    try:
        return asyncio.run(khoiphuc.chay(_Cfg(), _con(), ctl, "HT"))
    finally:
        khoiphuc.gg.count_param_entries = goc_dem
        khoiphuc.gg.status_of = goc_tt


@test
def thuc_thi_dung_thu_tu_replicat_truoc_extract():
    ctl = _CtlGia()
    r = _chay(27, 27, "STOPPED", "STOPPED", ctl)
    assert r["ok"] is True
    assert ctl.goi == ["start RHT", "start ETHT"], ctl.goi


@test
def thuc_thi_mat_du_lieu_chi_DUNG_khong_chay_gi():
    ctl = _CtlGia()
    r = _chay(28, 27, "RUNNING", "RUNNING", ctl)
    assert r["ok"] is True
    assert ctl.goi == ["stop RHT", "stop ETHT"], ctl.goi
    assert not any(g.startswith("start") for g in ctl.goi)


@test
def thuc_thi_manager_truoc_tien():
    ctl = _CtlGia()
    _chay(27, 27, "STOPPED", "STOPPED", ctl, mgr="STOPPED")
    assert ctl.goi[0] == "start MGR", ctl.goi


@test
def thuc_thi_DUNG_NGAY_khi_lenh_dau_khong_dat():
    """Chay tiep khi lenh truoc khong dat la lam tinh hinh xau them."""
    ctl = _CtlGia(no={"start RHT"})
    r = _chay(27, 27, "STOPPED", "STOPPED", ctl)
    assert r["ok"] is False
    assert ctl.goi == ["start RHT"], "da chay tiep du lenh dau khong dat: %r" % ctl.goi
    assert "KHÔNG" in r["msg"]


@test
def thuc_thi_DUNG_NGAY_khi_lenh_dau_nem_ngoai_le():
    ctl = _CtlGia(hong={"start RHT"})
    r = _chay(27, 27, "STOPPED", "STOPPED", ctl)
    assert r["ok"] is False
    assert ctl.goi == ["start RHT"], ctl.goi
    assert "ggsci chet" in r["msg"]


@test
def thuc_thi_khong_co_viec_gi_thi_khong_goi_lenh_nao():
    ctl = _CtlGia()
    r = _chay(27, 27, "RUNNING", "RUNNING", ctl)
    assert r["ok"] is True and ctl.goi == []
    assert "Không chạy lệnh nào" in r["msg"]


@test
def khong_nap_dan_khi_khong_co_viec_gi():
    """Hien nut "Chay" ma bam vao khong lam gi la noi doi voi nguoi dung."""
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    # Neo vao `khoiphuc.kham(` chu KHONG vao `if op == "KHOI_PHUC":`: chuoi
    # do xuat hien 3 lan trong main.py (muc kiem, nap dan, thuc thi) va
    # `index` tra lan DAU - dung cach da lam vo chinh tinh nang nay mot lan.
    i = src.index("khoiphuc.kham(")
    khoi = src[i - 400:i + 1200]
    assert 'if not k["the_chay"]' in khoi, "van nap dan du khong co viec gi"
    assert "_tin.html" in khoi
    # Phai tra ve TRUOC khi dat `dif`/`extra` (tuc truoc khi nap dan)
    assert khoi.index('_tin.html') < khoi.index("dif = khoiphuc.xem_truoc")
