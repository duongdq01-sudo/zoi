"""SQLite cho ggadmin.

Vi sao SQLite chu khong ghi vao Oracle: DB dich CHINH LA doi tuong duoc
giam sat. Ghi metric vao do thi khi DB chet la mat luon du lieu can de
chan doan. Them nua `/` la mot LV duy nhat chua ca datafile lan trail,
nen khong nen nhoi them vao.

Khoi luong: 6 process x ~8 metric x 1 diem/30s ~ 138k dong/ngay ~ 12 MB/thang.
Giu 90 ngay raw + rollup theo gio -> file duoi 200 MB vinh vien.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import shutil
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("ggadmin.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (v INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS app_user (
  username        TEXT PRIMARY KEY,
  full_name       TEXT,
  pwd_hash        TEXT NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('VIEWER','OPERATOR','ADMIN')),
  is_active       INTEGER NOT NULL DEFAULT 1,
  must_change_pwd INTEGER NOT NULL DEFAULT 0,
  failed_count    INTEGER NOT NULL DEFAULT 0,
  locked_until    INTEGER,
  last_login_at   INTEGER,
  created_at      INTEGER NOT NULL
);

-- long/narrow: them metric moi KHONG can ALTER TABLE.
-- WITHOUT ROWID + PK (scope,scope_key,metric,ts): "lag cua RPMNVODS 7 ngay
-- qua" la mot range scan lien mach, dung pattern ma do thi can.
CREATE TABLE IF NOT EXISTS metric_sample (
  scope     TEXT NOT NULL,   -- PROCESS | TRAIL | DISCARD | FS | TABLESPACE | DATAFILE | CAPTURE | APPLY
  scope_key TEXT NOT NULL,   -- RPMNVODS | pm | / | BSH_DATA | ...
  metric    TEXT NOT NULL,   -- lag_s | time_since_chkpt_s | bytes | avail_bytes | pct_used | scn ...
  ts        INTEGER NOT NULL,
  val       REAL NOT NULL,
  PRIMARY KEY (scope, scope_key, metric, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_metric_ts ON metric_sample(ts);

-- Trang thai process chi ghi KHI THAY DOI, khong ghi moi 30s.
CREATE TABLE IF NOT EXISTS process_status_event (
  id          INTEGER PRIMARY KEY,
  ts          INTEGER NOT NULL,
  proc_name   TEXT NOT NULL,
  status      TEXT NOT NULL,
  prev_status TEXT,
  detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_pse ON process_status_event(proc_name, ts DESC);

CREATE TABLE IF NOT EXISTS alert_rule (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  scope        TEXT,
  scope_key    TEXT,
  metric       TEXT,
  op           TEXT CHECK (op IN ('>','>=','<','<=','==','!=')),
  threshold    REAL,
  for_seconds  INTEGER NOT NULL DEFAULT 0,
  severity     TEXT NOT NULL CHECK (severity IN ('INFO','WARN','CRIT')),
  enabled      INTEGER NOT NULL DEFAULT 1,
  note         TEXT,
  unit         TEXT          -- don vi cua gia tri do: %, GB, MB, giay, byte...
);

CREATE TABLE IF NOT EXISTS alert_event (
  id         INTEGER PRIMARY KEY,
  rule_id    INTEGER REFERENCES alert_rule(id),
  scope_key  TEXT,
  opened_at  INTEGER NOT NULL,
  closed_at  INTEGER,
  peak_val   REAL,
  ack_by     TEXT,
  ack_at     INTEGER,
  note       TEXT
);
CREATE INDEX IF NOT EXISTS ix_alert_open ON alert_event(closed_at, opened_at DESC);

-- Ghi 2 noi: bang nay (de query) VA logs/audit-YYYYMM.jsonl (de khong xoa
-- duoc bang cung dac quyen). Hash chain chong sua.
CREATE TABLE IF NOT EXISTS audit_log (
  id             INTEGER PRIMARY KEY,
  ts             INTEGER NOT NULL,
  username       TEXT NOT NULL,
  peer           TEXT,
  level          TEXT NOT NULL CHECK (level IN ('L0','L1','L2','SECURITY_BLOCK')),
  action         TEXT NOT NULL,
  target         TEXT,
  reason         TEXT,
  payload_before TEXT,
  payload_after  TEXT,
  cmd_text       TEXT,
  exit_code      INTEGER,
  stdout_tail    TEXT,
  duration_ms    INTEGER,
  prev_hash      TEXT,
  row_hash       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_ts   ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS ix_audit_user ON audit_log(username, ts DESC);

-- Registry cac bang trong luong. VUA la danh muc de doi chieu, VUA la
-- ALLOWLIST lop 5 chong SQL injection: bang ton tai trong ODS nhung khong
-- thuoc pham vi GG thi cong cu KHONG duoc cham.
CREATE TABLE IF NOT EXISTS managed_table (
  id            INTEGER PRIMARY KEY,
  src_owner     TEXT NOT NULL DEFAULT 'KT_BSH',
  src_table     TEXT NOT NULL,
  tgt_owner     TEXT NOT NULL DEFAULT 'ODS',
  tgt_table     TEXT NOT NULL,
  stream        TEXT,                    -- PM | HT | DT | NULL khi chua ro
  keycols       TEXT,
  in_extract    INTEGER NOT NULL DEFAULT 0,   -- co dong TABLE trong *.prm
  has_map       INTEGER NOT NULL DEFAULT 0,   -- co dong MAP trong *.prm
  prepared      INTEGER NOT NULL DEFAULT 0,   -- co trong dba_capture_prepared_tables
  handlecollisions INTEGER NOT NULL DEFAULT 0,
  src_rows      INTEGER,
  tgt_rows      INTEGER,
  is_partitioned INTEGER,
  note          TEXT,
  updated_at    INTEGER,
  UNIQUE (src_owner, src_table)
);
CREATE INDEX IF NOT EXISTS ix_mt_stream ON managed_table(stream, src_table);

-- Ket qua verify. BAT BUOC luu hash_expr_sha256: param co DDL INCLUDE MAPPED
-- nen DDL them cot -> bieu thuc checksum doi -> hash doi MA KHONG PHAI vi
-- du lieu lech. Khong luu bieu thuc thi se mat ca buoi truy mot "sai lech"
-- khong ton tai.
CREATE TABLE IF NOT EXISTS verify_run (
  id          INTEGER PRIMARY KEY,
  batch_id    INTEGER,                  -- nhom nhieu bang trong mot lan chay
  started_at  INTEGER NOT NULL,
  finished_at INTEGER,
  mode        TEXT NOT NULL CHECK (mode IN ('LIVE','SNAPSHOT_SCN')),
  scn         INTEGER,
  stream      TEXT,
  src_table   TEXT NOT NULL,
  tgt_table   TEXT NOT NULL,
  src_count   INTEGER,
  tgt_count   INTEGER,
  src_hash    TEXT,                     -- TEXT: SUM(ora_hash) vuot 2^53
  tgt_hash    TEXT,
  matched     INTEGER,                  -- 1 khop | 0 lech | NULL dang chay
  col_count   INTEGER,
  hash_expr_sha256 TEXT,
  duration_ms INTEGER,
  err_msg     TEXT,
  run_by      TEXT
);
CREATE INDEX IF NOT EXISTS ix_vr_tbl   ON verify_run(src_table, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_vr_batch ON verify_run(batch_id, src_table);

-- Rollup theo gio. Giu 90 ngay raw roi purge; rollup giu mai -> file duoi
-- 200 MB vinh vien.
CREATE TABLE IF NOT EXISTS metric_hourly (
  scope     TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  metric    TEXT NOT NULL,
  hour_ts   INTEGER NOT NULL,
  v_min REAL, v_max REAL, v_avg REAL, v_last REAL, n INTEGER,
  PRIMARY KEY (scope, scope_key, metric, hour_ts)
) WITHOUT ROWID;

-- Job dai (verify ca luong). Trang thai o DB de UI poll duoc va de biet
-- dang co job chay hay khong - toi da MOT job verify cung luc.
CREATE TABLE IF NOT EXISTS job (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  state       TEXT NOT NULL CHECK (state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','KILLED')),
  created_at  INTEGER NOT NULL,
  started_at  INTEGER,
  finished_at INTEGER,
  created_by  TEXT NOT NULL,
  progress_pct  REAL,
  progress_note TEXT,
  params_json TEXT,
  result_json TEXT,
  err_msg     TEXT,
  batch_id    INTEGER
);
CREATE INDEX IF NOT EXISTS ix_job_state ON job(state, created_at DESC);

-- Moi lan sua param file. Luu ca DIFF va duong dan backup, de tra loi duoc
-- cau hoi "ai doi gi luc nao va lay lai o dau" ma khong phai doc dirprm.
-- `restored=1` nghia la lan do checkprm FAIL va app da TU PHUC HOI ban cu.
CREATE TABLE IF NOT EXISTS param_version (
  id            INTEGER PRIMARY KEY,
  ts            INTEGER NOT NULL,
  process       TEXT NOT NULL,
  changed_by    TEXT NOT NULL,
  reason        TEXT,
  backup_path   TEXT,
  sha256_before TEXT,
  sha256_after  TEXT,
  diff          TEXT,
  checkprm_ok   INTEGER,
  restored      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pv ON param_version(process, id DESC);

-- May trang thai cua wizard. STATE O SERVER, khong o client.
-- Day khong phai tien nghi ma la BAT BUOC dung nghiep vu: co buoc nap dai
-- hang chuc phut va buoc cho `Low WM` vuot `MAXSCN`. State o client se mat
-- trang khi tunnel dut hoac DBA F5. State o server thi hom sau mo lai wizard
-- van o buoc 8, va MOT DBA KHAC tiep duoc viec cua nguoi truoc.
--
-- Nhung con so quyet dinh tinh dung dan cua ca runbook duoc luu thanh COT
-- RIENG, khong nhet vao ctx_json: chung can query duoc, index duoc, hien
-- duoc tren bao cao.
CREATE TABLE IF NOT EXISTS wizard_run (
  id         INTEGER PRIMARY KEY,
  kind       TEXT NOT NULL CHECK (kind IN ('ADD_TABLE','RESYNC_TABLE','NEW_STREAM','LUONG_MOI')),
  state      TEXT NOT NULL CHECK (state IN ('OPEN','DONE','ABORTED')),
  stream     TEXT,
  src_owner  TEXT NOT NULL DEFAULT 'KT_BSH',
  src_table  TEXT,
  tgt_owner  TEXT NOT NULL DEFAULT 'ODS',
  tgt_table  TEXT,
  created_at INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  updated_at INTEGER,
  finished_at INTEGER,
  cur_step   INTEGER NOT NULL DEFAULT 1,
  keycols    TEXT,
  -- LO NHIEU BANG (chi ADD_TABLE). JSON list ten bang nguon.
  -- `src_table` van giu ten bang DAU TIEN, nen moi cho hien thi, ten script
  -- va khoa doc quyen dung duoc y nhu cu; phien MOT bang la truong hop rieng
  -- cua list mot phan tu, khong co hai duong code song song.
  -- VI SAO CAN: CN_CH/CN_CT/CN_PS/CN_TT dung chung khoa (MA_DVI, SO_ID) tuc
  -- la quan he master-detail. Nap moi bang o mot SCN rieng se sinh ra dong
  -- chi tiet ma dong goc chua co o dich. Ca lo phai nap o MOT SCN duy nhat.
  src_tables TEXT,
  -- cac moc SCN: sai mot cai la mat du lieu im lang hoac duong tinh gia
  scn_floor  INTEGER,      -- High WM luc stop, nguong duoi cua du lieu nap
  maxscn     INTEGER,      -- SCN cao nhat da nap; cho Low WM vuot moc nay
  snapshot_scn INTEGER,    -- SCN dung de verify
  trail_seqno INTEGER,
  trail_rba  INTEGER,
  rows_before_truncate INTEGER,
  -- rieng NEW_STREAM: day la danh sach quyet dinh lenh DELETE/UNREGISTER
  -- duoc phep cham vao TEN NAO. Phai la cot, khong phai JSON.
  created_extract      TEXT,
  created_replicat     TEXT,
  created_trail_prefix TEXT,
  created_ckpt_table   TEXT,
  register_scn         INTEGER,
  ctx_json   TEXT,
  note       TEXT
);
CREATE INDEX IF NOT EXISTS ix_wr_state ON wizard_run(state, id DESC);
CREATE INDEX IF NOT EXISTS ix_wr_stream ON wizard_run(stream, state);

CREATE TABLE IF NOT EXISTS wizard_step (
  id          INTEGER PRIMARY KEY,
  run_id      INTEGER NOT NULL REFERENCES wizard_run(id),
  step_no     INTEGER NOT NULL,
  name        TEXT NOT NULL,
  state       TEXT NOT NULL CHECK (state IN ('CHO','DANG','PASS','FAIL','BO_QUA')),
  started_at  INTEGER,
  finished_at INTEGER,
  by_user     TEXT,
  reason      TEXT,
  preview     TEXT,        -- lenh/SQL se chay, hien TRUOC khi chay
  output      TEXT,
  err_msg     TEXT,
  UNIQUE (run_id, step_no)
);
CREATE INDEX IF NOT EXISTS ix_ws ON wizard_step(run_id, step_no);
"""

# Nguong lay tu moc THAT cua he nay, khong bia.
# Nguong o day CHI la gia tri khoi tao lan dau; sau do doc tu DB.
# Phan `note` CO Y khong chua con so: con so duoc in rieng trong tin canh
# bao, lay tu nguong dang co hieu luc. Hardcode so vao note se lam tin
# canh bao tu mau thuan khi nguong bi doi.
DEFAULT_RULES = [
    # name, scope, scope_key, metric, op, threshold, for_seconds, severity, note, unit
    ("process_khong_running", "PROCESS", None, "is_running", "<", 1, 0, "CRIT",
     "Có tiến trình GoldenGate không ở trạng thái đang chạy.", ""),
    ("lag_replicat_canh_bao", "PROCESS", None, "lag_s", ">", 300, 120, "WARN",
     "Replicat tre hon nguong canh bao.", "giay"),
    ("lag_replicat_nang", "PROCESS", None, "lag_s", ">", 1800, 120, "CRIT",
     "Replicat tre nang.", "giay"),
    ("discard_lech_moc_rong", "DISCARD", None, "so_dong_la", ">", 0, 0, "CRIT",
     "File discard có dòng KHÁC dòng mở đầu của GoldenGate -> Replicat đang "
     "bỏ bản ghi. Đếm theo DÒNG chứ không theo BYTE: mỗi lần dừng và chạy "
     "lại Replicat, GoldenGate ghi thêm một dòng mở đầu làm file to lên 94 "
     "byte mà không bỏ bản ghi nào - đếm theo kích thước thì cảnh báo này nổ "
     "sai mỗi lần bảo trì, và đó là cách chắc chắn nhất để người dùng học "
     "cách bỏ qua chính cảnh báo quan trọng nhất.",
     "dòng khác dòng mở đầu"),
    ("room_tablespace_thap", "TABLESPACE", None, "room_mb", "<", 10240, 0, "WARN",
     "Vùng lưu trữ còn ít chỗ. Hết chỗ thì Replicat sẽ dừng lỗi (ORA-01654).", "MB"),
    ("datafile_cham_tran_va_het_room", "DATAFILE", None, "size_mb", ">", 30720, 0, "WARN",
     "Một datafile đã cụng trần cứng 32 GB VÀ vùng lưu trữ của nó cũng sắp "
     "hết chỗ. Chỉ báo khi CẢ HAI cùng đúng: datafile cụng trần một mình là "
     "bình thường, vì chỗ mới vào file 02 hoặc 03.", "MB"),
    ("dia_root_thap", "FS", "/", "avail_pct", "<", 15, 0, "CRIT",
     "Đĩa / còn ít chỗ trống. / là ổ logic duy nhất chứa đồng thời datafile "
     "Oracle, thư mục GoldenGate và file trail - đây là rủi ro lớn nhất của "
     "hệ này.", "% trong"),
    ("dia_root_doc_quyen", "FS", "/", "avail_gb", "<", 25, 0, "CRIT",
     "Đĩa / xuống dưới ngưỡng an toàn -> công cụ chuyển sang chế độ chỉ đọc "
     "toàn cục, mọi thao tác ghi bị tắt.", "GB trong"),
    ("abend_moi", "PROCESS", None, "abend_today", ">", 0, 0, "CRIT",
     "Có dòng ABEND hoặc FATAL mới trong ggserr.log hôm nay (đã loại "
     "QU_ERROR).", "dong"),
    ("bang_capture_khong_map", "REGISTRY", "ALL", "so_bang", ">", 0, 0, "WARN",
     "Có bảng đã bật TRANDATA và có dòng TABLE trong Extract nhưng THIẾU "
     "dòng MAP trong Replicat. Extract vẫn đọc redo của bảng đó và ghi vào "
     "trail, nhưng Replicat BỎ QUA mà vẫn dời mốc checkpoint -> dữ liệu mất "
     "IM LẶNG, file discard không tăng, log không báo gì. Đây là cái bẫy số "
     "một của hệ này.",
     "bang"),
    ("bang_map_khong_prepared", "REGISTRY", "ALL", "so_bang", ">", 0, 0, "CRIT",
     "Có bảng có dòng MAP trong Replicat nhưng CHƯA bật TRANDATA ở nguồn. "
     "Replicat chờ mãi không có dữ liệu, hoặc khoá bị suy luận sai.", "bang"),
    ("verify_lech", "VERIFY", "ALL", "so_bang", ">", 0, 0, "CRIT",
     "Lần đối chiếu gần nhất phát hiện bảng lệch giữa nguồn và đích.", "bang"),
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _nang_cap_kind(con: sqlite3.Connection, path: Path) -> None:
    """Them 'LUONG_MOI' vao CHECK cua wizard_run.kind (2026-10-15).

    SQLite khong ALTER duoc rang buoc CHECK -> phai dung bang moi roi chep
    sang. Ba chot:
      1. Chep file DB ra `.premigrate` TRUOC khi dong vao.
      2. LIET KE COT TUONG MINH, khong `INSERT ... SELECT *`: cot
         `src_tables` o DB cu duoc them bang ALTER nen nam CUOI, thu tu vat
         ly khac bang moi -> `SELECT *` se do nham cot.
      3. Ca cuc trong mot giao dich; hong thi khong doi gi.
    """
    r = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wizard_run'"
    ).fetchone()
    if not r or "LUONG_MOI" in (r[0] or ""):
        return
    cot = [x[1] for x in con.execute("PRAGMA table_info(wizard_run)")]
    ds = ", ".join(cot)
    moi = (r[0] or "").replace("CREATE TABLE IF NOT EXISTS wizard_run",
                               "CREATE TABLE wizard_run_moi", 1)                       .replace("CREATE TABLE wizard_run",
                               "CREATE TABLE wizard_run_moi", 1)                       .replace("'NEW_STREAM'", "'NEW_STREAM','LUONG_MOI'", 1)
    if "wizard_run_moi" not in moi or "LUONG_MOI" not in moi:
        raise RuntimeError("khong dung lai duoc DDL cua wizard_run de nang cap")
    try:
        shutil.copy2(path, path.with_name(
            f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}.premigrate"))
    except OSError as e:
        log.warning("khong luu duoc ban DB truoc nang cap: %s", e)
    log.info("nang cap wizard_run: them LUONG_MOI vao CHECK(kind)")
    con.execute("PRAGMA foreign_keys=OFF")
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(moi)
        con.execute(f"INSERT INTO wizard_run_moi({ds}) SELECT {ds} FROM wizard_run")
        con.execute("DROP TABLE wizard_run")
        con.execute("ALTER TABLE wizard_run_moi RENAME TO wizard_run")
        con.execute("CREATE INDEX IF NOT EXISTS ix_wr_state"
                    " ON wizard_run(state, id DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_wr_stream"
                    " ON wizard_run(stream, state)")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.execute("PRAGMA foreign_keys=ON")


def init(path: Path) -> sqlite3.Connection:
    con = connect(path)
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        con.execute("INSERT INTO schema_version(v) VALUES (1)")
    # Them cot `unit` cho DB cu (migration nho, idempotent)
    cols = {r[1] for r in con.execute("PRAGMA table_info(alert_rule)")}
    if "unit" not in cols:
        con.execute("ALTER TABLE alert_rule ADD COLUMN unit TEXT")
    # Them cot `src_tables` cho DB cu (lo nhieu bang). Phien cu khong co gia
    # tri nay; `_cac_bang()` tu suy ra list mot phan tu tu `src_table`, nen
    # phien dang mo giua luc nang cap van chay tiep binh thuong.
    cols = {r[1] for r in con.execute("PRAGMA table_info(wizard_run)")}
    if "src_tables" not in cols:
        con.execute("ALTER TABLE wizard_run ADD COLUMN src_tables TEXT")
    _nang_cap_kind(con, path)
    for r in DEFAULT_RULES:
        con.execute(
            "INSERT OR IGNORE INTO alert_rule"
            "(name,scope,scope_key,metric,op,threshold,for_seconds,severity,note,unit)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)", r)
        # note/unit la van ban dien giai, khong phai cau hinh nguoi dung dat
        # -> dong bo lai moi lan khoi dong de sua loi chinh ta/dien dat duoc.
        con.execute("UPDATE alert_rule SET note=?, unit=?, metric=?, op=?"
                    " WHERE name=?", (r[8], r[9], r[3], r[4], r[0]))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return con


# ---------------------------------------------------------------- mat khau
def hash_pwd(pw: str, *, iterations: int = 600_000) -> str:
    """pbkdf2_hmac trong stdlib - khong them phu thuoc nao."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_pwd(pw: str, stored: str) -> bool:
    try:
        algo, it, salt_hex, want = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(it))
    return secrets.compare_digest(dk.hex(), want)


def ensure_user(con: sqlite3.Connection, username: str, pw: str, role: str,
                full_name: str | None = None, must_change: bool = False) -> bool:
    """Tao neu chua co. Tra True neu vua tao. Khong doi mat khau nguoi da co."""
    row = con.execute("SELECT 1 FROM app_user WHERE username=?", (username,)).fetchone()
    if row:
        return False
    con.execute(
        "INSERT INTO app_user(username,full_name,pwd_hash,role,must_change_pwd,created_at)"
        " VALUES (?,?,?,?,?,?)",
        (username, full_name or username, hash_pwd(pw), role,
         1 if must_change else 0, int(time.time())))
    return True
