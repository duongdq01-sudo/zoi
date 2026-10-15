"""ggadmin - FastAPI app.

Bind 127.0.0.1, 1 worker (nhieu worker se nhan ban poller va tranh ghi SQLite).

Phase 1 chi doc. Phase 2 co thao tac GHI, va MOI thao tac ghi di qua DUNG
MOT duong: /action/arm -> /action/fire.

Vi sao mot duong duy nhat thay vi mot endpoint cho tung viec: khung an toan
(pre-flight, token 60 giay, go tay ten, ly do, audit truoc khi chay) chi can
dung o MOT cho. Them mot thao tac ghi moi la them mot dong vao bang OPS, va
no tu dong duoc bao boc day du. Lam nguoc lai - mot endpoint moi cho moi
viec - thi som muon se co mot endpoint quen mot lop.
"""

from __future__ import annotations

import json
import logging
import time
from html import escape
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auto, config, db, history, khoiphuc, registry
from . import params as paramsmod
from .adapters import gg, osfs
from .adapters.ora import Ora
from .auth import COOKIE, MAX_AGE, Auth
from .collector import Collector
from .control import Control, ControlError
from .notify import MAX_TEST_MSG, Notifier
from .params import ParamError
from .preflight import Preflight
from .safety import L1, L2, Denied, Guard
from .verify import Busy, Verifier
from . import wizard as wzmod
from .wizard import Wizard, WizardError

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ggadmin")

cfg = config.load()
con = db.init(cfg.db_path)
ora = Ora(cfg, "ggadm_ro")
auth = Auth(cfg, con)
notifier = Notifier(cfg, con)
collector = Collector(cfg, con, ora, notifier)
control = Control(cfg, con, collector)
pre = Preflight(cfg, con, ora, collector)
guard = Guard(cfg, con, collector, auth)
verifier = Verifier(cfg, con, ora, collector, control)
wizard = Wizard(cfg, con, ora, control, verifier)
tudong = auto.TuDong(cfg, con, wizard, pre, auth, lambda o, t: _pf_cua(o, t))

# Tai khoan dau tien. must_change=False theo yeu cau nguoi dung (admin/a123),
# nhung co che doi mat khau van giu de siet sau nay de.
if db.ensure_user(con, "admin", "a123", "ADMIN", "Quan tri", must_change=False):
    log.info("đã tạo tài khoản quản trị đầu tiên")


def _reconcile_jobs() -> None:
    """Doi chieu trang thai job sau khi app khoi dong lai.

    HAI LOAI JOB, xu ly KHAC NHAU:

    - Job TRONG TIEN TRINH (verify): chay bang asyncio task cua chinh tien
      trinh web, nen restart app la task chet - KE CA khoi `finally` khong
      chay. Phai danh dau KILLED, neu khong no treo 'RUNNING' vinh vien va
      `preflight` se chan moi lan verify ve sau.

    - Job DETACH (nap, index, stats): chay o session rieng nen restart app
      KHONG anh huong. Danh dau KILLED oan mot dot nap dang chay thi vua bao
      sai cho nguoi dung, vua lam pre-flight `khong_co_job_khac` cho phep
      mot dot nap THU HAI khoi dong. Da xay ra that trong phien xay dung.

    Nguy hiem hon ca: job trong tien trinh chet giua luc quiesce se de lai
    Extract/Replicat STOPPED. Vi vay o day canh bao to.
    """
    from . import jobs as jobmod

    rows = [dict(r) for r in con.execute(
        "SELECT * FROM job WHERE state IN ('QUEUED','RUNNING')")]
    if not rows:
        return
    for r in rows:
        if jobmod.con_song(r):
            log.info("việc #%s (%s) chạy rời ra và tiến trình còn sống - giữ nguyên trạng "
                     "thái đang chạy", r["id"], r["kind"])
            continue
        con.execute(
            "UPDATE job SET state='KILLED', finished_at=?, err_msg=? WHERE id=?",
            (int(time.time()),
             "công cụ khởi động lại và không thấy tiến trình việc nào còn chạy. Nếu "
             "đó là việc chạy trong tiến trình web (đối chiếu) thì phần dọn dẹp "
             "KHÔNG kịp chạy - kiểm `info all`, có thể còn tiến trình đang dừng.", r["id"]))
        log.warning("việc #%s (%s) bị đánh dấu đã chết. KIỂM NGAY xem có tiến trình nào còn "
                    "dừng không.", r["id"], r["kind"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    _reconcile_jobs()
    try:
        await collector.tick()          # co du lieu ngay tu request dau tien
    except Exception:
        log.exception("lần đo đầu tiên thất bại")
    for p in collector.snap.procs:
        if not p.is_running:
            log.warning("tiến trình %s đang %s lúc công cụ khởi động", p.name, p.status)
    collector.start()
    log.info("ggadmin đã sẵn sàng tại http://%s:%s", cfg.host, cfg.port)
    log.info("kenh canh bao: %s", ", ".join(notifier.channel_names()))
    yield
    await collector.stop()
    ora.close()
    con.close()


app = FastAPI(title="ggadmin", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
tpl = Jinja2Templates(directory=str(ROOT / "web" / "templates"))


# ---------------------------------------------------------------- chot CSRF
# Cookie phien da dat SameSite=Strict nen trinh duyet doi nay khong gui kem
# cookie trong request POST tu site khac. Day la lop thu hai, re: neu co
# header Origin thi no PHAI trung host cua chinh request. Trinh duyet cu
# khong gui Origin cho form POST -> khong chan, de khong lam hong ai dang
# dung duoc; con fetch/htmx thi luon co Origin.
GHI = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def chot_origin(req: Request, goi_tiep):
    if req.method in GHI:
        origin = req.headers.get("origin")
        if origin:
            if urlsplit(origin).netloc != req.headers.get("host"):
                log.warning("chan %s %s: Origin %s khong khop host %s",
                            req.method, req.url.path, origin,
                            req.headers.get("host"))
                return HTMLResponse(
                    "<p class='bad'>Yêu cầu bị chặn: Origin không khớp.</p>",
                    status_code=403)
    return await goi_tiep(req)


# ------------------------------------------------------------------ tien ich
def so_vn(s: str) -> str:
    """Doi dinh dang so tu kieu Anh sang kieu Viet.

    `'{:,.1f}'.format(305.4)` cho `305.4`, va `32768.0` cho `32,768.0`:
    dau phay la hang nghin, dau cham la thap phan - nguoc voi chuan Viet.
    Bo quy uoc (design-system.md muc 8.4) yeu cau thap phan DAU PHAY va
    hang nghin DAU CHAM: `2,45%`, `2.480 ty`.

    Doi qua mot ky tu trung gian, khong doi truc tiep: `.replace(",", ".")`
    roi `.replace(".", ",")` se doi LAI tat ca ve dau phay.
    """
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_bytes(n: float | int | None) -> str:
    if n is None or n < 0:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return so_vn(f"{n:,.0f} {unit}" if unit == "B"
                         else f"{n:,.1f} {unit}")
        n /= 1024
    return "-"


def fmt_so(n, le: int = 1) -> str:
    """So thuc theo chuan Viet: `305,4` / `61,7`."""
    if n is None:
        return "-"
    try:
        return so_vn(f"{float(n):,.{le}f}")
    except (TypeError, ValueError):
        return str(n)


def fmt_hms(s: int | None) -> str:
    if s is None:
        return "-"
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_ts(t: int | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else "-"


def fmt_int(n) -> str:
    """So nguyen, hang nghin bang DAU CHAM theo chuan Viet. Oracle tra
    ROUND() ve float nen '{:,}'.format(32768.0) cho '32,768.0' - da gap
    that tren dashboard, nen phai .0f chu khong phai ','."""
    if n is None:
        return "-"
    try:
        return so_vn(f"{float(n):,.0f}")
    except (TypeError, ValueError):
        return str(n)


tpl.env.filters["songuyen"] = fmt_int
tpl.env.filters["so"] = fmt_so
tpl.env.filters["bytes"] = fmt_bytes
tpl.env.filters["hms"] = fmt_hms
tpl.env.filters["hhmm"] = fmt_ts


def _from_json(s):
    """Doc cot JSON trong template. JSON hong thi tra list rong, khong no:

    mot trang danh sach khong duoc chet vi mot dong du lieu xau.
    """
    try:
        return json.loads(s) if s else []
    except (TypeError, ValueError):
        return []


tpl.env.filters["from_json"] = _from_json


def peer_of(req: Request) -> str:
    # Qua SSH tunnel thi client luon la 127.0.0.1; giu SSH_CLIENT de doi chieu
    return f"{req.client.host if req.client else '?'}"


def current_user(req: Request):
    return auth.identify(req.cookies.get(COOKIE))


def ctx(req: Request, **kw):
    s = collector.snap
    base = {
        "request": req,
        "user": current_user(req),
        "s": s,
        "cfg": cfg,
        "mismatch": collector.param_mismatch(),
        "recon": s.recon,
        "kenh": notifier.channel_names(),
        "now": fmt_ts(int(time.time())),
    }
    base.update(kw)
    return base


# -------------------------------------------------------------------- routes
@app.get("/login", response_class=HTMLResponse)
async def login_form(req: Request, loi: str = ""):
    return tpl.TemplateResponse(req, "login.html", {"request": req, "loi": loi})


@app.post("/login")
async def login_post(req: Request, username: str = Form(...), password: str = Form(...)):
    token, err = auth.login(username.strip(), password, peer_of(req))
    if not token:
        return RedirectResponse(f"/login?loi={err}", status_code=303)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, token, max_age=MAX_AGE, httponly=True,
                 samesite="strict", path="/")
    return r


@app.get("/logout")
async def logout(req: Request):
    u = current_user(req)
    if u:
        auth.audit(u["username"], peer_of(req), "L0", "LOGOUT")
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(COOKIE, path="/")
    return r


@app.get("/", response_class=HTMLResponse)
async def dashboard(req: Request):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    return tpl.TemplateResponse(req, "dashboard.html", ctx(req))


@app.get("/fragment/dashboard", response_class=HTMLResponse)
async def fragment_dashboard(req: Request):
    """htmx hx-get moi 15s. Server render luon HTML - khong JSON, khong
    template phia client, khong code render trung lap."""
    if not current_user(req):
        return HTMLResponse('<p class="err">Mất phiên đăng nhập. <a href="/login">Đăng nhập '
                            "lại</a></p>")
    return tpl.TemplateResponse(req, "_dashboard_body.html", ctx(req))


@app.get("/logs", response_class=HTMLResponse)
async def logs(req: Request, chi_loi: int = 0, n: int = 120):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    auth.audit(u["username"], peer_of(req), "L0", "VIEW_LOG",
               target=f"ggserr.log chi_loi={chi_loi}")
    lines = osfs.ggserr_tail(cfg, lines=min(n, 1000), only_errors=bool(chi_loi))
    dsc = {r: osfs.discard_tail(cfg, r, 20) for r in cfg.discard_baseline}
    return tpl.TemplateResponse(req, "logs.html",
                                ctx(req, lines=lines, dsc=dsc, chi_loi=chi_loi))


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(req: Request, n: int = 200):
    u = current_user(req)
    if not u or not Auth.has_role(u, "ADMIN"):
        return HTMLResponse("<p>Cần quyền quản trị</p>", status_code=403)
    rows = [dict(r) for r in con.execute(
        "SELECT ts,username,peer,level,action,target,reason FROM audit_log"
        " ORDER BY id DESC LIMIT ?", (min(n, 2000),))]
    ok, cnt, msg = auth.verify_chain()
    return tpl.TemplateResponse(req, "audit.html",
                                ctx(req, rows=rows, chain_ok=ok, chain_msg=msg))


@app.get("/healthz", response_class=Response)
async def healthz():
    s = collector.snap
    running = sum(1 for p in s.procs if p.is_running)
    body = (f"ts={s.ts} age_s={s.age_s} procs={len(s.procs)} running={running} "
            f"alerts={len(s.alerts)} errors={len(s.errors)} "
            f"poll_ms={s.duration_ms} read_only={int(s.read_only)}\n")
    code = 200 if (s.ts and s.age_s < cfg.poll_seconds * 3 and not s.errors) else 503
    return Response(body, status_code=code, media_type="text/plain")

@app.post("/notify/test")
async def notify_test(req: Request):
    """Gui mot tin thu qua MOI kenh. Chi ADMIN.

    Day la hanh dong duy nhat trong Phase 1 co tac dong ra ngoai server, va
    no khong doi gi tren he GoldenGate nen van dung tinh than "Phase 1 chi doc".
    """
    u = current_user(req)
    if not u or not Auth.has_role(u, "ADMIN"):
        return HTMLResponse("<p>Cần quyền quản trị</p>", status_code=403)
    form = await req.form()
    message = str(form.get("message", "")).strip()
    if len(message) > MAX_TEST_MSG:
        return HTMLResponse(
            f"<p class='bad'>Nội dung tin thử tối đa {MAX_TEST_MSG:,} ký tự.</p>"
            .replace(",", "."))
    res = notifier.send_test(u["username"], message)
    # Audit phai giu LAI noi dung: day la hanh dong gui ra ngoai voi chu tu
    # go, ghi moi ten kenh va ket qua thi sau nay khong tra duoc "ai da gui
    # cai gi vao group".
    if message:
        noi_dung = f"{len(message)} ky tu: " + " | ".join(message.splitlines())[:200]
    else:
        noi_dung = "(tin thu mac dinh)"
    auth.audit(u["username"], peer_of(req), "L1", "NOTIFY_TEST",
               target=",".join(n for n, _, _ in res),
               reason="; ".join(f"{n}={'OK' if ok else d}" for n, ok, d in res),
               payload_after=noi_dung)
    out = ["<table role='grid'><tbody>"]
    for n, ok, d in res:
        cls = "ok" if ok else "bad"
        txt = "OK" if ok else "LOI"
        out.append(f"<tr><td><code>{escape(n)}</code></td>"
                   f"<td class='{cls}'>{txt}</td><td><small>{escape(d)}</small></td></tr>")
    out.append("</tbody></table>")
    return HTMLResponse("".join(out))


@app.get("/notify", response_class=HTMLResponse)
async def notify_page(req: Request):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    rows = [dict(r) for r in con.execute(
        "SELECT ts,channel,kind,rule_name,scope_key,ok,detail FROM notify_log"
        " ORDER BY id DESC LIMIT 100")]
    return tpl.TemplateResponse(req, "notify.html", ctx(req, rows=rows))

# ===================================================================
# Registry 73 bang + doi chieu 3 nguon su that
# ===================================================================
@app.get("/tables", response_class=HTMLResponse)
async def tables_page(req: Request, stream: str = ""):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    rows = registry.listing(con, stream or None)
    return tpl.TemplateResponse(req, "tables.html",
                                ctx(req, rows=rows, stream=stream))


# ===================================================================
# Lich su + do thi
# ===================================================================
@app.get("/history", response_class=HTMLResponse)
async def history_page(req: Request, kind: str = "lag", hours: int = 24):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    return tpl.TemplateResponse(req, "history.html", ctx(
        req, kind=kind, hours=hours, series=history.SERIES,
        hstats=history.stats(con)))


@app.get("/api/series/{kind}")
async def api_series(req: Request, kind: str, hours: int = 24):
    if not current_user(req):
        return JSONResponse({"loi": "chưa đăng nhập"}, status_code=401)
    try:
        return JSONResponse(history.series(con, kind, max(1, min(hours, 24 * 400))))
    except ValueError as e:
        return JSONResponse({"loi": str(e)}, status_code=400)


# ===================================================================
# Verify
# ===================================================================
@app.get("/verify", response_class=HTMLResponse)
async def verify_page(req: Request, job: int = 0):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    jb = verifier.job_state(job) if job else verifier.running_job()
    rows = verifier.last_batch(jb["id"] if jb else None)
    return tpl.TemplateResponse(req, "verify.html", ctx(
        req, job=jb, rows=rows, busy=verifier.is_busy()))


@app.post("/verify/run")
async def verify_run(req: Request, stream: str = Form(...),
                     mode: str = Form("LIVE")):
    """Phase 1 chi co che do LIVE - no KHONG dung process nao.

    SNAPSHOT_SCN phai stop extract + cho drain + stop replicat, tuc la mot
    thao tac GHI; de Phase 2 cung voi khung L1/L2.
    """
    u = current_user(req)
    if not u:
        return HTMLResponse("<p>Chưa đăng nhập</p>", status_code=401)
    if mode != "LIVE":
        return HTMLResponse(
            "<p class='bad'>Đường này chỉ chạy chế độ nhanh. Chế độ chuẩn phải dừng "
            "Extract và Replicat nên đi qua khung xác nhận riêng.</p>",
            status_code=400)
    if stream not in cfg.streams:
        return HTMLResponse("<p class='bad'>Luồng không hợp lệ</p>", status_code=400)
    tables = [r["src_table"] for r in registry.listing(con, stream)
              if r["has_map"] and r["in_extract"]]
    if not tables:
        return HTMLResponse("<p class='bad'>Không có bảng nào đủ điều kiện</p>",
                            status_code=400)
    try:
        job_id = await verifier.run_batch(tables, mode=mode,
                                          who=u["username"], stream=stream)
    except Busy as e:
        return HTMLResponse(f"<p class='bad'>{e}</p>", status_code=409)
    auth.audit(u["username"], peer_of(req), "L0", "VERIFY_RUN",
               target=f"{stream} ({len(tables)} bảng)", reason=f"mode={mode}")
    return RedirectResponse(f"/verify?job={job_id}", status_code=303)


@app.get("/verify/job/{job_id}", response_class=HTMLResponse)
async def verify_job_fragment(req: Request, job_id: int):
    if not current_user(req):
        return HTMLResponse("")
    jb = verifier.job_state(job_id)
    rows = verifier.last_batch(job_id)
    return tpl.TemplateResponse(req, "_verify_body.html",
                                ctx(req, job=jb, rows=rows,
                                    busy=verifier.is_busy()))


# ===================================================================
# Stream log bang SSE
# ===================================================================
@app.get("/logs/stream")
async def logs_stream(req: Request, chi_loi: int = 0):
    """SSE doc duoi ggserr.log.

    SSE dung hon WebSocket o day: mot chieu, tu reconnect, va di qua SSH
    tunnel khong van de gi.
    """
    if not current_user(req):
        return Response("chưa đăng nhập", status_code=401)

    async def gen():
        import asyncio as _a
        last: list[str] = []
        while True:
            if await req.is_disconnected():
                break
            lines = osfs.ggserr_tail(cfg, lines=40, only_errors=bool(chi_loi))
            moi = [l for l in lines if l not in last]
            for l in moi:
                yield f"data: {l}\n\n"
            last = lines
            await _a.sleep(5)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ===================================================================
# PHASE 2 - DUONG GHI DUY NHAT
#
#   POST /action/arm   -> do pre-flight, nap dan, tra fragment xac nhan
#   POST /action/fire  -> ban co (token 60s, L2 phai go tay ten)
#
# Them mot thao tac ghi moi = them mot dong vao OPS. Khong them endpoint.
# ===================================================================

# op -> (muc, ten pre-flight, nhan hien thi)
OPS: dict[str, tuple[str, str, str]] = {
    "START":             (L1, "START",             "Chạy tiến trình"),
    "STOP":              (L1, "STOP",              "Dừng tiến trình"),
    "ADD_TRANDATA":      (L1, "ADD_TRANDATA",      "Bật TRANDATA cho bảng ở nguồn"),
    "PARAM_SAVE":        (L1, "PARAM_EDIT",        "Ghi file tham số"),
    "VERIFY_SNAPSHOT":   (L2, "VERIFY_SNAPSHOT",   "Đối chiếu chuẩn (dừng tạm cả luồng)"),
    "RESTART_ADD_TABLE": (L2, "RESTART_ADD_TABLE", "Chạy lại Extract sau khi thêm bảng"),
    # Muc o day chi de hien nhan. Muc THAT cua tung buoc wizard duoc luu
    # trong Pending luc arm, va `fire` dung p.level chu khong dung bang nay.
    "WIZARD_STEP":       (L2, "WIZARD",            "Bước trong quy trình"),
    "WIZARD_ROLLBACK":   (L2, "KHONG_CO",          "Dọn sạch phiên tạo luồng mới"),
    "REGISTRY_ADD":      (L1, "KHONG_CO",          "Đăng ký bảng mới vào danh sách quản lý"),
    # Thao tac nhanh: MOT nghi thuc L2 cho CA LUOT thay vi tung buoc.
    # Xem app/auto.py de biet cai gi duoc danh doi va cai gi thi khong.
    "AUTO_ADD_TABLE":    (L2, "KHONG_CO",          "Thao tác nhanh: thêm bảng vào luồng"),
    "AUTO_RESYNC":       (L2, "KHONG_CO",          "Thao tác nhanh: đồng bộ lại bảng"),
    # Khoi phuc an toan: CHI start/stop, nen hoan tac duoc -> L1.
    # Khong phai L2: khi dang co su co, bat go tay ten bang la dat them ma
    # sat vao dung luc can nhanh nhat, ma khong doi lay them an toan nao -
    # moi thao tac no chay deu dao nguoc duoc trong mot cu bam.
    "KHOI_PHUC":         (L1, "KHOI_PHUC",         "Khôi phục an toàn một luồng"),
}


def _deny_html(e: Exception, code: int = 403) -> HTMLResponse:
    return HTMLResponse(
        '<article class="xacnhan"><strong class="bad">TỪ CHỐI</strong>'
        f"<p>{e}</p></article>", status_code=code)


def _pf_cua(op: str, target: str) -> tuple[str, str]:
    """Pre-flight nao ap cho op nay, tren doi tuong nao."""
    if op in ("AUTO_ADD_TABLE", "AUTO_RESYNC"):
        # Muc kiem rieng, KHONG dung "KHONG_CO": nhan cua KHONG_CO la "thao
        # tac chi doc", hien dong do truoc mot luot co the xoa sach bang la
        # gay nham theo huong nguy hiem nhat.
        return "AUTO", target
    if op == "KHOI_PHUC":
        return "KHOI_PHUC", target
    if op == "WIZARD_ROLLBACK":
        # Rollback khong co pre-flight rieng: dieu kien that la phien phai
        # dang mo va phai go tay ten Extract. Van giu 3 check chung.
        return "KHONG_CO", target
    if op == "WIZARD_STEP":
        rid, no = (int(x) for x in target.split(":", 1))
        pf_op, pf_target = wizard.pf_cua_buoc(rid, no)
        return (pf_op or "KHONG_CO"), pf_target
    if op == "RESTART_ADD_TABLE":
        st = cfg.streams.get(target) or {}
        return "START", st.get("extract", target)
    return OPS[op][1], target


@app.post("/action/arm", response_class=HTMLResponse)
async def action_arm(req: Request, op: str = Form(...), target: str = Form(""),
                     reason: str = Form(""), payload: str = Form(""),
                     expect_sha: str = Form(""), stream_sel: str = Form("")):
    u = current_user(req)
    if not u:
        return HTMLResponse("<p>Chưa đăng nhập</p>", status_code=401)
    if op not in OPS:
        return _deny_html(ValueError(f"thao tác không hợp lệ: {op!r}"), 400)
    level, _, nhan = OPS[op]

    # Pre-flight do NGAY tai day. Ket qua vao Pending va se duoc do LAI o
    # /action/fire - token song 60 giay chinh la de ep dieu nay.
    pf_op, pf_target = _pf_cua(op, target)
    pf = await pre.run(pf_op, pf_target)

    extra: dict = {}
    dif = ""
    if op == "REGISTRY_ADD":
        # Luong di qua `payload` (o select dat vao hidden) hoac `stream_sel`.
        extra = {"stream": (payload or stream_sel or "").strip()}
        dif = ""
    if op == "KHOI_PHUC":
        # Ban xem truoc la CHINH ban chan doan: nguoi dung thay ro se chay
        # lenh nao, theo thu tu nao, va vi sao - truoc khi bam.
        try:
            k = await khoiphuc.kham(cfg, con, (target or "").strip().upper())
        except khoiphuc.KhoiPhucError as e:
            return _deny_html(e, 400)
        if not k["the_chay"]:
            # KHONG nap dan khi khong co viec gi can lam. Hien mot nut
            # "Chay" ma bam vao khong lam gi la noi doi voi nguoi dung -
            # va trong luc su co thi mot nut vo nghia lam mat thoi gian
            # dung luc it co thoi gian nhat.
            return tpl.TemplateResponse(req, "_tin.html", ctx(
                req, tieu_de="Không có việc gì cần làm cho luồng %s"
                             % k["stream"],
                noi_dung=khoiphuc.xem_truoc(k), ghi_chu=k["ket_luan"]))
        dif = khoiphuc.xem_truoc(k)
        extra = {"kham_luc_arm": {"n_tab": k["n_tab"], "n_map": k["n_map"],
                                  "so_lenh": len(k["ke_hoach"])}}
    if op in ("AUTO_ADD_TABLE", "AUTO_RESYNC"):
        # Kiem DIEU KIEN NGAY LUC ARM, truoc khi nguoi dung go ten bang: bi
        # tu choi thi phai biet ly do NGAY, khong phai sau khi da mo mot
        # phien roi no chet o buoc 1.
        kind = "ADD_TABLE" if op == "AUTO_ADD_TABLE" else "RESYNC_TABLE"
        try:
            auto.kiem_dieu_kien(con, kind, (target or "").strip().upper())
        except auto.AutoError as e:
            return _deny_html(e, 400)
        # Go tay TEN BANG - nghi thuc L2 DUY NHAT cua ca luot.
        extra = {"go_tay": (target or "").strip().upper(),
                 "stream": (payload or stream_sel or "").strip()}
        dif = ""
    if op == "WIZARD_ROLLBACK":
        try:
            run = wizard.lay(int(target))
        except (WizardError, ValueError) as e:
            return _deny_html(e, 400)
        if run["kind"] != "NEW_STREAM":
            return _deny_html(ValueError("rollback nay chi cho NEW_STREAM"), 400)
        # Go tay TEN EXTRACT: do moi la thu nguoi dung can doc lai mot lan
        # nua truoc khi UNREGISTER mot capture o DB nguon production.
        extra = {"go_tay": run["created_extract"] or f"#{target}"}
        dif = ""
    if op == "PARAM_SAVE":
        try:
            cur = paramsmod.read(cfg, target)
        except ParamError as e:
            return _deny_html(e, 400)
        body = payload.replace("\r\n", "\n")
        if not body.endswith("\n"):
            body += "\n"
        if body == cur.text:
            return _deny_html(ValueError("Nội dung không thay đổi"), 400)
        if loi := paramsmod.guard(cur.text, body, process=target):
            return _deny_html(ValueError("Khung an toàn từ chối: " + " | ".join(loi)), 400)
        dif = paramsmod.diff(cur.text, body, name=cur.path.name)
        extra = {"text": body, "expect_sha": expect_sha or cur.sha256,
                 "stats": paramsmod.diff_stats(cur.text, body)}

    try:
        p = guard.arm(u, action=op, target=target, level=level, reason=reason,
                      preflight_ok=pf.ok, preflight_note=pf.note,
                      peer=peer_of(req), extra=extra)
    except Denied as e:
        return _deny_html(e)

    return tpl.TemplateResponse(req, "_confirm.html", ctx(
        req, p=p, pf=pf, nhan=nhan, dif=dif, op=op))


@app.post("/action/fire", response_class=HTMLResponse)
async def action_fire(req: Request, token: str = Form(...),
                      typed: str = Form("")):
    u = current_user(req)
    if not u:
        return HTMLResponse("<p>Chưa đăng nhập</p>", status_code=401)
    p0 = guard.pending_of(token)
    if p0 is None:
        return _deny_html(ValueError(
            "Lần xác nhận không hợp lệ hoặc đã hết hạn 60 giây. Làm lại từ đầu để "
            "phần kiểm tra trước được đo lại."))

    # Do LAI pre-flight ngay truoc khi chay. Day la diem khac biet that so voi
    # mot modal xac nhan thong thuong: trang thai he thong co the da doi trong
    # 60 giay vua roi.
    level, _, nhan = OPS[p0.action]
    pf_op, pf_target = _pf_cua(p0.action, p0.target)
    pf_now = await pre.run(pf_op, pf_target)

    try:
        p = guard.fire(token, u, typed=typed, preflight_now=pf_now.ok,
                       peer=peer_of(req))
    except Denied as e:
        return _deny_html(e)

    t0 = time.monotonic()
    try:
        res = await _thuc_thi(p, u["username"])
        ok, msg = True, res.get("msg", "xong")
        chi_tiet = res.get("chi_tiet", [])
    except (ControlError, ParamError, WizardError, Busy, Denied, gg.Blocked,
            ValueError) as e:
        ok, msg, chi_tiet = False, str(e), list(getattr(e, "lines", []) or [])
        log.warning("thao tác %s trên %s thất bại: %s", p.action, p.target, e)
    except Exception as e:                                  # noqa: BLE001
        ok, msg, chi_tiet = False, f"lỗi không lường trước: {e}", []
        log.exception("thao tác %s trên %s lỗi", p.action, p.target)

    auth.audit(p.user, peer_of(req), p.level, p.action, target=p.target,
               reason=p.reason, exit_code=0 if ok else 1,
               stdout_tail="\n".join(str(x) for x in chi_tiet[-40:]),
               duration_ms=int((time.monotonic() - t0) * 1000))

    # BUOC QUY TRINH DAT -> TU TAI LAI TRANG QUY TRINH.
    #
    # Khung ket qua la fragment htmx nhet vao `#khung`, nen the "Buoc hien
    # tai" phia tren GIU NGUYEN buoc vua chay. Khong tu tai lai thi nguoi
    # dung phai F5 sau MOI buoc - 23 buoc la 23 lan F5, va de tuong la bi
    # treo. `HX-Redirect` lam htmx dieu huong ca trang.
    #
    # Khong mat thong tin: ket qua tung buoc duoc luu vao
    # `wizard_step.output`, van doc lai duoc o phan "ket qua chi tiet" cua
    # dong buoc do tren chinh trang quy trinh.
    #
    # CHI khi DAT. Buoc LOI thi giu khung ket qua lai de nguoi dung doc duoc
    # loi - tu dong nhay trang luc do la cuop mat thong tin can nhat.
    if ok and p.action == "WIZARD_STEP" and ":" in (p.target or ""):
        rid = p.target.split(":", 1)[0]
        return HTMLResponse("", headers={"HX-Redirect": f"/wizard/{rid}"})
    return tpl.TemplateResponse(req, "_result.html", ctx(
        req, ok=ok, msg=msg, chi_tiet=chi_tiet, nhan=nhan, p=p,
        luong_loi="" if ok else _luong_cua(p)))


def _luong_cua(p) -> str:
    """Thao tac nay anh huong luong nao - de hien nut khoi phuc khi loi.

    Tra "" khi khong suy ra duoc. Tha khong hien nut con hon hien nut tro
    vao SAI LUONG: bam vao do la dung/chay mot luong dang khoe manh.
    """
    t = (p.target or "").strip()
    try:
        if p.action == "KHOI_PHUC":
            return t.upper() if t.upper() in cfg.streams else ""
        if p.action in ("WIZARD_STEP", "WIZARD_ROLLBACK"):
            rid = t.split(":", 1)[0]
            r = con.execute("SELECT stream FROM wizard_run WHERE id=?",
                            (int(rid),)).fetchone()
            return (r["stream"] or "") if r else ""
        if p.action in ("AUTO_ADD_TABLE", "AUTO_RESYNC", "ADD_TRANDATA"):
            r = con.execute(
                "SELECT stream FROM managed_table WHERE src_table=?",
                (t.upper(),)).fetchone()
            return (r["stream"] or "") if r else ""
        if p.action in ("VERIFY_SNAPSHOT", "RESTART_ADD_TABLE"):
            return t.upper() if t.upper() in cfg.streams else ""
        if p.action in ("START", "STOP", "PARAM_SAVE"):
            # Target la TEN TIEN TRINH hoac ten file param -> tra ve luong
            # chua tien trinh do.
            ten = t.upper().replace(".PRM", "")
            for luong, st in cfg.streams.items():
                if ten in (st["extract"].upper(), st["replicat"].upper()):
                    return luong
            return ""
    except (ValueError, KeyError, TypeError):
        return ""
    return ""


async def _thuc_thi(p, who: str) -> dict:
    """Dispatch. MOI nhanh o day da di qua toan bo khung an toan."""
    op, t = p.action, p.target

    if op in ("START", "STOP"):
        if t not in gg.PROCESSES:
            raise ValueError(f"Process ngoai enum: {t!r}")
        r = await (control.start(t) if op == "START" else control.stop(t))
        return {"msg": f"{op} {t}: {'đạt' if r['ok'] else 'KHÔNG đạt'}, trạng thái "
                       f"{r['status']} ({r['duration_ms']} ms)",
                "chi_tiet": r["lines"]}

    if op == "ADD_TRANDATA":
        row = con.execute(
            "SELECT src_owner, src_table FROM managed_table WHERE src_table=?",
            (t,)).fetchone()
        if row is None:
            raise ValueError(f"{t} không nằm trong danh sách quản lý -> từ chối")
        ok, lines = await gg.add_trandata(cfg, row["src_owner"], row["src_table"])
        if ok:
            con.execute("UPDATE managed_table SET prepared=1, updated_at=?"
                        " WHERE src_table=?", (int(time.time()), t))
        return {"msg": f"ADD TRANDATA {row['src_owner']}.{t}: "
                       f"{'dat' if ok else "KHÔNG xác nhận được"}",
                "chi_tiet": lines}

    if op == "PARAM_SAVE":
        r = await paramsmod.save(cfg, con, t, p.extra["text"], who=who,
                                 reason=p.reason,
                                 expect_sha=p.extra.get("expect_sha"))
        st = r["stats"]
        return {"msg": f"Đã ghi {t}.prm (thêm {st['them']}/bỏ {st['xoa']} dòng, TABLE "
                       f"{st['table_truoc']}->{st['table_sau']}, MAP "
                       f"{st['map_truoc']}->{st['map_sau']}), kiểm cú pháp đạt. Bản lưu: "
                       f"{r['backup'].rsplit('/', 1)[-1]}",
                "chi_tiet": r["checkprm_lines"]}

    if op == "VERIFY_SNAPSHOT":
        tables = [r["src_table"] for r in registry.listing(con, t)
                  if r["has_map"] and r["in_extract"]]
        if not tables:
            raise ValueError("Không có bảng nào đủ điều kiện")
        job_id = await verifier.run_batch(tables, mode="SNAPSHOT_SCN",
                                          who=who, stream=t)
        return {"msg": f"Đã tạo việc #{job_id}: dừng tạm luồng {t} rồi đối chiếu {len(tables)} "
                       "bảng ở mốc High Watermark. Luồng sẽ được chạy lại tự động khi xong - "
                       "KỂ CẢ khi đối chiếu lỗi.",
                "chi_tiet": [f"theo doi tai /verify?job={job_id}"]}

    if op == "RESTART_ADD_TABLE":
        r = await control.restart_extract_sau_khi_them_bang(t)
        return {"msg": f"Đã chạy lại luồng {t} đúng thứ tự (dừng Replicat -> chạy lại Extract "
                       "-> chạy Replicat)",
                "chi_tiet": [f"{b['buoc']}: {'đạt' if b['ok'] else 'KHÔNG đạt'} ({b['chi_tiet']})" for b in r["buoc"]]
                            + [r["ghi_chu"]]}

    if op == "WIZARD_ROLLBACK":
        r = await wizard.rollback_new_stream(int(t), who, p.reason)
        return {"msg": r["msg"], "chi_tiet": r["output"]}

    if op == "KHOI_PHUC":
        r = await khoiphuc.chay(cfg, con, control, t.strip().upper())
        ct = ["%s: %s (%s)" % (b["lenh"], "đạt" if b["ok"] else "KHÔNG đạt",
                               b["chi_tiet"]) for b in r["da_chay"]]
        if not r["da_chay"]:
            ct = ["Không có lệnh nào cần chạy."]
        for c in (r.get("sau") or r["kham"])["canh_bao"]:
            ct.append("CẢNH BÁO: " + c)
        if not r["ok"]:
            # `ControlError` la dung loai: day la mot chuoi start/stop that
            # bai. Dinh `lines` de khung ket qua hien duoc cac lenh DA chay
            # truoc khi dung - nguoi dung can biet he dang o trang thai nao.
            err = ControlError(r["msg"])
            err.lines = ct
            raise err
        return {"msg": r["msg"], "chi_tiet": ct}

    if op in ("AUTO_ADD_TABLE", "AUTO_RESYNC"):
        # Mo phien roi THA cho mot task NEN chay het. Khong chay trong
        # request: mot luot co the mat hang chuc phut (buoc nap, buoc cho
        # Extract bat kip, buoc cho Replicat ap het phan chong lan), con
        # request HTTP thi khong the giu lau nhu vay.
        import asyncio as _a
        kind = "ADD_TABLE" if op == "AUTO_ADD_TABLE" else "RESYNC_TABLE"
        rid = tudong.mo_phien(kind, t.strip().upper(),
                              stream=p.extra.get("stream") or "", who=who)
        _a.create_task(tudong.chay(rid, who=who, ly_do=p.reason))
        return {"msg": "Đã mở quy trình #%s và bắt đầu chạy tự động. Mỗi bước "
                       "vẫn phải đạt mới sang bước sau; bước nào không đạt thì "
                       "dừng ngay tại đó. Theo dõi ở trang quy trình." % rid,
                "chi_tiet": [
                    "Mọi chốt kỹ thuật vẫn chạy: điều kiện vào của từng bước,",
                    "kiểm tra trước khi chạy, và kiểm chứng lại trên DB sau mỗi",
                    "việc chạy nền.",
                    "",
                    "Cái được đánh đổi: không còn phải gõ tay tên bảng trước",
                    "TỪNG bước mức L2 - gộp thành một lần xác nhận cho cả lượt.",
                    "Mỗi bước vẫn ghi một dòng vết thao tác AUTO_STEP.",
                    "",
                    "/wizard/%s" % rid]}

    if op == "REGISTRY_ADD":
        # Registry VUA la danh muc doi chieu, VUA la allowlist lop 5 chong SQL
        # injection. Vi the them mot bang vao day la mot THAO TAC CO AUDIT,
        # khong phai mot tac dung phu cua viec mo wizard.
        #
        # `registry.sync()` khong xoa dong nao, nhung no danh dau cac dong
        # khong con trong nguon nao - va dieu kien cua no la `note IS NULL`.
        # Vi vay dong dang ky moi PHAI co `note`, neu khong lan sync ke tiep
        # se ghi de ghi chu cua no.
        from .adapters.ora import check_ident as _ci
        tb = _ci(t.strip().upper())
        st_ten = p.extra.get("stream") or ""
        if st_ten not in cfg.streams:
            raise ValueError(f"luồng không hợp lệ: {st_ten!r}")
        if con.execute("SELECT 1 FROM managed_table WHERE src_table=?",
                       (tb,)).fetchone():
            raise ValueError(f"{tb} đã có trong danh sách quản lý")
        n_src = await ora.scalar(
            "SELECT COUNT(*) FROM dba_tables@DBL_PMNV"
            " WHERE owner='KT_BSH' AND table_name=:t", t=tb)
        if not n_src:
            raise ValueError(
                f"KT_BSH.{tb} KHÔNG tồn tại ở nguồn. Danh sách quản lý chỉ nhận bảng có "
                "thật ở nguồn - đây là chốt chặn cuối chống SQL injection, không phải "
                "một ô nhập tự do.")
        n_tgt = await ora.scalar(
            "SELECT COUNT(*) FROM dba_tables WHERE owner='ODS' AND table_name=:t",
            t=tb)
        con.execute(
            "INSERT INTO managed_table(src_owner,src_table,tgt_owner,tgt_table,"
            "stream,in_extract,has_map,prepared,handlecollisions,note,updated_at)"
            " VALUES ('KT_BSH',?,'ODS',?,?,0,0,0,0,?,?)",
            (tb, tb, st_ten,
             f"mới đăng ký bởi {who} - chưa vào luồng nào",
             int(time.time())))
        return {"msg": f"Da dang ky KT_BSH.{tb} vao registry (luong {st_ten}). "
                       f"Bang dich o ODS: "
                       f"{'da co' if n_tgt else "CHƯA có - quy trình sẽ tạo ở bước 3"}",
                "chi_tiet": [
                    f"nguồn KT_BSH.{tb}: tồn tại",
                    f"dich ODS.{tb}: {'ton tai' if n_tgt else "chưa tồn tại"}",
                    "cả 3 cờ trong danh sách quản lý đặt 0 - bảng chưa thuộc luồng nào",
                    "giờ có thể mở quy trình thêm bảng cho bảng này"]}

    if op == "WIZARD_STEP":
        rid, no = (int(x) for x in t.split(":", 1))
        ts = dict(p.extra, who=who, reason=p.reason)
        r = await wizard.chay_buoc(rid, no, who=who, reason=p.reason, tham_so=ts)
        run = wizard.lay(rid)
        tong = max(x.no for x in wzmod.BO_BUOC[run["kind"]])
        tiep = (f" -> bước kế tiếp: {no + 1}/{tong}" if run["state"] == "OPEN"
                else " -> PHIÊN HOÀN TẤT")
        return {"msg": f"Bước {no}/{tong} {r.get('nhan', '')}: {r.get('msg', 'xong')}{tiep}",
                "chi_tiet": r.get("output") or []}

    raise ValueError(f"thao tác chưa có phần thực thi: {op!r}")


@app.get("/preflight/{op}", response_class=HTMLResponse)
async def preflight_fragment(req: Request, op: str, target: str = ""):
    if not current_user(req):
        return HTMLResponse("")
    r = await pre.run(op, target)
    return tpl.TemplateResponse(req, "_preflight.html", ctx(req, pf=r))


# ===================================================================
# Trang dieu khien process
# ===================================================================
@app.get("/control", response_class=HTMLResponse)
async def control_page(req: Request):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    rows = []
    for ten, st in cfg.streams.items():
        n_table = gg.count_param_entries(cfg, st["extract"], "TABLE")
        n_map = gg.count_param_entries(cfg, st["replicat"], "MAP")
        for vai in ("extract", "replicat"):
            name = st.get(vai)
            if not name:
                continue
            p = next((x for x in collector.snap.procs if x.name == name), None)
            rows.append({"luong": ten, "vai": vai, "name": name,
                         "status": p.status if p else "?",
                         "lag_s": p.lag_s if p else None,
                         "n_table": n_table, "n_map": n_map})
    return tpl.TemplateResponse(req, "control.html", ctx(req, rows=rows))


# ===================================================================
# Param editor
# ===================================================================
@app.get("/params", response_class=HTMLResponse)
async def params_page(req: Request, process: str = ""):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    danh_sach = sorted(p for p in gg.PROCESSES if p != "MGR")
    cur = None
    bak: list[dict] = []
    if process:
        if process not in gg.PROCESSES:
            return HTMLResponse("<p class='bad'>Process ngoai enum</p>",
                                status_code=400)
        try:
            cur = paramsmod.read(cfg, process)
            bak = paramsmod.backups(cfg, process)
        except ParamError as e:
            return HTMLResponse(f"<p class='bad'>{e}</p>", status_code=404)
        auth.audit(u["username"], peer_of(req), "L0", "VIEW_PARAM", target=process)
    p = next((x for x in collector.snap.procs if x.name == process), None)
    return tpl.TemplateResponse(req, "params.html", ctx(
        req, danh_sach=danh_sach, process=process, cur=cur, bak=bak,
        proc=p, lich_su=paramsmod.history(con, process or None, 20)))


# ===================================================================
# Job
# ===================================================================
@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(req: Request, n: int = 50):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM job ORDER BY id DESC LIMIT ?", (min(n, 500),))]
    return tpl.TemplateResponse(req, "jobs.html", ctx(req, rows=rows))


# ===================================================================
# PHASE 3 - WIZARD
#
# Buoc ghi cua wizard di qua DUNG khung an toan cua Phase 2:
# /wizard/{id}/arm -> /action/fire. Khong co khung an toan thu hai.
# ===================================================================
@app.get("/wizard", response_class=HTMLResponse)
async def wizard_list(req: Request):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    # Bang du dieu kien cho tung loai wizard
    them = [dict(r) for r in con.execute(
        "SELECT src_table, stream, keycols, prepared, in_extract, has_map"
        " FROM managed_table WHERE NOT (in_extract=1 AND has_map=1)"
        " ORDER BY src_table")]
    resync = [dict(r) for r in con.execute(
        "SELECT src_table, stream, keycols, handlecollisions, src_rows, tgt_rows"
        " FROM managed_table WHERE has_map=1 ORDER BY stream, src_table")]
    moi = [dict(r) for r in con.execute(
        "SELECT src_table, keycols FROM managed_table WHERE has_map=0"
        " ORDER BY src_table")]
    return tpl.TemplateResponse(req, "wizard.html", ctx(
        req, runs=wizard.danh_sach(), them=them, resync=resync, moi=moi,
        kinds=list(wzmod.BO_BUOC)))


@app.get("/nhanh", response_class=HTMLResponse)
async def trang_nhanh(req: Request):
    """Thao tac nhanh: go ten bang, cong cu chay het quy trinh.

    Danh sach bang o day chi de GOI Y khi go (datalist), khong phai de chon:
    nguoi dung co the go ten bang chua co trong goi y, va luc do `kiem_dieu_kien`
    se noi ro ly do tu choi.
    """
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    them = [dict(r) for r in con.execute(
        "SELECT src_table, stream FROM managed_table"
        " WHERE NOT (in_extract=1 AND has_map=1) AND src_table=tgt_table"
        " ORDER BY src_table")]
    resync = [dict(r) for r in con.execute(
        "SELECT src_table, stream FROM managed_table"
        " WHERE has_map=1 AND src_table=tgt_table ORDER BY stream, src_table")]
    # Khong truyen danh sach phien o day: khung theo doi song (`/nhanh/song`)
    # tu doc, va doc moi 3-10 giay chu khong phai mot lan luc tai trang.
    return tpl.TemplateResponse(req, "nhanh.html", ctx(
        req, them=them, resync=resync))


@app.get("/nhanh/song", response_class=HTMLResponse)
async def nhanh_song(req: Request):
    """Theo doi SONG: tung buoc dang chay cai gi, lenh nao, ket qua nao.

    htmx poll 3s. Gop thanh mot dong su kien theo THU TU THOI GIAN:
      - trang thai buoc (bat dau / xong / loi) kem gio
      - LENH se chay cua buoc do (`wizard_step.preview`, do bo lai luu lai
        truoc khi chay - duong bam tay hien no tren man hinh, che do nhanh
        thi khong ai doc nen phai luu)
      - ket qua buoc (`wizard_step.output`)
      - voi buoc cho viec nen: DUOI LOG cua viec do, doc truc tiep tu file
        nen thay duoc tien do that trong luc no dang chay

    Chi doc. Khong chay lenh nao, khong doi gi.
    """
    if not current_user(req):
        return HTMLResponse("")
    mo = [dict(r) for r in con.execute(
        "SELECT id, kind, state, stream, src_table, cur_step, note,"
        " created_at, created_by FROM wizard_run WHERE state='OPEN'"
        " ORDER BY id DESC LIMIT 3")]
    phien = []
    for run in mo:
        rid = run["id"]
        tong = max(x.no for x in wzmod.BO_BUOC[run["kind"]])
        buoc = [dict(r) for r in con.execute(
            "SELECT step_no, name, state, started_at, finished_at, preview,"
            " output, err_msg FROM wizard_step WHERE run_id=?"
            " AND state<>'CHO' ORDER BY step_no", (rid,))]
        nhan = {s.no: s.nhan for s in wzmod.BO_BUOC[run["kind"]]}
        for b in buoc:
            b["nhan"] = nhan.get(b["step_no"], b["name"])
            b["log"] = []
            # Buoc dang chay va cho viec nen -> lay duoi log SONG
            if b["state"] == "DANG":
                jid = wizard.job_cua_buoc(rid, b["step_no"])
                if jid:
                    from . import jobs as jobmod
                    b["job_id"] = jid
                    b["log"] = jobmod.duoi_log(cfg, jid, 18)
        phien.append({"run": run, "tong": tong, "buoc": buoc,
                      "dang_chay": rid in tudong.dang_chay})
    return tpl.TemplateResponse(req, "_nhanh_song.html", ctx(req, phien=phien))


@app.get("/huongdan", response_class=HTMLResponse)
async def huongdan(req: Request):
    """Huong dan su dung, dat NGAY TRONG app.

    De o file rieng hay o Obsidian thi nguoi dang xu ly su co luc 2 gio sang
    khong mo duoc. Trang nay khong doc DB, khong goi ggsci - no van hien
    duoc ca khi app dang o che do chi doc.
    """
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    return tpl.TemplateResponse(req, "huongdan.html", ctx(req))


@app.post("/wizard/new")
async def wizard_new(req: Request, kind: str = Form(...),
                     src_table: list[str] = Form(...), stream: str = Form("")):
    """Mo phien wizard.

    `src_table` la LIST vi form ADD_TABLE dung `<select multiple>`: chon
    nhieu bang thi browser gui nhieu gia tri CUNG TEN. Khai bao `str` o day
    thi FastAPI am tham chi lay MOT gia tri - nguoi dung chon 8 bang, UI bao
    da mo phien, va phien chi co 1 bang. Noi cac ten lai roi de
    `tach_ten_bang()` tach, nho vay o nhap tay nhieu ten cung chay duoc.
    """
    u = current_user(req)
    if not u:
        return HTMLResponse("<p>Chưa đăng nhập</p>", status_code=401)
    ten = ", ".join(x.strip().upper() for x in src_table if x.strip())
    try:
        guard.require(u, L1, peer=peer_of(req), action=f"WIZARD_NEW_{kind}")
        rid = wizard.tao(kind, stream=stream, src_table=ten, who=u["username"])
    except (Denied, WizardError) as e:
        return HTMLResponse(f"<p class='bad'><pre>{e}</pre></p>",
                            status_code=400)
    auth.audit(u["username"], peer_of(req), "L1", f"WIZARD_NEW",
               target=f"{kind} {ten} luồng {stream}",
               reason=f"mở phiên quy trình #{rid}")
    return RedirectResponse(f"/wizard/{rid}", status_code=303)


@app.get("/wizard/{rid}", response_class=HTMLResponse)
async def wizard_detail(req: Request, rid: int):
    u = current_user(req)
    if not u:
        return RedirectResponse("/login", status_code=303)
    try:
        run = wizard.lay(rid)
    except WizardError as e:
        return HTMLResponse(f"<p class='bad'>{e}</p>", status_code=404)
    buoc = wizard.cac_buoc(rid)
    cur = run["cur_step"]
    ok_vao, vi_sao = wizard.dieu_kien_vao(rid, cur)
    xt = await wizard.xem_truoc(rid, cur) if ok_vao else ""
    pf = None
    pf_op, pf_target = wizard.pf_cua_buoc(rid, cur)
    if ok_vao and pf_op:
        pf = await pre.run(pf_op, pf_target)
    return tpl.TemplateResponse(req, "wizard_run.html", ctx(
        req, run=run, buoc=buoc, cur=cur, ok_vao=ok_vao, vi_sao=vi_sao,
        xt=xt, pf=pf, dn=wzmod.buoc_cua(run["kind"], cur),
        # Danh sach bang cua phien + buoc nao chay theo tung bang. Trang phai
        # noi ro dieu nay: nguoi bam nut can biet buoc sap chay tac dong len
        # MOT bang hay len ca 8.
        ds_bang=wizard._cac_bang(run), moi_bang=wzmod.MOI_BANG))


@app.post("/wizard/{rid}/arm", response_class=HTMLResponse)
async def wizard_arm(req: Request, rid: int, reason: str = Form(""),
                     keycols: str = Form(""), extract: str = Form(""),
                     replicat: str = Form(""), trail_prefix: str = Form(""),
                     ckpt_table: str = Form("")):
    """Nap dan buoc HIEN TAI cua phien. Khong nhan so buoc tu client.

    Co y khong nhan `step_no` tu form: nguoi dung khong duoc chon buoc. Buoc
    la `cur_step` cua phien, va engine da chan nhay buoc.
    """
    u = current_user(req)
    if not u:
        return HTMLResponse("<p>Chưa đăng nhập</p>", status_code=401)
    try:
        run = wizard.lay(rid)
        no = run["cur_step"]
        s = wzmod.buoc_cua(run["kind"], no)
        ok_vao, vi_sao = wizard.dieu_kien_vao(rid, no)
        if not ok_vao:
            return _deny_html(ValueError(f"Chưa đủ điều kiện để chạy bước này: {vi_sao}"), 400)
        pf_op, pf_target = wizard.pf_cua_buoc(rid, no)
        pf = (await pre.run(pf_op, pf_target)) if pf_op else None
        pf_ok = pf.ok if pf else True
        pf_note = pf.note if pf else "bước này không có mục kiểm nào"
        extra = {"rid": rid, "no": no}
        if keycols.strip():
            extra["keycols"] = keycols.strip()
        for k, v in (("extract", extract), ("replicat", replicat),
                     ("trail_prefix", trail_prefix), ("ckpt_table", ckpt_table)):
            if v.strip():
                extra[k] = v.strip()
        p = guard.arm(u, action="WIZARD_STEP", target=f"{rid}:{no}",
                      level=s.level, reason=reason, preflight_ok=pf_ok,
                      preflight_note=pf_note, peer=peer_of(req), extra=extra)
    except (Denied, WizardError) as e:
        return _deny_html(e)

    dif = await wizard.xem_truoc(rid, no)
    # Go tay o muc L2: go dung TEN BANG, khong phai "rid:no"
    p.extra["go_tay"] = run["src_table"]
    return tpl.TemplateResponse(req, "_confirm.html", ctx(
        req, p=p, pf=pf or _pf_rong(), nhan=f"Bước {no}/{len(wzmod.BO_BUOC[run['kind']])} - {s.nhan}",
        dif=dif if dif.startswith(("---", "+++", "@@")) else "", op="WIZARD_STEP",
        xt_tho=dif, go_tay=run["src_table"]))


@app.post("/wizard/{rid}/abort")
async def wizard_abort(req: Request, rid: int, reason: str = Form("")):
    u = current_user(req)
    if not u:
        return HTMLResponse("<p>Chưa đăng nhập</p>", status_code=401)
    try:
        guard.require(u, L1, peer=peer_of(req), action="WIZARD_ABORT")
        r = guard.check_reason(reason)
    except Denied as e:
        return HTMLResponse(f"<p class='bad'>{e}</p>", status_code=403)
    wizard.bo_phien(rid, u["username"], r)
    auth.audit(u["username"], peer_of(req), "L1", "WIZARD_ABORT",
               target=str(rid), reason=r)
    return RedirectResponse("/wizard", status_code=303)


def _pf_rong():
    from .preflight import Result
    return Result("KHONG_CO", "", [])


# ===================================================================
# Theo doi job ROI RA cua mot buoc wizard
# ===================================================================
@app.get("/wizard/{rid}/job", response_class=HTMLResponse)
async def wizard_job(req: Request, rid: int):
    """htmx poll moi 10s khi buoc hien tai dang CHO mot job roi ra.

    `kiem_job()` chi cho buoc PASS sau khi da KIEM CHUNG TRANG THAI CUOI tren
    DB - ma thoat 0 cua sqlplus khong du de ket luan viec da xong dung.
    """
    if not current_user(req):
        return HTMLResponse("")
    try:
        run = wizard.lay(rid)
    except WizardError as e:
        return HTMLResponse(f"<p class='bad'>{e}</p>", status_code=404)
    no = run["cur_step"]
    try:
        r = await wizard.kiem_job(rid, no)
    except Exception as e:                                  # noqa: BLE001
        log.exception("kiểm việc chạy của quy trình #%s bước %s lỗi", rid, no)
        return HTMLResponse(f"<p class='bad'>Lỗi khi kiểm việc chạy: {e}</p>")
    # BUOC DAT -> TU TAI LAI CA TRANG, khong de nguoi dung phai F5.
    #
    # Vi sao can: khung nay la mot fragment htmx, no tu cap nhat moi 10s;
    # nhung the "Buoc hien tai" o phia tren thi KHONG, nen khi viec nen xong
    # thi trang hien dong chu "dang cho mot viec chay nen xong" trong khi
    # buoc do DA PASS va phien da sang buoc sau. Nguoi dung tuong bi treo.
    # Da xay ra that o buoc 15 quy trinh #4.
    #
    # `HX-Redirect` lam htmx dieu huong CA TRANG, nen the buoc hien tai,
    # bang cac buoc va khung xac nhan deu ve dung trang thai moi. Khong mat
    # thong tin gi: ket qua tung buoc duoc luu vao `wizard_step.output` va
    # van xem lai duoc trong phan "ket qua chi tiet" cua dong buoc do.
    #
    # CHI tu dieu huong khi DAT. Khi buoc LOI thi GIU NGUYEN khung nay -
    # nguoi dung phai doc duoc loi, tu dong nhay trang luc do la cuop mat
    # thong tin quan trong nhat.
    if r.get("state") == "PASS":
        return HTMLResponse("", headers={"HX-Redirect": f"/wizard/{rid}"})
    return tpl.TemplateResponse(req, "_wizard_job.html", ctx(
        req, r=r, rid=rid, no=no, run=run))


@app.get("/jobs/{job_id}/log", response_class=HTMLResponse)
async def job_log(req: Request, job_id: int, n: int = 80):
    """Duoi log cua mot job. DBA van `tail -f logs/job_<id>.log` duoc bang tay."""
    if not current_user(req):
        return HTMLResponse("", status_code=401)
    from . import jobs as jobmod
    j = jobmod.trang_thai(con, job_id)
    if j is None:
        return HTMLResponse("<p class='bad'>không có việc này</p>", status_code=404)
    lines = jobmod.duoi_log(cfg, job_id, min(n, 500))
    return HTMLResponse(
        "<article><header><strong>việc #%d</strong> <code>%s</code> &mdash; "
        "%s</header><pre class='diff'>%s</pre><small class='muted'>Chạy ngoài "
        "tiến trình web. Theo dõi bằng tay: <code>tail -f "
        "logs/job_%d.log</code></small></article>"
        % (job_id, j["kind"], j["state"],
           "\n".join(lines) or "(chưa có dòng nào)", job_id))
