"""Poller: MOT task nen duy nhat cho MOI browser.

Khong de moi tab tu goi ggsci. ggsci khong an toan khi goi song song, va
mot poller duy nhat giu tai len he thong co dinh bat ke co bao nhieu nguoi
dang mo dashboard.

Ket qua gan nhat giu trong bo nho (`Collector.snap`) de request HTTP tra
loi tuc thi; SQLite chi de ve do thi va truy nguyen.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from . import history, logsize, registry
from .adapters import gg, osfs

log = logging.getLogger("ggadmin.collector")


def process_tam_dung(cfg) -> set[str]:
    """Extract/Replicat cua cac luong co `tam_dung = true` trong [streams.X].

    Luong dung CO Y (2026-10-30: luong DT dung vi job tong hop 3 bang doanh
    thu da chuyen sang chay tren ODS) khong duoc bao process_khong_running
    hay lag moi phut - mot canh bao khong bao gio tat se lam mat phan xa
    voi canh bao that. Bo co nay ra la canh bao tro lai ngay.
    """
    out: set[str] = set()
    for st in (cfg.streams or {}).values():
        if st.get("tam_dung") is True:
            out |= {p.upper() for p in (st.get("extract"), st.get("replicat")) if p}
    return out


@dataclass
class Snapshot:
    ts: int = 0
    procs: list[gg.ProcState] = field(default_factory=list)
    fs: dict = field(default_factory=dict)
    trails: dict = field(default_factory=dict)
    discards: dict = field(default_factory=dict)
    datafiles: list = field(default_factory=list)
    capture: list = field(default_factory=list)
    apply: list = field(default_factory=list)
    checkpoints: list = field(default_factory=list)
    prepared: int = 0
    scn_src: int = 0
    scn_tgt: int = 0
    abend_today: int = 0
    plaintext_pw: int = -1
    credstore: bool = False
    param_counts: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)
    recon: dict = field(default_factory=dict)      # doi chieu registry
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    read_only: bool = False      # / duoi 25 GB -> chan moi thao tac ghi

    @property
    def age_s(self) -> int:
        return max(0, int(time.time()) - self.ts) if self.ts else -1

    @property
    def tablespaces(self) -> list[dict]:
        """Gop `datafiles` theo tablespace cho bang tong quat o trang chu.

        - da_cap: tong kich thuoc file hien tai (dba_data_files.bytes)
        - tran:   tong MAXSIZE; file khong autoextend thi tran = kich thuoc
        - dung:   da_cap - free (dba_free_space). Voi UNDO, extent EXPIRED
                  duoc tinh la free - dung y nghia "con tai dung duoc".
        - con:    tran - dung
        """
        agg: dict[str, dict] = {}
        for d in self.datafiles:
            size = float(d.get("size_mb") or 0)
            mx = float(d.get("max_mb") or 0)
            tran = max(mx, size) if d.get("autoextensible") == "YES" else size
            if d.get("free_mb") is not None:
                free = float(d["free_mb"] or 0)
            else:   # snapshot cu chua co free_mb: suy tu room
                free = max(0.0, float(d.get("room_mb") or 0) - (mx - size))
            a = agg.setdefault(d["tablespace_name"], {
                "ten": d["tablespace_name"], "so_file": 0,
                "da_cap": 0.0, "tran": 0.0, "dung": 0.0})
            a["so_file"] += 1
            a["da_cap"] += size
            a["tran"] += tran
            a["dung"] += size - free
        out = []
        for a in agg.values():
            a["con"] = a["tran"] - a["dung"]
            a["pct"] = round(a["dung"] / a["tran"] * 100, 1) if a["tran"] else 0.0
            out.append(a)
        return sorted(out, key=lambda a: a["ten"])


READ_ONLY_GB = 25.0


class Collector:
    def __init__(self, cfg, con: sqlite3.Connection, ora, notifier=None):
        self.cfg = cfg
        self.con = con
        self.ora = ora
        self.notifier = notifier
        self.snap = Snapshot()
        self._task: asyncio.Task | None = None
        self._prev_status: dict[str, str] = {}
        # (ten_luat, doi_tuong) -> thoi diem LAN DAU thay vi pham.
        # Dung de thuc thi for_seconds: chi mo canh bao khi vi pham KEO DAI
        # du lau, khong mo ngay o tick dau tien.
        self._pending: dict[tuple[str, str], int] = {}
        self._tick_n = 0
        self._last_rollup = 0
        # Lan do log gan nhat (Bieu do log). Giu RIENG ngoai Snapshot vi chi
        # do moi logsize.DO_MOI_N_TICK tick, khong phai moi tick.
        self.log_do: dict = {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="collector")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("lần đo thất bại")
            await asyncio.sleep(self.cfg.poll_seconds)

    # ------------------------------------------------------------------ tick
    async def tick(self) -> Snapshot:
        t0 = time.monotonic()
        s = Snapshot(ts=int(time.time()))

        # ggsci va filesystem truoc: chung luon chay duoc du Oracle co van de
        try:
            s.procs = await gg.info_all(self.cfg)
        except Exception as e:
            s.errors.append(f"ggsci info all: {e}")

        try:
            fsx = await osfs.a_snapshot(self.cfg)
            s.fs = fsx["fs"]
            s.trails = fsx["trails"]
            s.discards = fsx["discards"]
            s.abend_today = fsx["abend_today"]
            s.plaintext_pw = fsx["plaintext_pw"]
            s.credstore = fsx["credstore"]
            s.param_counts = fsx["param_counts"]
        except Exception as e:
            s.errors.append(f"filesystem: {e}")

        # Oracle: moi muc bat loi rieng de mot cai hong khong lam mu het
        reps = [(v["replicat"], v.get("ckpt_table")) for v in self.cfg.streams.values()]
        for name, coro in (
            ("capture", self.ora.capture_status()),
            ("apply", self.ora.apply_status()),
            ("checkpoints", self.ora.replicat_checkpoints(reps)),
            ("datafiles", self.ora.tablespace_room()),
            ("prepared", self.ora.prepared_tables()),
            ("scn_src", self.ora.source_scn()),
            ("scn_tgt", self.ora.target_scn()),
        ):
            try:
                setattr(s, name, await coro)
            except Exception as e:
                s.errors.append(f"oracle {name}: {str(e).splitlines()[0]}")

        # Registry: doc lai param file + prepared tables. Khong can moi
        # tick - param file it doi. 10 tick = 5 phut la du nhanh.
        self._tick_n += 1
        try:
            if self._tick_n == 1 or self._tick_n % 10 == 0:
                s.recon = await registry.sync(self.cfg, self.con, self.ora)
            else:
                s.recon = registry.reconcile(self.con)
        except Exception as e:
            s.errors.append(f"registry: {str(e).splitlines()[0]}")

        # Dung luong log (trang Bieu do log): 5 phut mot lan la du, log tang
        # theo gio/ngay chu khong theo giay.
        if self._tick_n == 1 or self._tick_n % logsize.DO_MOI_N_TICK == 0:
            try:
                self.log_do = await logsize.a_do(self.cfg)
                self.con.executemany(
                    "INSERT OR REPLACE INTO metric_sample(scope,scope_key,metric,ts,val)"
                    " VALUES (?,?,?,?,?)", logsize.hang_luu(self.log_do))
            except Exception as e:
                s.errors.append(f"dung lượng log: {str(e).splitlines()[0]}")

        # Rollup + purge moi gio. THU TU QUAN TRONG: rollup TRUOC roi moi
        # purge - purge truoc se mat du lieu vinh vien.
        now_h = int(time.time())
        if now_h - self._last_rollup >= 3600:
            try:
                n = history.rollup(self.con)
                history.purge(self.con, self.cfg.retain_raw_days)
                self._last_rollup = now_h
                log.info("đã gom %d dòng theo giờ, xoá số liệu gốc cũ hơn %d ngày",
                         n, self.cfg.retain_raw_days)
            except Exception:
                log.exception("gom và xoá số liệu cũ thất bại")

        s.read_only = bool(s.fs) and s.fs.get("avail_gb", 0) < READ_ONLY_GB
        s.duration_ms = int((time.monotonic() - t0) * 1000)
        self._persist(s)
        s.alerts = self._open_alerts()
        self.snap = s
        # Gui canh bao ra ngoai UI. Bat loi RIENG: kenh gui hong thi KHONG
        # duoc lam chet poller - mat canh bao con do hon mat ca giam sat.
        if self.notifier is not None:
            try:
                self.notifier.process(s.alerts, s)
            except Exception:
                log.exception("gửi cảnh báo thất bại")
        return s

    # --------------------------------------------------------------- luu tru
    def _persist(self, s: Snapshot) -> None:
        ts = s.ts
        rows: list[tuple] = []

        for p in s.procs:
            rows.append(("PROCESS", p.name, "is_running", ts, 1.0 if p.is_running else 0.0))
            if p.lag_s is not None:
                rows.append(("PROCESS", p.name, "lag_s", ts, float(p.lag_s)))
            if p.since_chkpt_s is not None:
                rows.append(("PROCESS", p.name, "since_chkpt_s", ts, float(p.since_chkpt_s)))
            # Trang thai chi ghi KHI THAY DOI, khong moi 30s
            prev = self._prev_status.get(p.name)
            if prev != p.status:
                self.con.execute(
                    "INSERT INTO process_status_event(ts,proc_name,status,prev_status)"
                    " VALUES (?,?,?,?)", (ts, p.name, p.status, prev))
                self._prev_status[p.name] = p.status

        for k, v in s.trails.items():
            rows.append(("TRAIL", k, "total_bytes", ts, float(v["total_bytes"])))
            rows.append(("TRAIL", k, "files", ts, float(v["files"])))
        for k, v in s.discards.items():
            rows.append(("DISCARD", k, "bytes", ts, float(v["bytes"])))
            rows.append(("DISCARD", k, "delta_bytes", ts, float(v["delta_bytes"])))
            rows.append(("DISCARD", k, "so_dong_la", ts,
                         float(v.get("so_dong_la", -1))))
        if s.fs:
            for m in ("avail_bytes", "avail_pct", "avail_gb", "used_bytes"):
                rows.append(("FS", "/", m, ts, float(s.fs[m])))
        for d in s.datafiles:
            key = d["file_name"].rsplit("/", 1)[-1]
            rows.append(("DATAFILE", key, "size_mb", ts, float(d["size_mb"] or 0)))
            rows.append(("DATAFILE", key, "room_mb", ts, float(d["room_mb"] or 0)))
        if s.datafiles:
            agg: dict[str, float] = {}
            for d in s.datafiles:
                agg[d["tablespace_name"]] = agg.get(d["tablespace_name"], 0.0) + float(d["room_mb"] or 0)
            for tsname, room in agg.items():
                rows.append(("TABLESPACE", tsname, "room_mb", ts, room))
        for c in s.checkpoints:
            if "seqno" in c:
                rows.append(("APPLY", c["group_name"], "seqno", ts, float(c["seqno"] or 0)))
                rows.append(("APPLY", c["group_name"], "rba", ts, float(c["rba"] or 0)))
        if s.scn_src:
            rows.append(("CAPTURE", "SOURCE", "scn", ts, float(s.scn_src)))
        rows.append(("PROCESS", "ALL", "abend_today", ts, float(s.abend_today)))

        try:
            self.con.executemany(
                "INSERT OR REPLACE INTO metric_sample(scope,scope_key,metric,ts,val)"
                " VALUES (?,?,?,?,?)", rows)
        except sqlite3.Error:
            log.exception("ghi số liệu đo thất bại")

        self._eval_alerts(s)

    # --------------------------------------------------------------- canh bao
    def _load_rules(self) -> dict[str, dict]:
        """Doc luat tu DB, KE CA nguong.

        Truoc day nguong bi hardcode trong Python trong khi bang alert_rule
        van co cot `threshold` -> bang do vo nghia va khong the cau hinh tu
        UI nhu ke hoach noi. Gio nguong lay tu DB, con cac so trong
        DEFAULT_RULES chi la gia tri khoi tao lan dau.

        Ngoai le CO Y: nguong chuyen app sang read-only toan cuc van la hang
        so READ_ONLY_GB trong code. Do la CHOT AN TOAN, khong nen noi long
        duoc tu UI.
        """
        return {r["name"]: dict(r) for r in self.con.execute(
            "SELECT id,name,threshold,severity,for_seconds,enabled FROM alert_rule")}

    def _eval_alerts(self, s: Snapshot) -> None:
        """Mo alert khi vi pham, dong khi het vi pham. Nguong lay tu DB."""
        rules = self._load_rules()

        def th(name: str, default: float) -> float | None:
            """Nguong cua luat. None = luat bi tat -> bo qua han."""
            r = rules.get(name)
            if r is None or not r["enabled"]:
                return None
            v = r["threshold"]
            return float(v) if v is not None else default

        fired: list[tuple[str, str, float]] = []   # (rule_name, scope_key, val)

        t_nang = th("lag_replicat_nang", 1800)
        t_lag = th("lag_replicat_canh_bao", 300)
        t_run = th("process_khong_running", 1)
        bo_qua = process_tam_dung(self.cfg)
        for p in s.procs:
            if p.name in bo_qua:
                continue
            if t_run is not None and not p.is_running:
                fired.append(("process_khong_running", p.name, 0.0))
            if p.program == "REPLICAT" and p.lag_s is not None:
                if t_nang is not None and p.lag_s > t_nang:
                    fired.append(("lag_replicat_nang", p.name, float(p.lag_s)))
                elif t_lag is not None and p.lag_s > t_lag:
                    fired.append(("lag_replicat_canh_bao", p.name, float(p.lag_s)))

        t_dsc = th("discard_lech_moc_rong", 0)
        if t_dsc is not None:
            for rep, d in s.discards.items():
                # Dem DONG, khong dem BYTE: stop/start Replicat ghi them mot
                # dong banner lam file to len 94 byte ma khong bo ban ghi nao.
                # Dung byte thi canh bao no sai moi lan bao tri.
                n_la = d.get("so_dong_la", 0)
                if n_la is not None and n_la > t_dsc:
                    fired.append(("discard_lech_moc_rong", rep, float(n_la)))

        t_ab = th("abend_moi", 0)
        if t_ab is not None and s.abend_today > t_ab:
            fired.append(("abend_moi", "ggserr.log", float(s.abend_today)))

        # Doi chieu registry. 'bang_capture_khong_map' la cai bay so mot cua
        # he nay: Extract dao redo va ghi trail, Replicat bo qua va VAN DOI
        # CHECKPOINT -> mat du lieu IM LANG, discard khong tang, log sach.
        if s.recon:
            t_km = th("bang_capture_khong_map", 0)
            n_km = len(s.recon.get("capture_khong_map") or [])
            if t_km is not None and n_km > t_km:
                fired.append(("bang_capture_khong_map", "ALL", float(n_km)))
            t_mp = th("bang_map_khong_prepared", 0)
            n_mp = len(s.recon.get("map_khong_prepared") or [])
            if t_mp is not None and n_mp > t_mp:
                fired.append(("bang_map_khong_prepared", "ALL", float(n_mp)))

        if s.fs:
            t_pct = th("dia_root_thap", 15)
            if t_pct is not None and s.fs["avail_pct"] < t_pct:
                fired.append(("dia_root_thap", "/", s.fs["avail_pct"]))
            t_ro = th("dia_root_doc_quyen", READ_ONLY_GB)
            if t_ro is not None and s.fs["avail_gb"] < t_ro:
                fired.append(("dia_root_doc_quyen", "/", s.fs["avail_gb"]))

        agg: dict[str, float] = {}
        for d in s.datafiles:
            agg[d["tablespace_name"]] = agg.get(d["tablespace_name"], 0.0) + float(d["room_mb"] or 0)

        # Chi bao khi CA HAI dung: datafile cham tran VA tablespace het room.
        # Datafile cham tran mot minh la binh thuong - cho moi vao file 02/03.
        # Mot canh bao khong bao gio tat se lam mat phan xa voi canh bao that.
        t_df = th("datafile_cham_tran_va_het_room", 30720)
        t_room = th("room_tablespace_thap", 10240)
        if t_df is not None:
            for d in s.datafiles:
                if (d["size_mb"] or 0) > t_df and agg.get(d["tablespace_name"], 0.0) < 20480:
                    fired.append(("datafile_cham_tran_va_het_room",
                                  d["file_name"].rsplit("/", 1)[-1], float(d["size_mb"])))
        if t_room is not None:
            for name in ("BSH_DATA", "BSH_INDEX"):
                if name in agg and agg[name] < t_room:
                    fired.append(("room_tablespace_thap", name, agg[name]))

        rule_ids = {n: r["id"] for n, r in rules.items() if r["enabled"]}

        # ---- THUC THI for_seconds (chong nhay) ----
        # Da gap that 2026-10-13: RPMNVODS co gai lag 465s va 327s trong DUNG
        # MOT tick roi ve 0 ngay - mot lo ban ghi cu trong trail duoc apply
        # nen "lag at checkpoint" phan anh tuoi cua chung. Ve ky thuat la lag
        # that, ve van hanh KHONG phai su co. Luat da co for_seconds=120
        # nhung code truoc day BO QUA no -> nhung gai nhu vay van ban tin
        # Zalo. Moi dem vai tin vo nghia la du lam nguoi ta mat phan xa.
        ts_now = s.ts
        keys_fired = {(n, k) for n, k, _ in fired}
        # bo khoi hang cho nhung cai khong con vi pham
        for key in list(self._pending):
            if key not in keys_fired:
                del self._pending[key]

        chin: list[tuple[str, str, float]] = []   # da du lau -> duoc mo
        for name, key, val in fired:
            need = int((rules.get(name) or {}).get("for_seconds") or 0)
            if need <= 0:
                chin.append((name, key, val))
                continue
            first = self._pending.setdefault((name, key), ts_now)
            held = ts_now - first
            if held >= need:
                chin.append((name, key, val))
            else:
                log.info("hoan canh bao %s/%s: vi pham %ds, can >=%ds",
                         name, key, held, need)
        fired = chin
        keys_now = {(n, k) for n, k, _ in fired}
        now = s.ts

        for name, key, val in fired:
            rid = rule_ids.get(name)
            if rid is None:
                continue
            row = self.con.execute(
                "SELECT id,peak_val FROM alert_event"
                " WHERE rule_id=? AND scope_key=? AND closed_at IS NULL",
                (rid, key)).fetchone()
            if row:
                if val > (row["peak_val"] or 0):
                    self.con.execute("UPDATE alert_event SET peak_val=? WHERE id=?",
                                     (val, row["id"]))
            else:
                self.con.execute(
                    "INSERT INTO alert_event(rule_id,scope_key,opened_at,peak_val)"
                    " VALUES (?,?,?,?)", (rid, key, now, val))

        # dong nhung alert khong con vi pham
        for row in self.con.execute(
                "SELECT e.id, r.name, e.scope_key FROM alert_event e"
                " JOIN alert_rule r ON r.id=e.rule_id WHERE e.closed_at IS NULL"):
            if (row["name"], row["scope_key"]) not in keys_now:
                self.con.execute("UPDATE alert_event SET closed_at=? WHERE id=?",
                                 (now, row["id"]))

    def _open_alerts(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.con.execute("""
            SELECT e.id, r.name, r.severity, r.note, r.op, r.threshold,
                   r.metric, r.unit, e.scope_key, e.opened_at, e.peak_val
              FROM alert_event e JOIN alert_rule r ON r.id = e.rule_id
             WHERE e.closed_at IS NULL
             ORDER BY CASE r.severity WHEN 'CRIT' THEN 0 WHEN 'WARN' THEN 1
                      ELSE 2 END, e.opened_at""")]

    # ------------------------------------------------------------- doi chieu
    def param_mismatch(self) -> list[str]:
        """Moi luong: so TABLE cua extract phai bang so MAP cua replicat.

        Truoc 2026-10-30 so voi HANG SO viet tay luc dung he thong (etht 18,
        TRANDATA 73...) - them bang qua quy trinh la bao lech vinh vien du
        compare khop. Bang MAP ma chua bat TRANDATA da co canh bao rieng
        `bang_map_khong_prepared` (doi chieu theo TUNG bang), nen khong so
        tong so bang prepared nua: con so do gom ca bang da bat TRANDATA ma
        khong nam trong luong nao, khong co gia tri "dung" co dinh.
        """
        out = []
        for sid, (n_tab, n_map) in sorted(self.snap.param_counts.items()):
            st = self.cfg.streams.get(sid) or {}
            ext, rep = st.get("extract", "?"), st.get("replicat", "?")
            if n_tab < 0 or n_map < 0:
                thieu = [f"{p.lower()}.prm" for p, n in ((ext, n_tab), (rep, n_map)) if n < 0]
                out.append(f"luồng {sid}: không đọc được {', '.join(thieu)}")
            elif n_tab != n_map:
                out.append(f"luồng {sid}: {ext.lower()}.prm có {n_tab} dòng TABLE nhưng "
                           f"{rep.lower()}.prm có {n_map} dòng MAP")
        if self.snap.plaintext_pw > 0:
            out.append(f"{self.snap.plaintext_pw} file tham số còn mật khẩu để dạng rõ (phải là "
                       "0)")
        if not self.snap.credstore:
            out.append("KHÔNG thấy kho mật khẩu dircrd/cwallet.sso - 6 tiến trình sẽ không "
                       "chạy được")
        return out

    def purge_old(self) -> int:
        cutoff = int(time.time()) - self.cfg.retain_raw_days * 86400
        cur = self.con.execute("DELETE FROM metric_sample WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0
