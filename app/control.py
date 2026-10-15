"""Start/stop process + quiesce dung quy trinh.

CAI DUY NHAT KHIEN MODULE NAY DANG TON TAI RIENG: thu tu.

Thu tu dung khi THEM BANG vao mot luong dang chay:

    stop replicat   -> doi drain -> restart extract -> start replicat

Dao lai thanh "restart extract truoc, stop replicat sau" la MAT DU LIEU IM
LANG: Extract nap param moi va bat dau ghi redo cua bang moi vao trail;
Replicat cu chua co dong MAP tuong ung nen BO QUA ban ghi do VA VAN DOI
CHECKPOINT. Khong discard, khong log, `info all` van RUNNING lag 0. Khi
phat hien ra thi checkpoint da di qua, khong co duong lui nao ngoai nap lai
ca bang.

Vi the `restart_extract_sau_khi_them_bang()` KHONG nhan tham so thu tu.
Thu tu la hang so trong code. UI khong co cho dao.
"""

from __future__ import annotations

import logging
import time

from .adapters import gg

log = logging.getLogger("ggadmin.control")


class ControlError(RuntimeError):
    pass


def _stream_of(cfg, process: str) -> tuple[str | None, dict | None]:
    for ten, st in cfg.streams.items():
        if process in (st.get("extract"), st.get("replicat")):
            return ten, st
    return None, None


class Control:
    def __init__(self, cfg, con, collector):
        self.cfg = cfg
        self.con = con
        self.collector = collector

    # --------------------------------------------------------------- co ban
    async def start(self, process: str) -> dict:
        t0 = time.monotonic()
        ok, last, lines = await gg.start_process(self.cfg, process)
        await self._ghi_su_kien(process, "START", ok, last)
        return {"ok": ok, "status": last, "lines": lines,
                "duration_ms": int((time.monotonic() - t0) * 1000)}

    async def stop(self, process: str) -> dict:
        t0 = time.monotonic()
        ok, last, lines = await gg.stop_process(self.cfg, process)
        await self._ghi_su_kien(process, "STOP", ok, last)
        return {"ok": ok, "status": last, "lines": lines,
                "duration_ms": int((time.monotonic() - t0) * 1000)}

    async def _ghi_su_kien(self, process: str, hanh_dong: str, ok: bool,
                           status: str) -> None:
        self.con.execute(
            "INSERT INTO process_status_event(ts,proc_name,status,prev_status,detail)"
            " VALUES (?,?,?,?,?)",
            (int(time.time()), process, status, None,
             f"{hanh_dong} từ ggadmin: {'đạt' if ok else 'KHÔNG đạt'} ({status})"))
        # Buoc poller do lai ngay, de dashboard khong hien trang thai cu
        try:
            await self.collector.tick()
        except Exception:                             # noqa: BLE001
            log.exception("lần đo sau %s %s thất bại", hanh_dong, process)

    # ------------------------------------------------------------- quiesce
    async def quiesce(self, stream: str, *, timeout_s: int = 900) -> dict:
        """Dung ca luong DUNG THU TU, de doc duoc mot moc nhat quan.

        Thu tu: stop EXTRACT -> doi DRAIN -> stop REPLICAT.

        Vi sao Extract truoc: dung Replicat truoc thi Extract van ghi them
        vao trail, trail phinh ra va khong bao gio drain duoc.

        DIEU KIEN DRAIN: `rba` = kich thuoc file trail VA lag `00:00:00`.
        KHONG cho `Low WM = High WM` - hai moc do khong gap nhau.
        """
        st = self.cfg.streams.get(stream)
        if st is None:
            raise ControlError(f"Không có luồng {stream!r}")
        et, rep, prefix = st["extract"], st["replicat"], st["trail"]
        buoc: list[dict] = []
        t0 = time.monotonic()

        ok, last, _ = await gg.stop_process(self.cfg, et, timeout_s=300)
        buoc.append({"buoc": f"stop extract {et}", "ok": ok, "chi_tiet": last})
        if not ok:
            raise ControlError(f"{et} không dừng được (đang {last}) - KHÔNG đi tiếp, không dừng "
                               "Replicat")

        # Cho REPLICAT doc het trail - KHONG do tren Extract. Xem ghi chu
        # trong gg.drain_state() ve ly do.
        drained, d = await gg.wait_drain(
            self.cfg, et, rep, prefix,
            timeout_s=max(60, timeout_s - int(time.monotonic() - t0)))
        buoc.append({"buoc": f"chờ {rep} đọc hết trail của {et}",
                     "ok": drained,
                     "chi_tiet": (f"ghi đến số {d.get('seqno_ghi')} / đọc đến số {d.get('seqno_doc')}, "
                                  f"{d.get('rba_doc')}/{d.get('trail_bytes')} byte, chậm {d.get('lag_s')}s "
                                  f"-> {d.get('vi_sao')}")})
        if not drained:
            # Khong drain xong thi KHONG dung Replicat: dung luc nay se de
            # lai ban ghi trong trail chua apply, va moc SCN doc ra se sai.
            await self.start(et)
            raise ControlError(
                f"{rep} không đọc hết trail trong thời gian chờ: {d.get('vi_sao')}. Đã "
                f"CHẠY LẠI {et}, KHÔNG dừng {rep} - không có bản ghi nào bị bỏ.")

        ok, last, _ = await gg.stop_process(self.cfg, rep, timeout_s=300)
        buoc.append({"buoc": f"stop replicat {rep}", "ok": ok, "chi_tiet": last})
        if not ok:
            await self.start(et)
            raise ControlError(f"{rep} không dừng được (đang {last}). Đã CHẠY LẠI {et}.")
        await self.collector.tick()
        return {"stream": stream, "extract": et, "replicat": rep,
                "buoc": buoc, "drain": d,
                "duration_ms": int((time.monotonic() - t0) * 1000)}

    async def unquiesce(self, stream: str) -> dict:
        """Start lai DUNG THU TU NGUOC: Replicat truoc, Extract sau.

        Replicat truoc de khi Extract bat dau ghi trail thi da co nguoi
        apply, trail khong phinh vo ich. Day cung la thu tu da dung trong
        dot chuyen credential store cho ca 6 process.
        """
        st = self.cfg.streams.get(stream)
        if st is None:
            raise ControlError(f"Không có luồng {stream!r}")
        buoc = []
        for p in (st["replicat"], st["extract"]):
            r = await self.start(p)
            buoc.append({"buoc": f"start {p}", "ok": r["ok"],
                         "chi_tiet": r["status"]})
        return {"stream": stream, "buoc": buoc}

    # ------------------------------------------------- them bang (thu tu cung)
    async def restart_extract_sau_khi_them_bang(self, stream: str) -> dict:
        """Chuoi dung khi vua them dong TABLE vao Extract dang chay.

        KHONG nhan tham so thu tu. Xem ghi chu dau file ve ly do.
        """
        st = self.cfg.streams.get(stream)
        if st is None:
            raise ControlError(f"Không có luồng {stream!r}")
        et, rep = st["extract"], st["replicat"]

        n_tab = gg.count_param_entries(self.cfg, et, "TABLE")
        n_map = gg.count_param_entries(self.cfg, rep, "MAP")
        if n_tab > n_map:
            raise ControlError(
                f"{et} có {n_tab} dòng TABLE mà {rep} chỉ có {n_map} dòng MAP. Chạy lại "
                f"{et} lúc này sẽ làm {n_tab - n_map} bảng bị Replicat BỎ QUA trong khi "
                "mốc checkpoint VẪN DỜI: mất dữ liệu im lặng. Thêm dòng MAP vào "
                f"{rep}.prm TRƯỚC.")

        buoc = []
        r = await self.stop(rep)
        buoc.append({"buoc": f"stop replicat {rep} (TRUOC)", "ok": r["ok"],
                     "chi_tiet": r["status"]})
        if not r["ok"]:
            raise ControlError(f"{rep} không dừng được - KHÔNG chạy lại {et}")

        r = await self.stop(et)
        buoc.append({"buoc": f"stop extract {et}", "ok": r["ok"],
                     "chi_tiet": r["status"]})
        r = await self.start(et)
        buoc.append({"buoc": f"start extract {et} (nap param moi)",
                     "ok": r["ok"], "chi_tiet": r["status"]})
        r = await self.start(rep)
        buoc.append({"buoc": f"start replicat {rep} (SAU)", "ok": r["ok"],
                     "chi_tiet": r["status"]})
        return {"stream": stream, "buoc": buoc,
                "ghi_chu": "Extract chạy lại từ MỐC CHECKPOINT nên các bảng cũ không mất bản ghi "
                           "nào."}
