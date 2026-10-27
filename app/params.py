"""Sua param file co cau truc, co diff, co tu phuc hoi.

QUY TRINH BAT BUOC, khong buoc nao bo duoc:

    doc ban hien tai -> kiem sha (chong ghi de nguoi khac)
    -> guard noi dung -> backup `cp -p` co timestamp
    -> ghi nguyen tu (temp + os.replace)
    -> checkprm  -> FAIL thi TU RESTORE tu backup roi raise
    -> ghi param_version + luu ban da ap vao scripts/

HAI LUAT TUYET DOI

1. KHONG BAO GIO ghi param cua process dang RUNNING.
   Extract/Replicat chi doc param file LUC KHOI DONG (cam bay 34). Sua file
   cua process dang chay khong co hieu luc ngay; no co hieu luc o lan
   restart bat ky ve sau - ke ca restart ngoai y muon luc 2 gio sang. Do la
   thay doi co hieu luc o thoi diem khong ai doan duoc, te hon ca loi.

2. Giam so dong MAP la MAT DU LIEU IM LANG.
   Bo mot dong MAP trong khi dong TABLE tuong ung van con: Extract tiep tuc
   dao redo cua bang do va ghi vao trail, Replicat khong co MAP nen BO QUA
   ban ghi VA VAN DOI CHECKPOINT. Khong discard, khong log, `info all` van
   RUNNING lag 0. Nen giam MAP bi chan o muc nay, khong phai nhac nho.

GUARD NOI DUNG - nhung gi tuyet doi khong cho vao param file:
  - `USERID ... PASSWORD <...>`: mat khau dang ro. Thanh qua 0/7 cua Phase 0
    khong duoc phep thoai lui bang mot lan sua param.
  - bo `USERIDALIAS` khi ban cu da co: process se khong dang nhap duoc.
  - `MEGABYTES` tren THAM SO `EXTTRAIL`: chi hop le tren LENH
    `ADD EXTTRAIL`. Da gap that: checkprm bao
    `parameter [exttrail] has unrecognized keyword or extra value "MEGABYTES"`.
  - `ALLOWNOOPUPDATES`, `HANDLECOLLISIONS` toan cuc (khong gan voi MAP):
    bat toan cuc thi no am tham bo qua loi tren MOI bang, ke ca bang dang
    chay on dinh.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .adapters import gg

log = logging.getLogger("ggadmin.params")

RE_USERID_PW = re.compile(r"^\s*USERID\b.*\bPASSWORD\b", re.I | re.M)
RE_USERIDALIAS = re.compile(r"^\s*USERIDALIAS\s+(\S+)", re.I | re.M)
RE_EXTTRAIL_MB = re.compile(r"^\s*EXTTRAIL\b.*\bMEGABYTES\b", re.I | re.M)
RE_HC_GLOBAL = re.compile(r"^\s*HANDLECOLLISIONS\s*$", re.I | re.M)
RE_TABLE = re.compile(r"^\s*TABLE\s+(\S+?)\s*;", re.I | re.M)
RE_MAP = re.compile(r"^\s*MAP\s+(\S+?)\s*,", re.I | re.M)

MAX_SIZE = 512 * 1024          # param file that lon nhat ~40 KB


class ParamError(RuntimeError):
    def __init__(self, msg: str, lines: list[str] | None = None):
        super().__init__(msg)
        self.lines = lines or []


@dataclass(frozen=True)
class ParamFile:
    process: str
    path: Path
    text: str
    sha256: str
    mtime: int
    n_table: int
    n_map: int
    alias: str | None

    @property
    def sha_short(self) -> str:
        return self.sha256[:12]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def read(cfg, process: str) -> ParamFile:
    if process not in gg.PROCESSES:
        raise ParamError(f"Process ngoai enum: {process!r}")
    p = cfg.param_file(process)
    if not p.exists():
        raise ParamError(f"Không thấy {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    m = RE_USERIDALIAS.search(text)
    return ParamFile(process=process, path=p, text=text, sha256=_sha(text),
                     mtime=int(p.stat().st_mtime),
                     n_table=len(RE_TABLE.findall(text)),
                     n_map=len(RE_MAP.findall(text)),
                     alias=m.group(1) if m else None)


def diff(old: str, new: str, *, name: str = "param") -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{name} (bản đang có)", tofile=f"{name} (sau khi sửa)", n=3))


def diff_stats(old: str, new: str) -> dict:
    a, b = old.splitlines(), new.splitlines()
    sm = difflib.SequenceMatcher(None, a, b)
    them = xoa = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            xoa += i2 - i1
        if tag in ("replace", "insert"):
            them += j2 - j1
    return {"them": them, "xoa": xoa,
            "table_truoc": len(RE_TABLE.findall(old)),
            "table_sau": len(RE_TABLE.findall(new)),
            "map_truoc": len(RE_MAP.findall(old)),
            "map_sau": len(RE_MAP.findall(new))}


# ------------------------------------------------------------------- guard
def guard(old: str, new: str, *, process: str,
          cho_phep_bo: tuple[str, str] | None = None,
          extract_cap_doi: str | None = None) -> list[str]:
    """Tra danh sach ly do TU CHOI. Rong la cho qua.

    `cho_phep_bo=(owner, table)` - CHI quy trinh GO_BANG truyen. No mo dung
    MOT cua: cho phep so dong TABLE/MAP giam, voi dieu kien phan bi bo la
    DUNG MOT dong cua DUNG bang do va khong dong nao khac doi. Trinh sua
    tham so bang tay KHONG truyen -> van tu choi moi lan giam nhu truoc.
    Bo MAP con doi `extract_cap_doi` (noi dung .prm cua Extract cung luong):
    dong TABLE cua bang PHAI da het o do - neu khong la mat du lieu im lang.
    """
    loi: list[str] = []
    if len(new.encode()) > MAX_SIZE:
        loi.append(f"File quá lớn ({len(new.encode()):,} byte, trần {MAX_SIZE:,})")
    if not new.strip():
        loi.append("Nội dung rỗng")

    if RE_USERID_PW.search(new):
        loi.append("Có dòng `USERID ... PASSWORD` - mật khẩu để dạng rõ. Dùng "
                   "`USERIDALIAS` để lấy từ kho mật khẩu. Hệ này đã được đưa về 0/7 file "
                   "có mật khẩu rõ, không để thoái lui.")

    old_alias = RE_USERIDALIAS.search(old)
    if old_alias and not RE_USERIDALIAS.search(new):
        loi.append(f"Bản cũ có `USERIDALIAS {old_alias.group(1)}` mà bản mới "
                   f"không có - tiến trình sẽ không đăng nhập được vào DB.")

    if RE_EXTTRAIL_MB.search(new):
        loi.append("`MEGABYTES` chỉ hợp lệ trên LỆNH `ADD EXTTRAIL`, KHÔNG hợp lệ trên "
                   "THAM SỐ `EXTTRAIL` trong file .prm. Bước kiểm cú pháp sẽ báo "
                   "`unrecognized keyword or extra value \"MEGABYTES\"`.")

    if RE_HC_GLOBAL.search(new) and not RE_HC_GLOBAL.search(old):
        loi.append("`HANDLECOLLISIONS` đặt toàn cục (không gắn vào một dòng MAP) sẽ âm "
                   "thầm bỏ qua lỗi trên MỌI bảng, kể cả bảng đang chạy ổn định. Phải đặt "
                   "trên từng dòng MAP đang nạp.")

    st = diff_stats(old, new)
    if cho_phep_bo is not None:
        loi_bo = _kiem_bo_mot_bang(old, new, process, cho_phep_bo, extract_cap_doi)
        if loi_bo is None:
            return loi                   # dung mot dong cua dung bang - cho qua
        loi.append(loi_bo)
        return loi
    if st["map_sau"] < st["map_truoc"]:
        loi.append(
            f"Số dòng MAP giảm {st['map_truoc']} -> {st['map_sau']}. Bỏ dòng MAP "
            "trong khi dòng TABLE tương ứng còn lại = MẤT DỮ LIỆU IM LẶNG: Extract "
            "vẫn ghi vào trail, Replicat bỏ qua MÀ VẪN DỜI MỐC CHECKPOINT. Muốn bỏ "
            "bảng khỏi luồng thì bỏ dòng TABLE TRƯỚC, theo đúng quy trình.")
    if process.startswith("ET") and st["table_sau"] < st["table_truoc"]:
        loi.append(
            f"Số dòng TABLE giảm {st['table_truoc']} -> {st['table_sau']}. Đúng thứ "
            "tự quy trình: bỏ dòng TABLE, chạy lại Extract, chờ Replicat áp hết, "
            "rồi mới bỏ dòng MAP. Dùng thao tác nhanh «Gỡ bảng khỏi luồng» - nó đi "
            "đúng thứ tự đó; trình sửa tham số tay không được bỏ dòng.")
    return loi


# -------------------------------------------------------------------- ghi
async def save(cfg, con, process: str, new_text: str, *, who: str,
               reason: str, expect_sha: str | None = None,
               cho_phep_bo: tuple[str, str] | None = None,
               extract_cap_doi: str | None = None) -> dict:
    """Ghi param file. Raise ParamError neu bat ky buoc nao khong dat."""
    cur = read(cfg, process)

    # Chong ghi de: ai do (hoac chinh DBA o tab khac) da sua giua luc nay
    if expect_sha and expect_sha != cur.sha256:
        raise ParamError(
            "File đã bị sửa bởi người hoặc tiến trình khác (chữ ký hiện tại "
            f"{cur.sha_short}, bản bạn đang xem {expect_sha[:12]}). Tải lại rồi làm "
            "lại để không ghi đè thay đổi của người khác.")

    # Luat tuyet doi 1: khong ghi param cua process dang RUNNING
    st = await gg.status_of(cfg, process)
    if st is not None and st.status != "STOPPED":
        raise ParamError(
            f"{process} đang {st.status}, phải DỪNG mới sửa được tham số. Tiến trình "
            "chỉ đọc tham số lúc khởi động, nên sửa lúc đang chạy là tạo ra một "
            "thay đổi có hiệu lực ở thời điểm không ai đoán được.")

    new_text = new_text.replace("\r\n", "\n")
    if not new_text.endswith("\n"):
        new_text += "\n"
    if new_text == cur.text:
        raise ParamError("Nội dung không thay đổi")

    if loi := guard(cur.text, new_text, process=process,
                    cho_phep_bo=cho_phep_bo, extract_cap_doi=extract_cap_doi):
        raise ParamError("Khung an toàn từ chối:\n- " + "\n- ".join(loi))

    d = diff(cur.text, new_text, name=cur.path.name)
    st_diff = diff_stats(cur.text, new_text)

    # Backup `cp -p` (copy2 giu mode + mtime) co timestamp trong ten
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = cur.path.with_name(f"{cur.path.name}.bak_{stamp}")
    shutil.copy2(cur.path, bak)

    ok_prm = False
    prm_lines: list[str] = []
    try:
        _write_atomic(cur.path, new_text)
        kind = "EXTRACT" if process.startswith("ET") else "REPLICAT"
        ok_prm, prm_lines = await gg.checkprm(cfg, cur.path.name, kind)
        if not ok_prm:
            shutil.copy2(bak, cur.path)          # TU RESTORE
            _record(con, process, who, reason, bak, cur.sha256,
                    _sha(new_text), d, 0, 1)
            raise ParamError(
                "kiểm cú pháp KHÔNG ĐẠT - đã tự động phục hồi bản cũ từ "
                f"{bak.name}. File tren dia hien la ban TRUOC khi sua.",
                prm_lines)
    except ParamError:
        raise
    except Exception as e:                            # noqa: BLE001
        # Bat ky loi nao khac cung phai phuc hoi, khong de file nua voi
        try:
            shutil.copy2(bak, cur.path)
        except OSError:
            log.exception("KHÔNG phục hồi được %s từ %s", cur.path, bak)
        raise ParamError(f"Lỗi khi ghi tham số, đã phục hồi bản cũ: {e}") from e

    # Luu ban da ap vao scripts/ - cong cu khong duoc la hop den
    try:
        cfg.scripts_dir.mkdir(parents=True, exist_ok=True)
        (cfg.scripts_dir / f"{process.lower()}.prm.{stamp}").write_text(
            new_text, encoding="utf-8")
    except OSError:
        log.warning("không lưu được bản sao vào thư mục scripts/")

    pv = _record(con, process, who, reason, bak, cur.sha256, _sha(new_text),
                 d, 1, 0)
    return {"param_version_id": pv, "backup": str(bak), "diff": d,
            "checkprm_lines": prm_lines, "stats": st_diff,
            "sha_truoc": cur.sha256, "sha_sau": _sha(new_text)}


def _write_atomic(path: Path, text: str) -> None:
    """Ghi nguyen tu: temp cung thu muc roi os.replace.

    Cung thu muc de os.replace la atomic rename trong CUNG filesystem.
    Giu nguyen mode cua file cu (640 oracle:oinstall).
    """
    mode = path.stat().st_mode & 0o777
    tmp = path.with_name(path.name + ".tmp_ggadmin")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _record(con, process: str, who: str, reason: str, bak: Path,
            sha_before: str, sha_after: str, d: str,
            ok: int, restored: int) -> int:
    cur = con.execute(
        "INSERT INTO param_version(ts,process,changed_by,reason,backup_path,"
        "sha256_before,sha256_after,diff,checkprm_ok,restored)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (int(time.time()), process, who, reason, str(bak), sha_before,
         sha_after, d[:200_000], ok, restored))
    return int(cur.lastrowid)


def history(con, process: str | None = None, limit: int = 50) -> list[dict]:
    if process:
        rows = con.execute(
            "SELECT * FROM param_version WHERE process=? ORDER BY id DESC LIMIT ?",
            (process, limit))
    else:
        rows = con.execute(
            "SELECT * FROM param_version ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def backups(cfg, process: str) -> list[dict]:
    p = cfg.param_file(process)
    out = []
    for f in sorted(p.parent.glob(f"{p.name}.bak_*"), reverse=True):
        s = f.stat()
        out.append({"name": f.name, "bytes": s.st_size,
                    "mtime": int(s.st_mtime), "path": str(f)})
    return out


# ------------------------------------------------- sua co cau truc (helper)
def them_dong_table(text: str, owner: str, table: str) -> str:
    """Them `TABLE <owner>.<table>;` neu chua co. Giu nguyen thu tu con lai."""
    dong = f"TABLE {owner}.{table};"
    if re.search(rf"^\s*TABLE\s+{re.escape(owner)}\.{re.escape(table)}\s*;",
                 text, re.I | re.M):
        return text
    body = text if text.endswith("\n") else text + "\n"
    return body + dong + "\n"


def them_dong_map(text: str, owner: str, table: str, tgt_owner: str,
                  tgt_table: str, keycols: str | None,
                  handlecollisions: bool) -> str:
    """Them mot dong MAP day du.

    `COLMAP (USEDEFAULTS)` giu dung mau da chay that cho ca 3 luong.
    `KEYCOLS` dat TUONG MINH khi bang khong co PK that - GG khong dung duoc
    index function-based lam khoa.
    """
    if re.search(rf"^\s*MAP\s+{re.escape(owner)}\.{re.escape(table)}\s*,",
                 text, re.I | re.M):
        return text
    phan = [f"MAP {owner}.{table}", f"TARGET {tgt_owner}.{tgt_table}"]
    if keycols:
        phan.append(f"KEYCOLS ({keycols})")
    if handlecollisions:
        phan.append("HANDLECOLLISIONS")
    phan.append("COLMAP (USEDEFAULTS)")
    body = text if text.endswith("\n") else text + "\n"
    return body + ", ".join(phan) + ";\n"


def _re_table(owner: str, table: str) -> "re.Pattern":
    return re.compile(rf"^\s*TABLE\s+{re.escape(owner)}\.{re.escape(table)}\s*;",
                      re.I)


def _re_map(owner: str, table: str) -> "re.Pattern":
    return re.compile(rf"^\s*MAP\s+{re.escape(owner)}\.{re.escape(table)}\s*,", re.I)


def _bo_dong(text: str, pat: "re.Pattern", loai: str, owner: str, table: str) -> str:
    """Bo DUNG MOT dong khop `pat`. Khop 0 dong -> tra nguyen van (da bo
    roi, chay lai duoc). Khop TU 2 dong -> TU CHOI: file dang co dong trung,
    bo mot dong thi con mot dong, ma bo ca hai la tu doan."""
    dong = text.splitlines(keepends=True)
    trung = [i for i, l in enumerate(dong) if pat.match(l)]
    if len(trung) > 1:
        raise ParamError(f"Có {len(trung)} dòng {loai} cho {owner}.{table} - file đang "
                         "có dòng trùng. Sửa tay rồi làm lại, công cụ không tự chọn "
                         "bỏ dòng nào.")
    if not trung:
        return text
    del dong[trung[0]]
    return "".join(dong)


def bo_dong_table(text: str, owner: str, table: str) -> str:
    """Bo dong `TABLE <owner>.<table>;` - buoc 4 cua GO_BANG."""
    return _bo_dong(text, _re_table(owner, table), "TABLE", owner, table)


def bo_dong_map(text: str, owner: str, table: str) -> str:
    """Bo dong `MAP <owner>.<table>, ...;` - buoc 8 cua GO_BANG.

    Chi bo dong MAP nam tren MOT dong vat ly (dang cong cu tu sinh). Dong MAP
    keo dai nhieu dong (dau `&`) thi `_kiem_bo_mot_bang` se thay so dong bi
    bo khac 1 va tu choi.
    """
    return _bo_dong(text, _re_map(owner, table), "MAP", owner, table)


def _kiem_bo_mot_bang(old: str, new: str, process: str, bang: tuple[str, str],
                      extract_cap_doi: str | None) -> str | None:
    """None = dung mot dong cua dung bang bi bo, khong gi khac doi."""
    owner, table = bang
    a, b = old.splitlines(), new.splitlines()
    bo = [l for l in a if l not in b]
    them = [l for l in b if l not in a]
    if them:
        return f"Quy trình gỡ bảng chỉ được BỎ dòng, nhưng bản mới THÊM {len(them)} dòng."
    if len(bo) != 1 or len(a) - len(b) != 1:
        return (f"Quy trình gỡ bảng chỉ được bỏ ĐÚNG MỘT dòng, bản mới bỏ "
                f"{len(a) - len(b)} dòng.")
    la_extract = process.startswith("ET")
    pat = _re_table(owner, table) if la_extract else _re_map(owner, table)
    if not pat.match(bo[0]):
        return (f"Dòng bị bỏ không phải dòng {'TABLE' if la_extract else 'MAP'} của "
                f"{owner}.{table}: {bo[0].strip()[:120]}")
    if not la_extract:
        if extract_cap_doi is None:
            return "Bỏ dòng MAP mà không đối chiếu được file tham số của Extract."
        if any(_re_table(owner, table).match(l) for l in extract_cap_doi.splitlines()):
            return (f"Extract VẪN còn dòng TABLE {owner}.{table}. Bỏ MAP trước TABLE là "
                    "MẤT DỮ LIỆU IM LẶNG: Extract vẫn ghi vào trail, Replicat bỏ qua mà "
                    "vẫn dời mốc. Bỏ dòng TABLE trước.")
    return None


def dong_map_log(owner: str, table: str, tgt_owner: str, tgt_table: str) -> str:
    """Dong MAP cua LUONG LOG: moi thao tac thanh MOT dong INSERT.

    `INSERTALLRECORDS` chi chay voi `UPDATERECORDFORMAT FULL` o Extract (tai
    lieu GG 21.3) - Extract cua luong log dat san tham so do. UPDATE ghi anh
    SAU (Replicat mac dinh IGNOREUPDATEBEFORES), DELETE ghi gia tri cua dong
    bi xoa (Extract LOGALLSUPCOLS + nguon TRANDATA ALLCOLS).
    OPTYPE co the la INSERT / UPDATE / SQL COMPUPDATE / PK UPDATE / DELETE:
    moi bien the cua UPDATE gop ve 'U'.
    LOG_TIME PHAI co trong COLMAP (@DATENOW = gio may Replicat = may DB
    dich): bang _LOG khong co khoa nen GG lay MOI cot lam khoa, cot khoa
    khong duoc map thi abend OGG-00918 'Key column LOG_TIME is missing from
    map' (2026-10-27). DEFAULT SYSDATE o dich van giu, chi khong dung toi.

    Chuoi hang PHAI la NHAY DON. GG 21.3 doc param theo cu phap ANSI SQL
    (OGG-02695): "..." la TEN COT. Ban dau dung nhay kep -> checkprm van PASS
    nhung ban ghi dau tien vao la RLOG abend OGG-00919 'could not find column
    "GGHEADER"' (gap that 2026-10-27, 24 lan abend + AUTORESTART).
    """
    return (f"MAP {owner}.{table}, TARGET {tgt_owner}.{tgt_table}, INSERTALLRECORDS, "
            "COLMAP (USEDEFAULTS, LOG_TYPE = @CASE (@GETENV ('GGHEADER', 'OPTYPE'), "
            "'INSERT', 'I', 'DELETE', 'D', 'U'), "
            "COMMIT_TS = @GETENV ('GGHEADER', 'COMMITTIMESTAMP'), "
            "LOG_TIME = @DATENOW ());")


def them_dong_map_log(text: str, owner: str, table: str, tgt_owner: str,
                      tgt_table: str) -> str:
    """Them dong MAP log neu chua co (khop theo `MAP <owner>.<table>,`)."""
    if re.search(rf"^\s*MAP\s+{re.escape(owner)}\.{re.escape(table)}\s*,",
                 text, re.I | re.M):
        return text
    body = text if text.endswith("\n") else text + "\n"
    return body + dong_map_log(owner, table, tgt_owner, tgt_table) + "\n"


def bo_handlecollisions(text: str, owner: str, table: str) -> str:
    """Bo `HANDLECOLLISIONS` khoi DUNG mot dong MAP.

    Chi goi khi `Low WM > MAXSCN` - day la cho DUY NHAT trong ca he thong
    ma Low Watermark la moc dung.
    """
    out = []
    pat = re.compile(rf"^\s*MAP\s+{re.escape(owner)}\.{re.escape(table)}\s*,",
                     re.I)
    for line in text.splitlines(keepends=True):
        if pat.match(line):
            line = re.sub(r",\s*HANDLECOLLISIONS\b", "", line, flags=re.I)
        out.append(line)
    return "".join(out)
