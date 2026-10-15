"""Phan bo sung cho wizard: index, chia lo, va buoc cho job roi ra.

Tach thanh module rieng thay vi noi vao `wizard.py`. Ly do rat cu the: trong
phien lam viec nay toi da lam hong `wizard.py` mot lan vi sua file 1.600 dong
bang cach khop chuoi - `next()` bat dung chuoi DAU TIEN trong file thay vi
chuoi trong ham can sua, va xoa mat 1.580 dong. Module rieng thi them tinh
nang moi khong phai cham vao file cu.

BA VIEC O DAY

1. INDEX: mark UNUSABLE truoc khi nap, REBUILD sau khi nap.
   Lo BC do duoc: nap NHANH x2,3-3,8 va tranh index phinh +64%.
   CHI mark index GLOBAL NONUNIQUE. GIU index UNIQUE o trang thai VALID -
   chung la thu duy nhat bat duoc dong trung NGAY trong luc nap. Mark het
   cho nhanh roi phat hien trung sau khi da nap 100 trieu dong thi khong con
   duong lui nao ngoai TRUNCATE lam lai.

2. CHIA LO: mot anh flashback co song du lau cho ca dot nap khong?
   Lo 4 nap 64 PHUT da an that `ORA-01555: snapshot too old` +
   `ORA-02063: preceding line from DBL_PMNV`. Nguon co undo_retention 900s.
   Mot SCN duy nhat chi an toan khi dot nap NGAN hon thoi gian giu undo.
   Module nay PHAT HIEN va CHAN; no KHONG tu bia ra co che chia lo cho bang
   bat ky - xem ghi chu trong `_kiem_chia_lo`.

3. BA DUONG DI de chay DDL tren schema ODS, theo thu tu quyen tu HEP den RONG.
   Xem `_chay_hoac_huong_dan`.
"""

from __future__ import annotations

import json
import logging
import time

from .adapters.ora import check_ident

log = logging.getLogger("ggadmin.wizard.index")

# Bien DUOI cua toc do da do (11.000-100.000 dong/giay). Dung bien duoi de
# uoc LAU hon thuc te, tuc la nghieng ve phia an toan.
TOC_DO_NAP_THAP = 15_000
TS_INDEX = "BSH_INDEX"
# Nguong 0,7: de lai bien an toan cho rebuild index va bien dong tai.
TY_LE_AN_TOAN = 0.7


class IndexMixin:
    """Tron vao `Wizard`. Chi chua phuong thuc MOI, khong ghi de cai cu."""

    # ================================================== doc trang thai index
    async def _index_dich(self, run: dict) -> list[dict]:
        return await self.ora.rows(
            "SELECT index_name, uniqueness, index_type, partitioned, status,"
            "       tablespace_name, degree"
            "  FROM dba_indexes"
            " WHERE table_owner = :o AND table_name = :t"
            " ORDER BY uniqueness DESC, index_name",
            o=run["tgt_owner"], t=check_ident(run["tgt_table"]))

    @staticmethod
    def _duoc_mark(r: dict) -> bool:
        """Index nao duoc phep mark UNUSABLE: GLOBAL + NONUNIQUE + binh thuong."""
        return (r["uniqueness"] == "NONUNIQUE"
                and r["partitioned"] == "NO"
                and (r["index_type"] or "").startswith(
                    ("NORMAL", "FUNCTION-BASED NORMAL")))

    def _sql_index(self, run: dict, ds: list[str], hanh_dong: str,
                   cua_bang: dict[str, str] | None = None) -> str:
        """Sinh script index.

        `ds`       danh sach TEN INDEX
        `cua_bang` index nao thuoc bang nguon nao: {ten_index: ten_bang}.
                   Chi can cho LO NHIEU BANG, vi do song song phai tinh theo
                   so dong CUA BANG CHU moi index. Thieu map nay thi index
                   cua bang 283 dong se an do song song cua bang 1,5 trieu
                   dong dung chung script - lay PGA ma khong doi lay duoc gi,
                   tren mot may da vuot RAM.
        """
        o = run["tgt_owner"]
        cua_bang = cua_bang or {}
        nhieu = len(self._cac_bang(run)) > 1
        dong: list[str] = [
            "-- ggadmin wizard #%s: %s %d index" % (run["id"], hanh_dong, len(ds))
            + (" của %d bảng" % len(self._cac_bang(run)) if nhieu
               else " cua %s.%s" % (o, run["tgt_table"])),
            "-- Chay bang CHINH CHU SCHEMA %s (khong can sysdba)." % o,
            "-- Sinh luc %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "WHENEVER SQLERROR EXIT FAILURE",
        ]

        def par_cua(ten_index: str) -> int:
            b = cua_bang.get(ten_index)
            if nhieu and b:
                return self.do_song_song(self._so_dong_bang(run, b))
            return self.do_song_song(self._so_dong(run))

        if nhieu:
            dong.insert(3, "-- Do song song tinh RIENG theo so dong cua bang"
                           " chu moi index.")
        else:
            dong.insert(3, "-- Bang co %s dong -> do song song %d"
                           " (do_song_song())."
                        % (format(self._so_dong(run), ","),
                           self.do_song_song(self._so_dong(run))))
        for ten in ds:
            par = par_cua(ten)
            if hanh_dong == "UNUSABLE":
                dong.append('ALTER INDEX %s."%s" UNUSABLE;' % (o, ten))
            elif par <= 1:
                # Bang nho: KHONG song song. Moi parallel slave lay PGA tu mot
                # tran da vuot RAM cua may, va khong doi lay duoc gi cho mot
                # bang vai nghin dong.
                dong.append('ALTER INDEX %s."%s" REBUILD TABLESPACE %s'
                            ' NOPARALLEL;' % (o, ten, TS_INDEX))
            else:
                # REBUILD ... PARALLEL n dat DO SONG SONG CO DINH cho index,
                # nen moi truy van ve sau se lay ke hoach song song. Phai
                # NOPARALLEL ngay sau do - cai bay rat de bo qua.
                dong.append('ALTER INDEX %s."%s" REBUILD TABLESPACE %s'
                            ' PARALLEL %d;' % (o, ten, TS_INDEX, par))
                dong.append('ALTER INDEX %s."%s" NOPARALLEL;' % (o, ten))
        dong.append("PROMPT SO_INDEX=%d" % len(ds))
        dong.append("EXIT")
        return chr(10).join(dong) + chr(10)

    @staticmethod
    def do_song_song(so_dong: int) -> int:
        """Do song song theo SO DONG cua bang, khong phai mot con so co dinh.

        VI SAO - do duoc tren CHINH server nay 2026-10-14

        `sga_target` = 21.056 MB va `pga_aggregate_limit` = 14.000 MB tren mot
        may 31 GB RAM: cong lai 35 GB tren 31 GB. Swap 4 GB da DAY 100% va he
        VAN dang day ra ~9 MB/phut. Moi parallel slave them vao deu lay PGA tu
        cai tran da vuot do; `parallel_max_servers` = 320 nen khong co gi chan.

        `PARALLEL 8` cho mot bang 3.355 dong (HT_MA_KVUC vua them) la vo nghia:
        chi phi dieu phoi 8 slave lon hon ca viec doc bang. No chi dung cho
        bang hang tram trieu dong nhu 3 bang BC_*.
        """
        if so_dong <= 100_000:
            return 1                 # bang danh muc: khong song song
        if so_dong <= 5_000_000:
            return 4
        return 8

    def _so_dong(self, run: dict) -> int:
        return int(json.loads(run["ctx_json"] or "{}").get("n_src") or 0)

    def _so_dong_bang(self, run: dict, bang: str) -> int:
        """So dong nguon cua MOT bang trong lo (tu ctx["bang"][ten])."""
        return int((self._ctx_bang(run, bang) or {}).get("n_src") or 0)

    async def _index_cua_lo(self, run: dict
                            ) -> tuple[list[dict], dict[str, str]]:
        """Toan bo index cua MOI bang trong lo + map index -> bang nguon.

        Tra ve (danh sach row index, {ten_index: ten_bang_nguon}).
        Ten index la duy nhat trong mot schema nen map nay khong nhap nhang.
        """
        ds = self._cac_bang(run)
        tat: list[dict] = []
        cua: dict[str, str] = {}
        for b in ds:
            rb = self._run_bang(run, b) if len(ds) > 1 else run
            for r in await self._index_dich(rb):
                tat.append(dict(r, _bang=b))
                cua[r["index_name"]] = b
        return tat, cua

    # ======================================================= ba duong di
    async def _co_package(self) -> bool:
        """Package ODS.GGADM_API co san va VALID ca hai phan khong?"""
        try:
            n = await self.ora.scalar(
                "SELECT COUNT(*) FROM dba_objects WHERE owner='ODS'"
                " AND object_name='GGADM_API' AND status='VALID'"
                " AND object_type IN ('PACKAGE','PACKAGE BODY')")
            return int(n or 0) == 2
        except Exception:                                       # noqa: BLE001
            return False

    def _sql_goi_package(self, run: dict, proc: str) -> str:
        """Script goi ODS.GGADM_API - chay bang GGADM_LOAD, quyen hep nhat.

        Pham vi cua package TU GIOI HAN o cac bang ma GGADM_LOAD da co quyen
        INSERT (= 73 bang dang trong luong), nen khong can dong bo danh sach
        tay va khong co quyen ANY nao duoc cap.
        """
        ds = self._cac_bang(run)
        nhieu = len(ds) > 1
        dong = [
            "-- ggadmin wizard #%s: goi ODS.GGADM_API.%s" % (run["id"], proc),
            "-- Chay bang GGADM_LOAD, KHONG giu mat khau chu schema.",
        ]
        if nhieu:
            dong.append("-- %d bang; do song song tinh RIENG theo so dong"
                        " tung bang." % len(ds))
        dong += [
            "WHENEVER SQLERROR EXIT FAILURE",
            "SET SERVEROUTPUT ON SIZE UNLIMITED",
            "BEGIN",
        ]
        for b in ds:
            rb = self._run_bang(run, b) if nhieu else run
            t = check_ident(rb["tgt_table"])
            n = self._so_dong_bang(run, b) if nhieu else self._so_dong(run)
            # `index_rebuild` nhan do song song. Truyen theo SO DONG thay vi
            # de mac dinh 8 - xem ghi chu trong do_song_song() ve suc ep bo
            # nho that do duoc tren server nay.
            if proc == "index_rebuild":
                dong.append(
                    "  ODS.GGADM_API.index_rebuild(%s%s%s, %s%s%s, %d);"
                    % (chr(39), t, chr(39), chr(39), TS_INDEX, chr(39),
                       self.do_song_song(n)))
            else:
                dong.append("  ODS.GGADM_API.%s(%s%s%s);"
                            % (proc, chr(39), t, chr(39)))
            if nhieu:
                dong.append("  DBMS_OUTPUT.PUT_LINE('%s: %s xong (%s dòng, độ song song %d)');"
                            % (t, proc, format(n, ","), self.do_song_song(n)))
        dong += ["END;", "/", "EXIT"]
        return chr(10).join(dong) + chr(10)

    async def _chay_hoac_huong_dan(self, run: dict, ten_file: str, sql: str,
                                   nhan: str, ts: dict,
                                   proc_package: str | None = None) -> dict:
        """Ba duong di, theo thu tu quyen tu HEP den RONG.

        1. `ODS.GGADM_API` (definer-rights, chay bang GGADM_LOAD)
           Quyen hep nhat: GGADM_LOAD co dung 4 y dinh tren 73 bang, khong
           co quyen ANY nao, khong giu mat khau chu schema.

        2. khoi [ods] trong secrets.ini
           Chay duoc, nhung cong cu GIU MAT KHAU CHU SCHEMA - tuc la giu ca
           kha nang DROP bat ky object nao trong 3.600 object cua ODS. Tinh
           chat "du web app bi chiem quyen hoan toan, ke tan cong khong DROP
           duoc bang nao" KHONG con dung nua.

        3. huong dan chay tay
           Va KHONG tin vao viec "da chay": lan bam lai, buoc se DO LAI
           trang thai tren DB va chi cho PASS neu so do dung.
        """
        from . import jobs as jobmod
        from .wizard import WizardError

        if proc_package and await self._co_package():
            p = self._luu_script(ten_file.replace(".sql", "_pkg.sql"),
                                 self._sql_goi_package(run, proc_package))
            jid = jobmod.spawn_sqlplus(
                self.cfg, self.con, kind="WZ%s_%s" % (run["id"], nhan),
                sql_path=p, cred="ggadm_load",
                created_by=ts.get("who", "wizard"),
                params={"wizard": run["id"], "buoc": nhan, "qua": "package"})
            return {"msg": "Đã tạo việc chạy nền #%s (%s) qua gói ODS.GGADM_API - đường quyền hẹp "
                           "nhất, chạy bằng tài khoản nạp. Theo dõi: tail -f logs/job_%s.log" % (jid, nhan, jid),
                    "cho_job": jid, "output": [str(p)]}

        p = self._luu_script(ten_file, sql)
        if "ods" in self.cfg.creds:
            jid = jobmod.spawn_sqlplus(
                self.cfg, self.con, kind="WZ%s_%s" % (run["id"], nhan),
                sql_path=p, cred="ods", created_by=ts.get("who", "wizard"),
                params={"wizard": run["id"], "buoc": nhan, "qua": "ods"})
            return {"msg": "Đã tạo việc chạy nền #%s (%s) bằng chính tài khoản chủ schema ODS. "
                           "Theo dõi: tail -f logs/job_%s.log"
                           % (jid, nhan, jid),
                    "cho_job": jid,
                    "output": [str(p), "",
                               "GHI CHÚ AN TOÀN: đang dùng mật khẩu chủ schema ODS, tức là một mật "
                               "khẩu có thể xoá bất kỳ đối tượng nào của ODS. Chạy "
                               "tools/cs10_ods_index_api.sql (một lần, bằng sysdba) để chuyển sang gói "
                               "ODS.GGADM_API, rồi thu hồi quyền đăng nhập của ODS - khi đó công cụ "
                               "không còn giữ mật khẩu nào xoá được gì."]}

        raise WizardError(
            "Không có đường nào để chạy lệnh này:" + chr(10)
            + "  - chưa có gói ODS.GGADM_API (chạy tools/cs10_ods_index_api.sql bằng "
              "sysdba, một lần)" + chr(10)
            + "  - và chưa có khối [ods] trong conf/secrets.ini" + chr(10)
            + chr(10)
            + "Hoặc chạy tay: sqlplus ODS/<mật khẩu>@BSHC @%s" % p + chr(10)
            + "rồi bấm lại bước này - công cụ sẽ ĐO LẠI trạng thái trên DB và chỉ cho "
              "qua nếu đúng. Không tin vào lời nói, chỉ tin số đo.")

    # ============================================== buoc mark UNUSABLE
    async def _b_index_unusable(self, run: dict, ts: dict) -> dict:
        idx, cua_bang = await self._index_cua_lo(run)
        can = [r["index_name"] for r in idx if self._duoc_mark(r)]
        giu = [r["index_name"] + " (" + r["uniqueness"] + ")"
               for r in idx if not self._duoc_mark(r)]
        da = [r["index_name"] for r in idx if r["status"] == "UNUSABLE"]

        ds_bang = self._cac_bang(run)
        out = ["%d index trên %s" % (
            len(idx),
            "%d bang" % len(ds_bang) if len(ds_bang) > 1
            else "%s.%s" % (run["tgt_owner"], run["tgt_table"]))]
        for r in idx:
            out.append("  %-12s %-28s %-9s %-8s part=%s degree=%s %s" % (
                r.get("_bang") or "", r["index_name"], r["uniqueness"],
                r["status"], r["partitioned"], r["degree"],
                "-> mark UNUSABLE" if self._duoc_mark(r) else "-> GIU VALID"))
        if giu:
            out.append("GIU VALID: " + ", ".join(giu)
                       + "   <- index duy nhất là thứ duy nhất bắt được dòng trùng NGAY trong lúc "
                         "nạp")

        ctx = json.loads(run["ctx_json"] or "{}")
        ctx["index_mark"] = can
        self._ghi_run(run["id"], ctx_json=json.dumps(ctx, ensure_ascii=False))

        if not can:
            return {"msg": "Không có index thường nào cần vô hiệu",
                    "output": out}
        if set(can) <= set(da):
            return {"msg": "Cả %d index đã bị vô hiệu sẵn" % len(can),
                    "output": out}
        r = await self._chay_hoac_huong_dan(
            run, "wz%s_index_unusable.sql" % run["id"],
            self._sql_index(run, can, "UNUSABLE", cua_bang), "INDEX_UNUSABLE",
            ts, proc_package="index_unusable")
        r["output"] = out + list(r.get("output") or [])
        return r

    async def _xn_index_unusable(self, run: dict, j: dict):
        ctx = json.loads(run["ctx_json"] or "{}")
        can = set(ctx.get("index_mark") or [])
        tat, _ = await self._index_cua_lo(run)
        idx = {r["index_name"]: r["status"] for r in tat}
        chua = sorted(t for t in can if idx.get(t) != "UNUSABLE")
        if chua:
            return False, ["còn %d index CHƯA bị vô hiệu: %s"
                           % (len(chua), ", ".join(chua[:8]))]
        return True, ["kiểm chứng trên DB: cả %d index đã bị vô hiệu" % len(can)]

    # ================================================== buoc REBUILD
    async def _b_index_rebuild(self, run: dict, ts: dict) -> dict:
        ctx = json.loads(run["ctx_json"] or "{}")
        can = ctx.get("index_mark") or []
        idx, cua_bang = await self._index_cua_lo(run)
        hien = {r["index_name"] for r in idx}
        unusable = [r["index_name"] for r in idx if r["status"] == "UNUSABLE"]
        # Rebuild MOI index dang UNUSABLE, khong chi cac index minh mark: mot
        # index UNUSABLE sot lai se lam moi truy van bo qua no IM LANG
        # (skip_unusable_indexes = TRUE la mac dinh tu 11g).
        ds = sorted((set(can) | set(unusable)) & hien)
        out = ["index đang bị vô hiệu: %d" % len(unusable),
               "sẽ dựng lại: %d index" % len(ds)]
        if not ds:
            return {"msg": "Không có index nào cần dựng lại", "output": out}
        out.append("Lưu ý: dựng lại index kèm độ song song sẽ GHI CỐ ĐỊNH độ song song đó "
                   "vào index; script tự trả index về chế độ không song song ngay sau đó.")
        r = await self._chay_hoac_huong_dan(
            run, "wz%s_index_rebuild.sql" % run["id"],
            self._sql_index(run, ds, "REBUILD", cua_bang), "INDEX_REBUILD", ts,
            proc_package="index_rebuild")
        r["output"] = out + list(r.get("output") or [])
        return r

    async def _xn_index_rebuild(self, run: dict, j: dict):
        idx, _ = await self._index_cua_lo(run)
        con = [r["index_name"] for r in idx if r["status"] == "UNUSABLE"]
        ss = ["%s(degree=%s)" % (r["index_name"], r["degree"]) for r in idx
              if str(r["degree"] or "1").strip() not in ("1", "DEFAULT")]
        if con:
            return False, ["còn %d index bị vô hiệu: %s"
                           % (len(con), ", ".join(con[:8]))]
        gc = ["kiểm chứng: %d index, 0 index bị vô hiệu" % len(idx)]
        if ss:
            gc.append("CẢNH BÁO: index còn độ song song lớn hơn 1: " + ", ".join(ss)
                      + " -> mọi truy vấn về sau sẽ lấy kế hoạch song song. Chạy ALTER INDEX ... "
                        "NOPARALLEL.")
        return True, gc

    # ============================================ quyet dinh chia lo
    async def _quyet_dinh_chia_lo(self, run: dict) -> dict:
        # LO NHIEU BANG: lay TONG so dong ca lo, khong phai so dong mot bang.
        # Ca lo nap trong MOT giao dich doc o MOT SCN, nen anh flashback phai
        # con hieu luc den het bang CUOI CUNG. Tinh theo mot bang la uoc
        # thieu, va uoc thieu o day dan den ORA-01555 giua dot nap.
        ds = self._cac_bang(run)
        nhieu = len(ds) > 1
        if nhieu:
            n = sum(self._so_dong_bang(run, b) for b in ds)
        else:
            n = int(json.loads(run["ctx_json"] or "{}").get("n_src") or 0)
        try:
            undo = int(await self.ora.undo_retention_source() or 0)
        except Exception:                                       # noqa: BLE001
            undo = 0
        uoc = (n / TOC_DO_NAP_THAP) if n else 0.0
        an_toan = bool(undo) and uoc <= TY_LE_AN_TOAN * undo
        part: list[dict] = []
        for b in ds:
            rb = self._run_bang(run, b) if nhieu else run
            part += await self.ora.rows(
                "SELECT partition_name FROM dba_tab_partitions"
                " WHERE table_owner=:o AND table_name=:t"
                " ORDER BY partition_position",
                o=rb["tgt_owner"], t=check_ident(rb["tgt_table"]))
        if not undo:
            ly_do = "không đọc được thời gian nguồn giữ ảnh dữ liệu cũ"
        elif an_toan:
            ly_do = ("ước nạp %ds, không quá %d%% của %ds mà nguồn còn giữ được"
                     % (int(uoc), int(TY_LE_AN_TOAN * 100), undo))
        else:
            ly_do = ("ước nạp %ds, VƯỢT %d%% của %ds mà nguồn còn giữ được"
                     % (int(uoc), int(TY_LE_AN_TOAN * 100), undo))
        return {"so_dong": n, "uoc_giay": int(uoc), "undo_giay": undo,
                "an_toan": an_toan, "so_partition": len(part),
                "so_bang": len(ds),
                "partitions": [r["partition_name"] for r in part],
                "ly_do": ly_do}

    async def _kiem_chia_lo(self, run: dict) -> list[str]:
        """Chan truoc khi sinh script nap neu mot SCN duy nhat khong an toan.

        CO Y KHONG tu bia ra co che chia lo cho bang bat ky. Chia lo dung
        phai theo PARTITION KEY CUA DICH, tuc la phai doc va phan tich
        `high_value` cua tung partition - mot cot LONG, da tung gay ORA-00932
        khi so sanh. Viet mot trinh phan tich bien partition ma KHONG co bang
        nao de thu hom nay la dung cach lam da sinh ra 5 loi trong phien nay.

        Nen o day: PHAT HIEN va CHAN, kem hai duong di that cho DBA.
        """
        from .wizard import WizardError
        d = await self._quyet_dinh_chia_lo(run)
        out = ["số bảng trong lô      : %d" % d.get("so_bang", 1),
               "số dòng ở nguồn       : %s%s" % (
                   format(d["so_dong"], ","),
                   "  (TỔNG cả lô - cả lô nạp ở một thời điểm nên ảnh dữ liệu cũ phải còn "
                   "hiệu lực đến hết bảng cuối)"
                   if d.get("so_bang", 1) > 1 else ""),
               "ước thời gian nạp     : %ds (ở %s dòng/giây - biên DƯỚI của khoảng "
               "11k-100k đã đo)" % (d["uoc_giay"], format(TOC_DO_NAP_THAP, ",")),
               "nguồn giữ ảnh dữ liệu : %ds" % d["undo_giay"],
               "phân vùng ở đích      : %d" % d["so_partition"],
               "ket luan           : %s" % d["ly_do"]]
        if d["an_toan"]:
            out.append("-> nạp ở MỘT thời điểm duy nhất là an toàn cho cả đợt")
            return out
        goi_y = []
        if d["so_partition"] > 1:
            goi_y.append("  - bảng đích có %d phân vùng: chia lô theo KHOÁ PHÂN VÙNG CỦA ĐÍCH, mỗi "
                         "lô một thời điểm mới (mỗi thời điểm vẫn không được cũ hơn ngưỡng "
                         "dưới). Đây là cách đã dùng thật cho 3 bảng BC_*." % d["so_partition"])
        goi_y.append("  - hoặc nâng tạm `undo_retention` ở NGUỒN cho đủ đợt nạp, rồi hạ lại")
        goi_y.append("  - hoặc nạp tay theo tài liệu "
                     "Quy-trinh-them-bang-vao-luong-GoldenGate.md")
        raise WizardError(
            "Nạp ở một thời điểm duy nhất KHÔNG an toàn cho đợt này." + chr(10)
            + chr(10).join(out) + chr(10) + chr(10)
            + "Ảnh dữ liệu cũ sẽ hết hiệu lực GIỮA đợt nạp -> lỗi ORA-01555 kèm "
              "ORA-02063 (đã gặp thật ở lô 4, nạp 64 phút)."
            + chr(10) + chr(10) + "Ba duong di:" + chr(10)
            + chr(10).join(goi_y) + chr(10) + chr(10)
            + "Công cụ CÓ Ý không tự chia lô: chia lô đúng phải phân tích biên của "
              "từng phân vùng ở đích (một cột kiểu LONG), và viết phần đó mà không có "
              "bảng nào để thử chính là cách làm đã sinh ra 5 lỗi trong phiên xây "
              "dựng.")

    # ======================================= buoc cho JOB ROI RA
    def job_cua_buoc(self, rid: int, no: int) -> int | None:
        ctx = json.loads(self.lay(rid)["ctx_json"] or "{}")
        v = ctx.get("job_buoc_%d" % no)
        return int(v) if v else None

    def _ghi_job_buoc(self, rid: int, no: int, job_id: int) -> None:
        ctx = json.loads(self.lay(rid)["ctx_json"] or "{}")
        ctx["job_buoc_%d" % no] = int(job_id)
        self._ghi_run(rid, ctx_json=json.dumps(ctx, ensure_ascii=False))

    async def kiem_job(self, rid: int, no: int) -> dict:
        """Doi chieu trang thai job roi ra voi buoc dang CHO.

        Sau khi job bao SUCCEEDED, VAN chay `_xn_<ten_buoc>` neu co, de KIEM
        CHUNG TRANG THAI CUOI tren DB. Khong tin vao ma thoat: bai hoc cua ca
        phien nay la ma thoat 0 khong co nghia la viec da xong dung.
        """
        from . import jobs as jobmod
        from .wizard import BO_BUOC, buoc_cua

        jid = self.job_cua_buoc(rid, no)
        if jid is None:
            return {"state": "KHONG_CO_JOB"}
        j = jobmod.trang_thai(self.con, jid)
        if j is None:
            return {"state": "KHONG_CO_JOB"}
        if j["state"] in ("QUEUED", "RUNNING"):
            return {"state": j["state"], "job": j,
                    "log": jobmod.duoi_log(self.cfg, jid, 25)}

        run = self.lay(rid)
        st = buoc_cua(run["kind"], no)
        if j["state"] != "SUCCEEDED":
            self._ghi_buoc(rid, no, state="FAIL",
                           finished_at=int(time.time()),
                           err_msg="job #%s: %s" % (jid, j["err_msg"] or j["state"]))
            return {"state": "FAIL", "job": j,
                    "log": jobmod.duoi_log(self.cfg, jid, 40)}

        xn = getattr(self, "_xn_" + st.name.lower(), None)
        gc: list[str] = []
        if xn is not None:
            try:
                ok, gc = await xn(run, j)
            except Exception as e:                              # noqa: BLE001
                ok, gc = False, ["kiểm chứng lỗi: %s" % e]
            if not ok:
                self._ghi_buoc(
                    rid, no, state="FAIL", finished_at=int(time.time()),
                    err_msg=("việc chạy #%s báo thành công NHƯNG kiểm chứng trên DB không đạt: %s" % (jid, "; ".join(gc)))[:400])
                return {"state": "FAIL", "job": j, "ghi_chu": gc,
                        "log": jobmod.duoi_log(self.cfg, jid, 40)}

        r0 = self.con.execute(
            "SELECT output FROM wizard_step WHERE run_id=? AND step_no=?",
            (rid, no)).fetchone()
        cu = (r0["output"] if r0 else "") or ""
        self._ghi_buoc(rid, no, state="PASS", finished_at=int(time.time()),
                       output=(cu + chr(10) + chr(10).join(gc))[:60_000],
                       err_msg=None)
        tong = max(x.no for x in BO_BUOC[run["kind"]])
        if no >= tong:
            self._ghi_run(rid, state="DONE", finished_at=int(time.time()),
                          cur_step=no)
        else:
            self._ghi_run(rid, cur_step=no + 1)
        return {"state": "PASS", "job": j, "ghi_chu": gc,
                "log": jobmod.duoi_log(self.cfg, jid, 25)}

    # ================================================= tao bang dich
    async def _meta_nguon(self, run: dict) -> tuple[list, list, list]:
        """Doc cot / PK / index cua bang o NGUON."""
        o, t = run["src_owner"], check_ident(run["src_table"])
        cols = await self.ora.rows(
            "SELECT column_id, column_name, data_type, data_length,"
            "       char_length, char_used, data_precision, data_scale, nullable"
            "  FROM dba_tab_columns@DBL_PMNV"
            " WHERE owner = :o AND table_name = :t ORDER BY column_id",
            o=o, t=t)
        pk = await self.ora.rows(
            "SELECT cc.column_name, cc.position"
            "  FROM dba_constraints@DBL_PMNV c"
            "  JOIN dba_cons_columns@DBL_PMNV cc"
            "    ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name"
            " WHERE c.owner = :o AND c.table_name = :t AND c.constraint_type = 'P'"
            " ORDER BY cc.position", o=o, t=t)
        idx = await self.ora.rows(
            "SELECT i.index_name, i.uniqueness, i.index_type,"
            "       LISTAGG(ic.column_name, ', ')"
            "         WITHIN GROUP (ORDER BY ic.column_position) cols"
            "  FROM dba_indexes@DBL_PMNV i"
            "  JOIN dba_ind_columns@DBL_PMNV ic"
            "    ON ic.index_owner = i.owner AND ic.index_name = i.index_name"
            " WHERE i.table_owner = :o AND i.table_name = :t"
            " GROUP BY i.index_name, i.uniqueness, i.index_type"
            " ORDER BY i.uniqueness DESC, i.index_name", o=o, t=t)
        return cols, pk, idx

    async def _xt_tao_bang_dich(self, run: dict) -> str:
        from .ddl import sinh_ddl
        n = await self.ora.scalar(
            "SELECT COUNT(*) FROM dba_tables WHERE owner=:o AND table_name=:t",
            o=run["tgt_owner"], t=check_ident(run["tgt_table"]))
        if n:
            return (f'Bảng {run["tgt_owner"]}."{run["tgt_table"]}" ĐÃ TỒN TẠI - bước này sẽ '
                    "bỏ qua, không sinh lệnh tạo bảng.")
        cols, pk, idx = await self._meta_nguon(run)
        return sinh_ddl(tgt_owner=run["tgt_owner"], tgt_table=run["tgt_table"],
                        cols=cols, pk=pk, idx=idx,
                        src=f'{run["src_owner"]}.{run["src_table"]}')

    async def _b_tao_bang_dich(self, run: dict, ts: dict) -> dict:
        """Tao cac bang dich CON THIEU cua lo trong MOT script.

        Buoc nay chay qua job nen khong lap theo bang duoc (mot buoc doi
        dung mot job). Thay vao do no gop DDL cua moi bang con thieu vao mot
        script. Bang da ton tai thi bo qua - va o lo CN_* thi CA 8 BANG da
        ton tai san tu truoc, nen buoc nay chi bao cao va di tiep.
        """
        from .ddl import sinh_ddl
        ds = self._cac_bang(run)
        nhieu = len(ds) > 1
        out: list[str] = []
        phai_tao: list[str] = []
        sql_phan: list[str] = []
        for b in ds:
            rb = self._run_bang(run, b) if nhieu else run
            o, t = rb["tgt_owner"], check_ident(rb["tgt_table"])
            n = await self.ora.scalar(
                "SELECT COUNT(*) FROM dba_tables WHERE owner=:o"
                " AND table_name=:t", o=o, t=t)
            if n:
                # Kiem DU bo quyen, khong chi INSERT: script nap con can
                # SELECT tren bang dich cho lop chan 1. Xem QUYEN_BANG_DICH.
                from .wizard import QUYEN_BANG_DICH
                co = {r["grantee"] + "." + r["privilege"]
                      for r in await self.ora.rows(
                          "SELECT grantee, privilege FROM dba_tab_privs"
                          " WHERE owner=:o AND table_name=:t"
                          " AND grantee IN ('GGADM_LOAD','GGADM_RO')",
                          o=o, t=t)}
                thieu_q = [c for c in QUYEN_BANG_DICH if c not in co]
                out.append('%-14s %s."%s" đã tồn tại - không tạo lại%s'
                           % (b, o, t, "" if not thieu_q else
                              "  <- THIẾU quyền: " + ", ".join(thieu_q)))
                if thieu_q:
                    out.append("    Bước kiểm bảng đích sẽ chặn và in ra lệnh "
                               "cấp quyền cần chạy.")
                continue
            cols, pk, idx = await self._meta_nguon(rb)
            phai_tao.append(b)
            sql_phan.append(sinh_ddl(
                tgt_owner=o, tgt_table=t, cols=cols, pk=pk, idx=idx,
                src='%s.%s' % (rb["src_owner"], rb["src_table"])))
            out.append("%-14s sẽ TẠO: %d cột, %s khoá chính, %d index ở nguồn"
                       % (b, len(cols), "co" if pk else "KHONG co", len(idx)))

        if not phai_tao:
            return {"msg": "Cả %d bảng đích đã tồn tại - bỏ qua bước này"
                           % len(ds), "output": out}

        # Gop nhieu DDL: bo dong EXIT giua cac phan, chi giu mot cai o cuoi.
        than = []
        for s in sql_phan:
            than.append(chr(10).join(
                d for d in s.splitlines() if d.strip().upper() != "EXIT"))
        sql = (chr(10).join(than) + chr(10) + "EXIT" + chr(10))
        ten = ("wz%s_ddl_lo%d.sql" % (run["id"], len(phai_tao)) if nhieu
               else "wz%s_ddl_%s.sql" % (run["id"], phai_tao[0].lower()))
        r = await self._chay_hoac_huong_dan(run, ten, sql, "TAO_BANG_DICH", ts)
        r["output"] = out + list(r.get("output") or [])
        return r

    async def _xn_tao_bang_dich(self, run: dict, j: dict):
        """Kiem chung: bang ton tai, BO COT KHOP, va grant da co.

        Khong tin vao ma thoat - doi chieu bo cot nguon/dich. Dich thieu mot
        cot thi `COLMAP (USEDEFAULTS)` bo qua cot do IM LANG va du lieu cua
        no khong bao gio toi dich.
        """
        ds = self._cac_bang(run)
        nhieu = len(ds) > 1
        gc: list[str] = []
        for b in ds:
            rb = self._run_bang(run, b) if nhieu else run
            o, t = rb["tgt_owner"], check_ident(rb["tgt_table"])
            n = await self.ora.scalar(
                "SELECT COUNT(*) FROM dba_tables WHERE owner=:o"
                " AND table_name=:t", o=o, t=t)
            if not n:
                return False, gc + [
                    '%s: %s."%s" VẪN không tồn tại sau khi chạy lệnh tạo bảng' % (b, o, t)]
            src = {c["column_name"] for c in (await self._meta_nguon(rb))[0]}
            tgt = {r["column_name"] for r in await self.ora.rows(
                "SELECT column_name FROM dba_tab_columns"
                " WHERE owner=:o AND table_name=:t", o=o, t=t)}
            thieu = sorted(src - tgt)
            if thieu:
                return False, gc + [
                    "%s: đích THIẾU %d cột: %s"
                    % (b, len(thieu), ", ".join(thieu[:8]))]
            from .wizard import QUYEN_BANG_DICH
            co = {r["grantee"] + "." + r["privilege"]
                  for r in await self.ora.rows(
                      "SELECT grantee, privilege FROM dba_tab_privs"
                      " WHERE owner=:o AND table_name=:t"
                      " AND grantee IN ('GGADM_LOAD','GGADM_RO')", o=o, t=t)}
            thieu_q = [c for c in QUYEN_BANG_DICH if c not in co]
            if thieu_q:
                return False, gc + [
                    "%s: tài khoản nạp chưa có quyền ghi - bước nạp sẽ thất bại"
                    % b]
            ni = await self.ora.scalar(
                "SELECT COUNT(*) FROM dba_indexes WHERE table_owner=:o"
                " AND table_name=:t", o=o, t=t)
            gc.append('%-14s %s."%s": %d cột (khớp đủ %d cột nguồn), %d index, tài khoản nạp '
                      "có quyền ghi" % (b, o, t, len(tgt), len(src), ni))
        return True, gc
