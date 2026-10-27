# ggadmin — quản trị Oracle GoldenGate qua web

Công cụ web để vận hành GoldenGate (21.3 Classic) đồng bộ dữ liệu từ **Core PMNV** (nguồn) sang **ODS** (đích, DB `BSHC`) tại BSH.
Thay cho việc SSH vào gõ `ggsci` bằng tay: xem trạng thái, cảnh báo, đối chiếu dữ liệu, và chạy các quy trình
thêm/gỡ bảng, tạo luồng mới theo từng bước có kiểm tra.

> Lần đầu dùng? Đọc mục **1 → 3** là đủ. Muốn hiểu *vì sao* công cụ làm như vậy: [`docs/Thiet-ke-va-bai-hoc.md`](docs/Thiet-ke-va-bai-hoc.md).

---

## 1. Mở công cụ

Web chỉ nghe ở `127.0.0.1` trên server, nên phải đi qua **đường hầm SSH**.

**Cách nhanh:** bấm đôi `tools\gg-admin.cmd`. Nó tự mở đường hầm rồi mở trình duyệt.
**Giữ cửa sổ đó mở** trong lúc dùng — đóng cửa sổ là đóng đường hầm.

**Cách tay:**

```bash
ssh -N -L 8099:127.0.0.1:8099 oracle@172.16.33.90
# rồi mở http://127.0.0.1:8099
```

Tài khoản đăng nhập: hỏi người quản trị công cụ (không ghi mật khẩu vào tài liệu).

Ghi nhớ:
- App **tự chạy** (cron `@reboot` + watchdog 5 phút). Không cần khởi động tay.
- Đóng đường hầm **không** làm dừng việc đang chạy (nạp dữ liệu, rebuild index…) — các việc đó chạy riêng trên server.
- Kiểm nhanh không cần trình duyệt: `ssh oracle@172.16.33.90 'bash /u01/app/ggadmin/run.sh status'`

## 2. Các trang

| Menu | Dùng để |
|---|---|
| **Tổng quan** | Tình trạng từng luồng: Extract/Replicat, độ chậm, trail, dòng bị bỏ (discard), mốc đã áp ở đích, đĩa, tablespace. Tự làm mới. |
| **Biểu đồ log** | Dung lượng mọi loại log trên server (trail GG, audit/alert/trace Oracle, log app, `/var/log`), tốc độ tăng, bao lâu nữa đầy đĩa, nhóm nào **xoá được** |
| **Bảng** | Danh sách bảng đang đồng bộ, đối chiếu 3 nguồn (`TABLE` của Extract · `MAP` của Replicat · bảng đã prepare ở nguồn) |
| **Tra cứu bảng** | Tìm một bảng đang nằm ở luồng nào, trạng thái ra sao |
| **Đối chiếu** | So số dòng + checksum nguồn/đích |
| **Lịch sử** | Biểu đồ lag, trail, đĩa… từ 1 giờ đến 90 ngày |
| **Thao tác nhanh** | Lối vào gọn cho các quy trình hay dùng (thêm bảng, tạo luồng mới…) |
| **Luồng log** | Luồng ghi **lịch sử thay đổi** (mỗi INSERT/UPDATE/DELETE ở nguồn thành một dòng trong bảng `<TÊN>_LOG`) |
| **Quy trình** | Các wizard từng bước (xem mục 3) |
| **Điều khiển** | Start/stop Extract, Replicat |
| **Tham số** | Xem/sửa file `.prm` (tự backup, tự kiểm cú pháp, lỗi thì tự trả lại bản cũ) |
| **Việc chạy** | Các việc chạy nền (nạp dữ liệu, index, thống kê) |
| **Log** · **Cảnh báo** · **Vết thao tác** | `ggserr.log` + discard · cảnh báo gửi Zalo · ai đã làm gì, lúc nào |
| **Hướng dẫn** | Hướng dẫn trong app |

Các luồng hiện có (khai trong `conf/app.toml`, mục `[streams.*]`):

| Mã | Extract → Replicat | Trail | Nội dung |
|---|---|---|---|
| PM | ETPMNV → RPMNVODS | pm | Chính (BH_*, KT_*) |
| HT | ETHT → RHT | ht | Danh mục, thanh toán, công nợ |
| DT | ETDTHU → RDTHU | dt | Báo cáo doanh thu |
| PMNV2 | ETPMNV2 → RPMNV2 | pa | PMNV đợt 2 |
| PMNV3 | ETPMNV3 → RPMNV3 | pp | PMNV đợt 3 |
| LOG | ETLOG → RLOG | lo | Luồng log "thanh toán phí" |

## 3. Quy trình (wizard)

Mỗi quy trình là một chuỗi bước cố định. Mỗi bước: **xem trước lệnh → chạy → kiểm chứng**.
Bước trước chưa PASS thì bước sau không chạy được. Trạng thái lưu ở server, nên tắt trình duyệt hay đổi người vẫn làm tiếp được.

| Quy trình | Dùng khi |
|---|---|
| `ADD_TABLE` | Thêm một bảng **mới** vào luồng đang chạy (tạo bảng đích, nạp dữ liệu, bật đồng bộ) |
| `RESYNC_TABLE` | Bảng đã đồng bộ nhưng **dữ liệu lệch** — nạp lại. Bước 1 kiểm có lệch thật không, không lệch thì dừng |
| `GO_BANG` | Gỡ một bảng khỏi luồng (không gỡ được bảng cuối cùng) |
| `LUONG_MOI` | Tạo luồng mới **trọn bộ**: Extract + Replicat + bảng đích + nạp + tự ghi cấu hình |
| `NEW_STREAM` | Như trên nhưng bấm tay từng bước hạ tầng |
| `LUONG_LOG` | Tạo luồng log mới + bảng log đầu tiên |
| `THEM_LOG` / `GO_LOG` | Thêm / gỡ bảng trong luồng log (bảng `_LOG` được giữ nguyên) |

**Mức nguy hiểm của thao tác:**

| Mức | Ví dụ | Phải làm gì |
|---|---|---|
| L0 — chỉ đọc | xem trạng thái, log | đăng nhập |
| L1 — hoàn tác được | start/stop, sửa tham số | ghi lý do |
| L2 — không hoàn tác | nạp lại, tạo/xoá tiến trình | gõ lại tên đối tượng + lý do + kiểm tra trước PASS; xác nhận hết hạn sau 60 giây |

Kiểm tra trước (pre-flight) **FAIL thì nút không bấm được** — không có ô "tôi chấp nhận rủi ro".
Khi đĩa `/` còn dưới 25 GB, mọi thao tác ghi bị khoá.

## 4. Sau khi tạo luồng mới — 2 việc phải làm tay

Công cụ cố ý **không có `sysdba`** nên không tự làm được hai việc sau:

1. **Cấp quyền đọc bảng checkpoint** cho user của công cụ. Thiếu thì trang Tổng quan báo
   `ORA-00942` ở dòng *Mốc đã áp ở đích*.

   ```bash
   sqlplus / as sysdba @/u01/app/ggadmin/tools/cs13_grant_ckpt_ro.sql   # chạy lại bao nhiêu lần cũng được
   ```

2. **Chép khối `[streams.<MÃ>]` mới vào `conf/app.toml` của repo** (bước cuối của quy trình in sẵn khối này).
   Công cụ đã tự ghi vào file trên server, nhưng bản trong repo thì không — nếu sau này deploy kèm cấu hình bằng bản cũ sẽ **mất luồng**.

## 5. Sửa code và triển khai

Mã nguồn ở máy Windows: `D:\code\ggadmin`. Server: `/u01/app/ggadmin` (mọi thứ nằm trong thư mục này, không dùng Python hệ thống).

```bash
bash tools/deploy.sh                 # kiểm cú pháp + chạy test + đẩy code (KHÔNG đẩy conf/app.toml)
ssh oracle@172.16.33.90 'bash /u01/app/ggadmin/run.sh restart'   # chỉ cần khi đổi file .py
ssh oracle@172.16.33.90 'curl -s http://127.0.0.1:8099/healthz'  # kiểm sau khi chạy lại
```

Hoặc gộp cả ba (có backup DB trước khi restart): `bash tools/trien_khai.sh`.

- Chỉ sửa template HTML thì không cần restart.
- `deploy.sh` **không đẩy `conf/app.toml`** vì bản trên server có cấu hình thật (luồng mới, nhóm Zalo). Muốn đẩy thật: `bash tools/deploy.sh <host> --with-config` (tự backup bản cũ).
- Test: `python tests/run_all.py` — không cần Oracle, ggsci hay pytest.

**Lần đầu cài đặt** (máy Windows có internet): `tools\make_wheels.ps1` (gói Python) và `tools\fetch_python.ps1` (Python 3.12 độc lập).

## 6. Tài khoản Oracle và bí mật

| User | Quyền | Dùng cho |
|---|---|---|
| `GGADM_RO` | Chỉ đọc: bảng `ODS`, bảng checkpoint `OGG_BSH`, từ điển dữ liệu | Hầu hết các trang |
| `GGADM_LOAD` | Đọc/ghi/xoá **chỉ trên các bảng được quản lý** | Nạp dữ liệu |
| `ODS` | Chủ schema | Rebuild index, thống kê, truncate |
| `OGG_BSH` | Qua credential store của GG (`USERIDALIAS pmnv_ogg` / `bshc_ogg`) | Chỉ `ggsci` dùng |

- Mật khẩu nằm ở `conf/secrets.ini` (quyền `0600`, không commit). Mẫu: `conf/secrets.ini.example`.
- File `.prm` không chứa mật khẩu dạng rõ.
- `conf/app.toml` cũng không commit; mẫu: `conf/app.toml.example`.

## 7. Gặp lỗi thường gặp

| Hiện tượng | Nguyên nhân hay gặp | Làm gì |
|---|---|---|
| *Mốc đã áp ở đích*: `ORA-00942` | `GGADM_RO` chưa có quyền SELECT bảng checkpoint của luồng mới | Chạy `tools/cs13_grant_ckpt_ro.sql` (mục 4) |
| Bước kiểm chứng FAIL `ORA-00942` trên bảng `ODS` | Bảng đích thiếu quyền cho `GGADM_RO`/`GGADM_LOAD` | `tools/cs12_dong_bo_quyen_dich.sql` |
| Mọi nút ghi bị khoá | Đĩa `/` còn dưới 25 GB | Xem **Biểu đồ log**, dọn nhóm "xoá được" |
| Trang không mở được | Đường hầm SSH đã đóng, hoặc app chết | Mở lại `gg-admin.cmd`; `run.sh status` / `run.sh restart` |
| Mất luồng sau khi deploy | Deploy `--with-config` bằng `app.toml` cũ trong repo | Lấy lại từ `conf/app.toml.<thời điểm>.bak` trên server |

## 8. Cấu trúc thư mục

```
app/            mã Python (FastAPI). wizard.py = các quy trình, adapters/ = ggsci + Oracle
web/templates/  giao diện (Jinja2 + htmx), web/static/ = CSS, uPlot
tests/          test chạy bằng python tests/run_all.py
tools/          script triển khai, script SQL cấp quyền (cs*.sql), backup DB
conf/           app.toml + secrets.ini (không commit, chỉ có file *.example)
docs/           kế hoạch, thiết kế và bài học
```
