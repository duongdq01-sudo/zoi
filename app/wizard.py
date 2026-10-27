"""May trang thai wizard - Phase 3.

VI SAO STATE O SERVER, KHONG O CLIENT

Khong phai tien nghi ma la BAT BUOC dung nghiep vu: co buoc nap dai hang
chuc phut va buoc cho `Low WM` vuot `MAXSCN`. State o client se mat trang
khi tunnel dut hoac DBA F5. State o server thi hom sau mo lai wizard van o
buoc 8, va MOT DBA KHAC tiep duoc viec cua nguoi truoc.

MOI BUOC CO 4 PHAN CO DINH
    dieu kien vao  ->  xem truoc lenh  ->  chay  ->  kiem chung
Khong buoc nao chay khi buoc truoc chua PASS. Day la luat cua engine, khong
phai quy uoc cua tung buoc.

TAI SU DUNG KHUNG AN TOAN CUA PHASE 2
Buoc ghi cua wizard di qua DUNG `Guard` cua Phase 2 (`arm` -> `fire`, token
60 giay, go tay ten o muc L2, pre-flight do lai truoc khi chay, audit ghi
truoc). Khong co khung an toan thu hai: mot he thong co hai khung an toan
thi som muon se co mot khung thieu mot lop.

THU TU CUA ADD_TABLE LA HANG SO
`stop replicat` PHAI truoc `restart extract`. Dao lai la MAT DU LIEU IM
LANG: Extract nap param moi va ghi redo cua bang moi vao trail, Replicat cu
chua co dong MAP nen bo qua ban ghi VA VAN DOI CHECKPOINT - khong discard,
khong log, `info all` van RUNNING lag 0. Engine khong co duong dao thu tu:
thu tu nam trong danh sach buoc, va danh sach buoc la hang so trong code.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from . import cho
from . import params as paramsmod
from .adapters import gg
from .adapters.ora import SCHEMA_SOURCE, SCHEMA_TARGET, check_ident
from .safety import L0, L1, L2
from .wizard_index import IndexMixin

log = logging.getLogger("ggadmin.wizard")


class WizardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Step:
    no: int
    name: str
    nhan: str
    level: str
    pf: str | None = None          # ten pre-flight op, None = khong co
    ghi_chu: str = ""


# =====================================================================
# ADD_TABLE - ma hoa runbook 12 buoc da chay that qua 8 lo.
#
# Thu tu stop/start duoc TACH THANH TUNG BUOC thay vi mot thao tac goi.
# Ly do: moi buoc kiem chung duoc rieng, va bat buoc dung thu tu tro thanh
# thuoc tinh cua danh sach chu khong phai mot dong comment.
# =====================================================================
ADD_TABLE: tuple[Step, ...] = (
    Step(1, "DANG_KY_BANG", "Đăng ký bảng vào danh sách quản lý", L1,
         ghi_chu="Bảng chưa đăng ký thì công cụ tự đăng ký vào đúng luồng của phiên "
                 "này - không phải qua trang Quy trình trước nữa. Bảng vẫn phải có "
                 "THẬT ở nguồn KT_BSH. Bảng đã thuộc luồng KHÁC thì dừng."),
    Step(2, "KHAO_SAT", "Khảo sát bảng ở nguồn", L0,
         ghi_chu="Đếm số dòng, tìm khoá chính và index duy nhất, đếm cột cho phép "
                 "NULL, xem bảng có chia phân vùng hay không."),
    Step(3, "XAC_NHAN_KEYCOLS", "Xác nhận cột khoá (KEYCOLS)", L1,
         ghi_chu="Bảng có khoá chính thật thì để trống, công cụ lấy đúng các cột của "
                 "khoá chính đó. Bảng không có thì phải nhập tay. Dù cách nào, tính "
                 "duy nhất vẫn được kiểm TRÊN DỮ LIỆU THẬT, không tin vào cấu trúc "
                 "khai báo."),
    Step(4, "TAO_BANG_DICH", "Tạo bảng ở đích (nếu chưa có)", L2,
         ghi_chu="Sinh câu lệnh tạo bảng từ cấu trúc bảng nguồn: các cột, ràng buộc "
                 "NOT NULL, khoá chính, index và quyền cho tài khoản nạp. CÓ Ý không "
                 "mang theo giá trị mặc định, khoá ngoại và ràng buộc kiểm tra - một "
                 "khoá ngoại ở đích sẽ làm Replicat dừng lỗi khi thứ tự áp dữ liệu "
                 "khác thứ tự phát sinh ở nguồn. Bảng đã có thì bỏ qua."),
    Step(5, "KIEM_BANG_DICH", "Kiểm bảng đích: cấu trúc và quyền", L0,
         ghi_chu="Bảng đích phải tồn tại, bộ cột phải khớp nguồn, và phải đủ quyền: "
                 "tài khoản nạp cần quyền ghi, tài khoản đọc cần quyền đọc. Thiếu "
                 "quyền ghi thì bước nạp sẽ thất bại SAU khi đã dừng Replicat - tức "
                 "là ở chỗ đắt nhất."),
    Step(6, "ADD_TRANDATA", "Bật TRANDATA ở nguồn", L1, pf="ADD_TRANDATA"),
    Step(7, "STOP_REPLICAT", "Dừng Replicat (phải làm TRƯỚC)", L1, pf="STOP",
         ghi_chu="Phải dừng Replicat TRƯỚC khi chạy lại Extract. Đảo lại là mất dữ "
                 "liệu im lặng: Extract ghi dữ liệu bảng mới vào trail, Replicat cũ "
                 "chưa có dòng MAP nên bỏ qua mà vẫn dời mốc đã xử lý."),
    Step(8, "THEM_MAP", "Thêm dòng MAP, kèm chế độ chống trùng", L1, pf="PARAM_EDIT"),
    Step(9, "STOP_EXTRACT", "Dừng Extract", L1, pf="STOP"),
    Step(10, "THEM_TABLE", "Thêm dòng TABLE", L1, pf="PARAM_EDIT"),
    Step(11, "START_EXTRACT", "Chạy Extract để nạp tham số mới", L1, pf="START"),
    Step(12, "CHO_BAT_KIP", "Chờ Extract bắt kịp nguồn", L0,
         ghi_chu="Hỏi trạng thái liên tục cho đến khi bắt kịp, không chờ một khoảng "
                 "cố định."),
    Step(13, "SCN_FLOOR", "Đọc ngưỡng dưới của dữ liệu nạp", L0,
         ghi_chu="Chỉ lấy từ mốc High Watermark, và chỉ sau khi Extract đã bắt kịp. "
                 "Không nhận mốc Low Watermark hay thời điểm hiện tại của DB - cả "
                 "hai đều cho ngưỡng sai và làm bỏ lọt dữ liệu."),
    Step(14, "INDEX_UNUSABLE", "Tạm vô hiệu index để nạp nhanh", L2,
         ghi_chu="Chỉ vô hiệu index thường. GIỮ các index duy nhất ở trạng thái hoạt "
                 "động - chúng là thứ duy nhất bắt được dòng trùng NGAY trong lúc "
                 "nạp. Đo thực tế: nạp nhanh hơn 2,3 đến 3,8 lần."),
    Step(15, "SINH_SCRIPT_NAP", "Sinh script nạp", L0,
         ghi_chu="Lưu xuống thư mục scripts/ để đọc lại hoặc chạy tay. Bước này cũng "
                 "QUYẾT ĐỊNH xem nạp cả lô ở một thời điểm duy nhất có an toàn "
                 "không, dựa trên thời gian nguồn còn giữ được ảnh dữ liệu cũ."),
    Step(16, "NAP", "Nạp dữ liệu ban đầu", L2, pf="LOAD",
         ghi_chu="Chạy bằng việc nền RỜI RA khỏi tiến trình web: mất đường hầm hay "
                 "khởi động lại công cụ thì việc nạp vẫn chạy."),
    Step(17, "INDEX_REBUILD", "Dựng lại index", L2,
         ghi_chu="Dựng lại MỌI index đang vô hiệu, không chỉ những index mình vừa vô "
                 "hiệu: một index bị bỏ sót sẽ làm các truy vấn sau này bỏ qua nó IM "
                 "LẶNG. Script tự trả index về chế độ không song song ngay sau khi "
                 "dựng lại."),
    Step(18, "GATHER_STATS", "Cập nhật thống kê cho bộ tối ưu", L2),
    Step(19, "START_REPLICAT", "Chạy lại Replicat (làm SAU)", L1, pf="START",
         ghi_chu="Replicat dừng suốt cửa sổ nạp nên dữ liệu ở đích đứng yên, việc "
                 "nạp không tranh chấp. Đúng thứ tự đã chạy thật qua 8 lô."),
    Step(20, "CHO_LOW_WM", "Chờ Replicat áp hết phần chồng lấn", L0,
         ghi_chu="Đây là chỗ DUY NHẤT trong cả hệ thống mà mốc Low Watermark là mốc "
                 "đúng: nó cho biết mọi giao dịch trước đó đã áp xong, nên phần dữ "
                 "liệu chồng lấn với đợt nạp đã được xử lý hết."),
    Step(21, "STOP_REPLICAT_2", "Dừng Replicat để sửa tham số", L1, pf="STOP"),
    Step(22, "BO_HANDLECOLLISIONS", "Bỏ chế độ chống trùng", L2, pf="PARAM_EDIT",
         ghi_chu="Bỏ sớm là mất khả năng chống trùng; bỏ muộn là âm thầm bỏ qua lỗi "
                 "thật."),
    Step(23, "START_REPLICAT_2", "Chạy Replicat", L1, pf="START"),
    Step(24, "VERIFY", "Đối chiếu nguồn / đích", L1,
         ghi_chu="Lệch thì chuyển sang quy trình đồng bộ lại bảng."),
)

# =====================================================================
# RESYNC_TABLE - bang DA CO MAP nhung du lieu lech.
#
# Buoc 1 "bang co THUC SU lech?" la BAT BUOC, khong bo qua duoc. Muc 0 cua
# runbook ton tai vi sai lam nay da xay ra that: nap lai mot bang khong he
# lech. Engine bien no thanh KHONG THE BO QUA, khong phai mot loi nhac.
#
# Khac ADD_TABLE: GIU INDEX ONLINE (khong mark UNUSABLE) va verify HAI LAN.
# =====================================================================
RESYNC_TABLE: tuple[Step, ...] = (
    Step(1, "XAC_NHAN_LECH", "Bảng có THỰC SỰ lệch không?", L1,
         ghi_chu="BẮT BUỘC. Chạy đối chiếu trước; chỉ khi thật sự lệch mới mở các "
                 "bước sau. Không có đường bỏ qua bước này - chốt này tồn tại vì đã "
                 "từng nạp lại một bảng không hề lệch."),
    Step(2, "KHAO_SAT", "Khảo sát và ghi lại số dòng cũ", L0,
         ghi_chu="Ghi lại số dòng cũ TRƯỚC khi xoá."),
    Step(3, "STOP_REPLICAT", "Dừng Replicat", L1, pf="STOP"),
    Step(4, "THEM_HANDLECOLLISIONS", "Bật chế độ chống trùng trên dòng MAP đã có", L1,
         pf="PARAM_EDIT",
         ghi_chu="Bật trên dòng MAP ĐÃ CÓ, không thêm dòng MAP mới."),
    Step(5, "SCN_FLOOR", "Đọc ngưỡng dưới của dữ liệu nạp", L0),
    Step(6, "TRUNCATE", "Xoá sạch bảng đích", L2, pf="LOAD",
         ghi_chu="KHÔNG hoàn tác được. Số dòng cũ đã được ghi lại ở bước 2."),
    Step(7, "SINH_SCRIPT_NAP", "Sinh script nạp", L0),
    Step(8, "NAP", "Nạp lại từ nguồn", L2, pf="LOAD"),
    Step(9, "GATHER_STATS", "Cập nhật thống kê cho bộ tối ưu", L2),
    Step(10, "START_REPLICAT", "Chạy Replicat", L1, pf="START"),
    Step(11, "CHO_LOW_WM", "Chờ Replicat áp hết phần chồng lấn", L0),
    Step(12, "STOP_REPLICAT_2", "Dừng Replicat để sửa tham số", L1, pf="STOP"),
    Step(13, "BO_HANDLECOLLISIONS", "Bỏ chế độ chống trùng", L2, pf="PARAM_EDIT"),
    Step(14, "START_REPLICAT_2", "Chạy Replicat", L1, pf="START"),
    Step(15, "VERIFY", "Đối chiếu lần 2, chặt hơn", L1),
)


# =====================================================================
# PHASE 4 - NEW_STREAM: tao Extract + Replicat moi
#
# LUONG NGUY HIEM NHAT CUA CA CONG CU. No `REGISTER` mot capture process vao
# DB NGUON PRODUCTION. Neu don sai thi de lai capture MO COI dao redo VINH
# VIEN va giu archive log.
#
# THU TU DUOI DAY LA HANG SO. Hai cho de dao nhat, va ca hai da tung sai:
#   - `REGISTER EXTRACT` PHAI chay TRUOC `ADD EXTRACT`
#   - `ADD CHECKPOINTTABLE` PHAI chay TRUOC `ADD REPLICAT` (GLOBALS RONG nen
#     `ADD REPLICAT` khong co CHECKPOINTTABLE mac dinh, ma cu phap lai bat
#     buoc chon CHECKPOINTTABLE hoac NODBCHECKPOINT)
#
# HAI VIEC CONG CU KHONG LAM, CO CHU Y:
#   1. Khong tu chay `ALTER SYSTEM SET streams_pool_size` - lenh do tac dong
#      DB NGUON production. Cong cu chi KIEM TRA va SINH RA lenh.
#   2. Khong tu dat ten. Ten process, trail prefix (DUNG 2 ky tu), ten
#      checkpoint table deu do DBA go; cong cu kiem trung voi 6 process +
#      3 trail + 3 checkpoint table dang co.
#   3. Khong tu GRANT SELECT bang checkpoint cho GGADM_RO - cong cu khong co
#      sysdba va khong co ket noi OGG_BSH (co y). Thieu quyen thi trang chu
#      bao ORA-00942 o "Moc da ap o dich" (gap that 2026-10-27, RPMNV2/RPMNV3/
#      RLOG). Buoc tao bang checkpoint in san lenh de DBA chay.
# =====================================================================
_GHI_CHU_GRANT_CKPT = (
    "-- VIỆC LÀM TAY SAU BƯỚC NÀY (sysdba, DB đích): công cụ chỉ đọc bằng GGADM_RO,\n"
    "-- thiếu quyền thì trang chủ báo ORA-00942 ở 'Mốc đã áp ở đích'.\n"
    "--   GRANT SELECT ON {ck} TO GGADM_RO;\n"
    "-- hoặc: sqlplus / as sysdba @tools/cs13_grant_ckpt_ro.sql")

NEW_STREAM: tuple[Step, ...] = (
    Step(1, "TIEN_DIEU_KIEN", "Điều kiện tiên quyết", L0,
         ghi_chu="Các tiến trình cũ đang chạy và không bị chậm; file tham số và "
                 "trail mới CHƯA tồn tại; đĩa còn chỗ; bộ nhớ streams pool đủ ở CẢ "
                 "HAI ĐẦU."),
    Step(2, "KIEM_TEN", "Kiểm các tên mới", L1,
         ghi_chu="Người dùng gõ tên, công cụ kiểm trùng. Công cụ không tự đặt tên."),
    Step(3, "ADD_TRANDATA", "Bật TRANDATA cho bảng đầu tiên", L1,
         pf="ADD_TRANDATA"),
    Step(4, "TAO_PARAM", "Tạo 2 file tham số", L1,
         ghi_chu="Cả HAI file tham số phải qua được bước kiểm cú pháp trước khi đi "
                 "tiếp."),
    Step(5, "ADD_CHECKPOINTTABLE", "Tạo bảng checkpoint ở đích", L2,
         ghi_chu="PHẢI làm trước khi tạo Replicat: file GLOBALS rỗng nên không có "
                 "bảng checkpoint mặc định, mà cú pháp lại bắt buộc phải chọn."),
    Step(6, "REGISTER_EXTRACT", "Đăng ký tiến trình đọc redo ở nguồn", L2,
         ghi_chu="PHẢI làm trước khi tạo Extract. Đây là bước chạm vào DB NGUỒN "
                 "production."),
    Step(7, "ADD_EXTRACT", "Tạo Extract ở nguồn", L2),
    Step(8, "ADD_EXTTRAIL", "Tạo trail cho Extract", L2,
         ghi_chu="Tham số MEGABYTES chỉ hợp lệ ở LỆNH này, KHÔNG hợp lệ trong file "
                 ".prm - đã gặp lỗi thật vì chỗ này."),
    Step(9, "ADD_REPLICAT", "Tạo Replicat ở đích", L2),
    Step(10, "START_EXTRACT", "Chạy Extract mới", L1),
    Step(11, "CHO_BAT_KIP", "Chờ Extract bắt kịp nguồn", L0,
         ghi_chu="Extract mới phải đọc redo từ thời điểm đăng ký tới hiện tại. Lần "
                 "tạo ETHT mất khoảng 13 phút. Hỏi trạng thái liên tục, không chờ "
                 "một khoảng cố định."),
    Step(12, "START_REPLICAT", "Chạy Replicat mới", L1),
    Step(13, "KIEM_CHUNG", "Kiểm chứng luồng mới", L0,
         ghi_chu="Sinh ra khối cấu hình [streams] để thêm vào conf/app.toml."),
)

# =====================================================================
# PHASE 5 - LUONG_MOI: tu bang chua thuoc luong nao den luong chay that,
# du lieu da nap va da doi chieu. Mot luot, khong dut doan.
#
# QUAN HE VOI NEW_STREAM: dung chung 13 handler `_b_ns_*`, nhung NEW_STREAM
# chi tao ha tang (khong tao bang dich, khong nap du lieu, va bat nguoi tu
# dan khoi [streams] vao app.toml). LUONG_MOI tron bo: phan tao ha tang cua
# NEW_STREAM + phan dua du lieu vao cua ADD_TABLE + mot buoc MOI tu ghi cau
# hinh. NEW_STREAM giu nguyen cho duong bam tay tung buoc.
#
# BUOC 2 DANG_KY_BANG CHI CO O LUONG_MOI, va no o day vi mot VONG KHONG
# THE DI: o dang ky bang o trang Quy trinh bat chon mot luong DA CO trong
# conf/app.toml, nhung luong cua quy trinh nay chi duoc ghi vao cau hinh o
# buoc 15. Bat dang ky truoc tuc la: khong dang ky duoc thi khong mo duoc
# quy trinh, va khong chay quy trinh thi khong co luong de dang ky.
# NEW_STREAM (bam tay) CO Y khong co buoc nay: de di het duong do, o dang
# ky bang nhan CA truong hop "chua co luong" (luong rong) - nguoi dung dang
# ky truoc roi mo quy trinh.
#
# BON DIEU CUA THU TU NAY KHONG DUOC DAO:
#   0. DANG_KY_BANG truoc KHAO_SAT. Registry la allowlist lop 5, va buoc
#      DANG_KY_CAU_HINH sau nay UPDATE dong do - khong co dong thi no cap
#      nhat 0 hang va im lang.
#   1. XAC_NHAN_KEYCOLS truoc TAO_PARAM. Cot khoa duoc NHUNG THANG vao dong
#      MAP sinh ra o TAO_PARAM va khong buoc nao sua lai dong do ve sau.
#   2. TAO_BANG_DICH/KIEM_BANG_DICH truoc REGISTER_EXTRACT. Bay thieu quyen
#      phai no TRUOC khi dang ky capture vao DB nguon production.
#   3. DANG_KY_CAU_HINH truoc START_EXTRACT. Tu buoc do tro di cac handler
#      dung chung doc `cfg.streams[run["stream"]]` va `gg.PROCESSES`.
#
# CO Y KHONG CO: THEM_MAP / THEM_TABLE / STOP_EXTRACT / STOP_REPLICAT truoc
# nap. File tham so sinh o buoc TAO_PARAM DA co san dong TABLE va dong MAP (kem
# HANDLECOLLISIONS); chay lai may buoc do la NHAN DOI dong MAP. Cua so bao
# tri ma chung sinh ra cung khong ton tai o day: Replicat moi toanh, chua
# tung chay, nen no da dung san suot dot nap.
# =====================================================================
LUONG_MOI: tuple[Step, ...] = (
    Step(1, "TIEN_DIEU_KIEN", "Điều kiện tiên quyết", L0,
         ghi_chu="Mọi tiến trình đang chạy phải khoẻ, đĩa còn chỗ, và bộ nhớ "
                 "streams pool đủ ở CẢ HAI đầu - luồng mới dùng chung tài nguyên đó."),
    Step(2, "DANG_KY_BANG", "Đăng ký bảng vào danh sách quản lý", L1,
         ghi_chu="Bước này nằm TRONG quy trình vì ô đăng ký ở trang Quy trình bắt "
                 "chọn một luồng ĐÃ CÓ trong conf/app.toml - mà luồng của quy trình "
                 "này chỉ tồn tại từ bước ĐĂNG KÝ CẤU HÌNH. Bảng vẫn phải có THẬT ở "
                 "nguồn KT_BSH; dòng mới ghi với luồng rỗng và cả ba cờ = 0."),
    Step(3, "KHAO_SAT", "Khảo sát bảng ở nguồn", L0),
    Step(4, "XAC_NHAN_KEYCOLS", "Xác nhận cột khoá (KEYCOLS)", L1,
         ghi_chu="Phải chốt TRƯỚC bước tạo tham số: cột khoá được nhúng thẳng vào "
                 "dòng MAP và không bước nào sửa lại dòng đó về sau."),
    Step(5, "KIEM_TEN", "Kiểm các tên mới", L1),
    Step(6, "TAO_BANG_DICH", "Tạo bảng ở đích (nếu chưa có)", L2),
    Step(7, "KIEM_BANG_DICH", "Kiểm bảng đích: cấu trúc và quyền", L0,
         ghi_chu="Thiếu quyền phải lộ ra Ở ĐÂY - tức là TRƯỚC khi đăng ký capture "
                 "vào DB nguồn production."),
    Step(8, "ADD_TRANDATA", "Bật TRANDATA ở nguồn", L1, pf="ADD_TRANDATA"),
    Step(9, "TAO_PARAM", "Tạo 2 file tham số", L1,
         ghi_chu="File sinh ra đã có sẵn dòng TABLE và dòng MAP kèm KEYCOLS và chế độ "
                 "chống trùng - nên quy trình này KHÔNG có bước thêm MAP/TABLE."),
    Step(10, "ADD_CHECKPOINTTABLE", "Tạo bảng checkpoint ở đích", L2),
    Step(11, "REGISTER_EXTRACT", "Đăng ký tiến trình đọc redo ở nguồn", L2,
         ghi_chu="Từ bước này trở đi, hỏng mà không dọn là để lại một tiến trình đọc "
                 "redo MỒ CÔI giữ archive log ở DB nguồn."),
    Step(12, "ADD_EXTRACT", "Tạo Extract ở nguồn", L2),
    Step(13, "ADD_EXTTRAIL", "Tạo trail cho Extract", L2),
    Step(14, "ADD_REPLICAT", "Tạo Replicat ở đích", L2),
    Step(15, "DANG_KY_CAU_HINH", "Đăng ký luồng vào cấu hình công cụ", L2,
         ghi_chu="Ghi THÊM khối [streams] vào conf/app.toml (có lưu bản cũ, ghi "
                 "nguyên tử, đọc lại đối chiếu) rồi nạp nóng - không phải khởi động "
                 "lại. Từ bước sau, công cụ điều khiển được luồng mới."),
    Step(16, "START_EXTRACT", "Chạy Extract mới", L1),
    Step(17, "CHO_BAT_KIP", "Chờ Extract bắt kịp nguồn", L0),
    Step(18, "SCN_FLOOR", "Đọc ngưỡng dưới của dữ liệu nạp", L0,
         ghi_chu="Luồng mới thì ngưỡng đúng là SCN lúc ĐĂNG KÝ capture: mọi ảnh dữ "
                 "liệu từ mốc đó trở đi đều chắc chắn được phần bắt thay đổi phủ."),
    Step(19, "INDEX_UNUSABLE", "Tạm vô hiệu index để nạp nhanh", L2),
    Step(20, "SINH_SCRIPT_NAP", "Sinh script nạp", L0),
    Step(21, "NAP", "Nạp dữ liệu ban đầu", L2, pf="LOAD"),
    Step(22, "INDEX_REBUILD", "Dựng lại index", L2),
    Step(23, "GATHER_STATS", "Cập nhật thống kê cho bộ tối ưu", L2),
    Step(24, "START_REPLICAT_2", "Chạy Replicat lần đầu (SAU khi nạp)", L1, pf="START",
         ghi_chu="Replicat chưa từng chạy nên đích đứng yên suốt đợt nạp - không cần "
                 "dừng gì cả."),
    Step(25, "CHO_LOW_WM", "Chờ Replicat áp hết phần chồng lấn", L0),
    Step(26, "STOP_REPLICAT", "Dừng Replicat để sửa tham số", L1, pf="STOP"),
    Step(27, "BO_HANDLECOLLISIONS", "Bỏ chế độ chống trùng", L2, pf="PARAM_EDIT"),
    Step(28, "START_REPLICAT_3", "Chạy Replicat", L1, pf="START"),
    Step(29, "VERIFY", "Đối chiếu nguồn / đích", L1),
    Step(30, "KIEM_CHUNG", "Kiểm chứng luồng mới, ghi mốc discard", L0),
)

# =====================================================================
# GO_BANG: go MOT bang khoi luong dang chay (2026-10-20). Anh guong co thu
# tu cua ADD_TABLE.
#
# THU TU LA HANG SO, va ly do giong het ADD_TABLE nhung nguoc chieu:
#   BO TABLE TRUOC, BO MAP SAU. Dao lai la cai bay so mot cua he nay: Extract
#   con dong TABLE nen van ghi bang do vao trail, Replicat da mat MAP nen bo
#   qua MA VAN DOI MOC CHECKPOINT - mat du lieu IM LANG.
#
# BA CHOT RIENG:
#   1. Khong go BANG CUOI CUNG cua Extract: Extract khong con dong TABLE nao
#      la go ca luong, mot viec khac han.
#   2. Buoc 3 ghi SCN Extract da doc toi luc DUNG. Moi giao dich cua bang
#      commit truoc SCN do co the dang nam trong trail.
#   3. Chi bo MAP khi cac giao dich do DA DUOC AP (cung dinh nghia
#      `_qua_moc_nap` voi buoc 25/27 cua LUONG_MOI) - kiem o buoc 6 khi
#      Replicat chay, va KIEM LAI o buoc 8 khi no da dung. "Doc het trail"
#      la chua du: Replicat integrated khoi dong lai tu vi tri DA AP; neu MAP
#      da mat thi giao dich chua ap cua bang bi bo qua.
#
# CO Y KHONG LAM: tat TRANDATA o nguon (DELETE TRANDATA cham DB nguon, va
# bat lai thi rat re - de nguoi quyet), va xoa bang/du lieu o dich (du lieu
# dung yen o thoi diem go).
# =====================================================================
GO_BANG: tuple[Step, ...] = (
    Step(1, "KIEM_TRA_GO", "Kiểm tra trước khi gỡ", L0,
         ghi_chu="Bảng phải đang có dòng TABLE/MAP trong luồng; KHÔNG được là bảng "
                 "cuối cùng của Extract; Extract và Replicat phải đang chạy khoẻ."),
    Step(2, "STOP_EXTRACT", "Dừng Extract", L1, pf="STOP"),
    Step(3, "GHI_MOC_TRAIL", "Ghi mốc: SCN Extract đã đọc tới và cuối trail", L0,
         ghi_chu="Mọi giao dịch của bảng commit trước mốc này có thể đang nằm trong "
                 "trail - phải được áp hết rồi mới bỏ dòng MAP."),
    Step(4, "BO_TABLE", "Bỏ dòng TABLE (phải làm TRƯỚC)", L2, pf="PARAM_EDIT",
         ghi_chu="Bỏ TABLE trước MAP. Đảo lại là mất dữ liệu im lặng."),
    Step(5, "START_EXTRACT", "Chạy lại Extract (không còn bảng này)", L1, pf="START"),
    Step(6, "CHO_AP_HET", "Chờ Replicat áp hết phần đã ghi của bảng", L0),
    Step(7, "STOP_REPLICAT", "Dừng Replicat", L1, pf="STOP"),
    Step(8, "BO_MAP", "Bỏ dòng MAP", L2, pf="PARAM_EDIT",
         ghi_chu="Kiểm LẠI điều kiện đã áp hết ngay trước khi bỏ, lúc Replicat đã "
                 "dừng; và Extract phải không còn dòng TABLE của bảng."),
    Step(9, "START_REPLICAT", "Chạy lại Replicat", L1, pf="START"),
    Step(10, "KIEM_CHUNG_GO", "Kiểm chứng: hai tiến trình chạy, hai dòng đã hết", L0,
         ghi_chu="Giữ nguyên TRANDATA ở nguồn và dữ liệu ở đích - hai việc đó để "
                 "người quyết."),
)

# =====================================================================
# LUONG LOG (2026-10-20): dich <TEN>_LOG giu MOI thay doi cua bang nguon.
#
# Moi INSERT/UPDATE/DELETE o nguon thanh MOT dong INSERT o dich, kem
# LOG_TYPE (I/U/D), COMMIT_TS (luc commit o nguon) va LOG_TIME (SYSDATE luc
# ghi vao dich). Ca ba quy trinh chay tren LUONG LOG RIENG (khoi [streams]
# co `loai = "log"`) - tham so cua no ap cho CA Extract nen khong duoc tron
# voi luong thuong:
#   Extract  LOGALLSUPCOLS + UPDATERECORDFORMAT FULL, KHONG co DDL INCLUDE
#            (TRUNCATE/DROP o nguon khong duoc xoa lich su o dich)
#   Replicat INSERTALLRECORDS (chi chay voi UPDATERECORDFORMAT FULL - tai
#            lieu GG 21.3), IGNOREUPDATEBEFORES, REPERROR mac dinh = ABEND
#   Nguon    ADD TRANDATA ... ALLCOLS (kiem bang dba_log_groups)
#
# KHONG co: nap du lieu ban dau, che do chong trung, doi chieu so dong. Log
# bat dau tu luc luong chay - dong cu cua bang khong co trong log.
# Mot bang duoc VUA dong bo thuong VUA ghi log: danh sach quan ly cua log la
# bang `managed_log`, tach khoi managed_table.
# =====================================================================
LUONG_LOG: tuple[Step, ...] = (
    Step(1, "TIEN_DIEU_KIEN", "Điều kiện tiên quyết", L0),
    Step(2, "DANG_KY_BANG", "Đăng ký bảng vào danh sách log", L1),
    Step(3, "KIEM_TEN", "Kiểm các tên mới", L1),
    Step(4, "TAO_BANG_DICH", "Tạo bảng _LOG ở đích (nếu chưa có)", L2,
         ghi_chu="Không khoá chính, không index duy nhất, mọi cột cho phép NULL, thêm "
                 "LOG_TYPE / COMMIT_TS / LOG_TIME."),
    Step(5, "KIEM_BANG_DICH", "Kiểm bảng _LOG: cấu trúc, không khoá, quyền", L0),
    Step(6, "ADD_TRANDATA", "Bật TRANDATA ALLCOLS ở nguồn", L1, pf="ADD_TRANDATA",
         ghi_chu="DELETE/UPDATE phải mang đủ mọi cột. Kiểm bằng dba_log_groups: phải có "
                 "nhóm ALL COLUMN LOGGING."),
    Step(7, "TAO_PARAM", "Tạo 2 file tham số của luồng log", L1),
    Step(8, "ADD_CHECKPOINTTABLE", "Tạo bảng checkpoint ở đích", L2),
    Step(9, "REGISTER_EXTRACT", "Đăng ký tiến trình đọc redo ở nguồn", L2),
    Step(10, "ADD_EXTRACT", "Tạo Extract ở nguồn", L2),
    Step(11, "ADD_EXTTRAIL", "Tạo trail cho Extract", L2),
    Step(12, "ADD_REPLICAT", "Tạo Replicat ở đích", L2),
    Step(13, "DANG_KY_CAU_HINH", "Đăng ký luồng log vào cấu hình công cụ", L2),
    Step(14, "START_EXTRACT", "Chạy Extract mới", L1),
    Step(15, "CHO_BAT_KIP", "Chờ Extract bắt kịp nguồn", L0),
    Step(16, "START_REPLICAT_2", "Chạy Replicat", L1, pf="START"),
    Step(17, "KIEM_CHUNG", "Kiểm chứng luồng log", L0),
)

THEM_LOG: tuple[Step, ...] = (
    Step(1, "DANG_KY_BANG", "Đăng ký bảng vào danh sách log", L1),
    Step(2, "TAO_BANG_DICH", "Tạo bảng _LOG ở đích (nếu chưa có)", L2),
    Step(3, "KIEM_BANG_DICH", "Kiểm bảng _LOG: cấu trúc, không khoá, quyền", L0),
    Step(4, "ADD_TRANDATA", "Bật TRANDATA ALLCOLS ở nguồn", L1, pf="ADD_TRANDATA"),
    Step(5, "STOP_REPLICAT", "Dừng Replicat (phải làm TRƯỚC)", L1, pf="STOP",
         ghi_chu="Thêm MAP trước TABLE: đảo lại là Extract ghi bảng mới vào trail mà "
                 "Replicat chưa có MAP - bỏ qua mà vẫn dời mốc."),
    Step(6, "THEM_MAP", "Thêm dòng MAP log (INSERTALLRECORDS)", L1, pf="PARAM_EDIT"),
    Step(7, "STOP_EXTRACT", "Dừng Extract", L1, pf="STOP"),
    Step(8, "THEM_TABLE", "Thêm dòng TABLE", L1, pf="PARAM_EDIT"),
    Step(9, "START_EXTRACT", "Chạy Extract để nạp tham số mới", L1, pf="START"),
    Step(10, "CHO_BAT_KIP", "Chờ Extract bắt kịp nguồn", L0),
    Step(11, "START_REPLICAT", "Chạy Replicat", L1, pf="START"),
    Step(12, "KIEM_CHUNG_LOG", "Kiểm chứng", L0),
)

# Go mot bang khoi luong log: CUNG thu tu va CUNG handler voi GO_BANG; chi
# khac danh sach quan ly (managed_log) - xem `Wizard._bang_qly`.
GO_LOG: tuple[Step, ...] = GO_BANG

# Ba loai quy trinh lam viec tren LUONG LOG va danh sach `managed_log`.
KIND_LOG = ("LUONG_LOG", "THEM_LOG", "GO_LOG")

BO_BUOC = {"ADD_TABLE": ADD_TABLE, "RESYNC_TABLE": RESYNC_TABLE,
           "NEW_STREAM": NEW_STREAM, "LUONG_MOI": LUONG_MOI, "GO_BANG": GO_BANG,
           "LUONG_LOG": LUONG_LOG, "THEM_LOG": THEM_LOG, "GO_LOG": GO_LOG}

# Hai loai quy trinh CHAM VAO DB NGUON production: dung chung khoa doc
# quyen toan cuc (toi da MOT phien cung luc) va dung chung duong don sach.
KIND_LUONG_MOI = ("NEW_STREAM", "LUONG_MOI", "LUONG_LOG")


# Tien to handler rieng theo loai wizard.
#
# LUONG_MOI dung chung tien to "ns_" de tai su dung 13 handler tao ha tang.
# He qua phai nho: bat ky buoc nao cua LUONG_MOI trung TEN voi mot handler
# `_b_ns_*` se dinh vao handler do TRUOC. Day la ly do buoc 24/28 ten
# START_REPLICAT_2/_3 chu khong phai START_REPLICAT - neu khong chung se
# dinh `_b_ns_start_replicat` (goi thang gg.start_process) thay vi
# `_b_start_replicat` (di qua control, co ghi su kien).
TIEN_TO = {"NEW_STREAM": "ns_", "LUONG_MOI": "ns_", "LUONG_LOG": "ns_"}


# =====================================================================
# LO NHIEU BANG - chi ADD_TABLE
#
# VI SAO CAN, khong phai de tien tay
#   Lo CN_* dua vao thang 10/2026 co CN_CH (dong gom) va CN_CT / CN_PS /
#   CN_TT (dong chi tiet) dung chung khoa (MA_DVI, SO_ID). Chay tam phien
#   ADD_TABLE rieng nghia la tam `AS OF SCN` KHAC NHAU: dong chi tiet cua
#   mot chung tu co the vao dich trong khi dong gom cua no thi khong, vi hai
#   anh chup cach nhau hang chuc phut. HANDLECOLLISIONS + CDC roi cung keo
#   ve dung, nhung trong khoang giua do du lieu o dich KHONG nhat quan va
#   bao cao doc trong khoang do thi sai. Mot SCN duy nhat cho ca lo thi tinh
#   nhat quan la DUNG NGAY TU DAU, khong phai "roi se dung".
#   Loi ich thu hai: MOT cua so bao tri thay vi N cua so.
#
# BUOC NAO CHAY MOI BANG, BUOC NAO CHAY MOT LAN
#   `MOI_BANG` duoi day la cac buoc tac dong len TUNG bang va KHONG chay qua
#   job. Dispatcher goi dung handler MOT BANG da co san, lap qua tung bang,
#   voi mot ban sao `run` thay ten bang - nen khong co handler nao phai viet
#   lai, va phien mot bang di dung duong code cu.
#   Cac buoc con lai chay MOT LAN cho ca lo:
#     - stop/start, cho bat kip, SCN_FLOOR, cho Low WM: vi ban chat la
#       thao tac muc LUONG, khong phai muc bang
#     - TAO_BANG_DICH, INDEX_UNUSABLE, NAP, INDEX_REBUILD, GATHER_STATS: vi
#       chay qua JOB ROI RA, moi buoc doi dung mot job. Chung sinh MOT script
#       bao ca lo (xem `_sql_nap`, `_sql_index`, `_sql_stats`). Rieng NAP:
#       mot script, mot SCN, nhieu bang - day la cho tinh nhat quan nam.
# =====================================================================
# Trang thai inbound server cua Replicat integrated o DB DICH - doc bang
# GGADM_RO (da thu that 2026-10-21: doc duoc ca hai view). Xem `_ap_rong`.
# INACTIVE = apply server Oracle tam tat khi tu giam song song - khong giu
# viec gi. RDTHU / RPMNVODS luon co mot cai nhu vay; tinh no la 'ban' thi
# hai luong do ket o buoc cho y nhu loi cu.
SQL_INBOUND = """
    SELECT c.state, c.total_received, c.total_applied, c.total_ignored,
           c.total_errors, c.unassigned_complete_txns,
           (SELECT COUNT(*) FROM v$gg_apply_server s
             WHERE s.apply_name = c.apply_name) AS so_server,
           (SELECT COUNT(*) FROM v$gg_apply_server s
             WHERE s.apply_name = c.apply_name
               AND s.state NOT IN ('IDLE', 'INACTIVE')) AS server_ban
      FROM v$gg_apply_coordinator c
     WHERE c.apply_name = :a"""

MOI_BANG = frozenset({
    "KHAO_SAT", "XAC_NHAN_KEYCOLS", "KIEM_BANG_DICH", "ADD_TRANDATA",
    "THEM_MAP", "THEM_TABLE", "THEM_HANDLECOLLISIONS",
    "BO_HANDLECOLLISIONS", "VERIFY",
})

# Lo cang to thi cua so bao tri cang dai va mot buoc FAIL cang keo theo
# nhieu bang. 20 la tran cung, khong phai de xuat.
GIOI_HAN_LO = 20

# Buoc VERIFY (che do LIVE): lech thi doi chieu lai trong chung nay giay
# truoc khi ket luan LECH. Xem _b_verify.
VERIFY_TRAN_S = 60
VERIFY_TOI_THIEU = 3          # so lan doi chieu toi thieu truoc khi bao LECH

# =====================================================================
# QUYEN BAT BUOC tren bang dich, truoc khi dung bat cu thu gi.
#
# Day la danh sach DUY NHAT, dat o day de buoc KIEM_BANG_DICH va script
# nap khong the lech nhau. Lan dau toi viet chot kiem quyen thi bo sot
# `GGADM_LOAD.SELECT`, va buoc 15 vo voi ORA-01031 ngay tai LOP CHAN 1 -
# dung cai ma chot do sinh ra de chan.
#
#   GGADM_LOAD.INSERT  lenh nap
#   GGADM_LOAD.SELECT  LOP CHAN 1 (`SELECT 1 FROM <dich>` kiem bang rong)
#                      va buoc kiem chung dem lai so dong
#   GGADM_RO.SELECT    buoc doi chieu nguon / dich
# =====================================================================
QUYEN_BANG_DICH = ("GGADM_LOAD.INSERT", "GGADM_LOAD.SELECT",
                   "GGADM_RO.SELECT")


def tach_ten_bang(s: str) -> list[str]:
    """Tach danh sach ten bang: dau phay, khoang trang, dong moi, dau ;.

    Giu NGUYEN THU TU nguoi dung nhap va bo trung lap gan nhau. Thu tu co
    nghia: bang dau tien la ten hien tren moi cho va trong ten script.
    """
    ra: list[str] = []
    for t in str(s or "").replace(",", " ").replace(";", " ").split():
        t = t.strip().upper()
        if t:
            ra.append(t)
    return ra


def so_buoc(kind: str, ten: str) -> int | None:
    """So hieu cua buoc `ten` trong quy trinh `kind`, None neu khong co.

    Dung cho UI va cac thong bao: so hieu buoc KHONG duoc go cung o noi
    khac, vi them mot buoc vao giua la day toan bo phan sau len mot.
    """
    for s in BO_BUOC.get(kind, ()):
        if s.name == ten:
            return s.no
    return None


def tong_buoc(kind: str) -> int:
    return max((s.no for s in BO_BUOC.get(kind, ())), default=0)


def buoc_cua(kind: str, no: int) -> Step:
    for s in BO_BUOC[kind]:
        if s.no == no:
            return s
    raise WizardError(f"{kind} không có bước {no}")


class Wizard(IndexMixin):
    """Engine wizard.

    `IndexMixin` (app/wizard_index.py) bo sung: buoc index UNUSABLE/REBUILD,
    quyet dinh chia lo theo `undo_retention`, ba duong di de chay DDL tren
    schema ODS, va co che buoc CHO MOT JOB ROI RA. Tach module vi sua mot file
    1.600 dong bang khop chuoi da lam hong file do mot lan trong phien nay.
    """

    def __init__(self, cfg, con, ora, control, verifier):
        self.cfg = cfg
        self.con = con
        self.ora = ora
        self.control = control
        self.verifier = verifier

    # ================================================================ CRUD
    def tao(self, kind: str, *, stream: str, src_table: str, who: str) -> int:
        """Mo mot phien wizard. KHOA DOC QUYEN 1 wizard / 1 luong.

        `src_table` nhan MOT ten, hoac NHIEU ten cach nhau bang dau phay /
        khoang trang / dong moi (chi ADD_TABLE). Nhieu ten = mot LO: ca lo di
        qua DUNG MOT cua so bao tri va nap o DUNG MOT SCN.
        """
        if kind not in BO_BUOC:
            raise WizardError(f"loại quy trình không hợp lệ: {kind!r}")

        ds_bang = tach_ten_bang(src_table)
        if not ds_bang:
            raise WizardError("Chưa nhập tên bảng nào.")
        for b in ds_bang:
            check_ident(b)
        trung = sorted({b for b in ds_bang if ds_bang.count(b) > 1})
        if trung:
            raise WizardError(f"Tên bảng lặp lại: {', '.join(trung)}")
        if len(ds_bang) > 1 and kind != "ADD_TABLE":
            raise WizardError(
                f"{kind} chỉ làm MỘT bảng mỗi phiên. Lô nhiều bảng chỉ áp cho quy trình "
                "thêm bảng: quy trình đồng bộ lại có bước xoá sạch bảng không hoàn tác "
                "được, còn quy trình tạo luồng mới thì chạm vào DB nguồn production.")
        if len(ds_bang) > GIOI_HAN_LO:
            raise WizardError(
                f"{len(ds_bang)} bảng vượt trần {GIOI_HAN_LO} bảng mỗi lô. Lô càng to "
                "thì cửa sổ bảo trì càng dài và một bước lỗi càng kéo theo nhiều bảng. "
                "Chia nhỏ ra.")
        src_table = ds_bang[0]

        if kind in KIND_LUONG_MOI:
            # Luong CHUA ton tai nen khong kiem cfg.streams. Khoa doc quyen
            # o day la TOAN CUC: toi da MOT phien tao luong moi cung luc (ca
            # hai loai tinh chung), vi no cham vao DB nguon production.
            dang_mo = self.con.execute(
                "SELECT id, src_table FROM wizard_run"
                " WHERE kind IN ('NEW_STREAM','LUONG_MOI','LUONG_LOG') AND state='OPEN'"
            ).fetchone()
            if dang_mo:
                raise WizardError(
                    f"Đang có phiên tạo luồng mới #{dang_mo['id']} mở (trên "
                    f"{dang_mo['src_table']}). Tối đa MỘT phiên loại này cùng lúc - nó chạm "
                    "vào DB nguồn production.")
            stream = None
        else:
            if stream not in self.cfg.streams:
                raise WizardError(f"không có luồng {stream!r}")
            la_luong_log = str(self.cfg.streams[stream].get("loai", "")).lower() == "log"
            if kind in KIND_LOG and not la_luong_log:
                raise WizardError(f"Luồng {stream} là luồng THƯỜNG - quy trình log chỉ chạy "
                                  "trên luồng log.")
            if kind not in KIND_LOG and la_luong_log:
                raise WizardError(f"Luồng {stream} là luồng LOG (đích _LOG, chỉ INSERT) - "
                                  "dùng menu Luồng log, không dùng quy trình thường.")
            dang_mo = self.con.execute(
                "SELECT id, kind, src_table FROM wizard_run"
                " WHERE stream=? AND state='OPEN'", (stream,)).fetchone()
            if dang_mo:
                raise WizardError(
                    f"Luồng {stream} đang có quy trình #{dang_mo['id']} ({dang_mo['kind']} "
                    f"trên {dang_mo['src_table']}) mở. Một luồng chỉ một quy trình - đóng "
                    "hoặc bỏ phiên đó trước.")

        # Kiem TUNG bang cua lo. Kiem HET roi moi bao loi mot lan: bao loi
        # tung bang mot bat nguoi dung sua 8 lan cho 8 bang sai.
        hang = {}
        loi: list[str] = []
        bang_qly = "managed_log" if kind in KIND_LOG else "managed_table"
        for b in ds_bang:
            row = self.con.execute(
                "SELECT src_owner, src_table, tgt_owner, tgt_table, keycols,"
                f" stream, has_map, in_extract, prepared FROM {bang_qly}"
                " WHERE src_table=?", (b,)).fetchone()
            if row is None and kind in ("LUONG_LOG", "THEM_LOG"):
                # Dich LOG = <TEN>_LOG. Ten dinh danh toi da 30 ky tu (luat
                # check_ident cua cong cu) -> ten nguon toi da 26.
                if len(b) > 26:
                    loi.append(f"{b}: tên bảng log {b}_LOG vượt 30 ký tự.")
                    continue
                row = {"src_owner": SCHEMA_SOURCE, "src_table": b,
                       "tgt_owner": SCHEMA_TARGET, "tgt_table": f"{b}_LOG",
                       "keycols": None, "stream": None,
                       "has_map": 0, "in_extract": 0, "prepared": 0}
            elif row is None and kind in ("LUONG_MOI", "ADD_TABLE"):
                # LUONG_MOI tu dang ky bang o BUOC 2, nen o day KHONG doi
                # bang phai co san trong danh sach quan ly - doi thi thanh
                # mot vong khong the di (xem khoi chu thich cua LUONG_MOI).
                # Khong phai bo chot: buoc 2 van kiem bang co THAT o nguon
                # truoc khi ghi dong nao, va moi buoc cham vao GoldenGate
                # deu o SAU buoc do.
                row = {"src_owner": SCHEMA_SOURCE, "src_table": b,
                       "tgt_owner": SCHEMA_TARGET, "tgt_table": b,
                       "keycols": None, "stream": None,
                       "has_map": 0, "in_extract": 0, "prepared": 0}
            elif row is None:
                loi.append(f"{b}: không nằm trong danh sách quản lý -> từ chối. Bảng ngoài phạm vi "
                           "GoldenGate thì công cụ không được chạm. Dùng thao tác đăng ký bảng để "
                           "đưa vào danh sách.")
                continue
            hang[b] = row
            if kind == "ADD_TABLE" and row["has_map"] and row["in_extract"]:
                loi.append(f"{b}: ĐÃ có cả dòng TABLE lẫn dòng MAP trong luồng. Dùng quy trình đồng "
                           "bộ lại nếu dữ liệu lệch.")
            if kind == "RESYNC_TABLE" and not row["has_map"]:
                loi.append(f"{b}: chưa có dòng MAP. Dùng quy trình thêm bảng trước.")
            if kind == "THEM_LOG" and row["has_map"] and row["in_extract"]:
                loi.append(f"{b}: ĐÃ có trong luồng log {row['stream']} (đủ TABLE và MAP).")
            if kind in KIND_LOG and kind != "LUONG_LOG" and row["stream"] \
                    and row["stream"] != stream:
                loi.append(f"{b}: danh sách log ghi bảng này thuộc luồng {row['stream']}, "
                           f"không phải {stream}.")
            if kind in ("GO_BANG", "GO_LOG") and not (row["in_extract"] or row["has_map"]):
                loi.append(f"{b}: không có dòng TABLE lẫn MAP trong luồng nào - không có gì "
                           "để gỡ.")
            if kind in ("GO_BANG", "GO_LOG") and row["stream"] and row["stream"] != stream:
                loi.append(f"{b}: danh sách quản lý ghi bảng này thuộc luồng {row['stream']}, "
                           f"không phải {stream}.")
            if kind in KIND_LUONG_MOI and row["has_map"]:
                loi.append(f"{b}: đã có dòng MAP trong một luồng khác. Bảng đầu tiên của luồng mới "
                           "phải chưa thuộc luồng nào.")
            if kind == "ADD_TABLE" and row["stream"] and row["stream"] != stream:
                loi.append(f"{b}: danh sách quản lý ghi bảng này thuộc luồng {row['stream']}, không "
                           f"phải {stream}.")
        if loi:
            raise WizardError(f"{len(loi)} vấn đề:" + chr(10)
                              + chr(10).join("  - " + x for x in loi))

        row = hang[src_table]
        cur = self.con.execute(
            "INSERT INTO wizard_run(kind,state,stream,src_owner,src_table,"
            "src_tables,tgt_owner,tgt_table,created_at,created_by,updated_at,"
            "cur_step,keycols)"
            " VALUES (?,'OPEN',?,?,?,?,?,?,?,?,?,1,?)",
            (kind, stream, row["src_owner"], src_table,
             json.dumps(ds_bang), row["tgt_owner"],
             row["tgt_table"], int(time.time()), who, int(time.time()),
             row["keycols"]))
        rid = int(cur.lastrowid)
        for s in BO_BUOC[kind]:
            self.con.execute(
                "INSERT INTO wizard_step(run_id,step_no,name,state)"
                " VALUES (?,?,?,'CHO')", (rid, s.no, s.name))
        log.info("mở quy trình #%s %s trên %s luồng %s", rid, kind, src_table, stream)
        return rid

    def lay(self, rid: int) -> dict:
        r = self.con.execute("SELECT * FROM wizard_run WHERE id=?", (rid,)).fetchone()
        if r is None:
            raise WizardError(f"không có quy trình #{rid}")
        return dict(r)

    def cac_buoc(self, rid: int) -> list[dict]:
        run = self.lay(rid)
        ds = {r["step_no"]: dict(r) for r in self.con.execute(
            "SELECT * FROM wizard_step WHERE run_id=? ORDER BY step_no", (rid,))}
        out = []
        for s in BO_BUOC[run["kind"]]:
            d = ds.get(s.no, {"step_no": s.no, "state": "CHO"})
            d.update(dinh_nghia=s, nhan=s.nhan, level=s.level, ghi_chu=s.ghi_chu)
            out.append(d)
        return out

    def danh_sach(self, chi_mo: bool = False) -> list[dict]:
        q = ("SELECT * FROM wizard_run" + (" WHERE state='OPEN'" if chi_mo else "")
             + " ORDER BY id DESC LIMIT 50")
        return [dict(r) for r in self.con.execute(q)]

    def bo_phien(self, rid: int, who: str, reason: str) -> None:
        self.con.execute(
            "UPDATE wizard_run SET state='ABORTED', finished_at=?, note=?"
            " WHERE id=? AND state='OPEN'",
            (int(time.time()), f"bỏ bởi {who}: {reason}", rid))

    # ======================================================= engine
    def _ghi_buoc(self, rid: int, no: int, **kw) -> None:
        sets = ", ".join(f"{k}=:{k}" for k in kw)
        self.con.execute(
            f"UPDATE wizard_step SET {sets} WHERE run_id=:rid AND step_no=:no",
            dict(kw, rid=rid, no=no))

    # Ten bang dang chay trong vong lap cua `_lap_moi_bang`. None = khong o
    # trong lo, hoac dang o buoc chay MOT LAN cho ca lo.
    _lo_bang: str | None = None

    def _ghi_run(self, rid: int, **kw) -> None:
        """Ghi vao wizard_run.

        TRONG VONG LAP CUA LO (`_lo_bang` khac None) hai truong bi CHUYEN
        HUONG vao ctx["bang"][<ten bang>] thay vi ghi muc phien:

          ctx_json  handler mot bang ghi ctx CUA RIENG NO. Ghi thang se de
                    mat ctx["bang"] cua ca lo - tuc la mat ket qua khao sat
                    cua nhung bang truoc. Da thu: khong chan thi bang thu 2
                    xoa sach so dong cua bang thu 1, va `do_song_song()` sau
                    do tinh theo so dong bang 0.
          keycols   moi bang mot bo khoa rieng. Mot cot `keycols` muc phien
                    khong the giu 8 bo khoa khac nhau.

        Cac truong con lai (scn_floor, maxscn, cur_step...) la muc PHIEN va
        chi do buoc chay mot lan ghi, nen di thang nhu cu.
        """
        if self._lo_bang:
            rieng = {k: kw.pop(k) for k in ("ctx_json", "keycols") if k in kw}
            if rieng:
                run = self.lay(rid)
                ctx = json.loads(run["ctx_json"] or "{}")
                o = ctx.setdefault("bang", {}).setdefault(self._lo_bang, {})
                if "ctx_json" in rieng:
                    try:
                        o.update(json.loads(rieng["ctx_json"] or "{}"))
                    except (TypeError, ValueError):
                        pass
                if rieng.get("keycols"):
                    o["keycols"] = rieng["keycols"]
                kw["ctx_json"] = json.dumps(ctx, ensure_ascii=False)
            if not kw:
                return
        kw["updated_at"] = int(time.time())
        sets = ", ".join(f"{k}=:{k}" for k in kw)
        self.con.execute(f"UPDATE wizard_run SET {sets} WHERE id=:rid",
                         dict(kw, rid=rid))

    def dieu_kien_vao(self, rid: int, no: int) -> tuple[bool, str]:
        """Luat cua ENGINE: khong buoc nao chay khi buoc truoc chua PASS."""
        run = self.lay(rid)
        if run["state"] != "OPEN":
            return False, f"phiên đã {run['state']}"
        if no != run["cur_step"]:
            return False, (f"đang ở bước {run['cur_step']}, không nhảy sang bước {no} được")
        for s in self.con.execute(
                "SELECT step_no, state FROM wizard_step"
                " WHERE run_id=? AND step_no<? ORDER BY step_no", (rid, no)):
            if s["state"] not in ("PASS", "BO_QUA"):
                return False, f"bước {s['step_no']} đang {s['state']}, chưa xong"
        return True, "du dieu kien"

    async def xem_truoc(self, rid: int, no: int) -> str:
        """Lenh/SQL se chay - hien TRUOC khi chay. Khong thay doi gi."""
        run = self.lay(rid)
        s = buoc_cua(run["kind"], no)
        tt = TIEN_TO.get(run["kind"], "")
        fn = (getattr(self, f"_xt_{tt}{s.name.lower()}", None)
              or getattr(self, f"_xt_{s.name.lower()}", None))
        if fn is None:
            return f"(bước {s.no} {s.name}: không có lệnh nào để xem trước)"
        try:
            if s.name in MOI_BANG and self._la_lo(run):
                # Xem truoc PHAI hien lenh cua CA LO. Chi hien bang dau tien
                # thi nguoi duyet thay mot dong MAP roi bam chay, va thuc te
                # chay 8 dong - ban xem truoc nhu vay la sai lech chu khong
                # phai rut gon.
                kh: list[str] = []
                for i, b in enumerate(self._cac_bang(run), 1):
                    try:
                        kh.append(f"----- [{i}] {b} -----" + chr(10)
                                  + await fn(self._run_bang(run, b)))
                    except Exception as e:                      # noqa: BLE001
                        kh.append(f"----- [{i}] {b} -----{chr(10)}({e})")
                return (chr(10) * 2).join(kh)
            return await fn(run)
        except Exception as e:                                  # noqa: BLE001
            return f"(không dựng được bản xem trước: {e})"

    def pf_cua_buoc(self, rid: int, no: int) -> tuple[str | None, str]:
        """Pre-flight nao ap cho buoc nay, tren doi tuong nao."""
        run = self.lay(rid)
        s = buoc_cua(run["kind"], no)
        if s.pf is None:
            return None, ""
        if not run["stream"]:
            # Luong CHUA duoc dang ky vao cfg.streams (NEW_STREAM suot ca
            # phien; LUONG_MOI cho den buoc DANG_KY_CAU_HINH). Pre-flight duy
            # nhat co nghia luc nay la ADD_TRANDATA tren bang nguon.
            #
            # Moc theo `stream` chu KHONG theo kind: ngay khi buoc 14 dat
            # stream, cac buoc sau cua LUONG_MOI tu dong co pre-flight that
            # (STOP/START/PARAM_EDIT/LOAD) ma khong can them nhanh nao.
            return (s.pf if s.pf == "ADD_TRANDATA" else None), run["src_table"]
        if run["stream"] not in self.cfg.streams:
            # Phien GHI mot luong ma cau hinh KHONG CON luong do - ca phien
            # LUONG_MOI ma duong don da go khoi [streams] (su co #6,
            # 2026-10-20). Truoc day dong duoi KeyError, va vi trang chi
            # tiet phien goi ham nay luc ve, CA TRANG /wizard/<id> sap 500:
            # nut "Don sach" - thu duy nhat con dung duoc - khong bao gio
            # hien ra. Tra ve mot pre-flight CHAN, khong phai mot ngoai le.
            return "LUONG_MAT", run["stream"]
        st = self.cfg.streams[run["stream"]]
        if s.pf in ("STOP", "START"):
            return s.pf, (st["extract"] if "EXTRACT" in s.name else st["replicat"])
        if s.pf == "PARAM_EDIT":
            return s.pf, (st["extract"] if "TABLE" in s.name else st["replicat"])
        return s.pf, run["src_table"]

    async def chay_buoc(self, rid: int, no: int, *, who: str, reason: str,
                        tham_so: dict | None = None) -> dict:
        """Chay mot buoc. Goi tu /action/fire sau khi da qua Guard."""
        ok, vi_sao = self.dieu_kien_vao(rid, no)
        if not ok:
            raise WizardError(f"Chưa đủ điều kiện để chạy bước này: {vi_sao}")
        run = self.lay(rid)
        s = buoc_cua(run["kind"], no)
        # Tra cuu theo KIND truoc: NEW_STREAM co ten buoc trung voi
        # ADD_TABLE (START_EXTRACT, CHO_BAT_KIP...) nhung logic khac han vi
        # luong moi CHUA nam trong cfg.streams.
        tt = TIEN_TO.get(run["kind"], "")
        fn = (getattr(self, f"_b_{tt}{s.name.lower()}", None)
              or getattr(self, f"_b_{s.name.lower()}", None))
        if fn is None:
            raise WizardError(f"bước {s.name} chưa có phần thực thi")

        self._ghi_buoc(rid, no, state="DANG", started_at=int(time.time()),
                       by_user=who, reason=reason)
        try:
            if s.name in MOI_BANG and self._la_lo(run):
                r = await self._lap_moi_bang(run, tham_so or {}, fn, s.name)
            else:
                r = await fn(run, tham_so or {})
        except Exception as e:                                  # noqa: BLE001
            msg = str(e).splitlines()[0][:400]
            self._ghi_buoc(rid, no, state="FAIL", finished_at=int(time.time()),
                           err_msg=msg)
            log.exception("quy trình #%s bước %s %s thất bại", rid, no, s.name)
            raise

        out = "\n".join(str(x) for x in (r.get("output") or []))[:60_000]

        # Buoc co the CHO MOT JOB ROI RA. Job chay ngoai tien trinh web nen
        # handler tra ve ngay; buoc giu trang thai DANG va KHONG day cur_step.
        # UI poll /wizard/<id>/job; chi `kiem_job()` moi cho PASS, va chi sau
        # khi da KIEM CHUNG TRANG THAI CUOI tren DB.
        if r.get("cho_job"):
            self._ghi_buoc(rid, no, state="DANG", output=out, err_msg=None)
            self._ghi_job_buoc(rid, no, int(r["cho_job"]))
            r["buoc"] = s.name
            r["nhan"] = s.nhan
            return r

        self._ghi_buoc(rid, no, state="PASS", finished_at=int(time.time()),
                       output=out, err_msg=None)
        # Buoc cuoi -> dong phien
        if no >= max(x.no for x in BO_BUOC[run["kind"]]):
            self._ghi_run(rid, state="DONE", finished_at=int(time.time()),
                          cur_step=no)
        else:
            self._ghi_run(rid, cur_step=no + 1)
        r["buoc"] = s.name
        r["nhan"] = s.nhan
        return r

    # =============================================================== tien ich
    def _proc(self, run: dict) -> tuple[str, str, str]:
        st = self.cfg.streams.get(run["stream"])
        if st is None:
            raise WizardError(
                f"Luồng {run['stream']!r} KHÔNG còn trong conf/app.toml - phiên này không "
                "đi tiếp được. Nếu là phiên tạo luồng mới thì chỉ còn đường dọn sạch.")
        return st["extract"], st["replicat"], st["trail"]

    # ======================================================== lo nhieu bang
    def _cac_bang(self, run: dict) -> list[str]:
        """Danh sach bang cua phien. Phien MOT bang tra ve list mot phan tu.

        Doc tu cot `src_tables`; phien tao truoc khi co cot do (hoac dang mo
        giua luc nang cap) thi suy ra tu `src_table`. Nho vay khong co phien
        nao bi ket vi thieu du lieu.
        """
        try:
            ds = json.loads(run.get("src_tables") or "[]")
        except (TypeError, ValueError):
            ds = []
        ds = [str(x).upper() for x in ds if str(x).strip()]
        if not ds and run.get("src_table"):
            ds = [str(run["src_table"]).upper()]
        return ds

    def _la_lo(self, run: dict) -> bool:
        return len(self._cac_bang(run)) > 1

    def _run_bang(self, run: dict, bang: str) -> dict:
        """Ban sao `run` voi ten bang thay bang `bang`.

        DAY LA CHO QUAN TRONG NHAT cua lo nhieu bang: nho no ma TOAN BO
        handler mot bang viet o Phase 3 dung lai duoc KHONG SUA MOT DONG.
        Handler van nhan mot `run` co dung mot `src_table`/`tgt_table`/
        `keycols`; no khong biet va khong can biet minh dang o trong mot lo.

        `ctx_json` cung duoc thay: moi bang co khoi ctx rieng trong
        ctx["bang"][ten]. Neu khong tach thi `n_src` cua bang sau se de lên
        bang truoc, va `do_song_song()` / uoc thoi gian nap se tinh theo so
        dong cua SAI bang.
        """
        r = dict(run)
        hang = self.con.execute(
            "SELECT src_owner, tgt_owner, tgt_table, keycols FROM managed_table"
            " WHERE src_table=?", (bang,)).fetchone()
        if hang is None:
            raise WizardError(f"{bang} không còn trong danh sách quản lý")
        r["src_table"] = bang
        r["src_owner"] = hang["src_owner"]
        r["tgt_owner"] = hang["tgt_owner"]
        r["tgt_table"] = hang["tgt_table"]
        # keycols: uu tien cai da xac nhan trong ctx cua phien nay, roi moi
        # den registry. Buoc XAC_NHAN_KEYCOLS ghi vao ca hai cho.
        ctx = json.loads(run.get("ctx_json") or "{}")
        cb = (ctx.get("bang") or {}).get(bang) or {}
        r["keycols"] = cb.get("keycols") or hang["keycols"]
        r["ctx_json"] = json.dumps(cb, ensure_ascii=False)
        return r

    def _ctx_bang(self, run: dict, bang: str) -> dict:
        ctx = json.loads(run.get("ctx_json") or "{}")
        return (ctx.get("bang") or {}).get(bang) or {}

    async def _lap_moi_bang(self, run: dict, ts: dict, fn, ten_buoc: str) -> dict:
        """Chay handler MOT BANG cho tung bang cua lo, gop ket qua.

        MOT BANG FAIL LA CA BUOC FAIL. Khong co che "thanh cong mot phan":
        neu bang thu 5 trong 8 bang khong them duoc dong MAP thi buoc phai
        FAIL, vi di tiep se nap du lieu cho mot bang khong co MAP - tuc la
        nap xong roi CDC khong bao gio toi, dung kieu mat du lieu im lang ma
        ca cong cu nay sinh ra de chan. Ba bang dau da lam xong thi giu
        nguyen: moi buoc deu idempotent (dong MAP da co thi bo qua), nen bam
        lai buoc sau khi sua la chay tiep tu cho do.
        """
        ds = self._cac_bang(run)
        out: list[str] = []
        msg: list[str] = []
        for i, b in enumerate(ds, 1):
            # Doc lai run moi vong: vong truoc da ghi ctx["bang"] cua no.
            rb = self._run_bang(self.lay(run["id"]), b)
            out.append(f"===== [{i}/{len(ds)}] {b} =====")
            self._lo_bang = b
            try:
                r = await fn(rb, ts)
            except WizardError as e:
                raise WizardError(
                    f"[{i}/{len(ds)}] {b}: {e}" + chr(10)
                    + f"{i - 1}/{len(ds)} bảng trước đã xong. Mọi bước đều chạy lại được an "
                      "toàn (dòng MAP đã có thì bỏ qua), nên sửa xong bấm lại bước này sẽ "
                      f"chạy tiếp từ {b} chứ không làm lại các bảng đã xong.") from e
            finally:
                self._lo_bang = None
            out.extend(str(x) for x in (r.get("output") or []))
            if r.get("cho_job"):
                # Khong duoc xay ra: buoc chay qua job KHONG nam trong
                # MOI_BANG. Bat o day de neu ve sau ai them mot buoc co job
                # vao MOI_BANG thi no vo NGAY, chu khong am tham bo roi job
                # thu hai tro di - loai loi se khong ai thay.
                raise WizardError(
                    f"Lỗi thiết kế: bước {ten_buoc} tạo việc nền #{r['cho_job']} nhưng lại "
                    "nằm trong nhóm chạy theo từng bảng. Bước chạy qua việc nền phải sinh "
                    "MỘT script bao cả lô, không được lặp theo bảng.")
            msg.append(f"{b}: {r.get('msg') or 'xong'}")
        return {"msg": f"{len(ds)} bảng | " + " | ".join(msg), "output": out}

    def _luu_script(self, ten: str, noi_dung: str) -> str:
        """MOI script sinh ra deu luu xuong scripts/.

        Cong cu khong duoc la hop den: DBA phai soi lai duoc, va phai chay
        tay duoc neu muon.
        """
        self.cfg.scripts_dir.mkdir(parents=True, exist_ok=True)
        p = self.cfg.scripts_dir / ten
        p.write_text(noi_dung, encoding="utf-8")
        return str(p)

    # =================================================================
    # XEM TRUOC - khong thay doi gi, chi dung ra lenh/SQL se chay
    # =================================================================
    async def _xt_them_map(self, run: dict) -> str:
        _, rep, _ = self._proc(run)
        cur = paramsmod.read(self.cfg, rep)
        moi = paramsmod.them_dong_map(
            cur.text, run["src_owner"], run["src_table"],
            run["tgt_owner"], run["tgt_table"], run["keycols"], True)
        return paramsmod.diff(cur.text, moi, name=f"{rep.lower()}.prm")

    async def _xt_them_table(self, run: dict) -> str:
        et, _, _ = self._proc(run)
        cur = paramsmod.read(self.cfg, et)
        moi = paramsmod.them_dong_table(cur.text, run["src_owner"], run["src_table"])
        return paramsmod.diff(cur.text, moi, name=f"{et.lower()}.prm")

    async def _xt_them_handlecollisions(self, run: dict) -> str:
        _, rep, _ = self._proc(run)
        cur = paramsmod.read(self.cfg, rep)
        moi = self._them_hc(cur.text, run)
        return paramsmod.diff(cur.text, moi, name=f"{rep.lower()}.prm")

    async def _xt_bo_handlecollisions(self, run: dict) -> str:
        _, rep, _ = self._proc(run)
        cur = paramsmod.read(self.cfg, rep)
        moi = paramsmod.bo_handlecollisions(cur.text, run["src_owner"],
                                            run["src_table"])
        return paramsmod.diff(cur.text, moi, name=f"{rep.lower()}.prm")

    async def _xt_add_trandata(self, run: dict) -> str:
        return (f"dblogin useridalias {gg.ALIAS_SOURCE}\n"
                f"add trandata {run['src_owner']}.{run['src_table']}")

    async def _xt_stop_replicat(self, run: dict) -> str:
        _, rep, _ = self._proc(run)
        return f"stop replicat {rep}"

    _xt_stop_replicat_2 = _xt_stop_replicat

    async def _xt_start_replicat(self, run: dict) -> str:
        _, rep, _ = self._proc(run)
        return f"start replicat {rep}"

    _xt_start_replicat_2 = _xt_start_replicat
    _xt_start_replicat_3 = _xt_start_replicat

    async def _xt_stop_extract(self, run: dict) -> str:
        et, _, _ = self._proc(run)
        return f"stop extract {et}"

    async def _xt_start_extract(self, run: dict) -> str:
        et, _, _ = self._proc(run)
        return f"start extract {et}"

    async def _xt_truncate(self, run: dict) -> str:
        return (f'TRUNCATE TABLE {run["tgt_owner"]}."{run["tgt_table"]}";\n'
                f'-- so dong truoc khi xoa: {run["rows_before_truncate"]}')

    async def _xt_nap(self, run: dict) -> str:
        return self._sql_nap(run)

    _xt_sinh_script_nap = _xt_nap

    async def _xt_gather_stats(self, run: dict) -> str:
        return self._sql_stats(run)

    # =================================================================
    # BUOC 1 - KHAO SAT
    # =================================================================
    async def _b_khao_sat(self, run: dict, ts: dict) -> dict:
        o, t = run["src_owner"], run["src_table"]
        out: list[str] = []

        n_src = await self.ora.scalar(
            f'SELECT COUNT(*) FROM {SCHEMA_SOURCE}."{check_ident(t)}"'
            f'@DBL_PMNV')
        out.append(f"số dòng ở NGUỒN: {n_src:,}")

        n_tgt = None
        try:
            n_tgt = await self.ora.scalar(
                f'SELECT COUNT(*) FROM {SCHEMA_TARGET}."{check_ident(run["tgt_table"])}"')
            out.append(f"số dòng ở ĐÍCH : {n_tgt:,}")
        except Exception as e:                                  # noqa: BLE001
            out.append(f"số dòng ở ĐÍCH : không đọc được ({str(e)[:70]})")

        # PK that o nguon
        pk = await self.ora.rows(
            "SELECT c.constraint_name, cc.column_name, cc.position"
            " FROM dba_constraints@DBL_PMNV c"
            " JOIN dba_cons_columns@DBL_PMNV cc"
            "   ON cc.owner=c.owner AND cc.constraint_name=c.constraint_name"
            " WHERE c.owner=:o AND c.table_name=:t AND c.constraint_type='P'"
            " ORDER BY cc.position", o=o, t=t)
        if pk:
            cols = ", ".join(r["column_name"] for r in pk)
            out.append(f"khoá chính thật: {pk[0]['constraint_name']} ({cols})")
        else:
            out.append("khoá chính thật: KHÔNG CÓ -> phải nhập cột khoá ở bước xác nhận cột khoá")

        # Unique index - ke ca function-based (GG KHONG dung duoc loai nay)
        idx = await self.ora.rows(
            "SELECT i.index_name, i.uniqueness, i.index_type,"
            "       LISTAGG(ic.column_name, ', ')"
            "         WITHIN GROUP (ORDER BY ic.column_position) cols"
            "  FROM dba_indexes@DBL_PMNV i"
            "  JOIN dba_ind_columns@DBL_PMNV ic"
            "    ON ic.index_owner=i.owner AND ic.index_name=i.index_name"
            " WHERE i.table_owner=:o AND i.table_name=:t"
            " GROUP BY i.index_name, i.uniqueness, i.index_type"
            " ORDER BY i.uniqueness DESC, i.index_name", o=o, t=t)
        for r in idx:
            ghi = ""
            if "FUNCTION" in (r["index_type"] or ""):
                ghi = ("  <- index theo biểu thức: GoldenGate KHÔNG dùng được làm khoá, phải "
                       "khai cột khoá tường minh")
            out.append(f"index {r['index_name']} {r['uniqueness']} ({r['cols']}){ghi}")
        if not any(r["uniqueness"] == "UNIQUE" for r in idx):
            out.append("KHÔNG có index duy nhất -> mỗi lệnh cập nhật của Replicat phải quét "
                       "toàn bảng. Đây là vấn đề hiệu năng thật, không chỉ là vấn đề khoá.")

        # Cot nullable - cot khoa chua NULL la nguon goc cua ORA-01403 im lang
        nul = await self.ora.rows(
            "SELECT column_name, nullable, data_type FROM dba_tab_columns@DBL_PMNV"
            " WHERE owner=:o AND table_name=:t ORDER BY column_id", o=o, t=t)
        n_null = [r["column_name"] for r in nul if r["nullable"] == "Y"]
        out.append(f"số cột: {len(nul)}, trong đó {len(n_null)} cột cho phép rỗng")

        part = await self.ora.one(
            "SELECT partitioning_type, interval FROM dba_part_tables@DBL_PMNV"
            " WHERE owner=:o AND table_name=:t", o=o, t=t)
        if part:
            out.append(f"phân vùng ở nguồn: {part['partitioning_type']}"
                       + (f", INTERVAL {part['interval']}" if part["interval"] else ""))
            if part["interval"]:
                out.append("CẢNH BÁO: nguồn chia phân vùng theo khoảng tự sinh. Bảng đích phải có "
                           "phân vùng hứng, nếu không sẽ lỗi ORA-14400.")
        else:
            out.append("phân vùng ở nguồn: không có")

        # Khong PK -> lay unique index THUONG it cot nhat lam khoa (quyet dinh
        # nguoi dung 2026-10-23, lo PMNV3: "chi co unique thi dung luon cac cot
        # danh unique lam KEYCOLS"). Index theo bieu thuc bi loai: KEYCOLS chi
        # nhan ten cot. Cung la index GG tu chon neu khong khai KEYCOLS.
        ui = sorted((r for r in idx if r["uniqueness"] == "UNIQUE"
                     and "FUNCTION" not in (r["index_type"] or "")),
                    key=lambda r: (len(r["cols"].split(",")), r["index_name"]))
        ui_cols = [c.strip() for c in ui[0]["cols"].split(",")] if ui and not pk else []
        if ui_cols:
            out.append(f"không có khoá chính -> cột khoá sẽ lấy từ index duy nhất "
                       f"{ui[0]['index_name']} ({', '.join(ui_cols)})")

        ctx = json.loads(run["ctx_json"] or "{}")
        ctx.update(n_src=n_src, n_tgt=n_tgt, co_pk=bool(pk),
                   pk_cols=[r["column_name"] for r in pk],
                   pk_name=(pk[0]["constraint_name"] if pk else None),
                   ui_cols=ui_cols,
                   ui_name=(ui[0]["index_name"] if ui_cols else None),
                   cot_nullable=n_null,
                   co_unique=any(r["uniqueness"] == "UNIQUE" for r in idx))
        kw = dict(ctx_json=json.dumps(ctx, ensure_ascii=False))
        # RESYNC: ghi so dong cu TRUOC khi xoa
        if run["kind"] == "RESYNC_TABLE" and n_tgt is not None:
            kw["rows_before_truncate"] = n_tgt
            out.append(f"đã ghi lại số dòng cũ = {n_tgt:,}")
        self._ghi_run(run["id"], **kw)
        return {"msg": f"Khảo sát xong: nguồn {n_src:,} dòng", "output": out}

    # =================================================================
    # BUOC 2 - XAC NHAN KEYCOLS (kiem TREN DU LIEU THAT)
    # =================================================================
    async def _b_xac_nhan_keycols(self, run: dict, ts: dict) -> dict:
        kc = (ts.get("keycols") or run["keycols"] or "").strip()
        ctx0 = json.loads(run["ctx_json"] or "{}")
        tu_pk = tu_ui = False
        if not kc:
            # BANG CO PK THAT: suy KEYCOLS tu PK, khong bat go tay.
            #
            # Du an khai KEYCOLS TUONG MINH cho moi bang - co y, vi de
            # GoldenGate tu suy la de no di theo thu tu PK -> unique index ->
            # TOAN BO COT, va buoc cuoi do thi moi UPDATE cua Replicat la
            # full scan ma khong co gi bao. Nhung "tuong minh" khong dong
            # nghia voi "go tay": voi bang co PK that thi PK CHINH LA cau tra
            # loi dung, con go lai no bang tay chi them mot co hoi go sai.
            # Lo 8 bang CN_*/XD_/XL_ deu co PK that - bat go tay 8 lan la 8
            # dip go sai ma khong doi lay them mot kiem chung nao.
            #
            # Tinh duy nhat VAN duoc kiem tren DU LIEU THAT ngay duoi day:
            # o nguon PK co the dang DISABLE hoac NOVALIDATE, nen khong duoc
            # tin vao metadata.
            if ctx0.get("co_pk") and ctx0.get("pk_cols"):
                kc = ", ".join(ctx0["pk_cols"])
                tu_pk = True
            elif ctx0.get("ui_cols"):
                # Khong PK nhung co unique index thuong: dung cot cua index do.
                # Kiem duy nhat + dem NULL tren du lieu that ben duoi VAN chay.
                kc = ", ".join(ctx0["ui_cols"])
                tu_ui = True
        if not kc:
            raise WizardError(
                "Phải nhập cột khoá."
                + (" Chạy bước khảo sát trước đã - công cụ chưa biết bảng này có khoá chính "
                   "hay không." if not ctx0 else
                   " Bảng này KHÔNG có khoá chính thật nên công cụ không có gì để suy ra "
                   "khoá.")
                + " Khi không khai cột khoá, GoldenGate tự suy theo thứ tự: khoá chính -> "
                  "index duy nhất -> TOÀN BỘ CỘT; và index theo biểu thức thì nó không "
                  "dùng được.")
        cols = [c.strip().upper() for c in kc.split(",") if c.strip()]
        for c in cols:
            check_ident(c)

        o, t = run["src_owner"], run["src_table"]
        hop_le = {r["column_name"] for r in await self.ora.rows(
            "SELECT column_name FROM dba_tab_columns@DBL_PMNV"
            " WHERE owner=:o AND table_name=:t", o=o, t=t)}
        thieu = [c for c in cols if c not in hop_le]
        if thieu:
            raise WizardError(f"Cột không tồn tại ở nguồn: {', '.join(thieu)}")

        out: list[str] = []
        if tu_pk:
            out.append("cột khoá suy từ khoá chính thật: %s (%s)"
                       % (", ".join(cols),
                          ctx0.get("pk_name") or "PK cua bang"))
            out.append("Vẫn kiểm tính duy nhất trên DỮ LIỆU THẬT ngay dưới - ở nguồn khoá "
                       "chính có thể đang bị tắt hoặc chưa được kiểm chứng.")
        if tu_ui:
            out.append("cột khoá suy từ index duy nhất (bảng không có khoá chính): %s (%s)"
                       % (", ".join(cols), ctx0.get("ui_name") or "unique index"))
            out.append("Vẫn kiểm tính duy nhất và đếm giá trị rỗng trên DỮ LIỆU THẬT ngay "
                       "dưới - index duy nhất không chặn dòng có cột khoá rỗng.")
        dsc = ", ".join(f'"{c}"' for c in cols)

        # KIEM TREN DU LIEU THAT, khong tin vao metadata.
        #
        # KHONG dung `COUNT(DISTINCT a, b)`: Oracle chi nhan MOT bieu thuc
        # trong COUNT(DISTINCT ...) va tra ORA-00909 invalid number of
        # arguments. Loi nay nam im trong cong cu den 2026-10-14 vi bang duy
        # nhat tung chay qua buoc nay (HT_MA_KVUC) co PK MOT cot; moi bang
        # khoa nhieu cot deu se vo. Lo CN_* co PK 2-3 cot nen no lo ra ngay.
        #
        # Cung khong noi cot bang `||`: hai bo khoa khac nhau co the cho cung
        # mot chuoi ('A' || 'BC' = 'AB' || 'C'), tuc la bao trung khoa OAN.
        # GROUP BY thi dung theo dung nghia so sanh cua Oracle, mot lan quet,
        # va con cho biet to hop nao trung nang nhat.
        r = await self.ora.one(
            f"SELECT SUM(c) tong, COUNT(*) khac, MAX(c) trung_nhat FROM ("
            f'SELECT COUNT(*) c FROM {SCHEMA_SOURCE}."{check_ident(t)}"'
            f"@DBL_PMNV GROUP BY {dsc})")
        tong = int(r["tong"] or 0)
        khac = int(r["khac"] or 0)
        trung_nhat = int(r["trung_nhat"] or 0)
        out.append(f"trên dữ liệu thật: {tong:,} dòng -> {khac:,} tổ hợp khoá khác nhau")
        if tong != khac:
            raise WizardError(
                f"Cột khoá ({kc}) KHÔNG duy nhất trên dữ liệu thật: {tong:,} dòng nhưng "
                f"chỉ {khac:,} tổ hợp khác nhau -> {tong - khac:,} dòng trùng khoá, tổ "
                f"hợp trùng nặng nhất có {trung_nhat:,} dòng. Replicat sẽ áp sai dòng. "
                "Chọn thêm cột.")
        out.append("cột khoá DUY NHẤT trên dữ liệu thật")

        # Dem NULL tung cot khoa. NULL trong cot khoa -> `WHERE col = NULL`
        # khong bao gio dung -> ORA-01403 -> REPERROR (1403, DISCARD) bo IM LANG.
        canh_bao = []
        for c in cols:
            n = await self.ora.scalar(
                f'SELECT COUNT(*) FROM {SCHEMA_SOURCE}."{check_ident(t)}"'
                f'@DBL_PMNV WHERE "{c}" IS NULL')
            if n:
                canh_bao.append(f"{c}={n:,}")
            out.append(f"  {c}: {n:,} dòng rỗng")
        if canh_bao:
            out.append("CẢNH BÁO: cột khoá có giá trị rỗng (" + ", ".join(canh_bao)
                       + "). Replicat dùng các cột khoá làm điều kiện tìm dòng; một cột khoá "
                         "rỗng thì điều kiện `cột = NULL` KHÔNG BAO GIỜ đúng, Replicat không tìm "
                         "thấy dòng và bản ghi bị bỏ IM LẶNG vào file discard. Theo dõi file "
                         "discard sau khi chạy.")

        ctx = json.loads(run["ctx_json"] or "{}")
        ctx["keycols_null"] = canh_bao
        self._ghi_run(run["id"], keycols=", ".join(cols),
                      ctx_json=json.dumps(ctx, ensure_ascii=False))
        self.con.execute("UPDATE managed_table SET keycols=?, updated_at=?"
                         " WHERE src_table=?",
                         (", ".join(cols), int(time.time()), t))
        return {"msg": f"Đã xác nhận cột khoá: {', '.join(cols)}", "output": out}

    # =================================================================
    # BUOC 3 - KIEM BANG DICH
    # =================================================================
    async def _b_kiem_bang_dich(self, run: dict, ts: dict) -> dict:
        o, t = run["src_owner"], run["src_table"]
        tt = run["tgt_table"]
        out: list[str] = []
        n = await self.ora.scalar(
            "SELECT COUNT(*) FROM dba_tables WHERE owner=:o AND table_name=:t",
            o=run["tgt_owner"], t=tt)
        if not n:
            raise WizardError(
                f'Bảng đích {run["tgt_owner"]}."{tt}" KHÔNG tồn tại. Tạo bảng đích trước '
                "(xem gen_target_ddl.py), rồi quay lại bước này.")
        out.append(f'bảng đích {run["tgt_owner"]}."{tt}" tồn tại')
        if run.get("kind") in KIND_LOG:
            out += await self._kiem_bang_log(run, tt)

        if run.get("kind") == "LUONG_MOI":
            # BANG DICH PHAI RONG (su co #6, 2026-10-20). Script nap cua
            # LUONG_MOI chi `INSERT /*+ APPEND */`, KHONG xoa truoc. Chay lai
            # sau mot lan hong ma bang con du lieu cua lan truoc la NHAN DOI
            # moi dong - va khong co chot nao phia sau bat duoc, vi doi
            # chieu lan dau cua luong moi so voi chinh so dong vua nap.
            # Dat o day: TRUOC khi bat TRANDATA va dang ky capture vao DB
            # nguon. Cong cu CO Y khong tu TRUNCATE - xoa du lieu la viec
            # nguoi quyet.
            n_dong = await self.ora.scalar(
                f'SELECT COUNT(*) FROM {SCHEMA_TARGET}."{check_ident(tt)}"')
            if n_dong:
                raise WizardError(
                    f'Bảng đích {SCHEMA_TARGET}."{tt}" đang có {int(n_dong):,} dòng. Script '
                    "nạp của luồng mới chỉ INSERT, không xoá trước - nạp lên là NHÂN ĐÔI dữ "
                    "liệu. Nếu đó là dữ liệu của một lần chạy hỏng trước, chạy tay "
                    f'TRUNCATE TABLE {SCHEMA_TARGET}."{tt}" rồi chạy lại. Công cụ cố ý không '
                    "tự xoá.")
            out.append("bảng đích rỗng - nạp lần đầu không nhân đôi")

        src = await self.ora.rows(
            "SELECT column_name, data_type FROM dba_tab_columns@DBL_PMNV"
            " WHERE owner=:o AND table_name=:t ORDER BY column_id", o=o, t=t)
        tgt = await self.ora.rows(
            "SELECT column_name, data_type FROM dba_tab_columns"
            " WHERE owner=:o AND table_name=:t ORDER BY column_id",
            o=run["tgt_owner"], t=tt)
        sc = {r["column_name"] for r in src}
        tc = {r["column_name"] for r in tgt}
        out.append(f"cột ở nguồn {len(sc)}, cột ở đích {len(tc)}")
        thieu = sorted(sc - tc)
        them = sorted(tc - sc)
        if thieu:
            raise WizardError(
                f"Bảng đích THIẾU {len(thieu)} cột có ở nguồn: {', '.join(thieu[:8])}. "
                "Cách ánh xạ cột theo tên sẽ bỏ qua chúng IM LẶNG - dữ liệu của các cột "
                "đó không bao giờ tới đích.")
        if them:
            out.append(f"đích có thừa {len(them)} cột (không sao): {', '.join(them[:8])}")
        # Kieu du lieu lech
        st = {r["column_name"]: r["data_type"] for r in src}
        lech = [f"{c}: nguồn {st[c]} / đích {r['data_type']}"
                for r in tgt if (c := r["column_name"]) in st
                and st[c] != r["data_type"]]
        if lech:
            out.append(f"CẢNH BÁO {len(lech)} cột lệch kiểu dữ liệu: " + "; ".join(lech[:6]))
        else:
            out.append("kiểu dữ liệu khớp hết")

        # ============================================================
        # QUYEN TREN BANG DICH - chan cam bay so 10 o CA HAI CHIEU
        #
        # Lan chay that dau tien (HT_MA_KVUC): DDL do chinh cong cu sinh ra
        # chi grant cho GGADM_LOAD, nen nap 3.355 dong THANH CONG roi buoc
        # kiem chung FAIL voi ORA-00942 vi no doc bang GGADM_RO.
        # Chieu nguoc lai gap o lo CN_* thang 10/2026: bang dich do NGUOI
        # KHAC tao tu truoc (09/10), chi co GGADM_RO SELECT, GGADM_LOAD
        # KHONG co quyen gi. Buoc nay truoc day cho PASS, va mai den buoc
        # NAP - sau khi DA dung Replicat va DA mark index UNUSABLE - moi vo
        # vi thieu INSERT. Tuc la lo ra o dung cho dat nhat.
        #
        # BO QUYEN BAT BUOC o day phai khop DUNG nhung gi script THAT lam,
        # khong phai nhung gi minh nho la no lam. Lan dau viet chot nay toi
        # chi kiem INSERT va SELECT cua tai khoan doc - thieu
        # GGADM_LOAD.SELECT. Ket qua: buoc 15 cua quy trinh #4 vo voi
        # ORA-01031 ngay tai LOP CHAN 1 (`SELECT 1 FROM <dich>` de kiem bang
        # rong), tuc la chot dung de chan cai nay lai cho no di qua.
        #
        #   GGADM_LOAD.INSERT  -> lenh nap
        #   GGADM_LOAD.SELECT  -> LOP CHAN 1 cua script nap doc bang dich,
        #                         va buoc kiem chung dem lai so dong
        #   GGADM_RO.SELECT    -> buoc doi chieu nguon/dich
        #
        # Quyen INSERT cua GGADM_LOAD con la cai dinh nghia PHAM VI cua
        # package ODS.GGADM_API (`ten_hop_le()` doc user_tab_privs_made),
        # nen thieu no thi ca buoc index va stats cung khong chay duoc.
        # ============================================================
        qu = {r["grantee"] + "." + r["privilege"] for r in await self.ora.rows(
            "SELECT grantee, privilege FROM dba_tab_privs"
            " WHERE owner=:o AND table_name=:t"
            " AND grantee IN ('GGADM_LOAD','GGADM_RO')",
            o=run["tgt_owner"], t=tt)}
        thieu_q = [c for c in QUYEN_BANG_DICH if c not in qu]
        if thieu_q:
            lenh = chr(10).join(
                "  GRANT %s ON %s.%s TO %s;"
                % (c.split(".")[1], run["tgt_owner"], tt, c.split(".")[0])
                for c in thieu_q)
            raise WizardError(
                "Thiếu %d quyền trên bảng đích %s.%s: %s"
                % (len(thieu_q), run["tgt_owner"], tt, ", ".join(thieu_q))
                + chr(10)
                + "Tài khoản nạp cần cả quyền GHI (để nạp) và quyền ĐỌC (lớp "
                  "chặn thứ nhất của script nạp đọc bảng đích để chắc nó đang "
                  "rỗng, và bước kiểm chứng đếm lại số dòng). Tài khoản đọc cần "
                  "quyền ĐỌC để đối chiếu nguồn với đích. Thiếu quyền ghi còn "
                  "làm bảng này NGOÀI phạm vi của gói ODS.GGADM_API, nên bước "
                  "xử lý index và cập nhật thống kê cũng sẽ không chạy được."
                + chr(10)
                + "Chạy bằng tài khoản chủ schema ODS hoặc sysdba:" + chr(10) + lenh + chr(10)
                + "rồi bấm lại bước này.")
        out.append("quyền: đủ cả %d quyền cần thiết (%s)"
                   % (len(QUYEN_BANG_DICH), ", ".join(QUYEN_BANG_DICH)))
        return {"msg": f"Bảng đích hợp lệ ({len(tc)} cột), quyền đủ",
                "output": out}

    # =================================================================
    # BUOC 4 - ADD TRANDATA
    # =================================================================
    async def _b_add_trandata(self, run: dict, ts: dict) -> dict:
        if run["kind"] in KIND_LOG:
            return await self._add_trandata_log(run)
        o, t = run["src_owner"], run["src_table"]
        da, lines0 = await gg.info_trandata(self.cfg, o, t)
        if da:
            return {"msg": f"TRANDATA trên {o}.{t} ĐÃ bật từ trước - không làm lại",
                    "output": lines0}
        ok, lines = await gg.add_trandata(self.cfg, o, t)
        sau, lines2 = await gg.info_trandata(self.cfg, o, t)
        if not sau:
            raise WizardError(
                "Đã chạy lệnh bật TRANDATA nhưng kiểm lại vẫn báo chưa bật. Lưu ý: dòng "
                "cần đọc là `Logging of supplemental redo log data is enabled`, KHÔNG "
                "phải OGG-26035.")
        self.con.execute("UPDATE managed_table SET prepared=1, updated_at=?"
                         " WHERE src_table=?", (int(time.time()), t))
        return {"msg": f"Đã bật TRANDATA cho {o}.{t}",
                "output": lines + ["--- kiem chung ---"] + lines2}

    # =================================================================
    # BUOC stop/start - thu tu la hang so trong danh sach buoc
    # =================================================================
    async def _b_stop_replicat(self, run: dict, ts: dict) -> dict:
        _, rep, _ = self._proc(run)
        r = await self.control.stop(rep)
        if not r["ok"]:
            raise WizardError(f"{rep} không dừng được, đang {r['status']}")
        return {"msg": f"{rep} đã dừng", "output": r["lines"]}

    _b_stop_replicat_2 = _b_stop_replicat

    async def _b_start_replicat(self, run: dict, ts: dict) -> dict:
        _, rep, _ = self._proc(run)
        r = await self.control.start(rep)
        if not r["ok"]:
            raise WizardError(f"{rep} không chạy được, đang {r['status']}")
        return {"msg": f"{rep} đã chạy", "output": r["lines"]}

    _b_start_replicat_2 = _b_start_replicat
    # LUONG_MOI chay Replicat ba lan trong mot luot (lan dau sau khi nap,
    # roi dung de bo chong trung, roi chay lai) nen can them mot ten nua.
    # Ten buoc KHONG duoc la START_REPLICAT: tien to "ns_" se lam no dinh
    # vao `_b_ns_start_replicat` (goi thang gg.start_process) thay vi handler
    # nay (di qua control, co ghi su kien).
    _b_start_replicat_3 = _b_start_replicat

    async def _b_stop_extract(self, run: dict, ts: dict) -> dict:
        et, _, _ = self._proc(run)
        r = await self.control.stop(et)
        if not r["ok"]:
            raise WizardError(f"{et} không dừng được, đang {r['status']}")
        return {"msg": f"{et} đã dừng", "output": r["lines"]}

    async def _b_start_extract(self, run: dict, ts: dict) -> dict:
        et, rep, _ = self._proc(run)
        # CHOT: khong start Extract khi so dong TABLE vuot so dong MAP.
        n_tab = gg.count_param_entries(self.cfg, et, "TABLE")
        n_map = gg.count_param_entries(self.cfg, rep, "MAP")
        if n_tab > n_map:
            raise WizardError(
                f"{et} có {n_tab} dòng TABLE mà {rep} chỉ có {n_map} dòng MAP. Chạy lúc "
                f"này sẽ làm {n_tab - n_map} bảng bị Replicat BỎ QUA trong khi mốc "
                "checkpoint VẪN DỜI: mất dữ liệu im lặng.")
        r = await self.control.start(et)
        if not r["ok"]:
            raise WizardError(f"{et} không chạy được, đang {r['status']}")
        return {"msg": f"{et} đã chạy (đã nạp tham số mới, TABLE={n_tab})",
                "output": r["lines"]}

    # =================================================================
    # LUONG LOG - nhanh rieng trong cac buoc dung chung
    # =================================================================
    @staticmethod
    def _bang_qly(run: dict) -> str:
        """Danh sach quan ly cua phien: managed_log cho quy trinh log."""
        return "managed_log" if run["kind"] in KIND_LOG else "managed_table"

    async def _dang_ky_log(self, run: dict) -> dict:
        """Dua bang vao danh sach LOG. Dung cho LUONG_LOG (luong chua co ->
        stream rong) va THEM_LOG (gan luong cua phien)."""
        tb = check_ident((run["src_table"] or "").upper())
        tgt = check_ident(run["tgt_table"])
        if not await self.ora.scalar(
                "SELECT COUNT(*) FROM dba_tables@DBL_PMNV"
                " WHERE owner=:o AND table_name=:t", o=SCHEMA_SOURCE, t=tb):
            raise WizardError(
                f"{SCHEMA_SOURCE}.{tb} KHÔNG tồn tại ở nguồn. Danh sách chỉ nhận bảng có "
                "thật ở nguồn - đó là chốt chặn cuối chống SQL injection.")
        n_tgt = await self.ora.scalar(
            "SELECT COUNT(*) FROM dba_tables WHERE owner=:o AND table_name=:t",
            o=SCHEMA_TARGET, t=tgt)
        out = [f"nguồn {SCHEMA_SOURCE}.{tb}: tồn tại",
               f"đích {SCHEMA_TARGET}.{tgt}: "
               + ("đã có" if n_tgt else "chưa có - bước tạo bảng _LOG sẽ tạo")]
        thuong = self.con.execute(
            "SELECT stream FROM managed_table WHERE src_table=? AND has_map=1",
            (tb,)).fetchone()
        if thuong:
            out.append(f"bảng này CŨNG đang đồng bộ thường ở luồng {thuong['stream']} "
                       f"(ODS.{tb}) - hai việc độc lập, không ảnh hưởng nhau")
        lu = run["stream"]
        cu = self.con.execute(
            "SELECT stream, in_extract, has_map FROM managed_log WHERE src_table=?",
            (tb,)).fetchone()
        if cu is None:
            self.con.execute(
                "INSERT INTO managed_log(src_owner,src_table,tgt_owner,tgt_table,"
                "stream,note,updated_at) VALUES (?,?,?,?,?,?,?)",
                (SCHEMA_SOURCE, tb, SCHEMA_TARGET, tgt, lu,
                 f"đăng ký bởi quy trình log #{run['id']}", int(time.time())))
            out.append("đã thêm vào danh sách log" + (f", luồng {lu}" if lu else
                                                       " (luồng gắn ở bước đăng ký cấu hình)"))
            msg = f"Đã đăng ký {tb} vào danh sách log"
        elif cu["stream"] and lu and cu["stream"] != lu:
            raise WizardError(f"{tb}: danh sách log ghi bảng này thuộc luồng log "
                              f"{cu['stream']}, không phải {lu}.")
        elif run["kind"] == "LUONG_LOG" and (cu["has_map"] or cu["in_extract"]):
            raise WizardError(f"{tb} đã có trong luồng log {cu['stream']} - bảng đầu tiên "
                              "của luồng log mới phải chưa thuộc luồng log nào.")
        else:
            if lu and not cu["stream"]:
                self.con.execute("UPDATE managed_log SET stream=?, updated_at=?"
                                 " WHERE src_table=?", (lu, int(time.time()), tb))
            out.append("đã có trong danh sách log - không ghi lại")
            msg = f"{tb} đã có trong danh sách log"
        return {"msg": msg, "output": out}

    async def _co_allcols(self, t: str) -> bool:
        """Nguon co nhom log ALL COLUMN LOGGING cho bang? Doc TU DIEN DB, khong
        tin vao chu GGSCI in ra."""
        return bool(await self.ora.scalar(
            "SELECT COUNT(*) FROM dba_log_groups@DBL_PMNV WHERE owner=:o"
            " AND table_name=:t AND log_group_type='ALL COLUMN LOGGING'",
            o=SCHEMA_SOURCE, t=check_ident(t)))

    async def _add_trandata_log(self, run: dict) -> dict:
        o, t = run["src_owner"], run["src_table"]
        if await self._co_allcols(t):
            self.con.execute("UPDATE managed_log SET prepared=1, updated_at=?"
                             " WHERE src_table=?", (int(time.time()), t))
            return {"msg": f"{o}.{t} ĐÃ có nhóm log ALL COLUMN LOGGING - không làm lại",
                    "output": ["dba_log_groups: ALL COLUMN LOGGING = có"]}
        ok, lines = await gg.add_trandata(self.cfg, o, t, allcols=True)
        if not await self._co_allcols(t):
            raise WizardError(
                f"Đã chạy `add trandata {o}.{t} allcols` nhưng DB nguồn VẪN KHÔNG có nhóm "
                "log ALL COLUMN LOGGING (hay gặp khi bảng đã bật TRANDATA từ trước cho "
                "luồng thường). Công cụ KHÔNG tắt TRANDATA cũ - làm vậy là hỏng luồng "
                "thường đang chạy. Nhờ DBA nguồn chạy lệnh dưới đây (chỉ CỘNG THÊM một "
                "nhóm log, không gỡ gì) rồi bấm lại bước này:\n"
                f"  ALTER TABLE {o}.{t} ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;")
        self.con.execute("UPDATE managed_log SET prepared=1, updated_at=?"
                         " WHERE src_table=?", (int(time.time()), t))
        return {"msg": f"Đã bật TRANDATA ALLCOLS cho {o}.{t} (dba_log_groups: ALL COLUMN "
                       "LOGGING = có)", "output": lines}

    async def _kiem_bang_log(self, run: dict, tt: str) -> list[str]:
        """Bang _LOG: du 3 cot log, KHONG khoa chinh / duy nhat, cot nguon NULL."""
        from .ddl import COT_LOG
        o = run["tgt_owner"]
        cot = {r["column_name"]: r for r in await self.ora.rows(
            "SELECT column_name, nullable FROM dba_tab_columns"
            " WHERE owner=:o AND table_name=:t", o=o, t=tt)}
        thieu = [c for c, _, _ in COT_LOG if c not in cot]
        if thieu:
            raise WizardError(f'Bảng log {o}."{tt}" thiếu cột: {", ".join(thieu)}.')
        n_k = await self.ora.scalar(
            "SELECT COUNT(*) FROM dba_constraints WHERE owner=:o AND table_name=:t"
            " AND constraint_type IN ('P','U')", o=o, t=tt)
        n_i = await self.ora.scalar(
            "SELECT COUNT(*) FROM dba_indexes WHERE table_owner=:o AND table_name=:t"
            " AND uniqueness='UNIQUE'", o=o, t=tt)
        if n_k or n_i:
            raise WizardError(
                f'Bảng log {o}."{tt}" có {n_k} ràng buộc khoá chính/duy nhất và {n_i} index '
                "duy nhất. Bảng log ghi CÙNG một khoá nhiều lần - Replicat sẽ ABEND ngay "
                "lần thứ hai dòng đó bị sửa. Bỏ các khoá đó rồi bấm lại.")
        nn = sorted(c for c, r in cot.items() if r["nullable"] == "N" and c != "LOG_TIME")
        if nn:
            raise WizardError(
                f'Bảng log {o}."{tt}" có cột NOT NULL: {", ".join(nn[:8])}. Bỏ NOT NULL - '
                "một bản ghi thiếu giá trị ở cột đó sẽ làm Replicat ABEND.")
        return ["bảng log: đủ LOG_TYPE / COMMIT_TS / LOG_TIME, không khoá chính/duy "
                "nhất, các cột nguồn cho phép NULL"]

    async def _b_kiem_chung_log(self, run: dict, ts: dict) -> dict:
        et, rep, _ = self._proc(run)
        out = []
        for proc in (et, rep):
            st = await gg.status_of(self.cfg, proc)
            if not st or not st.is_running:
                raise WizardError(f"{proc} không chạy: {st.status if st else 'không thấy'}")
            out.append(f"{proc} RUNNING chậm {st.lag_s}s")
        if not self._co_dong(et, run, "TABLE") or not self._co_dong(rep, run, "MAP"):
            raise WizardError("Thiếu dòng TABLE hoặc MAP của bảng trong file tham số.")
        dong = [l for l in paramsmod.read(self.cfg, rep).text.splitlines()
                if paramsmod._re_map(run["src_owner"], run["src_table"]).match(l)]
        if not dong or "INSERTALLRECORDS" not in dong[0].upper():
            raise WizardError("Dòng MAP của bảng không có INSERTALLRECORDS - đây không "
                              "phải dòng MAP của luồng log.")
        self.con.execute(
            "UPDATE managed_log SET stream=?, in_extract=1, has_map=1, note=NULL,"
            " updated_at=? WHERE src_table=?",
            (run["stream"], int(time.time()), run["src_table"]))
        out += ["dòng MAP: INSERTALLRECORDS - có",
                "", "Log bắt đầu từ lúc này: các dòng ĐÃ CÓ trong bảng nguồn trước đó "
                "không nằm trong bảng log."]
        return {"msg": f"{run['src_table']} đã ghi log vào {run['tgt_owner']}."
                       f"{run['tgt_table']} (luồng {run['stream']})", "output": out}

    # =================================================================
    # ADD_TABLE buoc 1 - dang ky (ca lo) vao danh sach quan ly
    # =================================================================
    async def _b_dang_ky_bang(self, run: dict, ts: dict) -> dict:
        """Dua MOI bang cua phien vao danh sach quan ly, gan luong cua phien.

        Yeu cau nguoi dung 2026-10-20: thao tac nhanh "Them bang" tu choi
        bang chua dang ky va bat qua trang Quy trinh - mot buoc tay thua. Giong
        `_b_ns_dang_ky_bang` cua LUONG_MOI, khac o cho luong DA CO nen gan luon.

        KHONG nam trong MOI_BANG: cac buoc moi-bang doc dong registry qua
        `_run_bang`, ma dong do chua co - buoc nay tu lap qua ca lo.
        Chot giu nguyen: bang phai co THAT o nguon; bang da thuoc luong KHAC
        thi dung. Chay lai duoc.
        """
        if run["kind"] in KIND_LOG:
            return await self._dang_ky_log(run)
        out: list[str] = []
        moi = 0
        for b in self._cac_bang(run):
            tb = check_ident(b.upper())
            if not await self.ora.scalar(
                    "SELECT COUNT(*) FROM dba_tables@DBL_PMNV"
                    " WHERE owner=:o AND table_name=:t", o=SCHEMA_SOURCE, t=tb):
                raise WizardError(
                    f"{SCHEMA_SOURCE}.{tb} KHÔNG tồn tại ở nguồn. Danh sách quản lý chỉ "
                    "nhận bảng có thật ở nguồn - đó là chốt chặn cuối chống SQL "
                    "injection, không phải một ô nhập tự do.")
            cu = self.con.execute(
                "SELECT stream FROM managed_table WHERE src_table=?", (tb,)).fetchone()
            if cu is None:
                self.con.execute(
                    "INSERT INTO managed_table(src_owner,src_table,tgt_owner,tgt_table,"
                    "stream,in_extract,has_map,prepared,handlecollisions,note,updated_at)"
                    " VALUES (?,?,?,?,?,0,0,0,0,?,?)",
                    (SCHEMA_SOURCE, tb, SCHEMA_TARGET, tb, run["stream"],
                     f"đăng ký bởi quy trình thêm bảng #{run['id']}", int(time.time())))
                out.append(f"{tb}: tồn tại ở nguồn - ĐÃ đăng ký vào luồng {run['stream']}")
                moi += 1
            elif cu["stream"] and cu["stream"] != run["stream"]:
                raise WizardError(
                    f"{tb}: danh sách quản lý ghi bảng này thuộc luồng {cu['stream']}, "
                    f"không phải {run['stream']}.")
            elif not cu["stream"]:
                self.con.execute(
                    "UPDATE managed_table SET stream=?, updated_at=? WHERE src_table=?",
                    (run["stream"], int(time.time()), tb))
                out.append(f"{tb}: đã có trong danh sách, chưa gắn luồng - gắn vào "
                           f"{run['stream']}")
            else:
                out.append(f"{tb}: đã có trong danh sách quản lý, luồng {run['stream']}")
        return {"msg": (f"Đã đăng ký {moi} bảng mới vào danh sách quản lý" if moi
                        else "Mọi bảng đã có trong danh sách quản lý"),
                "output": out}

    # =================================================================
    # GO_BANG - go mot bang khoi luong dang chay
    # =================================================================
    def _moc_go(self, run: dict) -> dict:
        ctx = json.loads(run.get("ctx_json") or "{}")
        moc = ctx.get("moc_go")
        if not moc:
            raise WizardError("Chưa có mốc (bước 3) - không biết phải chờ Replicat áp tới đâu.")
        return moc

    def _co_dong(self, proc: str, run: dict, loai: str) -> bool:
        txt = paramsmod.read(self.cfg, proc).text
        pat = (paramsmod._re_table if loai == "TABLE" else paramsmod._re_map)(
            run["src_owner"], run["src_table"])
        return any(pat.match(l) for l in txt.splitlines())

    async def _b_kiem_tra_go(self, run: dict, ts: dict) -> dict:
        et, rep, _ = self._proc(run)
        co_tab = self._co_dong(et, run, "TABLE")
        co_map = self._co_dong(rep, run, "MAP")
        out = [f"{et}.prm: dòng TABLE {'CÓ' if co_tab else 'không có'}",
               f"{rep}.prm: dòng MAP {'CÓ' if co_map else 'không có'}"]
        if not co_tab and not co_map:
            raise WizardError(f"{run['src_table']} không có dòng TABLE lẫn MAP trong luồng "
                              f"{run['stream']} - không có gì để gỡ.")
        n_tab = gg.count_param_entries(self.cfg, et, "TABLE")
        out.append(f"{et} đang có {n_tab} dòng TABLE")
        if co_tab and n_tab <= 1:
            raise WizardError(
                f"{run['src_table']} là bảng CUỐI CÙNG của {et}. Bỏ nó thì Extract không còn "
                "bảng nào - đó là gỡ cả luồng, không phải gỡ một bảng.")
        if not co_tab:
            out.append("dòng TABLE đã được bỏ từ trước (phiên dở dang) - các bước sau tự bỏ "
                       "qua phần đã làm")
        for proc in (et, rep):
            st = await gg.status_of(self.cfg, proc)
            if not st or not st.is_running:
                raise WizardError(f"{proc} đang {st.status if st else 'không thấy'} - chỉ gỡ "
                                  "bảng khi cả Extract và Replicat đang chạy khoẻ.")
            out.append(f"{proc} RUNNING chậm {st.lag_s}s")
            if proc == rep and (st.lag_s or 0) > 60:
                raise WizardError(f"{rep} đang chậm {st.lag_s}s - chờ nó bắt kịp rồi gỡ.")
        return {"msg": f"Đủ điều kiện gỡ {run['src_table']} khỏi luồng {run['stream']}",
                "output": out}

    async def _b_ghi_moc_trail(self, run: dict, ts: dict) -> dict:
        import re as _re
        et, rep, tr = self._proc(run)
        st = await gg.status_of(self.cfg, et)
        if st is None or st.is_running:
            raise WizardError(f"{et} phải đang DỪNG mới ghi được mốc cuối.")
        d = await gg.detail(self.cfg, et)
        scn = int(d.get("read_scn") or 0)
        if not scn:
            raise WizardError(f"Không đọc được SCN mà {et} đã đọc tới - không có mốc thì "
                              "không biết phải chờ Replicat áp tới đâu.")
        mau = _re.compile(rf"^{_re.escape(tr)}\d{{9}}$")
        tep = sorted(f for f in (self.cfg.gg_home / "dirdat").iterdir() if mau.match(f.name))
        if not tep:
            raise WizardError(f"Không thấy file trail {tr}* nào.")
        f = tep[-1]
        moc = {"scn": scn, "file": str(f), "seqno": int(f.name[-9:]),
               "size": f.stat().st_size}
        ctx = json.loads(run.get("ctx_json") or "{}")
        ctx["moc_go"] = moc
        self._ghi_run(run["id"], ctx_json=json.dumps(ctx, ensure_ascii=False))
        return {"msg": f"Mốc: {et} đã đọc tới SCN {scn:,}; cuối trail {f.name} RBA "
                       f"{moc['size']:,}",
                "output": [f"read_scn = {scn}", f"trail cuối = {f}", f"kích thước = {moc['size']}"]}

    def _da_doc_qua_moc(self, d: dict, moc: dict) -> bool:
        from pathlib import Path
        f, rba = d.get("read_file"), d.get("rba")
        if not f or rba is None:
            return False
        so = Path(f).name[-9:]
        if not so.isdigit():
            return False
        return int(so) > moc["seqno"] or (int(so) == moc["seqno"]
                                          and int(rba) >= moc["size"])

    async def _b_bo_table(self, run: dict, ts: dict) -> dict:
        et, _, _ = self._proc(run)
        cur = paramsmod.read(self.cfg, et)
        moi = paramsmod.bo_dong_table(cur.text, run["src_owner"], run["src_table"])
        if moi != cur.text:
            r = await self._ghi_param_bo(run, et, moi, ts, "bỏ TABLE", None)
        else:
            r = {"msg": f"dòng TABLE của {run['src_table']} đã không còn - không làm lại",
                 "output": []}
        self.con.execute(f"UPDATE {self._bang_qly(run)} SET in_extract=0, updated_at=?"
                         " WHERE src_table=?", (int(time.time()), run["src_table"]))
        return r

    async def _xt_bo_table(self, run: dict) -> str:
        et, _, _ = self._proc(run)
        return self._xt_bo(et, run, "TABLE")

    async def _xt_bo_map(self, run: dict) -> str:
        _, rep, _ = self._proc(run)
        return self._xt_bo(rep, run, "MAP")

    def _xt_bo(self, proc: str, run: dict, loai: str) -> str:
        txt = paramsmod.read(self.cfg, proc).text
        pat = (paramsmod._re_table if loai == "TABLE" else paramsmod._re_map)(
            run["src_owner"], run["src_table"])
        dong = [l for l in txt.splitlines() if pat.match(l)]
        if not dong:
            return f"# {proc}.prm: dòng {loai} của {run['src_table']} đã không còn"
        return f"# {proc}.prm - bỏ đúng một dòng:\n- " + "\n- ".join(dong)

    async def _ghi_param_bo(self, run: dict, proc: str, moi: str, ts: dict,
                            nhan: str, extract_cap_doi: str | None) -> dict:
        r = await paramsmod.save(
            self.cfg, self.con, proc, moi,
            who=ts.get("who", "wizard"),
            reason=ts.get("reason", f"quy trình #{run['id']} {nhan}"),
            cho_phep_bo=(run["src_owner"], run["src_table"]),
            extract_cap_doi=extract_cap_doi)
        st = r["stats"]
        return {"msg": f"Đã ghi {proc}.prm (bỏ {st['xoa']} dòng, TABLE "
                       f"{st['table_truoc']}->{st['table_sau']}, MAP "
                       f"{st['map_truoc']}->{st['map_sau']}), kiểm cú pháp đạt",
                "output": [f"backup: {r['backup']}"] + r["checkprm_lines"]}

    async def _b_cho_ap_het(self, run: dict, ts: dict) -> dict:
        from pathlib import Path
        _, rep, _ = self._proc(run)
        moc = self._moc_go(run)
        giu: dict = {}

        async def do() -> tuple[bool, str]:
            st = await gg.status_of(self.cfg, rep)
            if not st or not st.is_running:
                giu.clear()
                return False, f"{rep} {st.status if st else '?'}"
            d = await gg.detail(self.cfg, rep)
            doc = self._da_doc_qua_moc(d, moc)
            ok, mo_ta = self._qua_moc_nap(d, moc["scn"])
            vi_tri = (f"đọc {Path(d.get('read_file') or '?').name} RBA {d.get('rba')}"
                      f" / mốc {Path(moc['file']).name} RBA {moc['size']:,}")
            if doc and ok:
                return True, f"{mo_ta}; {vi_tri}"
            if doc and ok is None:
                n = self._giu_yen(giu, d)
                return n >= 30, f"{mo_ta}; {vi_tri}; trail đứng yên {n}s/30s"
            if doc and self._doc_het_trail(d):
                # Duong thu ba: luong VANG, High WM dung duoi moc (phien #10).
                xong, mt = await self._qua_ap_rong(run, moc["scn"], giu, d)
                return xong, f"{mt}; {vi_tri}"
            giu.clear()
            return False, f"{mo_ta}; {vi_tri}"

        # Bang dang go da mat dong TABLE: update no khong vao trail, nen goi y
        # phai chon bang KHAC cua luong.
        boc, goi_y = self._boc_cho(run, "CHO_AP_HET", do, set(self._cac_bang(run)))
        r = await cho.cho_den(boc, tran_s=1200, nhan=f"{rep} áp hết tới mốc gỡ")
        if not r["xong"]:
            raise WizardError(
                f"{rep} chưa áp hết tới mốc gỡ sau {cho.gio_gon(r['giay'])} ({r['cuoi']}). "
                "Dòng TABLE đã bỏ và Extract đang chạy - luồng vẫn an toàn; mở lại quy "
                "trình sau để chạy tiếp, chưa bỏ dòng MAP."
                + (" Luồng vắng: xem lệnh gợi ý trong kết quả bước." if goi_y else ""))
        return {"msg": f"{r['cuoi']} - sau {cho.gio_gon(r['giay'])}",
                "output": goi_y + r["output"]}

    async def _b_bo_map(self, run: dict, ts: dict) -> dict:
        et, rep, _ = self._proc(run)
        moc = self._moc_go(run)
        if self._co_dong(et, run, "TABLE"):
            raise WizardError(f"{et} VẪN còn dòng TABLE {run['src_table']} - không được bỏ MAP "
                              "(mất dữ liệu im lặng). Bỏ TABLE trước.")
        # KIEM LAI luc Replicat da dung: vi tri no se chay lai tu do phai qua moc.
        d = await gg.detail(self.cfg, rep)
        ok, mo_ta = self._qua_moc_nap(d, moc["scn"])
        if ok is False and self._da_ap_rong_truoc(run, moc["scn"]):
            ok, mo_ta = None, self._mo_ta_ap_rong(run)
        if ok is False or not self._da_doc_qua_moc(d, moc):
            raise WizardError(f"{mo_ta}: chưa áp hết tới mốc gỡ (SCN {moc['scn']:,}). Bỏ MAP "
                              "lúc này là bỏ qua các giao dịch chưa áp của bảng. Chạy lại "
                              "Replicat, chờ thêm.")
        cur = paramsmod.read(self.cfg, rep)
        moi = paramsmod.bo_dong_map(cur.text, run["src_owner"], run["src_table"])
        if moi != cur.text:
            r = await self._ghi_param_bo(run, rep, moi, ts, "bỏ MAP",
                                         paramsmod.read(self.cfg, et).text)
        else:
            r = {"msg": f"dòng MAP của {run['src_table']} đã không còn - không làm lại",
                 "output": []}
        r["output"].insert(0, f"{mo_ta} - đủ điều kiện bỏ MAP")
        self.con.execute(f"UPDATE {self._bang_qly(run)} SET has_map=0, handlecollisions=0,"
                         " updated_at=? WHERE src_table=?",
                         (int(time.time()), run["src_table"]))
        return r

    async def _b_kiem_chung_go(self, run: dict, ts: dict) -> dict:
        et, rep, _ = self._proc(run)
        out = []
        for proc in (et, rep):
            st = await gg.status_of(self.cfg, proc)
            if not st or not st.is_running:
                raise WizardError(f"{proc} không chạy: {st.status if st else 'không thấy'}")
            out.append(f"{proc} RUNNING chậm {st.lag_s}s")
        if self._co_dong(et, run, "TABLE") or self._co_dong(rep, run, "MAP"):
            raise WizardError("Vẫn còn dòng TABLE hoặc MAP của bảng trong file tham số.")
        out.append("không còn dòng TABLE / MAP của bảng")
        self.con.execute(
            f"UPDATE {self._bang_qly(run)} SET stream=NULL, in_extract=0, has_map=0,"
            " handlecollisions=0, note=?, updated_at=? WHERE src_table=?",
            (f"đã gỡ khỏi luồng {run['stream']} bởi quy trình #{run['id']} - TRANDATA ở "
             "nguồn vẫn bật, dữ liệu ở đích giữ nguyên",
             int(time.time()), run["src_table"]))
        out += ["",
                f"Danh sách quản lý: {run['src_table']} trở lại «chưa thuộc luồng nào».",
                "CỐ Ý KHÔNG LÀM (để người quyết): tắt TRANDATA ở nguồn, và xoá bảng / dữ "
                "liệu ở đích (dữ liệu dừng ở thời điểm gỡ).",
                "Trang đối chiếu sẽ ghi bảng này vào nhóm «đã bật TRANDATA nhưng Extract "
                "không lấy» cho tới khi TRANDATA được tắt."]
        return {"msg": f"Đã gỡ {run['src_table']} khỏi luồng {run['stream']}", "output": out}

    # =================================================================
    # BUOC param
    # =================================================================
    def _them_hc(self, text: str, run: dict) -> str:
        """Them HANDLECOLLISIONS vao dong MAP DA TON TAI."""
        import re
        pat = re.compile(
            rf'^(\s*MAP\s+{re.escape(run["src_owner"])}\.'
            rf'{re.escape(run["src_table"])}\s*,)', re.I)
        ra = []
        thay = False
        for line in text.splitlines(keepends=True):
            if pat.match(line) and "HANDLECOLLISIONS" not in line.upper():
                line = line.replace(";", ", HANDLECOLLISIONS;", 1) \
                    if line.rstrip().endswith(";") else line
                thay = True
            ra.append(line)
        if not thay and "HANDLECOLLISIONS" not in text.upper():
            raise WizardError(
                f'Không thấy dòng MAP cho {run["src_owner"]}.{run["src_table"]} để bật '
                "chế độ chống trùng")
        return "".join(ra)

    async def _ghi_param(self, run: dict, proc: str, moi: str, ts: dict,
                         nhan: str) -> dict:
        r = await paramsmod.save(
            self.cfg, self.con, proc, moi,
            who=ts.get("who", "wizard"),
            reason=ts.get("reason", f"quy trình #{run['id']} {nhan}"))
        st = r["stats"]
        return {"msg": f"Đã ghi {proc}.prm (thêm {st['them']}/bỏ {st['xoa']} dòng, TABLE "
                       f"{st['table_truoc']}->{st['table_sau']}, MAP "
                       f"{st['map_truoc']}->{st['map_sau']}), kiểm cú pháp đạt",
                "output": [f"backup: {r['backup']}"] + r["checkprm_lines"]}

    async def _b_them_map(self, run: dict, ts: dict) -> dict:
        _, rep, _ = self._proc(run)
        if run["kind"] == "THEM_LOG":
            # Dong MAP log: INSERTALLRECORDS, khong KEYCOLS (chi INSERT), khong
            # che do chong trung (khong co dot nap nao de chong lan).
            cur = paramsmod.read(self.cfg, rep)
            moi = paramsmod.them_dong_map_log(cur.text, run["src_owner"], run["src_table"],
                                              run["tgt_owner"], run["tgt_table"])
            if moi == cur.text:
                return {"msg": f"dòng MAP log cho {run['src_table']} ĐÃ có - không làm lại",
                        "output": []}
            r = await self._ghi_param(run, rep, moi, ts, "them MAP log")
            self.con.execute("UPDATE managed_log SET has_map=1, updated_at=?"
                             " WHERE src_table=?", (int(time.time()), run["src_table"]))
            return r
        if not run["keycols"]:
            raise WizardError("Chưa xác nhận cột khoá (bước xác nhận cột khoá)")
        cur = paramsmod.read(self.cfg, rep)
        moi = paramsmod.them_dong_map(
            cur.text, run["src_owner"], run["src_table"],
            run["tgt_owner"], run["tgt_table"], run["keycols"], True)
        if moi == cur.text:
            return {"msg": f"dòng MAP cho {run['src_table']} ĐÃ có - không làm lại",
                    "output": []}
        r = await self._ghi_param(run, rep, moi, ts, "them MAP")
        self.con.execute("UPDATE managed_table SET has_map=1, handlecollisions=1,"
                         " updated_at=? WHERE src_table=?",
                         (int(time.time()), run["src_table"]))
        return r

    async def _b_them_table(self, run: dict, ts: dict) -> dict:
        et, _, _ = self._proc(run)
        cur = paramsmod.read(self.cfg, et)
        moi = paramsmod.them_dong_table(cur.text, run["src_owner"], run["src_table"])
        if moi == cur.text:
            return {"msg": f"dòng TABLE cho {run['src_table']} ĐÃ có", "output": []}
        r = await self._ghi_param(run, et, moi, ts, "them TABLE")
        self.con.execute(f"UPDATE {self._bang_qly(run)} SET in_extract=1, updated_at=?"
                         " WHERE src_table=?", (int(time.time()), run["src_table"]))
        return r

    async def _b_them_handlecollisions(self, run: dict, ts: dict) -> dict:
        _, rep, _ = self._proc(run)
        cur = paramsmod.read(self.cfg, rep)
        moi = self._them_hc(cur.text, run)
        if moi == cur.text:
            return {"msg": "chế độ chống trùng đã bật trên dòng MAP đó", "output": []}
        r = await self._ghi_param(run, rep, moi, ts, "bật chế độ chống trùng")
        self.con.execute("UPDATE managed_table SET handlecollisions=1,"
                         " updated_at=? WHERE src_table=?",
                         (int(time.time()), run["src_table"]))
        return r

    async def _b_bo_handlecollisions(self, run: dict, ts: dict) -> dict:
        # CHOT CUNG: chi bo khi Low WM > MAXSCN
        if run["maxscn"] is None:
            raise WizardError("Chưa có mốc nạp - đợt nạp chưa xong")
        _, rep, _ = self._proc(run)
        d = await gg.detail(self.cfg, rep)
        # CUNG dinh nghia voi buoc 25 (`_qua_moc_nap`). Phien #7: chot cu chi
        # doc Low WM, ma Low WM cua luong moi it giao dich la "(no data)" ->
        # FAIL o day sau khi buoc 25 da cho qua -> tu don ca luong.
        ok, mo_ta = self._qua_moc_nap(d, int(run["maxscn"]))
        if ok is False and self._da_ap_rong_truoc(run, int(run["maxscn"])):
            ok, mo_ta = None, self._mo_ta_ap_rong(run)
        if ok is False:
            raise WizardError(
                f"{mo_ta}: chưa vượt. Bỏ chế độ chống trùng lúc này là mất khả năng "
                "chống trùng cho phần dữ liệu còn đang chờ áp. Chờ thêm.")
        cur = paramsmod.read(self.cfg, rep)
        moi = paramsmod.bo_handlecollisions(cur.text, run["src_owner"],
                                            run["src_table"])
        if moi == cur.text:
            return {"msg": "không có chế độ chống trùng nào để bỏ", "output": []}
        r = await self._ghi_param(run, rep, moi, ts, "bỏ chế độ chống trùng")
        self.con.execute("UPDATE managed_table SET handlecollisions=0,"
                         " updated_at=? WHERE src_table=?",
                         (int(time.time()), run["src_table"]))
        r["output"].insert(0, f"{mo_ta} - đủ điều kiện")
        return r

    # =================================================================
    # BUOC cho / doc moc SCN
    # =================================================================
    async def _b_cho_bat_kip(self, run: dict, ts: dict) -> dict:
        et, _, _ = self._proc(run)

        async def do() -> tuple[bool, str]:
            p = await gg.status_of(self.cfg, et)
            lag = p.lag_s if p else None
            xong = bool(p and p.is_running and lag is not None and lag <= 5)
            return xong, f"{et} {p.status if p else '?'} chậm {lag}s"

        r = await cho.cho_den(do, tran_s=600, nhan=f"{et} bắt kịp")
        if not r["xong"]:
            raise WizardError(
                f"{et} chưa bắt kịp sau {cho.gio_gon(r['giay'])} "
                f"({r['cuoi']}). Extract mới phải đọc redo từ thời điểm đăng "
                "ký tới hiện tại - có thể cần lâu hơn. Tiến độ lưu ở server "
                "nên mở lại sau vẫn ở bước này.")
        return {"msg": f"{et} đã bắt kịp sau {cho.gio_gon(r['giay'])} "
                       f"({r['cuoi']})", "output": r["output"]}

    async def _scn_extract_da_doc(self, et: str) -> tuple[int | None, str]:
        """SCN Extract DA DOC TOI (`send extract, status`), chi khi het recovery.

        Dang recovery thi vi tri doc con LUI ve giao dich mo luc stop - lay
        luc do la lay mot moc chua phu bang moi. Tra (None, ly do) neu chua.
        """
        import re
        _, lines = await gg.run(self.cfg, f"send extract {gg.check_arg(et)}, status",
                                timeout=60)
        txt = "\n".join(lines)
        if "Recovery complete" not in txt:
            st = re.search(r"Current status:\s*(.+)", txt)
            return None, f"{et} chưa hết recovery ({st.group(1).strip() if st else '?'})"
        m = re.search(r"Current read position:.*?SCN:\s*[\d.]+\s*\((\d+)\)", txt, re.S)
        if not m:
            return None, f"không đọc được SCN đang đọc của {et}"
        return int(m.group(1)), f"{et} Recovery complete, đã đọc tới SCN {int(m.group(1)):,}"

    async def _b_scn_floor(self, run: dict, ts: dict) -> dict:
        et, rep, _ = self._proc(run)
        d = await gg.detail(self.cfg, rep)
        hi = d.get("high_wm")
        if not hi and not any(d.get(k) for k in ("low_wm", "log_bsn")):
            # Replicat CHUA TUNG AP giao dich nao (luong moi, bang vang - lo dau
            # cua PMNV3, phien #21 2026-10-23): DB khong co moc nao de in. Lay
            # SCN Extract DA DOC TOI sau khi bat kip. Moc nay >= moi High WM co
            # the co (Replicat khong ap duoc thu Extract chua doc) nen chan duoi
            # CHAT HON, khong long hon. Cung tinh than `_b_ns_scn_floor` dung
            # register_scn cho Replicat chua tung chay.
            scn, ly_do = await self._scn_extract_da_doc(et)
            if scn:
                self._ghi_run(run["id"], scn_floor=scn,
                              trail_seqno=None, trail_rba=None)
                return {"msg": f"Ngưỡng dưới = {scn:,} ({rep} chưa từng áp giao dịch "
                               f"nào nên không có High Watermark - lấy SCN {et} đã đọc tới)",
                        "output": [f"{k} = {v}" for k, v in d.items()] + [ly_do]}
            raise WizardError(
                f"{rep} chưa từng áp giao dịch nào nên không có High Watermark, và "
                f"chưa lấy được SCN thay thế: {ly_do}. Chạy lại bước này sau ít phút.")
        if not hi:
            raise WizardError(
                "Không đọc được mốc High Watermark -> DỪNG. Ngưỡng dưới sai thì script "
                "nạp sẽ chặn sai, hoặc bỏ lọt dữ liệu. Không đoán.")
        self._ghi_run(run["id"], scn_floor=int(hi),
                      trail_seqno=int(d["seqno"]) if d.get("seqno") else None,
                      trail_rba=int(d["rba"]) if d.get("rba") else None)
        return {"msg": f"Ngưỡng dưới = {int(hi):,} (mốc High Watermark của {rep})",
                "output": [f"{k} = {v}" for k, v in d.items()]}

    @staticmethod
    def _doc_het_trail(d: dict) -> bool:
        """Replicat da doc HET trail: RBA doc = kich thuoc file, va khong co
        file trail so ke tiep."""
        from pathlib import Path
        f, rba = d.get("read_file"), d.get("rba")
        if not f or rba is None:
            return False
        fp = Path(f)
        if not fp.exists() or fp.stat().st_size != int(rba):
            return False
        ten, so = fp.name[:-9], fp.name[-9:]
        if not so.isdigit():
            return False
        return not fp.with_name(f"{ten}{int(so) + 1:09d}").exists()

    def _qua_moc_nap(self, d: dict, mx: int) -> tuple[bool | None, str]:
        """MOT dinh nghia "phan chong lan da ap xong" cho CA buoc 25 lan 27.

        Tra (True, ...)  : Low WM > moc nap - duong chuan.
            (None, ...)  : Low WM "(no data)" nhung High WM > moc nap va da
                           doc het trail - DAT neu giu duoc (buoc 25 doi
                           giu 30s; buoc 27 chay SAU khi Replicat da dung
                           em o buoc 26 nen nhan ngay).
            (False, ...) : chua.
        Ly do co duong thu hai: xem _b_cho_low_wm. Luong ban (Low WM co gia
        tri) KHONG BAO GIO di duong do.
        """
        low = int(d.get("low_wm") or 0)
        if low > mx:
            return True, f"mốc đã áp {low:,} / mốc nạp {mx:,}"
        high = int(d.get("high_wm") or 0)
        # Low WM RONG (luong moi) hoac TRE MOT GIAO DICH (luong yen - Replicat
        # integrated chi cong bo Low WM toi giao dich TRUOC giao dich moi
        # nhat, phien #9). Ca hai deu chi DAT khi noi goi giu duoc 30 giay
        # TRAIL DUNG YEN - xem `_giu_yen`.
        if high > mx and self._doc_het_trail(d):
            lw = "chưa công bố" if not low else f"{low:,} (trễ một giao dịch)"
            return None, (f"Low WM {lw}; High WM {high:,} > mốc nạp {mx:,}, đã đọc "
                          "hết trail")
        return False, (f"mốc đã áp {low:,} / mốc nạp {mx:,}"
                       + (f" (High WM {high:,})" if high else ""))

    @staticmethod
    def _giu_yen(giu: dict, d: dict) -> int:
        """Dem so giay TRAIL DUNG YEN lien tuc (vi tri doc khong doi).

        `giu` la dict cua noi goi, song qua cac lan hoi. Vi tri doc doi = co
        du lieu moi -> dem lai tu 0. Day la cai lam cho duong phu cua
        `_qua_moc_nap` KHONG BAO GIO dat tren mot luong ban.
        """
        vt = (d.get("read_file"), d.get("rba"))
        if giu.get("vt") != vt:
            giu["vt"], giu["tu"] = vt, time.monotonic()
        return int(time.monotonic() - giu["tu"])

    async def _ap_rong(self, run: dict, mx: int) -> tuple[bool, str]:
        """Replicat da AP RONG moi thu truoc moc `mx` - KHONG can giao dich moi.

        VI SAO CO DUONG NAY (phien #10, 2026-10-21; va #7, #8 truoc do): hai
        duong cua `_qua_moc_nap` deu can Low WM hoac High WM VUOT moc. Hai moc
        do la SCN cua giao dich DA AP, nen chi nhich khi CO giao dich moi cua
        chinh cac bang trong luong. Luong vang (PMNV2: 2 bang) thi chung dung
        yen o duoi moc mai mai - phien #10 dung 4 phut 42 giay o buoc 6, va
        lan nao nguoi dung cung phai tu COMMIT o nguon cho qua.

        Duong nay chung minh tu phia khac, bang ba su that khong phu thuoc
        luong giao dich:
          1. Extract da doc redo QUA moc: moi giao dich commit truoc moc da
             nam trong trail. Checkpoint SCN cua Extract integrated tang theo
             redo cua CA DB, ke ca khi bang cua luong khong co gi.
          2. (noi goi kiem) Replicat da doc HET trail va vi tri doc dung yen.
          3. Inbound server o dich RANH: coordinator IDLE, khong con giao dich
             cho giao, moi apply server IDLE, so da nhan = so da xu ly.
        Ca ba dung lien tuc 30 giay (noi goi giu bang `_giu_yen`) thi moi giao
        dich truoc moc da ap xong.
        """
        et, rep, _ = self._proc(run)
        p = await gg.status_of(self.cfg, rep)
        if not (p and p.is_running):
            return False, f"{rep} {p.status if p else '?'}"
        de = await gg.detail(self.cfg, et)
        scn_et = int(de.get("read_scn") or 0)
        if scn_et <= mx:
            return False, f"{et} mới đọc redo tới SCN {scn_et:,}, chưa qua mốc {mx:,}"
        if self.ora is None:
            return False, "không có kết nối DB đích để xem inbound server"
        ten = f"OGG${rep}"
        try:
            r = await self.ora.one(SQL_INBOUND, a=ten)
        except Exception as e:  # noqa: BLE001 - loi DB chi la "chua chung minh duoc"
            return False, f"không đọc được trạng thái {ten}: {str(e).splitlines()[0][:150]}"
        if not r:
            return False, f"không thấy inbound server {ten} ở DB đích"
        nhan = int(r["total_received"] or 0)
        xu_ly = sum(int(r[k] or 0) for k in ("total_applied", "total_ignored",
                                             "total_errors"))
        ranh = (r["state"] == "IDLE" and not int(r["unassigned_complete_txns"] or 0)
                and int(r["so_server"] or 0) > 0 and not int(r["server_ban"] or 0)
                and xu_ly >= nhan)
        mo_ta = (f"{et} đọc redo tới SCN {scn_et:,} > mốc {mx:,}; {ten} {r['state']}, "
                 f"nhận {nhan} / xử lý {xu_ly} giao dịch, chờ giao "
                 f"{r['unassigned_complete_txns']}, apply server bận "
                 f"{r['server_ban']}/{r['so_server']}")
        return ranh, mo_ta

    async def _qua_ap_rong(self, run: dict, mx: int, giu: dict,
                           d: dict) -> tuple[bool, str]:
        """Mot luot hoi cua duong thu ba. Noi goi DA kiem doc het trail.

        Dat thi ghi `ctx["ap_rong"]` - chot kiem lai luc Replicat da dung
        (BO_MAP, BO_HANDLECOLLISIONS) khong xem duoc inbound server nua, nen
        nhan bang chung nay thay cho Low WM.
        """
        rong, mo_ta = await self._ap_rong(run, mx)
        if not rong:
            giu.clear()
            return False, mo_ta
        n = self._giu_yen(giu, d)
        if n < 30:
            return False, f"{mo_ta}; đứng yên {n}s/30s"
        ctx = json.loads(self.lay(run["id"])["ctx_json"] or "{}")
        ctx["ap_rong"] = {"mx": int(mx), "luc": int(time.time()), "mo_ta": mo_ta}
        self._ghi_run(run["id"], ctx_json=json.dumps(ctx, ensure_ascii=False))
        return True, f"đã áp rỗng: {mo_ta}; đứng yên {n}s/30s"

    def _da_ap_rong_truoc(self, run: dict, mx: int) -> bool:
        """Buoc cho cua CHINH phien nay da chung minh ap rong toi DUNG moc `mx`."""
        if not run.get("id") or self.con is None:
            return False
        ctx = json.loads(self.lay(run["id"])["ctx_json"] or "{}")
        return (ctx.get("ap_rong") or {}).get("mx") == int(mx)

    def _mo_ta_ap_rong(self, run: dict) -> str:
        ctx = json.loads(self.lay(run["id"])["ctx_json"] or "{}")
        a = ctx.get("ap_rong") or {}
        luc = time.strftime("%H:%M:%S", time.localtime(a.get("luc", 0)))
        return (f"bước chờ đã chứng minh áp rỗng tới mốc {a.get('mx', 0):,} "
                f"lúc {luc} ({a.get('mo_ta', '')})")

    # Cho bao lau ma chua qua thi hien lenh goi y. Luong ban thuong qua bang
    # Low WM trong vai giay, nen toi day gan nhu chac la luong VANG.
    GOI_Y_SAU_S = 60

    def _boc_cho(self, run: dict, ten_buoc: str, do, bo: set[str]):
        """Boc `do()` cua mot buoc cho moc nap.

        Hai viec, ca hai chi de NGUOI DUNG THAY, khong doi dieu kien qua buoc:
          1. Ghi tien do SONG vao `wizard_step.output` sau moi lan hoi. Truoc
             day output chi ghi khi buoc xong, nen suot 20 phut cho khung
             theo doi song (`/nhanh/song`) khong hien gi.
          2. Qua GOI_Y_SAU_S giay chua xong thi chen (MOT lan) lenh no-op de
             nguoi dung tu chay o nguon - xem `_goi_y_commit`.
        Tra (ham_boc, goi_y) - `goi_y` la list, rong neu chua hien.
        """
        no = so_buoc(run.get("kind"), ten_buoc)
        t0 = time.monotonic()
        goi_y: list[str] = []
        dong: list[str] = []

        async def boc() -> tuple[bool, str]:
            xong, mo_ta = await do()
            troi = time.monotonic() - t0
            if not xong and not goi_y and troi >= self.GOI_Y_SAU_S:
                goi_y.extend(await self._goi_y_commit(run, bo, troi))
            dong.append(f"{cho.gio_gon(troi)}: {mo_ta}")
            del dong[:-12]
            if no and run.get("id") and self.con is not None:
                self._ghi_buoc(run["id"], no,
                               output="\n".join(goi_y + ([""] if goi_y else []) + dong))
            return xong, mo_ta

        return boc, goi_y

    async def _goi_y_commit(self, run: dict, bo: set[str], troi: float) -> list[str]:
        """Lenh UPDATE no-op de NGUOI DUNG tu chay o NGUON khi luong vang ket.

        VI SAO CHI IN, KHONG TU CHAY (nguoi dung chon 2026-10-22): nguon PMNV
        la READ-ONLY tuyet doi voi cong cu (adapters/ora.py), cong cu khong co
        tai khoan ghi o nguon. Phien #12 (2026-10-22): ETPMNV2 dung o SCN
        449.548.191 < moc nap 449.576.349 suot 3 phut - duong ap rong khong
        dung duoc vi chinh Extract cung khong nhich. Chi mot giao dich that
        tren bang cua luong moi day duoc ca Extract lan Replicat qua moc.

        Chon bang: bang cua phien truoc, roi cac bang khac cua luong (co ca
        TABLE lan MAP), tru `bo` - GO_BANG da bo TABLE cua bang dang go nen
        update no khong vao trail. Uu tien bang KHONG co trigger dang bat:
        update no-op tren bang co trigger audit van sinh dong lich su that.
        Cot: cot dau tien khong thuoc khoa, kieu don gian - update cot khoa ve
        chinh no thi Replicat thay mot lenh doi khoa.

        Moi loi o day chi lam goi y kem cu the hon; KHONG duoc lam hong buoc
        cho (cho_den khong bat ngoai le tu do()).
        """
        o = run.get("src_owner") or SCHEMA_SOURCE
        ung_vien: list[tuple[str, str]] = []
        for b in self._cac_bang(run):
            if b not in bo:
                kc = None
                if self.con is not None:
                    r = self.con.execute(
                        "SELECT keycols FROM managed_table WHERE src_table=?", (b,)).fetchone()
                    kc = r[0] if r else None
                ung_vien.append((b, kc or run.get("keycols") or ""))
        if self.con is not None and run.get("stream"):
            for r in self.con.execute(
                    "SELECT src_table, keycols FROM managed_table WHERE stream=?"
                    " AND in_extract=1 AND has_map=1 ORDER BY src_table",
                    (run["stream"],)):
                if r[0] not in bo and r[0] not in {x for x, _ in ung_vien}:
                    ung_vien.append((r[0], r[1] or ""))

        chon = None            # (bang, cot, so_trigger)
        for b, kc in ung_vien[:6]:
            khoa = {c.strip().strip('"').upper() for c in kc.split(",") if c.strip()}
            try:
                cot = await self.ora.rows(
                    "SELECT column_name, data_type FROM dba_tab_cols@DBL_PMNV"
                    " WHERE owner=:o AND table_name=:t AND virtual_column='NO'"
                    " AND hidden_column='NO' AND identity_column='NO'"
                    " ORDER BY column_id", o=o, t=b)
                trg = int(await self.ora.scalar(
                    "SELECT COUNT(*) FROM dba_triggers@DBL_PMNV WHERE table_owner=:o"
                    " AND table_name=:t AND status='ENABLED'", o=o, t=b) or 0)
            except Exception:  # noqa: BLE001 - chi la goi y
                continue
            c = next((x["column_name"] for x in cot
                      if x["column_name"] not in khoa and khoa
                      and str(x["data_type"]).split("(")[0] in
                      ("DATE", "NUMBER", "VARCHAR2", "CHAR", "NVARCHAR2", "TIMESTAMP")),
                     None)
            if c is None:
                continue
            if chon is None or (chon[2] and not trg):
                chon = (b, c, trg)
            if not trg:
                break

        dau = [f"GỢI Ý - đã chờ {cho.gio_gon(troi)} mà mốc chưa qua: có thể luồng "
               f"{run.get('stream') or ''} đang vắng, không có giao dịch mới ở nguồn.",
               "Một giao dịch nhỏ trên bảng CỦA LUỒNG sẽ đẩy Extract và Replicat qua mốc. "
               "Công cụ KHÔNG tự chạy (nguồn chỉ đọc) - chạy tay ở DB NGUỒN:"]
        if chon is None:
            return dau + [
                f'  UPDATE {o}."<BẢNG CỦA LUỒNG>" SET "<CỘT KHÔNG PHẢI KHOÁ>" = '
                '"<CỘT KHÔNG PHẢI KHOÁ>" WHERE ROWNUM = 1;',
                "  COMMIT;",
                "(không tự chọn được bảng/cột - chọn bảng không có trigger đang bật)"]
        b, c, trg = chon
        return dau + [
            f'  UPDATE {o}."{b}" SET "{c}" = "{c}" WHERE ROWNUM = 1;',
            "  COMMIT;",
            (f"CẢNH BÁO: bảng {b} có {trg} trigger đang bật - update vẫn kích hoạt "
             "trigger; kiểm trước khi chạy." if trg else
             f"(bảng {b} không có trigger đang bật; cột {c} không thuộc khoá)"),
            "Lệnh treo nghĩa là dòng đó đang bị khoá: huỷ rồi chạy lại. Bước chờ tự "
            "qua khi mốc nhích, không cần bấm gì thêm."]

    async def _b_cho_low_wm(self, run: dict, ts: dict) -> dict:
        """Cho Low WM vuot MAXSCN.

        Day la cho DUY NHAT trong ca he thong ma Low Watermark la moc dung:
        Low WM = moc ma MOI transaction truoc no da apply xong. Vuot MAXSCN
        nghia la toan bo du lieu CDC chong lan voi dot nap da duoc apply, nen
        HANDLECOLLISIONS khong con can.
        """
        if run["maxscn"] is None:
            raise WizardError("Chưa có mốc nạp - đợt nạp chưa xong")
        _, rep, _ = self._proc(run)
        mx = int(run["maxscn"])

        # DUONG THU HAI, chi khi Low WM la "(no data)" (phien #7,
        # 2026-10-20). Replicat integrated cua mot LUONG MOI voi bang it giao
        # dich co the KHONG BAO GIO cong bo Low WM: da ap 1 update, High WM
        # 380.939.029 > moc nap, ma Low WM van "(no data)". Buoc nay luc do
        # cho het 20 phut roi FAIL -> che do nhanh tu don ca mot luong dang
        # chay dung.
        # Khi Low WM rong, coi la da ap het phan chong lan neu DONG THOI:
        #   - High WM > moc nap,
        #   - Replicat da doc HET trail (RBA doc = kich thuoc file, khong co
        #     file trail moi hon),
        #   - va hai dieu tren dung LIEN TUC it nhat 30 giay (giao dich da
        #     gui sang inbound server co thoi gian commit).
        # Luong ban (Low WM co gia tri) KHONG BAO GIO di duong nay. Va buoc
        # VERIFY phia sau van doi chieu nguon/dich - day khong phai cho
        # cuoi cung bat duoc sai lech.
        giu: dict = {}

        async def do() -> tuple[bool, str]:
            d = await gg.detail(self.cfg, rep)
            ok, mo_ta = self._qua_moc_nap(d, mx)
            if ok:
                return True, mo_ta
            if ok is None:
                n = self._giu_yen(giu, d)
                return n >= 30, f"{mo_ta}, trail đứng yên {n}s/30s"
            if self._doc_het_trail(d):
                # Duong thu ba: luong VANG, High WM dung duoi moc nap.
                return await self._qua_ap_rong(run, mx, giu, d)
            giu.clear()
            return False, mo_ta

        boc, goi_y = self._boc_cho(run, "CHO_LOW_WM", do, set())
        r = await cho.cho_den(boc, tran_s=1200, nhan=f"{rep} mốc đã áp")
        if not r["xong"]:
            raise WizardError(
                f"Mốc đã áp chưa vượt mốc nạp sau {cho.gio_gon(r['giay'])} "
                f"({r['cuoi']}). Đừng chờ tiếp ở đây; mở lại quy trình sau - "
                "tiến độ lưu ở server nên không mất gì."
                + (" Luồng vắng: xem lệnh gợi ý trong kết quả bước." if goi_y else ""))
        return {"msg": f"{r['cuoi']} - đã vượt, sau "
                       f"{cho.gio_gon(r['giay'])}", "output": goi_y + r["output"]}

    # =================================================================
    # BUOC nap du lieu
    # =================================================================
    def _sql_nap_lo(self, run: dict, ds: list[str]) -> str:
        """Nap CA LO trong MOT script, o DUNG MOT SCN.

        VI SAO MOT SCN CHO CA LO - day la ly do ky thuat cua ca tinh nang lo

        Lo CN_* co CN_CH (dong gom) va CN_CT / CN_PS / CN_TT (dong chi tiet)
        dung chung khoa (MA_DVI, SO_ID). Neu moi bang nap o mot `AS OF SCN`
        rieng - tuc la chay tam phien ADD_TABLE rieng - thi hai anh chup cach
        nhau hang chuc phut, va trong khoang do o dich se co dong chi tiet
        cua mot chung tu MA KHONG CO dong gom cua no, hoac nguoc lai.
        HANDLECOLLISIONS + CDC roi cung keo ve dung, nhung bat ky bao cao nao
        doc trong khoang giua deu cho so sai, va khong co canh bao nao noi
        rang du lieu dang o giua. Mot SCN duy nhat thi tinh nhat quan la DUNG
        NGAY TU DAU chu khong phai "roi se dung".

        BON LOP CHAN giu nguyen nhu ban mot bang, nhung lop 1 (dich phai
        RONG) kiem TUNG bang va kiem HET TRUOC khi nap bang dau tien: nua
        duong moi phat hien bang thu 6 khong rong thi 5 bang truoc da nap
        xong o SCN nay, con bang thu 6 thi khong - dung cai tinh nhat quan
        ma ca thiet ke nay dung de bao ve.

        Lop 2b uoc thoi gian nap theo TONG so dong ca lo, khong phai tung
        bang: anh flashback phai con hieu luc den het bang CUOI CUNG.
        """
        from .wizard_index import TOC_DO_NAP_THAP
        floor = int(run["scn_floor"] or 0)
        to_ = run["tgt_owner"]
        ctx = json.loads(run["ctx_json"] or "{}")
        cb = ctx.get("bang") or {}

        bang: list[tuple[str, str, int]] = []
        tong = 0
        for b in ds:
            hang = self.con.execute(
                "SELECT tgt_table FROM managed_table WHERE src_table=?",
                (b,)).fetchone()
            tt = check_ident(hang["tgt_table"] if hang else b)
            n = int((cb.get(b) or {}).get("n_src") or 0)
            tong += n
            bang.append((check_ident(b), tt, n))
        uoc = int(tong / TOC_DO_NAP_THAP) if tong else 0

        d: list[str] = [
            "-- ggadmin wizard #%s %s - LO %d BANG, MOT SCN DUY NHAT"
            % (run["id"], run["kind"], len(bang)),
            "-- SCN_FLOOR = %d" % floor,
            "-- Sinh luc %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "--",
            "-- Ca %d bang nap o CUNG mot `AS OF SCN`. Cac bang nay co quan he"
            % len(bang),
            "-- master-detail (dung chung khoa), nap o SCN khac nhau se sinh ra",
            "-- dong chi tiet khong co dong goc o dich.",
            "--",
            "-- Bang                 dong o nguon",
        ]
        for sb, tt, n in bang:
            d.append("--   %-20s %s" % (sb, format(n, ",")))
        d += [
            "--   %-20s %s  -> uoc %ds" % ("TONG", format(tong, ","), uoc),
            "SET SERVEROUTPUT ON SIZE UNLIMITED",
            "WHENEVER SQLERROR EXIT FAILURE",
            "DECLARE",
            "  v_floor NUMBER := %d;" % floor,
            "  v_scn   NUMBER;",
            "  v_rong  NUMBER;",
            "  v_undo  NUMBER := 0;",
            "  v_nap   NUMBER := 0;",
            "  v_tong  NUMBER := 0;",
            "BEGIN",
            "  SELECT current_scn INTO v_scn FROM v$database@DBL_PMNV;",
            "  DBMS_OUTPUT.PUT_LINE('SCN nạp   = ' || v_scn);",
            "  DBMS_OUTPUT.PUT_LINE('Ngưỡng dưới = ' || v_floor);",
            "",
            "  IF v_floor = 0 THEN",
            "    RAISE_APPLICATION_ERROR(-20010,",
            "      'Ngưỡng dưới = 0: chưa đọc được mốc High Watermark. Không nạp.');",
            "  END IF;",
            "",
            "  -- LOP 2",
            "  IF v_scn < v_floor THEN",
            "    RAISE_APPLICATION_ERROR(-20011,",
            "      'SCN nạp (' || v_scn || ') cũ hơn ngưỡng dưới (' || v_floor ||"
            " '). Sẽ bỏ lọt dữ liệu. Dừng lại.');",
            "  END IF;",
            "",
            "  -- LOP 2b: do LAI undo_retention ngay truoc khi nap, va so voi",
            "  -- uoc thoi gian nap CA LO - anh flashback phai con hieu luc",
            "  -- den het bang CUOI CUNG.",
            "  BEGIN",
            "    SELECT tuned_undoretention INTO v_undo",
            "      FROM (SELECT tuned_undoretention FROM v$undostat@DBL_PMNV",
            "             ORDER BY begin_time DESC)",
            "     WHERE ROWNUM = 1;",
            "  EXCEPTION",
            "    WHEN OTHERS THEN v_undo := 0;",
            "  END;",
            "  DBMS_OUTPUT.PUT_LINE('Nguồn giữ ảnh dữ liệu cũ = ' || v_undo",
            "    || 's, ước thời gian nạp cả lô = %d s');" % uoc,
            "  IF v_undo > 0 AND %d > v_undo THEN" % uoc,
            "    RAISE_APPLICATION_ERROR(-20013,",
            "      'Nguồn chỉ còn giữ ảnh dữ liệu cũ ' || v_undo || 's mà ước'",
            "      || ' thời gian nạp cả lô là %ds -> sẽ gặp ORA-01555 giữa đợt nạp.'"
            % uoc,
            "      || ' Chia lô nhỏ hơn, hoặc nâng undo_retention ở nguồn.');",
            "  END IF;",
            "",
            "  -- LOP 1 cho TUNG bang, kiem HET TRUOC KHI nap bang dau tien.",
            "  -- Phat hien bang thu 6 khong rong luc da nap 5 bang la pha",
            "  -- dung cai tinh nhat quan ma mot SCN duy nhat dung de bao ve.",
        ]
        for sb, tt, _ in bang:
            d += [
                '  SELECT COUNT(*) INTO v_rong FROM (SELECT 1 FROM %s."%s"'
                " WHERE ROWNUM = 1);" % (to_, tt),
                "  IF v_rong > 0 THEN",
                "    RAISE_APPLICATION_ERROR(-20012,",
                "      'Bảng đích %s.\"%s\" KHÔNG rỗng. Nạp vào bảng có sẵn"
                " là nhân bản dữ liệu.');" % (to_, tt),
                "  END IF;",
            ]
        d.append("  DBMS_OUTPUT.PUT_LINE('Lớp chặn 1: cả %d bảng đích đều rỗng');"
                 % len(bang))
        d.append("")
        for sb, tt, _ in bang:
            d += [
                "  -- ---- %s -> %s" % (sb, tt),
                "  BEGIN",
                "    EXECUTE IMMEDIATE",
                "      'INSERT /*+ APPEND */ INTO %s.\"%s\" '" % (to_, tt),
                "      || 'SELECT * FROM %s.\"%s\"@DBL_PMNV AS OF SCN '"
                " || v_scn;" % (SCHEMA_SOURCE, sb),
                "    v_nap := SQL%ROWCOUNT;",
                "    COMMIT;",
                "  EXCEPTION",
                "    WHEN OTHERS THEN",
                "      ROLLBACK;",
                "      DBMS_OUTPUT.PUT_LINE('%s: nạp nhanh lỗi (' || SQLCODE"
                " || '), thu conventional');" % sb,
                "      EXECUTE IMMEDIATE",
                "        'INSERT INTO %s.\"%s\" '" % (to_, tt),
                "        || 'SELECT * FROM %s.\"%s\"@DBL_PMNV AS OF SCN '"
                " || v_scn;" % (SCHEMA_SOURCE, sb),
                "      v_nap := SQL%ROWCOUNT;",
                "      COMMIT;",
                "  END;",
                "  v_tong := v_tong + v_nap;",
                "  DBMS_OUTPUT.PUT_LINE('SO_DONG_%s=' || v_nap);" % sb,
            ]
        d += [
            "",
            "  DBMS_OUTPUT.PUT_LINE('MAXSCN=' || v_scn);",
            "  DBMS_OUTPUT.PUT_LINE('SO_DONG=' || v_tong);",
            "END;",
            "/",
            "EXIT",
        ]
        return chr(10).join(d) + chr(10)

    def _sql_nap(self, run: dict) -> str:
        """Sinh PL/SQL nap voi BON LOP CHAN.

        1. dich phai RONG         -> nap vao bang co san la nhan ban du lieu
        2. SCN nap >= SCN_FLOOR   -> nap o SCN cu hon floor la BO LOT du lieu
                                     ma HANDLECOLLISIONS khong cuu duoc
        2b. undo_retention do LAI ngay truoc khi nap -> `tuned_undoretention`
                                     dao dong manh (900 -> 27528s trong vai
                                     ngay) va script co the chay nhieu gio sau
                                     khi sinh
        3. fallback conventional  -> `INSERT /*+ APPEND */` KHONG duoc
                                     HANDLECOLLISIONS bao ve, gap ORA-00001
                                     la abend; thu lai kieu thuong
        """
        from .wizard_index import TOC_DO_NAP_THAP
        ds = self._cac_bang(run)
        if len(ds) > 1:
            return self._sql_nap_lo(run, ds)
        o, t = SCHEMA_SOURCE, check_ident(run["src_table"])
        to_, tt = run["tgt_owner"], check_ident(run["tgt_table"])
        floor = int(run["scn_floor"] or 0)
        n_src = int(json.loads(run["ctx_json"] or "{}").get("n_src") or 0)
        uoc = int(n_src / TOC_DO_NAP_THAP) if n_src else 0
        return f'''-- ggadmin wizard #{run["id"]} {run["kind"]}
-- Nạp {o}."{t}"@DBL_PMNV  ->  {to_}."{tt}"
-- Ngưỡng dưới = {floor}
-- Sinh lúc {time.strftime("%Y-%m-%d %H:%M:%S")}
--
-- BA LỚP CHẶN, không bỏ lớp nào:
--   1. bảng đích phải RỖNG
--   2. SCN nạp không được cũ hơn ngưỡng dưới
--   3. nạp nhanh lỗi thì tự động thử lại cách thường
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR EXIT FAILURE
DECLARE
  v_floor  NUMBER := {floor};
  v_scn    NUMBER;
  v_rong   NUMBER;
  v_undo   NUMBER := 0;
  v_nap    NUMBER := 0;
BEGIN
  -- SCN nạp: lấy hiện tại ở NGUỒN. Không được cũ hơn ngưỡng dưới.
  SELECT current_scn INTO v_scn FROM v$database@DBL_PMNV;
  DBMS_OUTPUT.PUT_LINE('SCN nạp   = ' || v_scn);
  DBMS_OUTPUT.PUT_LINE('Ngưỡng dưới = ' || v_floor);

  IF v_floor = 0 THEN
    RAISE_APPLICATION_ERROR(-20010,
      'Ngưỡng dưới = 0: chưa đọc được mốc High Watermark. Không nạp.');
  END IF;

  -- LOP 2
  IF v_scn < v_floor THEN
    RAISE_APPLICATION_ERROR(-20011,
      'SCN nạp (' || v_scn || ') cũ hơn ngưỡng dưới (' || v_floor || '). '
      || 'Nạp ở mốc cũ hơn ngưỡng sẽ BỎ LỌT dữ liệu mà chế độ chống '
      || 'trùng không cứu được. Dừng lại.');
  END IF;

  -- LOP 2b: DO LAI undo_retention NGAY TRUOC KHI NAP.
  -- Wizard da quyet dinh "mot SCN la an toan" luc SINH script, nhung
  -- `tuned_undoretention` DAO DONG manh: do duoc 900 -> 2253 -> 21241 ->
  -- 27528 giay chi trong vai ngay. Script co the duoc chay nhieu gio sau khi
  -- sinh, hoac chay lai hom sau. Quyet dinh mot lan roi tin mai la cach de
  -- an ORA-01555 giua dot nap.
  BEGIN
    SELECT tuned_undoretention INTO v_undo
      FROM (SELECT tuned_undoretention FROM v$undostat@DBL_PMNV
             ORDER BY begin_time DESC)
     WHERE ROWNUM = 1;
  EXCEPTION
    WHEN OTHERS THEN v_undo := 0;
  END;
  DBMS_OUTPUT.PUT_LINE('Nguồn giữ ảnh dữ liệu cũ = ' || v_undo
    || 's, ước thời gian nạp = ' || {uoc} || 's');
  IF v_undo > 0 AND {uoc} > v_undo THEN
    RAISE_APPLICATION_ERROR(-20013,
      'Nguồn chỉ còn giữ ảnh dữ liệu cũ ' || v_undo || 's mà ước thời gian '
      || 'nạp là {uoc}s. Ảnh dữ liệu sẽ HẾT HIỆU LỰC giữa đợt nạp -> '
      || 'ORA-01555 kèm ORA-02063 (đã gặp thật ở lô 4, nạp 64 phút). Nâng '
      || 'tạm undo_retention ở nguồn, hoặc chia lô theo khoá phân vùng của '
      || 'đích.');
  END IF;

  -- LOP 1
  SELECT COUNT(*) INTO v_rong FROM (
    SELECT 1 FROM {to_}."{tt}" WHERE ROWNUM = 1);
  IF v_rong > 0 THEN
    RAISE_APPLICATION_ERROR(-20012,
      'Bảng đích {to_}."{tt}" KHÔNG rỗng. Nạp vào bảng có sẵn là nhân bản '
      || 'dữ liệu. Xoá sạch bảng trước (quy trình đồng bộ lại) hoặc kiểm lại.');
  END IF;

  -- LOP 3: thu APPEND, that bai thi conventional
  BEGIN
    EXECUTE IMMEDIATE
      'INSERT /*+ APPEND */ INTO {to_}."{tt}" '
      || 'SELECT * FROM {o}."{t}"@DBL_PMNV AS OF SCN ' || v_scn;
    v_nap := SQL%ROWCOUNT;
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Nạp nhanh: đã nạp ' || v_nap || ' dòng');
  EXCEPTION
    WHEN OTHERS THEN
      ROLLBACK;
      DBMS_OUTPUT.PUT_LINE('Nạp nhanh lỗi (' || SQLCODE || '), thử lại cách thường. '
        || 'Lưu ý: chế độ chống trùng KHÔNG bảo vệ lệnh nạp nhanh.');
      EXECUTE IMMEDIATE
        'INSERT INTO {to_}."{tt}" '
        || 'SELECT * FROM {o}."{t}"@DBL_PMNV AS OF SCN ' || v_scn;
      v_nap := SQL%ROWCOUNT;
      COMMIT;
      DBMS_OUTPUT.PUT_LINE('Cách thường: đã nạp ' || v_nap || ' dòng');
  END;

  DBMS_OUTPUT.PUT_LINE('MAXSCN=' || v_scn);
  DBMS_OUTPUT.PUT_LINE('SO_DONG=' || v_nap);
END;
/
EXIT
'''

    def _sql_stats(self, run: dict) -> str:
        """Gather stats cho ca lo trong MOT script.

        `degree => DBMS_STATS.AUTO_DEGREE` thay vi mot so co dinh: tren server
        nay `sga_target` + `pga_aggregate_limit` da vuot RAM va swap day 100%,
        nen mot con so co dinh cho bang 283 dong la lay PGA ma khong doi lay
        duoc gi. AUTO_DEGREE de Oracle tu quyet theo kich thuoc bang.
        """
        dong = [
            "-- ggadmin wizard #%s: gather statistics" % run["id"],
            "-- Chay bang CHINH CHU SCHEMA %s - chu schema luon lam duoc tren"
            % run["tgt_owner"],
            "-- object cua minh, nen KHONG can sysdba va khong can ANALYZE ANY.",
            "WHENEVER SQLERROR EXIT FAILURE",
            "SET SERVEROUTPUT ON SIZE UNLIMITED",
            "BEGIN",
        ]
        for b in self._cac_bang(run):
            rb = self._run_bang(run, b) if self._la_lo(run) else run
            dong += [
                "  DBMS_STATS.GATHER_TABLE_STATS(",
                "    ownname          => '%s'," % rb["tgt_owner"],
                "    tabname          => '%s'," % check_ident(rb["tgt_table"]),
                "    estimate_percent => DBMS_STATS.AUTO_SAMPLE_SIZE,",
                "    method_opt       => 'FOR ALL COLUMNS SIZE AUTO',",
                "    cascade          => TRUE,",
                "    degree           => DBMS_STATS.AUTO_DEGREE);",
                "  DBMS_OUTPUT.PUT_LINE('đã cập nhật thống kê: %s');"
                % check_ident(rb["tgt_table"]),
            ]
        dong += ["END;", "/", "EXIT"]
        return chr(10).join(dong) + chr(10)

    async def _b_sinh_script_nap(self, run: dict, ts: dict) -> dict:
        if not run["scn_floor"]:
            raise WizardError("Chưa có ngưỡng dưới của dữ liệu nạp")
        # QUYET DINH mot SCN duy nhat co an toan khong. Raise neu khong -
        # lo 4 nap 64 phut da an ORA-01555 that vi bo qua buoc nay.
        kl = await self._kiem_chia_lo(run)
        ten = f"wz{run['id']}_nap_{run['src_table'].lower()}.sql"
        p = self._luu_script(ten, self._sql_nap(run))
        ten2 = f"wz{run['id']}_stats_{run['src_table'].lower()}.sql"
        p2 = self._luu_script(ten2, self._sql_stats(run))
        return {"msg": "Đã sinh 2 script vào thư mục scripts/; nạp ở một thời điểm duy nhất là "
                       "an toàn",
                "output": kl + ["", p, p2, "",
                                "Công cụ không được là hộp đen: hai file trên đọc được và chạy tay "
                                "được."]}

    async def _b_nap(self, run: dict, ts: dict) -> dict:
        """Spawn job ROI RA tien trinh web de nap.

        Truoc day buoc nay chay `sqlplus` dong bo trong task cua app. Hai
        thu vo khi lam vay, va da vo THAT: restart app la mat job, va DBA
        khong `tail -f` duoc theo cach ho quen. Gio dung `jobs.spawn_sqlplus`.
        """
        from . import jobs as jobmod
        ten = f"wz{run['id']}_nap_{run['src_table'].lower()}.sql"
        p = self.cfg.scripts_dir / ten
        if not p.exists():
            raise WizardError(f"Chưa có {p} - chạy bước sinh script nạp trước")

        # VAO LAI SAU KHI BUOC FAIL: dot nap co the DA THANH CONG roi buoc
        # moi bi danh dau FAIL vi ly do khac. Lan chay that dau tien:
        # `_xn_nap` khong doc duoc bang vi thieu grant SELECT cho GGADM_RO,
        # nen buoc FAIL trong khi 3.355 dong DA nap xong. Chay lai script
        # luc do bi lop chan 1 tu choi "bang dich KHONG rong" - dung, nhung
        # be tac. Nen o day: da co job nap thanh cong cua CHINH buoc nay thi
        # KHONG nap lai, di thang sang kiem chung.
        cu = self.con.execute(
            "SELECT id, result_json FROM job WHERE kind=? AND state='SUCCEEDED'"
            " ORDER BY id DESC LIMIT 1", (f"WZ{run['id']}_NAP",)).fetchone()
        if cu is not None:
            moc = json.loads(cu["result_json"] or "{}")
            if moc.get("MAXSCN"):
                return {"msg": "Việc #%s trước đó ĐÃ nạp thành công (%s dòng, mốc nạp %s) - KHÔNG nạp "
                               "lại, chuyển sang kiểm chứng." % (cu["id"],
                                            format(int(moc.get("SO_DONG") or 0), ","),
                                            format(int(moc["MAXSCN"]), ",")),
                        "cho_job": int(cu["id"]),
                        "output": ["Bước này được vào lại sau khi báo lỗi. Đợt nạp đã xong;",
                                   "chạy lại script sẽ bị lớp chặn thứ nhất từ chối vì",
                                   "bảng đích không còn rỗng - đúng nhưng bế tắc."]}
        jid = jobmod.spawn_sqlplus(
            self.cfg, self.con, kind=f"WZ{run['id']}_NAP", sql_path=p,
            cred="ggadm_load", created_by=ts.get("who", "wizard"),
            params={"wizard": run["id"], "bang": run["src_table"],
                    "scn_floor": run["scn_floor"]})
        return {"msg": f"Đã tạo việc nạp #{jid} - chạy NGOÀI tiến trình web. Mất đường hầm hay "
                       "khởi động lại công cụ thì việc này vẫn chạy. Theo dõi: tail -f "
                       f"logs/job_{jid}.log",
                "cho_job": jid, "output": [str(p)]}

    async def _xn_nap(self, run: dict, j: dict):
        """Kiem chung sau khi job nap bao thanh cong.

        KHONG tin vao ma thoat: doc MAXSCN/SO_DONG tu ket qua job va doi
        chieu voi so dong THAT o bang dich.
        """
        moc = json.loads(j["result_json"] or "{}")
        maxscn = moc.get("MAXSCN")
        so_dong = moc.get("SO_DONG")
        if not maxscn:
            return False, ["việc chạy không in ra mốc nạp - không biết phải chờ Replicat vượt mốc "
                           "nào. Xem logs/job_%s.log" % j["id"]]
        # Dem lai TUNG bang cua lo. Khong duoc chi dem bang dau tien: lop
        # chan 1 chi dam bao cac bang RONG truoc khi nap, con viec moi bang
        # co nhan du dong hay khong thi phai dem moi biet. Mot bang rong sot
        # lai se im lang cho den khi co nguoi doc bao cao.
        ds = self._cac_bang(run)
        gc = ["MAXSCN = %s" % format(int(maxscn), ","),
              "script báo đã nạp TỔNG %s dòng" % format(int(so_dong or 0), ",")]
        rong: list[str] = []
        tong = 0
        for b in ds:
            rb = self._run_bang(run, b) if len(ds) > 1 else run
            n = int(await self.ora.scalar(
                'SELECT COUNT(*) FROM %s."%s"'
                % (rb["tgt_owner"], check_ident(rb["tgt_table"]))) or 0)
            tong += n
            bao = moc.get("SO_DONG_" + b)
            gc.append("  %-14s đếm được %s dòng%s"
                      % (b, format(n, ","),
                         "  (script bao %s)" % format(int(bao), ",")
                         if bao else ""))
            # Bang nguon RONG that thi dich rong la DUNG, khong phai loi.
            n_src = int((self._ctx_bang(run, b) or {}).get("n_src") or 0) \
                if len(ds) > 1 else \
                int(json.loads(run["ctx_json"] or "{}").get("n_src") or 0)
            if n == 0 and n_src > 0:
                rong.append(b)
        if rong:
            return False, gc + [
                "%d bảng VẪN RỖNG sau khi nạp dù nguồn có dữ liệu: %s"
                % (len(rong), ", ".join(rong))]

        ctx = json.loads(run["ctx_json"] or "{}")
        ctx["so_dong_nap"] = so_dong
        ctx["so_dong_dem"] = tong
        self._ghi_run(run["id"], maxscn=int(maxscn),
                      ctx_json=json.dumps(ctx, ensure_ascii=False))
        gc.append("tổng đếm lại: %s dòng" % format(tong, ","))
        if so_dong and int(so_dong) != tong:
            gc.append("CẢNH BÁO: số dòng script báo và số đếm được KHÁC nhau - có thể "
                      "Replicat đã áp thêm dữ liệu trong lúc đó (bình thường nếu Replicat "
                      "đang chạy), hoặc đợt nạp chưa trọn.")
        return True, gc

    async def _b_gather_stats(self, run: dict, ts: dict) -> dict:
        ten = f"wz{run['id']}_stats_{run['src_table'].lower()}.sql"
        if not (self.cfg.scripts_dir / ten).exists():
            self._luu_script(ten, self._sql_stats(run))
        return await self._chay_hoac_huong_dan(
            run, ten, self._sql_stats(run), "GATHER_STATS", ts,
            proc_package="gather_stats")

    async def _xn_gather_stats(self, run: dict, j: dict):
        ds = self._cac_bang(run)
        gc: list[str] = []
        for b in ds:
            rb = self._run_bang(run, b) if len(ds) > 1 else run
            r = await self.ora.one(
                "SELECT last_analyzed, num_rows FROM dba_tables"
                " WHERE owner=:o AND table_name=:t",
                o=rb["tgt_owner"], t=check_ident(rb["tgt_table"]))
            if not r or not r["last_analyzed"]:
                return False, gc + [
                    "%s: chưa có thời điểm cập nhật thống kê - bước này chưa chạy" % b]
            gc.append("%-14s last_analyzed = %s, num_rows = %s"
                      % (b, r["last_analyzed"],
                         format(int(r["num_rows"] or 0), ",")))
        return True, gc

    async def _b_truncate(self, run: dict, ts: dict) -> dict:
        if run["rows_before_truncate"] is None:
            raise WizardError(
                "Chưa ghi lại số dòng cũ (ở bước khảo sát). Không xoá bảng khi chưa ghi "
                "lại số dòng cũ.")
        sql = (f'TRUNCATE TABLE {run["tgt_owner"]}."'
               f'{check_ident(run["tgt_table"])}";\nEXIT\n')
        p = self._luu_script(f"wz{run['id']}_truncate.sql", sql)
        rc, lines = await self._sqlplus(p, cred="ods")
        if rc != 0:
            raise WizardError(f"TRUNCATE that bai (ma {rc})")
        n = await self.ora.scalar(
            f'SELECT COUNT(*) FROM {run["tgt_owner"]}."'
            f'{check_ident(run["tgt_table"])}"')
        if n:
            raise WizardError(f"Sau khi xoá sạch vẫn còn {n:,} dòng")
        return {"msg": f"Đã xoá sạch bảng (trước đó {int(run['rows_before_truncate']):,} dòng)",
                "output": lines}

    async def _sqlplus(self, path, cred: str = "ggadm_load"
                       ) -> tuple[int, list[str]]:
        """Chay mot file .sql bang sqlplus.

        MOI SQL nam trong FILE SCRIPT, khong nhung inline - cam bay da mac 2
        lan (nhay kep trong Oracle la identifier chu khong phai chuoi ->
        ORA-00904).
        """
        import asyncio
        import subprocess
        c = self.cfg.creds.get(cred)
        if c is None:
            raise WizardError(f"Thiếu khối [{cred}] trong conf/secrets.ini")
        p = await asyncio.to_thread(
            subprocess.run,
            [str(self.cfg.oracle_home / "bin" / "sqlplus"), "-S",
             f"{c.user}/{c.password}@{c.dsn}", f"@{path}"],
            capture_output=True, text=True, env=self.cfg.subprocess_env(),
            timeout=7200, shell=False)
        from .adapters.osfs import redact
        lines = [redact(l) for l in (p.stdout + "\n" + p.stderr).splitlines()
                 if l.strip()]
        return p.returncode, lines

    # =================================================================
    # BUOC verify
    # =================================================================
    async def _b_xac_nhan_lech(self, run: dict, ts: dict) -> dict:
        """BAT BUOC o dau RESYNC. Chi mo cac buoc sau khi THUC SU lech.

        Muc 0 cua runbook ton tai vi sai lam nay da xay ra that: nap lai mot
        bang khong he lech. Engine bien no thanh KHONG THE BO QUA.
        """
        r = await self.verifier.verify_table(
            run["src_table"], scn=None, mode="LIVE", stream=run["stream"],
            batch_id=None, who=ts.get("who", "wizard"))
        if r.get("err"):
            raise WizardError(f"Không đối chiếu được: {r['err']}")
        out = [f"nguồn {r['src_count']:,} / đích {r['tgt_count']:,}",
               f"checksum nguồn {r['src_hash']}", f"checksum đích  {r['tgt_hash']}"]
        if r["matched"]:
            raise WizardError(
                f"{run['src_table']} KHÔNG lệch: nguồn {r['src_count']:,} = đích "
                f"{r['tgt_count']:,} và checksum trùng. KHÔNG nạp lại. Đóng phiên này "
                "lại.\nLưu ý chế độ nhanh chỉ có thể báo lệch oan, chứ không bao giờ "
                "báo khớp khi đang lệch.")
        self._ghi_run(run["id"], snapshot_scn=None)
        return {"msg": f"Đã xác nhận LỆCH: nguồn {r['src_count']:,} so với đích "
                       f"{r['tgt_count']:,}", "output": out}

    async def _b_verify(self, run: dict, ts: dict) -> dict:
        # Che do LIVE dem DICH truoc roi NGUON: bang dang co giao dich lien
        # tuc thi nguon hay hon dich 1-2 dong ma khong phai lech. Gap that
        # 2026-10-23 o 4 bang PMNV3 (BH_2B_APIGCN, BH_HD_GOC_NB, BH_HT_SO_TT,
        # BH_HD_TTRANG): dem lai vai giay sau la khop, discard sach. Vi vay
        # lech thi DOI CHIEU LAI trong VERIFY_TRAN_S giay; chi ket luan LECH
        # khi het tran ma lan nao cung lech.
        kq: dict = {}

        async def do() -> tuple[bool, str]:
            r = await self.verifier.verify_table(
                run["src_table"], scn=None, mode="LIVE", stream=run["stream"],
                batch_id=None, who=ts.get("who", "wizard"))
            if r.get("err"):
                raise WizardError(f"Đối chiếu lỗi: {r['err']}")
            kq.update(r)
            return bool(r["matched"]), (
                f"nguồn {r['src_count']:,} / đích {r['tgt_count']:,} "
                + ("khớp" if r["matched"] else "lệch"))

        c = await cho.cho_den(do, tran_s=VERIFY_TRAN_S,
                              nhan=f"đối chiếu {run['src_table']}")
        # Bang lon: MOT lan dem da qua tran (bang 12 trieu dong o lo 17 PMNV3
        # mat 99 giay) -> cho_den chi kip hoi mot lan. Van phai doi chieu du VERIFY_TOI_THIEU lan; ban
        # than lan dem dai da la khoang cho nen khong can ngu them.
        while not c["xong"] and c["so_lan"] < VERIFY_TOI_THIEU:
            c2 = await cho.cho_den(do, tran_s=0, nhan=f"đối chiếu {run['src_table']}",
                                   ghi=c["output"])
            c = dict(c2, so_lan=c["so_lan"] + c2["so_lan"],
                     giay=c["giay"] + c2["giay"])
        r = kq
        out = (c["output"] if c["so_lan"] > 1 else []) + [
            f"nguồn {r['src_count']:,} / đích {r['tgt_count']:,}",
            f"checksum nguồn {r['src_hash']}", f"checksum đích  {r['tgt_hash']}",
            f"số cột {r['col_count']}"]
        if run["rows_before_truncate"] is not None:
            out.append(f"số dòng trước khi xoá sạch: {int(run['rows_before_truncate']):,}")
        if not r["matched"]:
            raise WizardError(
                f"VẪN LỆCH sau {c['so_lan']} lần đối chiếu trong "
                f"{cho.gio_gon(c['giay'])}: nguồn "
                f"{r['src_count']:,} so với đích {r['tgt_count']:,}. Lưu "
                "ý chế độ nhanh có thể báo lệch oan vì đích đang thay đổi - chạy đối "
                "chiếu chế độ chuẩn để chốt.")
        return {"msg": f"KHỚP: {r['src_count']:,} dòng, checksum trùng",
                "output": out}

    # =================================================================
    # PHASE 4 - NEW_STREAM
    # =================================================================
    def _ns_ten(self, run: dict) -> tuple[str, str, str, str]:
        """Ten do CHINH PHIEN NAY tao ra. Day la allowlist cua lop pham vi."""
        et = run["created_extract"]
        rep = run["created_replicat"]
        pre = run["created_trail_prefix"]
        ck = run["created_ckpt_table"]
        if not all([et, rep, pre, ck]):
            raise WizardError("Chưa xác nhận các tên mới (bước 2)")
        return et, rep, pre, ck

    def _ns_cho_phep(self, run: dict) -> set[str]:
        et, rep, pre, ck = self._ns_ten(run)
        return {et, rep, pre, ck, ck.split(".")[-1]}

    async def _xt_ns_kiem_ten(self, run: dict) -> str:
        return ("Người dùng gõ tên ở phần dưới. Công cụ kiểm:\n  - tên nhóm 2-8 ký tự "
                "[A-Z0-9_], tiền tố trail ĐÚNG 2 ký tự thường\n  - không trùng 6 tiến "
                "trình / 3 trail / 3 bảng checkpoint đang có\n  - chưa tồn tại "
                "dirprm/<tên>.prm và dirdat/<tiền tố>*")

    async def _xt_ns_add_checkpointtable(self, run: dict) -> str:
        _, _, _, ck = self._ns_ten(run)
        return (f"dblogin useridalias {gg.ALIAS_TARGET}\n"
                f"add checkpointtable {ck}\n"
                + _GHI_CHU_GRANT_CKPT.format(ck=ck))

    async def _xt_ns_register_extract(self, run: dict) -> str:
        et, _, _, _ = self._ns_ten(run)
        return (f"dblogin useridalias {gg.ALIAS_SOURCE}\n"
                f"register extract {et} database share automatic")

    async def _xt_ns_add_extract(self, run: dict) -> str:
        et, _, _, _ = self._ns_ten(run)
        return (f"dblogin useridalias {gg.ALIAS_SOURCE}\n"
                f"add extract {et}, integrated tranlog, begin now")

    async def _xt_ns_add_exttrail(self, run: dict) -> str:
        et, _, pre, _ = self._ns_ten(run)
        return (f"add exttrail {self.cfg.gg_home}/dirdat/{pre}, "
                f"extract {et}, megabytes 100\n"
                f"-- MEGABYTES chi hop le o LENH nay. Trong .prm thi checkprm "
                f"bao: unrecognized keyword or extra value \"MEGABYTES\"")

    async def _xt_ns_add_replicat(self, run: dict) -> str:
        et, rep, pre, ck = self._ns_ten(run)
        return (f"dblogin useridalias {gg.ALIAS_TARGET}\n"
                f"add replicat {rep}, integrated, "
                f"exttrail {self.cfg.gg_home}/dirdat/{pre}, checkpointtable {ck}")

    async def _xt_ns_tao_param(self, run: dict) -> str:
        et, rep, pre, ck = self._ns_ten(run)
        return (f"--- dirprm/{et.lower()}.prm ---\n{self._prm_extract(run)}\n"
                f"--- dirprm/{rep.lower()}.prm ---\n{self._prm_replicat(run)}")

    # ---------------------------------------------------------- buoc 1
    async def _b_ns_tien_dieu_kien(self, run: dict, ts: dict) -> dict:
        out: list[str] = []
        loi: list[str] = []

        ps = await gg.info_all(self.cfg)
        for p in ps:
            out.append(f"{p.program:8} {p.name:10} {p.status:9} lag {p.lag_s}s")
            if not p.is_running:
                loi.append(f"{p.name} đang {p.status}")
            elif p.program == "REPLICAT" and (p.lag_s or 0) > 60:
                loi.append(f"{p.name} lag {p.lag_s}s > 60s")

        from .adapters import osfs
        d = osfs.df("/")
        out.append(f"đĩa / còn {d['avail_gb']:.1f} GB")
        if d["avail_gb"] < 10:
            loi.append(f"đĩa / chỉ còn {d['avail_gb']:.1f} GB (cần hơn 10 GB)")

        # streams_pool_size o CA HAI DAU - bat buoc cho integrated capture/apply
        for nhan, q in (("DICH",
                         "SELECT ROUND(current_size/1048576) mb"
                         " FROM v$sga_dynamic_components"
                         " WHERE component LIKE '%streams%'"),
                        ("NGUON",
                         "SELECT ROUND(current_size/1048576) mb"
                         " FROM v$sga_dynamic_components@DBL_PMNV"
                         " WHERE component LIKE '%streams%'")):
            try:
                mb = await self.ora.scalar(q) or 0
                out.append(f"bộ nhớ streams pool {nhan}: {int(mb):,} MB")
                if int(mb) < 1024:
                    loi.append(f"bộ nhớ streams pool ở {nhan} chỉ {int(mb)} MB (cần từ 1024 MB cho một "
                               "luồng nữa)")
            except Exception as e:                              # noqa: BLE001
                loi.append(f"không đọc được bộ nhớ streams pool ở {nhan}: "
                           f"{str(e).splitlines()[0][:70]}")

        out.append("")
        out.append("Nếu bộ nhớ streams pool thiếu, người quản trị chạy TAY lệnh dưới đây. "
                   "Công cụ KHÔNG tự chạy: lệnh này tác động DB nguồn production và nằm "
                   "trong danh sách chặn.")
        out.append("  ALTER SYSTEM SET streams_pool_size = 2G SCOPE = BOTH;")
        out.append("  -- chay o CA HAI DAU (33.68 va 33.90)")

        if loi:
            raise WizardError("Điều kiện tiên quyết KHÔNG đạt:\n- " + "\n- ".join(loi))
        return {"msg": "Tien dieu kien dat", "output": out}

    # ------------------------------------------- buoc rieng cua LUONG_MOI
    async def _xt_ns_dang_ky_bang(self, run: dict) -> str:
        tb = (run["src_table"] or "").upper()
        return chr(10).join([
            "-- kiem bang co THAT o nguon (chot chan cuoi, khong bo duoc)",
            "SELECT COUNT(*) FROM dba_tables@DBL_PMNV",
            f"  WHERE owner = '{SCHEMA_SOURCE}' AND table_name = '{tb}';",
            "",
            "-- ghi vao danh sach quan ly cua cong cu (SQLite, khong phai Oracle)",
            "INSERT INTO managed_table(src_owner, src_table, tgt_owner, tgt_table,",
            "                          stream, in_extract, has_map, prepared,",
            "                          handlecollisions)",
            f"  VALUES ('{SCHEMA_SOURCE}', '{tb}', '{SCHEMA_TARGET}', '{tb}',",
            "          NULL, 0, 0, 0, 0);",
            "",
            "-- luong de RONG o day. Buoc DANG KY CAU HINH moi gan luong va dat co.",
        ])

    async def _b_ns_dang_ky_bang(self, run: dict, ts: dict) -> dict:
        """Dua bang dau tien vao danh sach quan ly.

        VI SAO BUOC NAY NAM TRONG QUY TRINH: xem khoi chu thich cua
        LUONG_MOI o dau file. Ngan gon la mot vong khong the di - o dang ky
        bang o trang Quy trinh bat chon mot luong DA CO trong app.toml,
        nhung luong cua quy trinh nay chi ton tai tu buoc DANG_KY_CAU_HINH.

        CAI KHONG DOI: bang phai co THAT o nguon KT_BSH. Registry la
        allowlist lop 5 chong SQL injection, nen mot dong khong co bang that
        dang sau se mo pham vi cho MOI buoc ve sau.

        Dong moi ghi voi `stream = NULL` va ca ba co = 0 - dung su that luc
        nay. Buoc DANG_KY_CAU_HINH moi gan luong va dat co.

        Chay lai duoc: da co dong roi thi khong ghi lan hai.
        """
        if run["kind"] in KIND_LOG:
            return await self._dang_ky_log(run)
        tb = check_ident((run["src_table"] or "").upper())
        out: list[str] = []

        if not await self.ora.scalar(
                "SELECT COUNT(*) FROM dba_tables@DBL_PMNV"
                " WHERE owner=:o AND table_name=:t", o=SCHEMA_SOURCE, t=tb):
            raise WizardError(
                    f"{SCHEMA_SOURCE}.{tb} KHÔNG tồn tại ở nguồn. Danh sách quản lý chỉ "
                "nhận bảng "
                "có thật ở nguồn - đó là chốt chặn cuối chống SQL injection, không "
                "phải một ô nhập tự do.")
        out.append(f"nguồn {SCHEMA_SOURCE}.{tb}: tồn tại")

        n_tgt = await self.ora.scalar(
            "SELECT COUNT(*) FROM dba_tables WHERE owner=:o AND table_name=:t",
            o=SCHEMA_TARGET, t=tb)
        out.append("đích %s.%s: %s" % (SCHEMA_TARGET,
            tb, "đã có" if n_tgt else "chưa có - bước TẠO BẢNG Ở ĐÍCH sẽ tạo"))

        cu = self.con.execute(
            "SELECT stream, in_extract, has_map FROM managed_table"
            " WHERE src_table=?", (tb,)).fetchone()
        if cu is None:
            # `note` PHAI co: `registry.sync()` danh dau lai moi dong co
            # `note IS NULL` va se ghi de dong vua tao neu de trong.
            self.con.execute(
                "INSERT INTO managed_table(src_owner,src_table,tgt_owner,tgt_table,"
                "stream,in_extract,has_map,prepared,handlecollisions,note,updated_at)"
                " VALUES (?,?,?,?,NULL,0,0,0,0,?,?)",
                (SCHEMA_SOURCE, tb, SCHEMA_TARGET, tb,
                 f"đăng ký bởi quy trình tạo luồng mới #{run['id']}"
                 " - chưa thuộc luồng nào",
                 int(time.time())))
            out.append("đã thêm vào danh sách quản lý: luồng để rỗng, cả 3 cờ = 0")
            msg = f"Đã đăng ký {SCHEMA_SOURCE}.{tb} vào danh sách quản lý"
        else:
            if cu["has_map"] or cu["in_extract"] or cu["stream"]:
                raise WizardError(
                    "Danh sách quản lý ghi %s thuộc luồng %s (TABLE %s, MAP %s). Bảng "
                    "đầu tiên của luồng mới phải chưa thuộc luồng nào."
                    % (tb, cu["stream"] or "(rỗng)",
                       "có" if cu["in_extract"] else "chưa",
                       "có" if cu["has_map"] else "chưa"))
            out.append("đã có trong danh sách quản lý và chưa thuộc luồng nào "
                       "- không ghi lại")
            msg = f"{SCHEMA_SOURCE}.{tb} đã có trong danh sách quản lý"

        out += ["",
                "Luồng được gắn vào dòng này ở bước ĐĂNG KÝ CẤU HÌNH - lúc đó luồng "
                "mới thực sự có trong conf/app.toml. Trước đó, bảng là «đã quản lý "
                "nhưng chưa thuộc luồng nào», đúng như sự thật."]
        return {"msg": msg, "output": out}

    # ---------------------------------------------------------- buoc 2
    async def _b_ns_kiem_ten(self, run: dict, ts: dict) -> dict:
        # Doc tu `ts` (nguoi go o man hinh buoc nay) HOAC tu dong run (che do
        # chay tu dong da dat truoc qua `TuDong.dat_ten_moi`). Giong het cach
        # `_b_xac_nhan_keycols` doc `ts.get("keycols") or run["keycols"]`.
        #
        # Dat truoc KHONG phai duong vong qua kiem chung: moi kiem tra duoi
        # day van chay day du, va chay TAI THOI DIEM NAY chu khong phai luc
        # nguoi bam nut - trang thai he thong co the da doi.
        et = gg.kiem_ten_moi(
            ts.get("extract") or run["created_extract"] or "", "extract")
        rep = gg.kiem_ten_moi(
            ts.get("replicat") or run["created_replicat"] or "", "replicat")
        pre = gg.kiem_ten_moi(
            ts.get("trail_prefix") or run["created_trail_prefix"] or "", "trail")
        ck = (ts.get("ckpt_table") or run["created_ckpt_table"]
              or f"OGG_BSH.{rep}").strip().upper()
        if "." not in ck:
            ck = f"OGG_BSH.{ck}"
        gg.kiem_ten_moi(ck.split(".")[-1], "checkpointtable")
        if et == rep:
            raise WizardError("Tên Extract và Replicat không được giống nhau")

        out = [f"extract         = {et}", f"replicat        = {rep}",
               f"trail prefix    = {pre}", f"checkpointtable = {ck}"]

        # Khong duoc trung bat ky process dang co
        ps = {p.name for p in await gg.info_all(self.cfg)}
        for t in (et, rep):
            if t in ps:
                raise WizardError(f"{t} ĐÃ tồn tại trong danh sách tiến trình - đổi tên khác")
        out.append(f"không trùng {len(ps)} tiến trình đang có")

        # param file va trail KHONG duoc ton tai
        for t in (et, rep):
            p = self.cfg.param_file(t)
            if p.exists():
                raise WizardError(
                    f"{p} ĐÃ tồn tại. Phiên trước có thể chưa dọn sạch - kiểm rồi xoá tay "
                    "trước khi tạo lại.")
            out.append(f"{p} chưa tồn tại")
        co = sorted((self.cfg.gg_home / "dirdat").glob(f"{pre}*"))
        if co:
            raise WizardError(
                f"Đã có {len(co)} file trail với tiền tố {pre!r}: "
                f"{', '.join(f.name for f in co[:4])}. Tiền tố này đã dùng rồi.")
        out.append(f"dirdat/{pre}* chưa có file nào")

        self._ghi_run(run["id"], created_extract=et, created_replicat=rep,
                      created_trail_prefix=pre, created_ckpt_table=ck)
        out.append("")
        out.append("Bốn tên trên giờ là DANH SÁCH DUY NHẤT mà các lệnh tạo / xoá / đăng ký "
                   "/ bỏ đăng ký của phiên này được chạm vào.")
        return {"msg": f"Đã xác nhận tên: {et} / {rep} / trail {pre} / {ck}",
                "output": out}

    # ---------------------------------------------------------- buoc 3
    async def _b_ns_add_trandata(self, run: dict, ts: dict) -> dict:
        return await self._b_add_trandata(run, ts)

    # ---------------------------------------------------------- buoc 4
    def _prm_extract(self, run: dict) -> str:
        et, _, pre, _ = self._ns_ten(run)
        if run.get("kind") == "LUONG_LOG":
            # LOGALLSUPCOLS: anh truoc DU moi cot (DELETE). UPDATERECORDFORMAT
            # FULL: anh sau DU moi cot - va la DIEU KIEN cua INSERTALLRECORDS.
            # Hai tham so ap cho CA Extract -> chi o luong log rieng.
            # KHONG co DDL INCLUDE: TRUNCATE/DROP o nguon khong lan sang de
            # xoa lich su o dich.
            return f"""EXTRACT {et}
USERIDALIAS {gg.ALIAS_SOURCE}
EXTTRAIL {self.cfg.gg_home}/dirdat/{pre}
TRANLOGOPTIONS INTEGRATEDPARAMS (max_sga_size 256, parallelism 2)
LOGALLSUPCOLS
UPDATERECORDFORMAT FULL
REPORTCOUNT EVERY 30 MINUTES, RATE
DISCARDFILE ./dirrpt/{et}.dsc, APPEND, MEGABYTES 100
TABLE {run["src_owner"]}.{run["src_table"]};
"""
        # EXTTRAIL PHAI LA DUNG CHUOI da dang ky o `add exttrail` (buoc
        # ADD_EXTTRAIL dung `{gg_home}/dirdat/<pre>`). GoldenGate so NGUYEN
        # CHUOI: ghi `./dirdat/pa` trong khi dang ky `/u01/app/ogg/dirdat/pa`
        # thi Extract ABEND ngay khi chay voi OGG-01044 "The trail
        # './dirdat/pa' is not assigned to extract" - da xay ra o CA phien #6
        # lan #7 (2026-10-20). Ba luong dang chay deu dung duong dan tuyet doi.
        return f"""EXTRACT {et}
USERIDALIAS {gg.ALIAS_SOURCE}
EXTTRAIL {self.cfg.gg_home}/dirdat/{pre}
TRANLOGOPTIONS INTEGRATEDPARAMS (max_sga_size 256, parallelism 2)
REPORTCOUNT EVERY 30 MINUTES, RATE
DISCARDFILE ./dirrpt/{et}.dsc, APPEND, MEGABYTES 100
DDL INCLUDE MAPPED
TABLE {run["src_owner"]}.{run["src_table"]};
"""

    def _prm_replicat(self, run: dict) -> str:
        et, rep, pre, ck = self._ns_ten(run)
        if run.get("kind") == "LUONG_LOG":
            # IGNOREUPDATEBEFORES: UPDATE chi ghi anh SAU (mot dong). Khong
            # REPERROR DISCARD: loi thi ABEND - bo im lang mot dong lich su
            # te hon la dung lai cho nguoi xu ly.
            return f"""REPLICAT {rep}
USERIDALIAS {gg.ALIAS_TARGET}
DBOPTIONS INTEGRATEDPARAMS(max_sga_size 256)
REPORTCOUNT EVERY 30 MINUTES, RATE
DISCARDFILE ./dirrpt/{rep}.dsc, APPEND, MEGABYTES 100
IGNOREUPDATEBEFORES
{paramsmod.dong_map_log(run["src_owner"], run["src_table"], run["tgt_owner"], run["tgt_table"])}
"""
        kc = f", KEYCOLS ({run['keycols']})" if run["keycols"] else ""
        return f"""REPLICAT {rep}
USERIDALIAS {gg.ALIAS_TARGET}
DBOPTIONS INTEGRATEDPARAMS(max_sga_size 256)
REPORTCOUNT EVERY 30 MINUTES, RATE
DISCARDFILE ./dirrpt/{rep}.dsc, APPEND, MEGABYTES 100
REPERROR (0001, DISCARD)
REPERROR (1403, DISCARD)
DDL INCLUDE MAPPED
DDLERROR DEFAULT IGNORE RETRYOP
MAP {run["src_owner"]}.{run["src_table"]}, TARGET {run["tgt_owner"]}.{run["tgt_table"]}{kc}, HANDLECOLLISIONS, COLMAP (USEDEFAULTS);
"""

    async def _b_ns_tao_param(self, run: dict, ts: dict) -> dict:
        et, rep, _, _ = self._ns_ten(run)
        out = []
        for proc, noi_dung, kind in ((et, self._prm_extract(run), "EXTRACT"),
                                     (rep, self._prm_replicat(run), "REPLICAT")):
            p = self.cfg.param_file(proc)
            if p.exists():
                raise WizardError(f"{p} đã tồn tại - không ghi đè")
            p.write_text(noi_dung, encoding="utf-8")
            try:
                import os
                os.chmod(p, 0o640)
            except OSError:
                pass
            self._luu_script(f"wz{run['id']}_{proc.lower()}.prm", noi_dung)
            ok, lines = await gg.checkprm(self.cfg, p.name, kind)
            out.append(f"--- {p.name} checkprm: {'PASS' if ok else 'FAIL'} ---")
            out += lines
            if not ok:
                p.unlink(missing_ok=True)
                raise WizardError(
                    f"Kiểm cú pháp KHÔNG ĐẠT cho {p.name} - đã xoá file vừa tạo. Không để "
                    "lại file tham số không hợp lệ trong dirprm.")
        return {"msg": "Đã tạo 2 file tham số, cả HAI đều qua kiểm cú pháp", "output": out}

    # ------------------------------------------- buoc 5-9: nhom pham vi
    async def _b_ns_add_checkpointtable(self, run: dict, ts: dict) -> dict:
        _, _, _, ck = self._ns_ten(run)
        rc, lines = await gg.run_pham_vi(
            self.cfg,
            f"dblogin useridalias {gg.ALIAS_TARGET}\nadd checkpointtable {ck}",
            self._ns_cho_phep(run))
        if not any("successfully created" in l.lower() or "already exists" in l.lower()
                   for l in lines):
            raise WizardError(f"Không xác nhận được bảng checkpoint {ck}:\n"
                              + "\n".join(lines[-6:]))
        return {"msg": f"Đã tạo bảng checkpoint {ck}",
                "output": lines + ["", *_GHI_CHU_GRANT_CKPT.format(ck=ck).splitlines()]}

    async def _b_ns_register_extract(self, run: dict, ts: dict) -> dict:
        """Buoc cham vao DB NGUON PRODUCTION.

        `OGG$CAP_<ET>` se o trang thai ENABLED NGAY sau REGISTER. Day la
        BINH THUONG - capture da dang ky o muc DB nhung process con STOPPED
        nen chua dao redo that. Checklist ban dau tung du kien DISABLED va
        da phai dinh chinh.
        """
        et, _, _, _ = self._ns_ten(run)
        rc, lines = await gg.run_pham_vi(
            self.cfg,
            f"dblogin useridalias {gg.ALIAS_SOURCE}\n"
            f"register extract {et} database share automatic",
            self._ns_cho_phep(run), timeout=600)
        scn = None
        for l in lines:
            import re as _re
            if m := _re.search(r"SCN\s+(\d+)", l):
                scn = int(m.group(1))
        if not any("successfully registered" in l.lower() for l in lines):
            raise WizardError(
                f"Không xác nhận được việc đăng ký {et}. CẢNH BÁO: nếu lệnh đã chạy một "
                "phần thì có thể đã có tiến trình đọc redo đăng ký ở nguồn - kiểm "
                "`dba_capture@DBL_PMNV` TRƯỚC khi thử lại, và dùng chức năng dọn "
                "sạch.\n" + "\n".join(lines[-8:]))
        self._ghi_run(run["id"], register_scn=scn)
        return {"msg": f"Đã đăng ký {et} ở DB nguồn"
                       + (f" tai SCN {scn:,}" if scn else ""),
                "output": lines + [
                    "",
                    f"Lưu ý: OGG$CAP_{et} sẽ ở trạng thái ENABLED NGAY bây giờ. Đó là BÌNH "
                    "THƯỜNG - đã đăng ký ở mức DB nhưng tiến trình còn dừng nên chưa đọc "
                    "redo thật."]}

    async def _b_ns_add_extract(self, run: dict, ts: dict) -> dict:
        et, _, _, _ = self._ns_ten(run)
        if not run["register_scn"] and run["cur_step"] <= 7:
            # Khong chan cung vi REGISTER co the khong in SCN, nhung phai
            # chac la buoc 6 da PASS - engine da bao dam dieu do.
            pass
        rc, lines = await gg.run_pham_vi(
            self.cfg,
            f"dblogin useridalias {gg.ALIAS_SOURCE}\n"
            f"add extract {et}, integrated tranlog, begin now",
            self._ns_cho_phep(run))
        if not any("added" in l.lower() for l in lines):
            raise WizardError(f"Không xác nhận được việc tạo Extract {et}:\n"
                              + "\n".join(lines[-6:]))
        return {"msg": f"Đã tạo Extract {et}", "output": lines}

    async def _b_ns_add_exttrail(self, run: dict, ts: dict) -> dict:
        et, _, pre, _ = self._ns_ten(run)
        rc, lines = await gg.run_pham_vi(
            self.cfg,
            f"add exttrail {self.cfg.gg_home}/dirdat/{pre}, "
            f"extract {et}, megabytes 100",
            self._ns_cho_phep(run))
        if not any("added" in l.lower() for l in lines):
            raise WizardError("Không xác nhận được việc tạo trail:\n"
                              + "\n".join(lines[-6:]))
        return {"msg": f"Đã tạo trail dirdat/{pre} cho {et} (100 MB mỗi file)",
                "output": lines}

    async def _b_ns_add_replicat(self, run: dict, ts: dict) -> dict:
        et, rep, pre, ck = self._ns_ten(run)
        rc, lines = await gg.run_pham_vi(
            self.cfg,
            f"dblogin useridalias {gg.ALIAS_TARGET}\n"
            f"add replicat {rep}, integrated, "
            f"exttrail {self.cfg.gg_home}/dirdat/{pre}, checkpointtable {ck}",
            self._ns_cho_phep(run))
        if not any("added" in l.lower() for l in lines):
            raise WizardError(f"Không xác nhận được việc tạo Replicat {rep}:\n"
                              + "\n".join(lines[-6:]))
        return {"msg": f"Đã tạo Replicat {rep} (chế độ integrated, bảng checkpoint {ck})", "output": lines}

    # ------------------------------------------------- buoc 10-13
    async def _b_ns_start_extract(self, run: dict, ts: dict) -> dict:
        et, _, _, _ = self._ns_ten(run)
        ok, last, lines = await gg.start_process(self.cfg, et)
        if not ok:
            raise WizardError(f"{et} không chạy được, đang {last}")
        return {"msg": f"{et} đã chạy", "output": lines}

    async def _b_ns_cho_bat_kip(self, run: dict, ts: dict) -> dict:
        et, _, pre, _ = self._ns_ten(run)
        # HAI DIEU KIEN THEM (phien #7, 2026-10-20): buoc nay tung cho qua
        # sau 0,1 GIAY trong khi Extract chi RUNNING vai giay roi ABEND
        # (OGG-01044). Mot lan thay "RUNNING lag 0" la chua du:
        #   - phai thay RUNNING lien tuc it nhat 30 giay (Extract abend sau
        #     khoang 9 giay ke tu luc gan vao logmining server);
        #   - file trail dau tien `<pre>000000000` PHAI ton tai - Extract
        #     khong mo duoc trail thi khong bao gio tao file do.
        thay_chay: list[float] = []

        async def do() -> tuple[bool, str]:
            p = await gg.status_of(self.cfg, et)
            lag = p.lag_s if p else None
            if not (p and p.is_running):
                thay_chay.clear()
                return False, f"{et} {p.status if p else '?'}"
            if not thay_chay:
                thay_chay.append(time.monotonic())
            on_dinh = time.monotonic() - thay_chay[0]
            co_trail = any((self.cfg.gg_home / "dirdat").glob(f"{pre}0*"))
            xong = (lag is not None and lag <= 10 and on_dinh >= 30 and co_trail)
            return xong, (f"{et} RUNNING chậm {lag}s, ổn định {int(on_dinh)}s, "
                          f"trail {pre}*: {'có' if co_trail else 'CHƯA có'}")

        r = await cho.cho_den(do, tran_s=1200, nhan=f"{et} bắt kịp")
        if not r["xong"]:
            raise WizardError(
                f"{et} chưa bắt kịp sau {cho.gio_gon(r['giay'])} "
                f"({r['cuoi']}). Extract mới phải đọc redo từ thời điểm đăng "
                "ký tới hiện tại; nguồn sinh khoảng 1,06 TB redo mỗi ngày nên "
                "có thể lâu hơn. Tiến độ lưu ở server nên mở lại sau vẫn ở "
                "bước này.")
        return {"msg": f"{et} đã bắt kịp sau {cho.gio_gon(r['giay'])} "
                       f"({r['cuoi']})", "output": r["output"]}

    async def _b_ns_start_replicat(self, run: dict, ts: dict) -> dict:
        _, rep, _, _ = self._ns_ten(run)
        ok, last, lines = await gg.start_process(self.cfg, rep)
        if not ok:
            raise WizardError(f"{rep} không chạy được, đang {last}")
        return {"msg": f"{rep} đã chạy", "output": lines}

    async def _b_ns_kiem_chung(self, run: dict, ts: dict) -> dict:
        et, rep, pre, ck = self._ns_ten(run)
        out = []
        ps = {p.name: p for p in await gg.info_all(self.cfg)}
        for t in (et, rep):
            p = ps.get(t)
            if p is None or not p.is_running:
                raise WizardError(f"{t} không chạy: {p.status if p else 'không thấy'}")
            out.append(f"{t} {p.status} lag {p.lag_s}s")

        dsc = self.cfg.gg_home / "dirrpt" / f"{rep}.dsc"
        if dsc.exists():
            n = dsc.stat().st_size
            out.append(f"{dsc.name} = {n} byte (mốc rỗng của riêng file này)")
            out.append("GHI MỐC NÀY vào khối [discard_baseline] của conf/app.toml: mốc rỗng "
                       "KHÁC NHAU theo ĐỘ DÀI TÊN FILE, phải nhớ riêng từng cái.")
        tr = sorted((self.cfg.gg_home / "dirdat").glob(f"{pre}*"))
        out.append(f"trail: {', '.join(f.name for f in tr) or '(chưa có)'}")

        # LUONG_MOI / LUONG_LOG: in DUNG khoi da ghi len server (ma luong +
        # mo ta that, va `loai = "log"` neu la luong log) de nguoi dung chep
        # vao ban repo. NEW_STREAM (bam tay) chua co ma luong -> dung tien to.
        from . import toml_ghi as _tg
        if run["kind"] in ("LUONG_MOI", "LUONG_LOG"):
            _sid, _ten = self._ns_ma_luong(run)
            toml = _tg.khoi_streams(_sid, et, rep, pre, _ten,
                                    loai="log" if run["kind"] == "LUONG_LOG" else "")
        else:
            toml = _tg.khoi_streams(pre.upper(), et, rep, pre, "<mo ta luong>")
        self._luu_script(f"wz{run['id']}_app_toml.txt", toml)
        if run["kind"] not in ("LUONG_MOI", "LUONG_LOG"):
            out += ["", "Thêm khối dưới đây vào conf/app.toml TRÊN SERVER rồi chạy `run.sh "
                        "restart` để công cụ bắt đầu theo dõi luồng mới.",
                    "Công cụ KHÔNG tự sửa app.toml: cấu hình trên server là nguồn sự thật, "
                    "và deploy.sh có ý không đẩy file đó lên.", "", toml]
            return {"msg": f"Luồng mới {et}/{rep} đã chạy. Còn 1 việc làm tay: thêm khối "
                           "[streams] vào conf/app.toml", "output": out}

        # LUONG_MOI: khoi [streams] da duoc ghi o buoc DANG_KY_CAU_HINH. Con
        # MOC RONG cua file discard - thieu no thi `cfg.discard_baseline` tra
        # 0 cho Replicat moi va sinh canh bao discard GIA, vinh vien.
        from . import toml_ghi
        if dsc.exists():
            n_dsc = dsc.stat().st_size
            try:
                r = toml_ghi.them_discard_baseline(self.cfg.app_toml, rep, n_dsc)
                self.cfg.discard_baseline[rep] = n_dsc
                out.append("đã ghi mốc rỗng %s = %d vào [discard_baseline]%s"
                           % (rep, n_dsc, " (đã có sẵn)" if r.get("da_co") else ""))
            except Exception as e:                            # noqa: BLE001
                out.append(f"KHÔNG ghi được mốc discard cho {rep}: {e}")
                out.append("Thêm tay dòng `%s = %d` vào khối [discard_baseline] của "
                           "conf/app.toml, nếu không sẽ có cảnh báo discard GIẢ."
                           % (rep, n_dsc))
        out += ["",
                f"Cấu hình: đã đăng ký luồng {run['stream']} vào conf/app.toml ở bước "
                "ĐĂNG KÝ CẤU HÌNH (có lưu bản cũ) và nạp nóng - không cần khởi động lại.",
                "CÒN MỘT VIỆC LÀM TAY: deploy.sh cố ý không đẩy conf/app.toml, nên bản "
                "trong repo vẫn chưa có luồng này. Chép khối dưới đây vào bản repo, nếu "
                "không một lần deploy có đẩy cấu hình sẽ làm mất luồng.",
                "", toml]
        return {"msg": f"Luồng mới {et}/{rep} đã chạy và đã đăng ký vào cấu hình.",
                "output": out}

    # ---------------------------------------- buoc rieng cua LUONG_MOI
    async def _xt_ns_dang_ky_cau_hinh(self, run: dict) -> str:
        """Ban xem truoc = DUNG KHOI se duoc ghi vao conf/app.toml.

        Khong phai mot ban mo ta gan dung: nguoi doc cai gi thi file nhan
        dung cai do.
        """
        from . import toml_ghi

        et, rep, pre, _ = self._ns_ten(run)
        sid, ten = self._ns_ma_luong(run)
        dong = [f"# Ghi THEM vao {self.cfg.app_toml} - luu ban cu truoc, ghi",
                "# nguyen tu, roi DOC LAI doi chieu: "
                f"{len(self.cfg.streams)} luong dang chay phai y nguyen."]
        return chr(10).join(dong) + toml_ghi.khoi_streams(
            sid, et, rep, pre, ten, loai="log" if run["kind"] == "LUONG_LOG" else "")

    def _ns_ma_luong(self, run: dict) -> tuple[str, str]:
        """Ma luong trong app.toml + mo ta. Mac dinh ma = tien to trail."""
        ctx = json.loads(run["ctx_json"] or "{}")
        lm = ctx.get("luong_moi") or {}
        _, _, pre, _ = self._ns_ten(run)
        sid = (lm.get("sid") or pre).strip().upper()
        ten = (lm.get("ten")
               or f"Luồng tạo bởi quy trình #{run['id']} ({run['src_table']})")
        return sid, ten

    async def _b_ns_dang_ky_cau_hinh(self, run: dict, ts: dict) -> dict:
        """Ghi luong moi vao conf/app.toml va nap nong.

        VI SAO BUOC NAY O DAY, khong som hon va khong muon hon:
          - som hon: dang ky mot luong ma Replicat chua ton tai -> bo thu
            thap bao dong gia.
          - muon hon: buoc ke tiep (START_EXTRACT) va toan bo nua sau doc
            `cfg.streams[run["stream"]]` va `gg.PROCESSES`. Khong dang ky
            truoc thi chung bao "Process ngoai enum".

        Chay lai duoc: khoi da co va trung y het thi khong ghi lan hai.
        """
        from . import toml_ghi

        et, rep, pre, _ = self._ns_ten(run)
        sid, ten = self._ns_ma_luong(run)
        la_log = run["kind"] == "LUONG_LOG"
        r = toml_ghi.dang_ky_luong(self.cfg, sid, et, rep, pre, ten,
                                   loai="log" if la_log else "")
        self._ghi_run(run["id"], stream=sid)

        # Danh sach quan ly: bang nay tu gio thuoc luong moi. `in_extract` va
        # `has_map` dat 1 vi file tham so sinh o buoc TAO_PARAM DA co san ca
        # hai dong - khong phai mot lan doan.
        self.con.execute(
            f"UPDATE {self._bang_qly(run)} SET stream=?, in_extract=1, has_map=1,"
            " handlecollisions=?, updated_at=? WHERE src_table=?",
            (sid, 0 if la_log else 1, int(time.time()), run["src_table"]))

        # Kiem chung ngay: `_proc` phai tra ve bo ba, khong KeyError.
        p_et, p_rep, p_tr = self._proc({"stream": sid})
        if (p_et, p_rep, p_tr) != (et, rep, pre):
            raise WizardError(
                f"nạp nóng cấu hình không khớp: đọc lại được {p_et}/{p_rep}/{p_tr}")

        out = [f"mã luồng        = {sid}",
               f"extract         = {et}",
               f"replicat        = {rep}",
               f"tiền tố trail   = {pre}",
               f"file cấu hình   = {self.cfg.app_toml}"]
        if r.get("da_co"):
            out.append("khối [streams] đã có sẵn và trùng khớp - không ghi lại")
        else:
            out.append(f"bản lưu trước khi ghi = {r.get('backup')}")
        out += ["",
                "Đã đọc lại file sau khi ghi và đối chiếu: các luồng đang chạy "
                + ", ".join(sorted(k for k in self.cfg.streams if k != sid))
                + " y nguyên từng giá trị.",
                "Công cụ điều khiển được luồng mới NGAY, không cần khởi động lại."]
        return {"msg": f"Đã đăng ký luồng {sid} vào cấu hình và nạp nóng", "output": out}

    async def _b_ns_scn_floor(self, run: dict, ts: dict) -> dict:
        """Nguong duoi cua du lieu nap, cho LUONG MOI.

        KHONG dung `_b_scn_floor` chung: no doc moc High Watermark cua
        Replicat qua `info replicat detail`, ma mot Replicat CHUA TUNG CHAY
        thi khong in moc do -> buoc se FAIL moi lan.

        Nguong dung o day la SCN luc DANG KY capture (`register_scn`, do
        buoc REGISTER_EXTRACT ghi lai): moi anh du lieu tu moc do tro di deu
        chac chan duoc phan bat thay doi phu. Script nap chi dung nguong nay
        lam chan duoi (`IF v_scn < v_floor THEN RAISE`), nen lay dung bang
        SCN dang ky la an toan va khong qua chat.
        """
        scn = run["register_scn"]
        if not scn:
            raise WizardError(
                "Chưa có SCN đăng ký capture -> DỪNG. Ngưỡng dưới sai thì script nạp "
                "chặn sai hoặc bỏ lọt dữ liệu. Chạy lại bước đăng ký Extract.")
        self._ghi_run(run["id"], scn_floor=int(scn))
        return {"msg": f"Ngưỡng dưới = {int(scn):,} (SCN lúc đăng ký capture)",
                "output": [
                    f"register_scn = {int(scn)}",
                    "Mọi ảnh dữ liệu nạp ở SCN này trở đi đều được phần bắt thay đổi phủ.",
                ]}

    # =================================================================
    # ROLLBACK - phan nguy hiem nhat cua wizard nay
    # =================================================================
    async def rollback_new_stream(self, rid: int, who: str, reason: str) -> dict:
        """Don sach mot phien NEW_STREAM. SAU lenh, thu tu CO DINH.

        HAI CAI BAY, ca hai deu de lai rac VINH VIEN:

        1. `DELETE EXTRACT` ma quen `UNREGISTER EXTRACT ... DATABASE`
           -> capture MO COI o DB nguon, van giu archive log va dao redo vo
           ich MAI MAI. Vi vay `unregister` la buoc KHONG THE BO QUA, khong
           phai tuy chon: ham nay chay ca hai, va neu `unregister` loi thi
           bao DO.

        2. `DELETE REPLICAT` ma quen `dblogin` TRUOC
           -> inbound server `OGG$<R>` mo coi, VAN GIU streams pool.
        """
        run = self.lay(rid)
        if run["kind"] not in KIND_LUONG_MOI:
            raise WizardError("rollback nay chi danh cho quy trinh tao luong moi")
        et, rep, pre, ck = self._ns_ten(run)
        cp = self._ns_cho_phep(run)
        buoc: list[dict] = []
        out: list[str] = []

        # GO CAU HINH TRUOC khi cham vao GoldenGate (chi LUONG_MOI moi co
        # cau hinh de go). Thu tu nay co ly do: de lai mot luong trong
        # app.toml ma process da bi xoa thi bo thu thap se bao dong gia va
        # `_proc()` cua cac duong khac van tro vao do. Go khong duoc thi
        # DUNG LAI, chua xoa gi ca - con sua duoc.
        if run["kind"] in ("LUONG_MOI", "LUONG_LOG") and run["stream"]:
            from . import toml_ghi
            try:
                r_cfg = toml_ghi.bo_dang_ky_luong(self.cfg, run["stream"])
                buoc.append({"lenh": f"go [streams.{run['stream']}] khoi app.toml",
                             "ok": True,
                             "chi_tiet": "ban luu %s" % (r_cfg.get("backup") or "-")})
                out.append(f"da go khoi [streams.{run['stream']}] khoi {self.cfg.app_toml}")
            except Exception as e:                              # noqa: BLE001
                raise WizardError(
                    f"Không gỡ được [streams.{run['stream']}] khỏi conf/app.toml ({e}). "
                    "DỪNG LẠI, chưa xoá gì ở GoldenGate - sửa cấu hình rồi dọn lại.") from e

        # Dung process truoc khi xoa
        for t in (rep, et):
            try:
                ok, last, lines = await gg.stop_process(self.cfg, t, timeout_s=90)
                buoc.append({"lenh": f"stop {t}", "ok": ok, "chi_tiet": last})
                out += lines
            except Exception as e:                              # noqa: BLE001
                buoc.append({"lenh": f"stop {t}", "ok": False,
                             "chi_tiet": str(e)[:120]})

        # SAU lenh don, dung thu tu. `dblogin` TRUOC `delete replicat`.
        day = [
            (f"dblogin useridalias {gg.ALIAS_TARGET}\ndelete replicat {rep}",
             "delete replicat", True),
            (f"dblogin useridalias {gg.ALIAS_TARGET}\n"
             # DAU `!` BAT BUOC: khong co no GGSCI HOI "Are you sure you want
             # to delete this checkpoint table? [Y]es or [N]o", ma ggsci chay
             # khong co ban phim -> cau hoi nhan `exit` roi EOF va ggsci LAP
             # HOI VO TAN o 100% CPU. Goc cua ca hai `ggsci` treo trong su co
             # #6 (15:43 va 16:06, 2026-10-20).
             f"delete checkpointtable {ck} !", "delete checkpointtable", False),
            (f"dblogin useridalias {gg.ALIAS_SOURCE}\ndelete extract {et}",
             "delete extract", True),
            (f"dblogin useridalias {gg.ALIAS_SOURCE}\n"
             f"unregister extract {et} database", "unregister extract", True),
        ]
        # TRAN 90 GIAY MOI LENH, khong phai 300.
        #
        # Su co 2026-10-20: mot lenh don treo o ggsci; voi tran 300 giay va
        # bon lenh thi ca luot don co the keo 20 PHUT. Trong cua so do
        # ggadmin bi khoi dong lai, tac vu don chet giua duong va de lai
        # trang thai nua voi: cau hinh da go, Extract van con, phien van
        # OPEN, va mot `ggsci` mo coi an 94% CPU. Lenh delete/unregister
        # xong trong vai giay; 90 giay la rat rong roi, va treo thi phai LO
        # RA SOM de nguoi con kip dọn tay.
        that_bai: list[str] = []
        for script, nhan, bat_buoc in day:
            try:
                rc, lines = await gg.run_pham_vi(self.cfg, script, cp,
                                                 timeout=90)
                out += [f"--- {nhan} ---"] + lines
                buoc.append({"lenh": nhan, "ok": True, "chi_tiet": "da chay"})
                log.info("rollback #%s: %s xong", rid, nhan)
            except Exception as e:                              # noqa: BLE001
                buoc.append({"lenh": nhan, "ok": False,
                             "chi_tiet": str(e).splitlines()[0][:160]})
                # GHI LOG NGAY, khong doi den cuoi: tac vu nay co the bi
                # giet giua duong (app khoi dong lai), va luc do `output`
                # cua ham chua kip vao DB - con dong log thi van con.
                log.warning("rollback #%s: %s THAT BAI - %s", rid, nhan,
                            str(e).splitlines()[0][:200])
                if bat_buoc:
                    that_bai.append(nhan)

        # KIEM KET QUA THAT, khong tin vao viec lenh "khong nem loi".
        #
        # `gg.run()` KHONG nem loi khi GGSCI in ra ERROR - no tra ve cac
        # dong output. Nghia la mot `unregister` that bai van bi tinh la
        # "da chay", va day la lenh duy nhat ma that bai la de lai capture
        # MO COI o DB nguon. Nguoc lai, chay lai don tren mot phien da don
        # tay (su co #6) thi moi lenh deu in "does not exist" - cung khong
        # phai loi. Ca hai chieu chi phan xu duoc bang cach NHIN VAO HE
        # THONG: tien trinh con trong `info all` khong, capture con trong
        # `dba_capture@DBL_PMNV` khong.
        try:
            con_ps = {x.name for x in await gg.info_all(self.cfg)} & {et, rep}
        except Exception as e:                                  # noqa: BLE001
            con_ps = set()
            that_bai.append(f"không đọc được info all để kiểm ({str(e)[:80]})")
        if con_ps:
            that_bai.append("tiến trình vẫn còn trong info all: "
                            + ", ".join(sorted(con_ps)))
        out.append("--- kiem ket qua that ---")
        out.append("info all: " + (", ".join(sorted(con_ps)) + " VẪN CÒN"
                                   if con_ps else f"không còn {et} / {rep}"))
        cap = f"OGG$CAP_{et}"
        try:
            n_cap = await self.ora.scalar(
                "SELECT COUNT(*) FROM dba_capture@DBL_PMNV WHERE capture_name=:c",
                c=cap)
        except Exception as e:                                  # noqa: BLE001
            n_cap = None
            that_bai.append(f"không đọc được dba_capture để kiểm ({str(e)[:80]})")
        if n_cap:
            that_bai.append(f"capture {cap} VẪN CÒN ở DB nguồn")
        out.append(f"dba_capture@DBL_PMNV: {cap} "
                   + ("VẪN CÒN - MỒ CÔI" if n_cap else
                      "không còn" if n_cap == 0 else "không kiểm được"))
        for x in that_bai:
            log.warning("rollback #%s: %s", rid, x)

        # Xoa param + trail do phien nay tao - CHI KHI cac lenh BAT BUOC
        # da xong.
        #
        # Su co 2026-10-20: lenh don treo, Extract VAN CON trong `info all`,
        # nhung neu buoc nay cu chay thi no xoa mat file tham so cua mot
        # tien trinh dang ton tai -> process khong con gi de chan doan, va
        # lan sau khoi dong lai no abend voi mot loi khong lien quan
        # (OGG-01044 trail khong con gan voi extract). Con file thi con
        # hien truong, va buoc KIEM_TEN cua luot sau se tu chan vi
        # "<ten>.prm DA ton tai - phien truoc co the chua don sach".
        if that_bai:
            out.append("")
            out.append("GIU LAI file tham so va trail: lenh don bat buoc chua xong "
                       "(%s) nen tien trinh co the van con - xoa file luc nay la "
                       "xoa mat hien truong." % ", ".join(that_bai))
        else:
            for t in (et, rep):
                p = self.cfg.param_file(t)
                if p.exists():
                    p.unlink()
                    out.append(f"da xoa {p}")
            for f in sorted((self.cfg.gg_home / "dirdat").glob(f"{pre}*")):
                f.unlink()
                out.append(f"da xoa {f}")

        if that_bai:
            # CHUA SACH -> GIU phien OPEN. Truoc day nhanh nay van dat
            # ABORTED, va vi khoi "Don sach" chi hien cho phien dang mo, nut
            # don BIEN MAT dung luc con mot capture mo coi o DB nguon.
            self._ghi_run(rid, note=(
                f"[dọn CHƯA xong - bấm Dọn sạch lại sau khi xử lý] {who}: "
                + "; ".join(that_bai))[:480])
            out.append("")
            out.append("!!! CHƯA DỌN SẠCH. Kiểm TAY ngay:")
            out.append("  SELECT capture_name, status FROM dba_capture@DBL_PMNV;")
            out.append("     -> phải trở về ĐÚNG 3 dòng (không có tiến trình đọc redo mồ côi)")
            out.append("  SELECT server_name FROM dba_apply;")
            out.append("     -> không được còn OGG$" + rep)
            out.append("Tiến trình đọc redo mồ côi sẽ giữ archive log và đọc redo VĨNH VIỄN.")
            out.append("Phiên GIỮ trạng thái mở để bấm Dọn sạch lại được - chạy lại an toàn: "
                       "lệnh nào đã xong thì lần sau chỉ báo 'does not exist'.")
            raise WizardError(
                "Dọn CHƯA sạch: " + "; ".join(that_bai) + ". Phiên vẫn mở - xử lý rồi bấm "
                "Dọn sạch lại.")

        # Tien trinh da bi xoa -> app khong con duoc dieu khien chung nua.
        gg.PROCESSES.difference_update({et, rep})
        if run["kind"] in ("LUONG_MOI", "LUONG_LOG"):
            # Bang tro lai "chua thuoc luong nao" de con mo lai duoc phien
            # khac. KHONG dung toi du lieu da nap va KHONG tat TRANDATA -
            # hai viec do xoa tu dong la doan mo, de nguoi quyet.
            self.con.execute(
                f"UPDATE {self._bang_qly(run)} SET stream=NULL, in_extract=0, has_map=0,"
                " handlecollisions=0, updated_at=? WHERE src_table=?",
                (int(time.time()), run["src_table"]))
            out.append(f"danh sach quan ly: {run['src_table']} tro lai chua thuoc luong nao")

        self._ghi_run(rid, state="ABORTED", finished_at=int(time.time()),
                      note=f"rollback boi {who}: {reason}")

        out += ["",
                "KHÔNG đụng tới (cố ý, để người quyết): bảng đích đã tạo và dữ liệu đã "
                "nạp trong đó, và TRANDATA đã bật ở bảng nguồn."]
        return {"msg": f"Đã dọn sạch phiên #{rid}: {et}, {rep}, trail {pre}, {ck}",
                "buoc": buoc, "output": out}
