"""KHOI PHUC AN TOAN - dua mot luong GG ve trang thai on dinh sau su co.

Dung khi mot buoc quy trinh bi loi va he dang o trang thai nua voi: Replicat
da dung, Extract co the da chay lai, dong TABLE da them ma dong MAP chua.
Luc do nguoi van hanh can mot nut lam dung viec, ngay, khong phai doc lai
runbook.

NGUYEN TAC XEP HANG - khong duoc doi:

    1. KHONG MAT DU LIEU   (uu tien tuyet doi)
    2. Luong chay lai duoc
    3. Do tre nho

Vi sao thu tu nay quan trong: muc tieu 1 va 3 XUNG DOT. Khi so dong TABLE
nhieu hon so dong MAP, cach giam do tre nhanh nhat la cho Replicat chay.
Nhung Replicat luc do BO QUA ban ghi cua bang khong co dong MAP, MA VAN DOI
CHECKPOINT - khong discard, khong log, khong dau vet. Do la cach mat du lieu
te nhat: khong ai biet no da xay ra. Nen trong truong hop do, viec "an toan"
la DUNG HAN ca hai, chap nhan do tre tang, cho den khi dong MAP duoc them.

Module nay chi dung hai thao tac co ban: `start` va `stop`. Khong sua param,
khong TRUNCATE, khong cham checkpoint. Nho the no luon HOAN TAC DUOC, va
khong bao gio la nguyen nhan cua mot su co moi.
"""

from __future__ import annotations

import logging

from .adapters import gg

log = logging.getLogger("ggadmin.khoiphuc")


class KhoiPhucError(Exception):
    pass


async def kham(cfg, con, stream: str) -> dict:
    """Kham mot luong - CHI DOC. Tra chan doan + ke hoach se chay.

    Khong chay bat cu thao tac ghi nao, nen goi bao nhieu lan cung duoc, va
    duoc dung lam ban xem truoc o khung xac nhan.
    """
    st = cfg.streams.get(stream)
    if st is None:
        raise KhoiPhucError("Không có luồng %r" % stream)
    et, rep = st["extract"], st["replicat"]

    n_tab = gg.count_param_entries(cfg, et, "TABLE")
    n_map = gg.count_param_entries(cfg, rep, "MAP")
    bat_bien_ok = n_tab <= n_map

    p_et = await gg.status_of(cfg, et)
    p_rep = await gg.status_of(cfg, rep)
    p_mgr = await gg.status_of(cfg, "MGR")
    et_chay = bool(p_et and p_et.is_running)
    rep_chay = bool(p_rep and p_rep.is_running)
    mgr_chay = bool(p_mgr and p_mgr.is_running)

    # Viec nen dang chay tren chinh luong nay. Chay lai Replicat trong luc
    # dang nap la dung vao bang dang bi INSERT APPEND giu khoa, ma
    # HANDLECOLLISIONS KHONG bao ve APPEND (cam bay da gap).
    nap = con.execute(
        "SELECT j.id AS jid, r.id AS rid, r.src_table FROM job j"
        " JOIN wizard_run r ON r.id = CAST("
        "   json_extract(j.params_json, '$.run_id') AS INTEGER)"
        " WHERE j.state IN ('QUEUED','RUNNING') AND r.stream = ?"
        " ORDER BY j.id DESC LIMIT 1", (stream,)).fetchone()
    dang_nap = dict(nap) if nap else None

    ke_hoach: list[dict] = []
    canh_bao: list[str] = []
    mat_du_lieu_dang_xay_ra = False

    if not bat_bien_ok:
        canh_bao.append(
            "%s có %d dòng TABLE mà %s chỉ có %d dòng MAP: %d bảng đang được "
            "bắt mà KHÔNG có đường đổ về đích."
            % (et, n_tab, rep, n_map, n_tab - n_map))

    # ---------------------------------------------- xep theo thu tu uu tien
    if not bat_bien_ok and rep_chay:
        # DANG MAT DU LIEU NGAY LUC NAY. Viec nay vuot moi quan tam khac, ke
        # ca viec nap dang chay: mat du lieu im lang KHONG hoan tac duoc,
        # con mot dot nap thi nap lai duoc.
        mat_du_lieu_dang_xay_ra = True
        ke_hoach.append({
            "lenh": "stop replicat %s" % rep,
            "vi_sao": "ĐANG MẤT DỮ LIỆU: %s chạy trong khi thiếu %d dòng "
                      "MAP. Mỗi bản ghi của bảng thiếu MAP bị bỏ qua mà mốc "
                      "checkpoint vẫn dời - không discard, không log. Dừng "
                      "ngay là việc cấp thiết nhất."
                      % (rep, n_tab - n_map)})
        if et_chay:
            ke_hoach.append({
                "lenh": "stop extract %s" % et,
                "vi_sao": "Dừng bắt thêm bản ghi của bảng chưa có MAP."})
        ket_luan = (
            "DỪNG cả hai. Sau đó thêm %d dòng MAP còn thiếu vào %s.prm "
            "(trang Tham số), rồi bấm lại nút này để chạy lại cả luồng. "
            "KHÔNG tự chạy lại Replicat trước khi có đủ MAP."
            % (n_tab - n_map, rep))
        the_chay = True

    elif dang_nap:
        ket_luan = (
            "Đang có việc nạp chạy (việc #%s, bảng %s). KHÔNG chạy lại "
            "GoldenGate lúc này: Replicat sẽ đụng vào bảng đang được nạp "
            "bằng INSERT APPEND, mà HANDLECOLLISIONS không bảo vệ APPEND - "
            "sẽ lỗi ORA-00001. Đợi việc nạp xong (xem trang Việc chạy nền), "
            "rồi bấm lại nút này."
            % (dang_nap["jid"], dang_nap["src_table"]))
        the_chay = False

    elif not bat_bien_ok:
        # Bat bien vo ma Replicat DA dung - chua mat gi. Giu nguyen trang
        # thai dung, va dung Extract de khong bat them.
        if et_chay:
            ke_hoach.append({
                "lenh": "stop extract %s" % et,
                "vi_sao": "Thiếu %d dòng MAP. Để %s chạy là tiếp tục dồn vào "
                          "trail những bản ghi mà Replicat sẽ bỏ qua khi "
                          "chạy lại." % (n_tab - n_map, et)})
            ket_luan = (
                "Dừng %s. %s đang dừng nên CHƯA mất gì - những bản ghi đã "
                "vào trail vẫn còn nguyên ở đó. Thêm %d dòng MAP vào %s.prm "
                "rồi bấm lại nút này: lúc đó Replicat chạy lên sẽ đổ đúng, "
                "không mất bản ghi nào." % (et, rep, n_tab - n_map, rep))
        else:
            ket_luan = (
                "Cả %s và %s đều đang dừng, nên CHƯA mất dữ liệu - những bản "
                "ghi đã vào trail vẫn còn nguyên. Nhưng thiếu %d dòng MAP "
                "nên KHÔNG được chạy lại. Thêm dòng MAP vào %s.prm (trang "
                "Tham số) trước, rồi bấm lại nút này."
                % (et, rep, n_tab - n_map, rep))
        the_chay = bool(ke_hoach)

    else:
        # Bat bien dat - chay lai duoc an toan.
        if not mgr_chay:
            ke_hoach.append({
                "lenh": "start manager",
                "vi_sao": "Manager phải chạy trước, nếu không Extract và "
                          "Replicat không khởi động được."})
        if not rep_chay:
            ke_hoach.append({
                "lenh": "start replicat %s" % rep,
                "vi_sao": "Replicat chạy TRƯỚC Extract. Ngược lại thì "
                          "Extract dồn thêm vào trail trong lúc chưa có ai "
                          "đổ ra, và nếu param vừa đổi thì đúng là tình "
                          "huống bỏ qua bản ghi."})
        if not et_chay:
            ke_hoach.append({
                "lenh": "start extract %s" % et,
                "vi_sao": "Bắt lại từ mốc checkpoint cũ - không bỏ sót "
                          "khoảng thời gian nó đã dừng."})
        if ke_hoach:
            ket_luan = ("Số dòng TABLE (%d) không vượt số dòng MAP (%d) nên "
                        "chạy lại an toàn: không bảng nào bị bắt mà thiếu "
                        "đường đổ về đích." % (n_tab, n_map))
            the_chay = True
        else:
            ket_luan = ("Luồng %s đã ổn định: %s và %s đều đang chạy, %d "
                        "dòng TABLE / %d dòng MAP. Không có gì cần làm."
                        % (stream, et, rep, n_tab, n_map))
            the_chay = False

    if n_map > n_tab:
        canh_bao.append(
            "%s có %d dòng MAP, nhiều hơn %d dòng TABLE của %s. Không nguy "
            "hiểm (Replicat chỉ không bao giờ nhận bản ghi cho các bảng đó), "
            "nhưng nên rà lại xem có phải bỏ sót dòng TABLE."
            % (rep, n_map, n_tab, et))

    return {
        "stream": stream, "et": et, "rep": rep,
        "n_tab": n_tab, "n_map": n_map, "bat_bien_ok": bat_bien_ok,
        "tt_et": p_et.status if p_et else "?",
        "tt_rep": p_rep.status if p_rep else "?",
        "tt_mgr": p_mgr.status if p_mgr else "?",
        "lag_et": p_et.lag_s if p_et else None,
        "lag_rep": p_rep.lag_s if p_rep else None,
        "et_chay": et_chay, "rep_chay": rep_chay, "mgr_chay": mgr_chay,
        "dang_nap": dang_nap,
        "mat_du_lieu_dang_xay_ra": mat_du_lieu_dang_xay_ra,
        "ke_hoach": ke_hoach, "canh_bao": canh_bao,
        "ket_luan": ket_luan, "the_chay": the_chay,
    }


def xem_truoc(k: dict) -> str:
    """Ban xem truoc dang van ban cho khung xac nhan."""
    d = ["Luồng %s   %s: %s   %s: %s   Manager: %s"
         % (k["stream"], k["et"], k["tt_et"], k["rep"], k["tt_rep"],
            k["tt_mgr"]),
         "Số dòng TABLE = %d   số dòng MAP = %d   %s"
         % (k["n_tab"], k["n_map"],
            "ĐẠT" if k["bat_bien_ok"] else "KHÔNG ĐẠT - thiếu MAP"),
         ""]
    for c in k["canh_bao"]:
        d.append("CẢNH BÁO: " + c)
    if k["canh_bao"]:
        d.append("")
    if k["ke_hoach"]:
        d.append("SẼ CHẠY, theo đúng thứ tự này:")
        for i, b in enumerate(k["ke_hoach"], 1):
            d.append("  %d. %s" % (i, b["lenh"]))
            d.append("     vì: %s" % b["vi_sao"])
    else:
        d.append("KHÔNG CHẠY LỆNH NÀO.")
    d += ["", "KẾT LUẬN: " + k["ket_luan"]]
    return "\n".join(d)


async def chay(cfg, con, control, stream: str) -> dict:
    """Chay ke hoach. DO LAI chan doan truoc khi chay, khong tin ban cu.

    Vi sao do lai: tu luc nguoi dung bam xac nhan den luc chay co the da vai
    chuc giay, va he nay dang co su co - trang thai doi duoc. Chay mot ke
    hoach dung tren so lieu cu la dung cach sinh ra su co moi.
    """
    k = await kham(cfg, con, stream)
    if not k["the_chay"]:
        return {"ok": True, "da_chay": [], "kham": k,
                "msg": "Không chạy lệnh nào. " + k["ket_luan"]}

    da_chay = []
    for b in k["ke_hoach"]:
        lenh = b["lenh"]
        try:
            if lenh.startswith("stop replicat"):
                r = await control.stop(k["rep"])
            elif lenh.startswith("stop extract"):
                r = await control.stop(k["et"])
            elif lenh.startswith("start replicat"):
                r = await control.start(k["rep"])
            elif lenh.startswith("start extract"):
                r = await control.start(k["et"])
            elif lenh.startswith("start manager"):
                r = await control.start("MGR")
            else:
                raise KhoiPhucError("lệnh lạ trong kế hoạch: %r" % lenh)
        except Exception as e:                                # noqa: BLE001
            da_chay.append({"lenh": lenh, "ok": False, "chi_tiet": str(e)})
            log.error("khoi phuc %s: %s LOI %s", stream, lenh, e)
            return {"ok": False, "da_chay": da_chay, "kham": k,
                    "msg": "Dừng tại %r: %s. Các lệnh sau KHÔNG chạy - chạy "
                           "tiếp khi chưa biết vì sao lệnh này lỗi là làm "
                           "tình hình xấu thêm." % (lenh, e)}
        da_chay.append({"lenh": lenh, "ok": bool(r.get("ok")),
                        "chi_tiet": r.get("status") or ""})
        if not r.get("ok"):
            log.error("khoi phuc %s: %s khong dat (%s)", stream, lenh,
                      r.get("status"))
            return {"ok": False, "da_chay": da_chay, "kham": k,
                    "msg": "Lệnh %r không đạt (đang %s). Các lệnh sau KHÔNG "
                           "chạy." % (lenh, r.get("status"))}

    sau = await kham(cfg, con, stream)
    return {"ok": True, "da_chay": da_chay, "kham": k, "sau": sau,
            "msg": "Đã chạy %d lệnh. %s" % (len(da_chay), sau["ket_luan"])}
