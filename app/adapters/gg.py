"""Boc ggsci.

HAI NGUYEN TAC khong thuong luong:

1. MOT HANG DOI TUAN TU DUY NHAT. ggsci khong an toan khi goi song song,
   ma nhieu browser mo cung luc se goi song song. Moi lenh di qua _LOCK.

2. KHONG CO HOP NHAP LENH TU DO. App chi gui cac "intent" da dinh nghia
   san, tham so validate theo enum. Blocklist chi la lop phong thu thu hai.
   Ly do: blocklist ban chat la va lo - luon con cu phap chua nghi toi
   (`OBEY` nap file script tuy y, `SHELL` escape, wildcard `stop RPM*`, va
   `ALTER ... EXTSEQNO` con nguy hon `BEGIN NOW` vi nhay checkpoint la
   mat du lieu IM LANG).

Cam bay da code hoa o day:
  - lag CHI doc tu `info all`. `info <proc>, detail` co dong "Last Started"
    lam cot 4 thanh chu 'Started', so sanh voi 00:00:00 khong bao gio bang
    -> vong cho drain chay het so lan vo ich (cam bay 26).
  - `MAP (TABLE) resolved` KHONG in du so dong luc Extract khoi dong: GG
    phan giai tung TABLE khi gap DML dau tien cua bang do. Dung dem so dong
    nay de ket luan "Extract da nhan du N bang".
  - `QU_ERROR` CO CHUA chuoi `ERROR` -> grep ERROR thuan cho duong tinh gia.
  - Tham so chua \\n \\r ; ! bi tu choi: ggsci tach lenh theo DONG, mot \\n
    trong ten bang la LENH THU HAI.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
from dataclasses import dataclass

_LOCK = asyncio.Lock()

NOISE = re.compile(r"post-quantum|store now|upgraded|^Copyright|^Version |"
                   r"^Oracle GoldenGate Command Interpreter|^Operating system char",
                   re.I)
ROW = re.compile(r"^(MANAGER|EXTRACT|REPLICAT)\s+(\S+)(?:\s+(\S+))?"
                 r"(?:\s+(\d\d:\d\d:\d\d))?(?:\s+(\d\d:\d\d:\d\d))?\s*$")
HHMMSS = re.compile(r"^\d\d:\d\d:\d\d$")
BAD_ARG = re.compile(r"[\n\r;!]")

# Process duoc phep tac dong - enum co dinh, khong nhan gia tri ngoai danh sach
PROCESSES = frozenset({"ETPMNV", "ETHT", "ETDTHU", "RPMNVODS", "RHT", "RDTHU", "MGR"})

# Alias SUY RA TU INTENT, nguoi dung KHONG duoc chon.
# Ly do: ca 2 DB deu co SERVICE_NAME = BSHC nen prompt GGSCI hien @BSHC cho
# CA HAI - nhin man hinh khong phan biet duoc dang noi vao dau.
ALIAS_SOURCE = "pmnv_ogg"   # @PMNV: add/info trandata, register extract
ALIAS_TARGET = "bshc_ogg"   # @BSHC: info replicat detail, checkpointtable

# Lop phong thu thu hai. Kiem tren chuoi DA RENDER XONG, ngay truoc khi ghi
# vao stdin cua ggsci. Khop thi raise + ghi audit SECURITY_BLOCK.
BLOCKED = [
    re.compile(p, re.I) for p in (
        r"\bALTER\s+(EXTRACT|REPLICAT)\b.*\b(BEGIN|EXTSEQNO|EXTRBA|SCN)\b",
        r"\bCLEANUP\b", r"\bDELETE\s+TRACETABLE\b",
        r"\b(ADD|DELETE)\s+RMTTRAIL\b",
        r"\bKILL\b", r"\bSTOP\s+MGR\b", r"\bSTOP\s+ER\b", r"\bSTART\s+ER\b",
        r"\bSHELL\b", r"^\s*!", r"\bOBEY\b", r"\bFORCESTOP\b",
        r"\bSET\s+EDITOR\b", r"\bEDIT\s+PARAM\b", r"\bCREATE\s+SUBDIRS\b",
        r"\*",                                    # moi wildcard trong ten process
        r"\b(REGISTER|UNREGISTER)\s+EXTRACT\b",   # chi mo trong wizard NEW_STREAM
        r"\b(ADD|DELETE)\s+(EXTRACT|REPLICAT|EXTTRAIL|CHECKPOINTTABLE)\b",
        r"\bDELETE\s+TRANDATA\b",
    )
]


class Blocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcState:
    program: str          # MANAGER | EXTRACT | REPLICAT
    name: str             # MGR | ETHT | ...
    status: str           # RUNNING | STOPPED | ABENDED | STARTING
    lag_s: int | None     # tu cot 4 cua `info all`
    since_chkpt_s: int | None

    @property
    def is_running(self) -> bool:
        return self.status == "RUNNING"


def _hhmmss_to_s(v: str | None) -> int | None:
    if not v or not HHMMSS.match(v):
        return None
    h, m, s = v.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def check_arg(v: str) -> str:
    """Tham so di vao script ggsci. Tu choi ky tu tach lenh."""
    if BAD_ARG.search(v):
        raise Blocked(f"Tham số chứa ký tự tách lệnh của ggsci: {v!r}")
    return v


def check_script(script: str) -> None:
    for pat in BLOCKED:
        if pat.search(script):
            raise Blocked(f"Lenh bi chan boi blocklist: /{pat.pattern}/")


def _clean(out: str) -> list[str]:
    return [l.rstrip() for l in out.splitlines()
            if l.strip() and not NOISE.search(l)]


async def run(cfg, script: str, *, timeout: int = 60, allow_blocked: bool = False
              ) -> tuple[int, list[str]]:
    """Chay mot script ggsci. TUAN TU TUYET DOI qua _LOCK."""
    if not allow_blocked:
        check_script(script)
    body = script if script.rstrip().endswith("exit") else script.rstrip() + "\nexit\n"

    async with _LOCK:
        t0 = time.monotonic()
        p = await asyncio.to_thread(
            subprocess.run,
            [str(cfg.gg_home / "ggsci")],
            input=body, capture_output=True, text=True,
            cwd=str(cfg.gg_home), env=cfg.subprocess_env(),
            timeout=timeout, shell=False,          # shell=False tuyet doi
        )
        dt = int((time.monotonic() - t0) * 1000)
    lines = _clean(p.stdout + "\n" + p.stderr)
    lines.append(f"__duration_ms={dt}")
    return p.returncode, lines


def parse_info_all(lines: list[str]) -> list[ProcState]:
    r"""Parse THUAN - tach ra de test duoc bang fixture output THAT.

    ROW neo ca dau VA cuoi dong (`$`), co y. Do la thu chan dong cua
    `info <proc>, detail`:

        REPLICAT   RHT   Last Started 2026-10-13 17:20   Status RUNNING

    Dong tren co qua nhieu token nen KHONG khop ROW. Neu khong neo cuoi
    dong thi `(\S+)` thu hai se bat chu `Last` va cot thu ba bat `Started`,
    roi so sanh `Started == "00:00:00"` khong bao gio bang -> vong cho drain
    chay het so lan vo ich tren mot he hoan toan binh thuong (cam bay 26).
    """
    out: list[ProcState] = []
    for l in lines:
        m = ROW.match(l)
        if not m:
            continue
        prog, c2, c3, c4, c5 = m.groups()
        if prog == "MANAGER":
            # MANAGER RUNNING  (khong co ten, khong co lag)
            out.append(ProcState("MANAGER", "MGR", c2, None, None))
            continue
        out.append(ProcState(prog, c3 or c2, c2,
                             _hhmmss_to_s(c4), _hhmmss_to_s(c5)))
    return out


async def info_all(cfg) -> list[ProcState]:
    """Nguon duy nhat de doc TRANG THAI va LAG. Xem cam bay 26."""
    _, lines = await run(cfg, "info all", timeout=45)
    return parse_info_all(lines)


def alias_for(process: str) -> str:
    """Alias SUY RA TU LOAI PROCESS, nguoi dung khong chon.

    Extract doc redo o NGUON -> pmnv_ogg. Replicat apply o DICH -> bshc_ogg.
    Hardcode MOT alias cho ca hai la loi: ca 2 DB cung SERVICE_NAME=BSHC nen
    prompt GGSCI hien @BSHC cho ca hai, nhin man hinh khong phat hien ra.
    (Chinh ham detail() ben duoi da mac loi do - sua 2026-10-14.)
    """
    return ALIAS_SOURCE if process.startswith("ET") else ALIAS_TARGET


async def detail(cfg, process: str) -> dict[str, str]:
    """`info <proc>, detail`. CHI dung cho seqno/RBA/watermark - KHONG lay lag.

    Phai dblogin bang ALIAS truoc, neu khong watermark in ra
    "(requires database login)" - de tuong la khong co.
    """
    if process not in PROCESSES:
        raise Blocked(f"Process ngoai enum: {process!r}")
    _, lines = await run(
        cfg, f"dblogin useridalias {alias_for(process)}\ninfo {check_arg(process)}, detail",
        timeout=60)
    return parse_detail(lines)


def parse_detail(lines: list[str]) -> dict[str, str]:
    """Parse THUAN output cua `info <proc>, detail` - tach ra de test duoc.

    Dong trail cua Extract co dang (khoang trang canh cot, khong co dau phay):

        Trail Name                     Seqno      RBA   Max MB Trail Type
        /u01/app/ogg/dirdat/ht             5   199839      100 EXTTRAIL

    Replicat thi khong co bang nay; no co `Log Read Checkpoint File <duong
    dan day du>` roi `RBA <n>` o dong KE TIEP - nen `rba` phai dung
    setdefault, de dong EXTTRAIL cua Extract thang truoc.
    """
    d: dict[str, str] = {}
    for l in lines:
        if m := re.search(r"High Watermark CSN value:\s*(\d+)", l):
            d["high_wm"] = m.group(1)
        elif m := re.search(r"Low Watermark CSN value:\s*(\d+)", l):
            d["low_wm"] = m.group(1)
        elif m := re.search(r"Current Log BSN value:\s*(\d+)", l):
            d["log_bsn"] = m.group(1)
        elif m := re.search(r"SCN\s+[\d.]+\s+\((\d+)\)", l):
            d.setdefault("read_scn", m.group(1))
        elif m := re.search(r"dirdat/(\w+)\s+(\d+)\s+(\d+)\s+(\d+)\s+EXTTRAIL", l):
            d["trail"], d["seqno"], d["rba"] = m.group(1), m.group(2), m.group(3)
            d["trail_max_mb"] = m.group(4)
        elif m := re.search(r"Log Read Checkpoint\s+File\s+(\S+)", l):
            d["read_file"] = m.group(1)
        elif m := re.search(r"RBA\s+(\d+)", l):
            d.setdefault("rba", m.group(1))
    return d


async def checkprm(cfg, param_file: str, kind: str) -> tuple[bool, list[str]]:
    if kind not in ("EXTRACT", "REPLICAT", "MANAGER"):
        raise Blocked(f"loại quy trình không hợp lệ: {kind!r}")
    async with _LOCK:
        p = await asyncio.to_thread(
            subprocess.run,
            [str(cfg.gg_home / "checkprm"),
             f"dirprm/{check_arg(param_file)}", "-c", kind],
            capture_output=True, text=True, cwd=str(cfg.gg_home),
            env=cfg.subprocess_env(), timeout=60, shell=False)
    lines = _clean(p.stdout + "\n" + p.stderr)
    bad = any(re.search(r"\b(error|invalid|unrecognized)\b", l, re.I) for l in lines)
    return (not bad), lines


def count_param_entries(cfg, process: str, keyword: str) -> int:
    """Dem dong TABLE/MAP trong param file.

    Day la cach DUNG de kiem Extract da nhan du bang hay chua - KHONG phai
    dem so dong `MAP (TABLE) resolved` trong ggserr.log.
    """
    path = cfg.param_file(process)
    if not path.exists():
        return -1
    n = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.upper().startswith(keyword.upper() + " "):
                n += 1
    return n


# =====================================================================
# PHASE 2 - cac intent GHI.
#
# Tat ca deu di qua `run()` nen tat ca deu: (a) qua hang doi tuan tu,
# (b) qua blocklist lop 2, (c) tham so qua check_arg + enum PROCESSES.
# Khong ham nao o duoi nhan chuoi lenh tu do.
# =====================================================================

TRANDATA_OK = re.compile(
    r"Logging of supplemental redo data enabled|"
    r"TRANDATA for .* table .* (?:is already )?(?:enabled|added)", re.I)
# Dong CAN DOC khi kiem TRANDATA. KHONG doc OGG-26035: dong do phan anh
# tham so DB-level `enable_goldengate_replication=TRUE` nen in ra cho MOI
# bang trong schema, ke ca bang chua add TRANDATA. Dem OGG-26035 la cach
# da cho ra con so sai "1.866 bang" phai dinh chinh.
TRANDATA_STATE = re.compile(
    r"Logging of supplemental redo log data is (enabled|disabled)", re.I)


def _assert_proc(process: str) -> str:
    if process not in PROCESSES:
        raise Blocked(f"Process ngoai enum: {process!r}")
    return process


def _kind_of(process: str) -> str:
    if process == "MGR":
        return "MANAGER"
    return "EXTRACT" if process.startswith("ET") else "REPLICAT"


def trail_file(cfg, prefix: str, seqno: int | str):
    """Duong dan file trail cho mot seqno.

    KHONG DOAN DO RONG CHUOI SO. Do rong khac nhau giua cac ban GoldenGate:
    ban cu 6 chu so (`ht000005`), ban 21.3 o he nay dung 9 (`ht000000005`).
    Doan 6 thi `exists()` tra False -> `trail_bytes` None -> dieu kien drain
    KHONG BAO GIO dung -> `wait_drain` chay het thoi gian cho tren mot he
    HOAN TOAN BINH THUONG -> `quiesce()` start lai Extract roi bao that bai.
    Tuc la verify che do chuan SE LUON LOI. Da gap that o lan chay dau tien
    (2026-10-14): trail_bytes=None, drained=False.

    Vi vay: quet thu muc va so KHOP THEO SO, khong theo ten.
    """
    d = cfg.gg_home / "dirdat"
    pre = check_arg(prefix)
    n = int(seqno)
    for f in sorted(d.glob(f"{pre}*")):
        suf = f.name[len(pre):]
        if suf.isdigit() and int(suf) == n:
            return f
    return d / f"{pre}{n:09d}"          # mac dinh theo ban 21.3


async def status_of(cfg, process: str) -> ProcState | None:
    for p in await info_all(cfg):
        if p.name == process:
            return p
    return None


async def wait_status(cfg, process: str, want: str, *, timeout_s: int = 120,
                      every_s: float = 3.0) -> tuple[bool, str]:
    """Poll `info all` cho den khi process ve trang thai `want`.

    POLL, khong `sleep` co dinh. Va doc tu `info all` chu KHONG tu
    `info <proc>, detail` - xem cam bay 26 o dau file.

    `STOPPING` KHONG tinh la `STOPPED`: do la trang thai trung gian, va
    coi no la da dung roi thi se sua param file cua mot process van dang
    ghi checkpoint.
    """
    _assert_proc(process)
    t0 = time.monotonic()
    last = "?"
    while time.monotonic() - t0 < timeout_s:
        p = await status_of(cfg, process)
        last = p.status if p else "khong thay"
        if p is not None and p.status == want:
            return True, last
        await asyncio.sleep(every_s)
    return False, last


async def start_process(cfg, process: str, *, timeout_s: int = 180
                        ) -> tuple[bool, str, list[str]]:
    _assert_proc(process)
    if process == "MGR":
        raise Blocked("START MGR khong nam trong pham vi cong cu")
    kind = "EXTRACT" if process.startswith("ET") else "REPLICAT"
    _, lines = await run(cfg, f"start {kind.lower()} {check_arg(process)}",
                         timeout=90)
    ok, last = await wait_status(cfg, process, "RUNNING", timeout_s=timeout_s)
    return ok, last, lines


async def stop_process(cfg, process: str, *, timeout_s: int = 300
                       ) -> tuple[bool, str, list[str]]:
    """STOP thuong (graceful). KHONG dung FORCESTOP - no bi blocklist chan.

    Ly do FORCESTOP bi chan: no cat ngang giua transaction dang apply, de
    lai checkpoint khong khop voi trang thai that cua DB dich.
    """
    _assert_proc(process)
    if process == "MGR":
        raise Blocked("STOP MGR bi chan: Manager dung thi moi process mat "
                      "nguoi giam sat va khong tu khoi dong lai")
    kind = "EXTRACT" if process.startswith("ET") else "REPLICAT"
    _, lines = await run(cfg, f"stop {kind.lower()} {check_arg(process)}",
                         timeout=90)
    ok, last = await wait_status(cfg, process, "STOPPED", timeout_s=timeout_s)
    return ok, last, lines


async def drain_state(cfg, extract: str, replicat: str, prefix: str) -> dict:
    """Do tinh trang drain SAU KHI da stop Extract.

    DO TREN REPLICAT, KHONG DO TREN EXTRACT. Day la loi thu tu da mac
    (2026-10-14, lan chay that dau tien):

      RBA cua Extract bang kich thuoc file trail NGAY KHI no dung flush.
      Do no thi dieu kien "rba = kich thuoc trail" dung ngay lap tuc va
      KHONG NOI GI ve viec Replicat da apply het trail chua. Te hon:
      lag cua mot Extract DA DUNG se dong bang o gia tri luc dung
      (vi du 00:00:01) va KHONG BAO GIO ve 00:00:00 - nen vong cho chay
      het thoi gian roi bao that bai tren mot he hoan toan binh thuong.

    Dieu kien DUNG, dung nhu runbook: ben DOC (Replicat) phai doc het ben
    GHI (Extract) da ghi:

      1. Replicat dang doc DUNG file trail cuoi cung ma Extract da ghi
      2. `rba` cua Replicat = KICH THUOC file trail do
      3. lag cua Replicat = 00:00:00

    Duong dan file lay TU OUTPUT (`Log Read Checkpoint File ...`), khong tu
    ghep ten - do rong chuoi so khac nhau giua cac ban GoldenGate.

    KHONG BAO GIO cho `Low WM = High WM`: hai moc do khong gap nhau, vong
    cho se chay het so lan roi bao that bai tren he binh thuong.
    """
    from pathlib import Path

    de = await detail(cfg, extract)     # ben GHI
    dr = await detail(cfg, replicat)    # ben DOC
    pr = await status_of(cfg, replicat)

    seq_ghi = de.get("seqno")
    rba_ghi = de.get("rba")
    rba_doc = dr.get("rba")
    doc_file = dr.get("read_file")
    lag = pr.lag_s if pr else None

    size = None
    seq_doc = None
    if doc_file:
        f = Path(doc_file)
        if f.exists():
            size = f.stat().st_size
        suf = f.name[len(prefix):] if f.name.startswith(prefix) else ""
        seq_doc = int(suf) if suf.isdigit() else None
    elif seq_ghi is not None:
        # Replicat chua in read_file (vua start chang han) -> suy ra tu seqno
        f = trail_file(cfg, prefix, seq_ghi)
        if f.exists():
            size = f.stat().st_size

    cung_file = (seq_doc is not None and seq_ghi is not None
                 and seq_doc == int(seq_ghi))
    het_file = (rba_doc is not None and size is not None
                and int(rba_doc) == int(size))
    done = bool(cung_file and het_file and lag == 0)

    # `vi_sao` de khi vong cho het gio thi biet NGAY tai sao, chu khong phai
    # doan. Chinh cho nay da an hai loi lien tiep.
    if done:
        vi_sao = "da drain"
    elif doc_file is None and seq_ghi is None:
        vi_sao = "không đọc được mốc checkpoint của cả hai bên"
    elif not cung_file:
        vi_sao = (f"{replicat} đang đọc trail số {seq_doc}, {extract} đã ghi đến số "
                  f"{seq_ghi}")
    elif size is None:
        vi_sao = f"không thấy file trail {doc_file}"
    elif not het_file:
        vi_sao = (f"{replicat} đọc đến {rba_doc}/{size} byte "
                  f"(còn {int(size) - int(rba_doc):,} byte chưa áp)")
    else:
        vi_sao = f"{replicat} còn chậm {lag}s, chưa về 0"

    return {"seqno_ghi": seq_ghi, "rba_ghi": int(rba_ghi) if rba_ghi else None,
            "seqno_doc": seq_doc, "rba_doc": int(rba_doc) if rba_doc else None,
            "trail_bytes": size, "trail_path": doc_file,
            "lag_s": lag, "status": pr.status if pr else "?",
            "drained": done, "vi_sao": vi_sao}


async def wait_drain(cfg, extract: str, replicat: str, prefix: str, *,
                     timeout_s: int = 600, every_s: float = 5.0
                     ) -> tuple[bool, dict]:
    t0 = time.monotonic()
    st: dict = {}
    while time.monotonic() - t0 < timeout_s:
        st = await drain_state(cfg, extract, replicat, prefix)
        if st["drained"]:
            return True, st
        await asyncio.sleep(every_s)
    return False, st


async def stats_process(cfg, process: str, table: str | None = None
                        ) -> list[str]:
    """`stats` - cach DUNG de xac nhan mot MAP moi da thuc su apply.

    Doc o day thay vi dem dong `MAP (TABLE) resolved` trong ggserr.log:
    GG phan giai tung TABLE khi gap DML dau tien cua bang do, nen so dong
    resolved luc khoi dong KHONG bang so bang.
    """
    _assert_proc(process)
    kind = "EXTRACT" if process.startswith("ET") else "REPLICAT"
    cmd = f"stats {kind.lower()} {check_arg(process)}, latest"
    if table:
        cmd += f", table {check_arg(table)}"
    _, lines = await run(cfg, cmd, timeout=90)
    return lines


async def view_param(cfg, process: str) -> list[str]:
    _assert_proc(process)
    _, lines = await run(cfg, f"view param {check_arg(process)}", timeout=45)
    return lines


async def info_trandata(cfg, owner: str, table: str) -> tuple[bool | None, list[str]]:
    """Trang thai TRANDATA cua MOT bang. Khong nhan wildcard.

    Tra (True da bat | False chua bat | None khong doc duoc, cac dong raw).
    """
    if owner != "KT_BSH":
        raise Blocked(f"Schema nguồn là hằng số KT_BSH, không nhận {owner!r}")
    _, lines = await run(
        cfg,
        f"dblogin useridalias {ALIAS_SOURCE}\n"
        f"info trandata {owner}.{check_arg(table)}",
        timeout=60)
    for l in lines:
        if m := TRANDATA_STATE.search(l):
            return (m.group(1).lower() == "enabled"), lines
    return None, lines


async def add_trandata(cfg, owner: str, table: str,
                       cols: list[str] | None = None
                       ) -> tuple[bool, list[str]]:
    """ADD TRANDATA cho MOT bang, tai @PMNV.

    Alias SUY RA TU INTENT (ALIAS_SOURCE), nguoi dung khong chon: ca 2 DB
    cung SERVICE_NAME=BSHC nen prompt GGSCI hien @BSHC cho ca hai.
    """
    if owner != "KT_BSH":
        raise Blocked(f"Schema nguồn là hằng số KT_BSH, không nhận {owner!r}")
    cmd = f"add trandata {owner}.{check_arg(table)}"
    if cols:
        for c in cols:
            check_arg(c)
            if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,29}", c):
                raise Blocked(f"Tên cột không hợp lệ: {c!r}")
        cmd += ", COLS (" + ", ".join(cols) + ")"
    _, lines = await run(
        cfg, f"dblogin useridalias {ALIAS_SOURCE}\n{cmd}", timeout=120)
    ok = any(TRANDATA_OK.search(l) for l in lines)
    return ok, lines


# =====================================================================
# PHASE 4 - LOP KHOANH VUNG cho nhom lenh KHONG CHAN DUOC
#
# Wizard NEW_STREAM can chinh nhung lenh nam trong blocklist:
#   REGISTER/UNREGISTER EXTRACT, ADD/DELETE EXTRACT, ADD/DELETE REPLICAT,
#   ADD/DELETE EXTTRAIL, ADD/DELETE CHECKPOINTTABLE
#
# Chan thuan thi wizard khong lam duoc gi; bo chan thi mat han phong tuyen.
# Luat thay the, CHAT HON chan thuan:
#
#   1. Chi chay duoc BEN TRONG mot phien NEW_STREAM dang mo, o dung buoc.
#   2. Chi tac dong len process/trail/checkpoint table ma CHINH PHIEN DO
#      da tao. Ten nam trong wizard_run.
#   3. 6 process + 3 trail + 3 checkpoint table dang chay la BAT KHA XAM
#      PHAM - khong co duong nao trong app cham toi duoc.
#
# Lop 3 duoc kiem TRUOC lop 2 va khong co ngoai le nao: du phien co khai
# ten do trong `cho_phep` thi van bi tu choi.
# =====================================================================

# Doi tuong dang CHAY - khong lenh nao cua app duoc cham vao.
BAT_KHA_XAM_PHAM = frozenset(
    {p.upper() for p in PROCESSES}
    | {"PM", "HT", "DT"}                                   # trail prefix
    | {"OGG_BSH.RPMNVODS", "OGG_BSH.RHT", "OGG_BSH.RDTHU"}  # checkpoint table
    | {"RPMNVODS", "RHT", "RDTHU"}
)

# Ten group GoldenGate: toi da 8 ky tu.
TEN_GROUP = re.compile(r"^[A-Z][A-Z0-9_]{1,7}$")
# Trail prefix: DUNG 2 ky tu. Khong phai quy uoc tham my - GG dung 2 ky tu
# dau lam tien to file va phan con lai la chuoi so.
TRAIL_PREFIX = re.compile(r"^[a-z]{2}$")

# Cac dong tu chi duoc chay trong pham vi phien NEW_STREAM.
DONG_TU_PHAM_VI = re.compile(
    r"\b(REGISTER|UNREGISTER|ADD|DELETE)\s+"
    r"(EXTRACT|REPLICAT|EXTTRAIL|CHECKPOINTTABLE)\b", re.I)


def kiem_ten_moi(ten: str, loai: str) -> str:
    """Ten moi cho process/trail. KHONG tu dat ten - DBA go, cong cu kiem."""
    t = ten.strip()
    if loai == "trail":
        if not TRAIL_PREFIX.match(t):
            raise Blocked(
                "Tiền tố trail phải ĐÚNG 2 ký tự thường (ví dụ pm/ht/dt), nhận được "
                f"{ten!r}")
    else:
        t = t.upper()
        if not TEN_GROUP.match(t):
            raise Blocked(
                f"Tên {loai} phải 2-8 ký tự, bắt đầu bằng chữ cái, chỉ gồm [A-Z0-9_]: "
                f"{ten!r}")
    if t.upper() in BAT_KHA_XAM_PHAM:
        raise Blocked(
            f"{ten!r} là đối tượng ĐANG CHẠY - bất khả xâm phạm. Công cụ không tạo, "
            "không sửa, không xoá được các tên này.")
    return t


def kiem_pham_vi(script: str, cho_phep: set[str]) -> None:
    """Kiem mot script dung nhom lenh pham vi.

    Thu tu kiem KHONG duoc dao: lop bat kha xam pham truoc, roi moi den
    allowlist cua phien. Neu dao lai thi mot phien khai `cho_phep={"RHT"}`
    se xoa duoc Replicat that.
    """
    tren = script.upper()

    # LOP 3 - khong co ngoai le
    for ten in sorted(BAT_KHA_XAM_PHAM):
        if re.search(rf"\b{re.escape(ten)}\b", tren):
            raise Blocked(
                f"Script chạm vào {ten} - đối tượng ĐANG CHẠY, bất khả xâm phạm. Từ chối "
                "kể cả khi phiên khai nó trong danh sách cho phép.")

    # LOP 1 - phai dung dong tu pham vi
    if not DONG_TU_PHAM_VI.search(script):
        raise Blocked("hàm kiểm phạm vi chỉ dùng cho nhóm lệnh tạo / xoá / đăng ký - không "
                      "dùng ở đây")

    # LOP 2 - moi identifier phai nam trong allowlist cua phien
    cp = {c.upper() for c in cho_phep}
    if not cp:
        raise Blocked("Phiên chưa tạo tên nào - không có gì được phép chạm")
    for m in DONG_TU_PHAM_VI.finditer(script):
        duoi = script[m.end():m.end() + 120]
        # Identifier dau tien sau dong tu: ten group, duong dan trail,
        # hoac schema.table cua checkpoint table
        im = re.search(r"[A-Za-z0-9_$#./]+", duoi)
        if not im:
            raise Blocked(f"Không đọc được tên đối tượng sau {m.group(0)!r}")
        doi_tuong = im.group(0).rstrip(",;").upper()
        goc = doi_tuong.rsplit("/", 1)[-1]      # duong dan trail -> ten file
        if doi_tuong not in cp and goc not in cp:
            raise Blocked(
                f"{m.group(0)} nhắm vào {doi_tuong!r} mà phiên này không tạo ra. Chỉ "
                f"được chạm vào: {', '.join(sorted(cp))}")


async def run_pham_vi(cfg, script: str, cho_phep: set[str], *,
                      timeout: int = 180) -> tuple[int, list[str]]:
    """Chay mot script trong nhom lenh pham vi. Qua kiem_pham_vi() truoc."""
    kiem_pham_vi(script, cho_phep)
    return await run(cfg, script, timeout=timeout, allow_blocked=True)
