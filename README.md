# ggadmin — công cụ quản trị Oracle GoldenGate (web)

Quản trị hệ GoldenGate 21.3 Classic tại BSH: 3 luồng, 73 bảng, ~374,8 triệu bản ghi.
Thay cho việc SSH vào gõ `ggsci` bằng tay, và **code hoá 3 runbook** đã chứng minh đúng qua 8 lô.

Kế hoạch: `docs/Ke-hoach-ggadmin.md` · Tài liệu nghiệp vụ gốc: `D:\code\ora_gg\*.md`

## Truy cập

**Cách nhanh nhất**: bấm đôi `tools\gg-admin.cmd` (đã copy ra Desktop). Nó mở
đường hầm SSH rồi mở browser. **Giữ cửa sổ đó mở** trong lúc dùng web — đóng
cửa sổ là đóng đường hầm.

Hoặc làm tay:

```bash
ssh -N -L 8099:127.0.0.1:8099 oracle@172.16.33.90
# roi mo http://127.0.0.1:8099   -  dang nhap: admin / a123
```

Hoặc thêm vào `~/.ssh/config` để gõ ngắn:

```
Host gg-admin
    HostName 172.16.33.90
    User oracle
    LocalForward 8099 127.0.0.1:8099
    ServerAliveInterval 30
```
rồi chỉ cần `ssh -N gg-admin`.

App **không cần khởi động tay**: cron `@reboot` + watchdog `*/5` giữ nó sống
(`run.sh start` là idempotent). Kiểm nhanh không cần browser:
`ssh oracle@172.16.33.90 'bash /u01/app/ggadmin/run.sh status'`.

Đóng đường hầm **không** làm chết job đang chạy: job nạp/index/stats chạy
ngoài tiến trình web ở session riêng.

Bind `127.0.0.1` vì `firewalld` đang bật và `oracle` không có sudo nên **không mở được port nào**. Và vì công cụ sửa được param file production, không phơi ra LAN là an toàn hơn — SSH lo luôn phần mã hoá và xác thực lớp một; đăng nhập trong app là lớp thứ hai để phân biệt *ai* trong audit log.

## Chạy ở đâu

| | |
|---|---|
| Server | `oracle@172.16.33.90` — OL 8.10, glibc 2.28, 16 vCPU, 31 GB RAM (~6 GB available) |
| Thư mục | `/u01/app/ggadmin/` — **toàn bộ nằm trong đây, `rm -rf` là gỡ sạch** |
| Runtime | CPython 3.12.15 standalone trong `py/` — **không đụng Python hệ thống (3.6.8)**, không cần sudo |
| Web | `uvicorn --host 127.0.0.1 --port 8099 --workers 1` |
| Tự chạy lại | cron `@reboot` + watchdog `*/5` (`run.sh start` idempotent) |
| DB | `oracledb` **thin mode** → không cần thư viện Oracle, tách hoàn toàn khỏi `$ORACLE_HOME` |

## Account

Công cụ **không dùng `sysdba`** và **không dùng `OGG_BSH`**:

| Account | Quyền | Dùng ở |
|---|---|---|
| `GGADM_RO` | `SELECT` 128 bảng `ODS` + 4 bảng `OGG_BSH` (3 checkpoint table + `GG_LOAD_SCN`) + `SELECT_CATALOG_ROLE` | 95% request |
| `GGADM_LOAD` | `SELECT/INSERT/DELETE` **đúng 73 bảng** — 55 bảng `ODS` còn lại không chạm được | Phase 2 trở đi |
| `ODS` (chủ schema) | sẵn có | `ALTER INDEX REBUILD`, `GATHER_TABLE_STATS`, `TRUNCATE` |

Cả hai `GGADM_*` **không có `UNLIMITED TABLESPACE`** nên không tạo được segment nào. Profile riêng `GGADMIN_SVC` có `PASSWORD_LIFE_TIME UNLIMITED` — profile `DEFAULT` là 180 ngày, dùng nó thì 180 ngày nữa công cụ tự chết.

`ggsci` dùng **GG credential store** (`USERIDALIAS pmnv_ogg` / `bshc_ogg`), nên `dirprm/*.prm` có **0/7** mật khẩu dạng rõ. Mật khẩu cho `sqlplus`/Python ở `conf/secrets.ini` (`0600`).

## Phase 1 — xong

### Trang chỉ đọc

| Trang | Nội dung |
|---|---|
| `/` Dashboard | 3 luồng × (Extract, Replicat), lag, trail, discard so mốc rỗng riêng từng file, checkpoint apply, capture/apply, đĩa, 11 datafile (tô đỏ file chạm trần 32 GB). Tự refresh 15s bằng htmx |
| `/tables` | Registry 73 bảng + **đối chiếu 3 nguồn sự thật** |
| `/verify` | Verify `COUNT(*)` + checksum `ORA_HASH` theo luồng, chạy nền, có tiến độ |
| `/history` | Đồ thị uPlot: lag, trail, discard, room tablespace, datafile, đĩa — 1 giờ đến 30 ngày |
| `/logs` | `ggserr.log` + 3 discard file, có bộ lọc ABEND/FATAL; `/logs/stream` là SSE |
| `/notify` | Kênh cảnh báo + 100 lần gửi gần nhất, có nút gửi tin thử |
| `/audit` | Audit log + kiểm hash chain |
| `/healthz` | Một dòng text cho cron/monitor ngoài |

### Đối chiếu 3 nguồn sự thật — cái bẫy số một của hệ này

`/tables` so ba danh sách: dòng `TABLE` trong Extract · dòng `MAP` trong Replicat · `dba_capture_prepared_tables` ở nguồn.

Bảng có (1) và (3) nhưng **thiếu (2)**: Extract đào redo và ghi vào trail, Replicat không có `MAP` nên bỏ qua bản ghi **và vẫn dời checkpoint** → mất dữ liệu **im lặng**: discard không tăng, `ggserr.log` sạch, `info all` vẫn RUNNING lag 0. Chỉ phát hiện được bằng cách đối chiếu.

Trạng thái đo được: **73 bảng, cả 73 đầy đủ cả 3 nguồn, 0 lệch**, 52/18/3 theo luồng, `KEYCOLS` tường minh 73/73, `HANDLECOLLISIONS` còn lại 0.

### Cảnh báo

9 luật + 3 luật registry/verify, ngưỡng **đọc từ DB** (`alert_rule`) nên cấu hình được. Ngoại lệ có chủ ý: ngưỡng chuyển app sang read-only toàn cục là hằng số trong code — đó là chốt an toàn, không nên nới lỏng từ UI.

`for_seconds` **đã cài**: chỉ mở cảnh báo khi vi phạm kéo dài đủ lâu. Cần thiết vì `RPMNVODS` có gai lag 465s/327s trong **đúng một tick** rồi về 0 — một lô bản ghi cũ trong trail được apply nên "Lag at Chkpt" phản ánh tuổi bản ghi, về vận hành không phải sự cố.

Kênh: `log` (luôn bật) · `webhook` (POST JSON) · `zalo_bot` (gửi vào group Zalo). Chống spam: 1 tin khi mở, 1 tin khi đóng, `CRIT` nhắc lại mỗi 60 phút, vượt 20 tin/giờ thì gom thành một tin.

## Triển khai

```powershell
# Tren may Windows co internet, lan dau
.\tools\make_wheels.ps1      # 21 wheel manylinux cho py3.12 -> wheelhouse\
.\tools\fetch_python.ps1     # CPython 3.12 standalone, verify SHA-256 upstream
```

```bash
bash tools/lint_sql.sh                        # chan 4 loi SQL*Plus hay tai pham
# deploy.sh tu chay: lint_sql.sh + compile() moi file .py + tests/run_all.py
bash tools/deploy.sh                          # day code, KHONG day config
bash tools/deploy.sh <host> --with-config     # day ca app.toml (tu backup ban cu)
ssh oracle@172.16.33.90 'bash /u01/app/ggadmin/run.sh restart'
```

**`deploy.sh` không đẩy `conf/app.toml` theo mặc định.** Lý do đã gặp thật: `chat_ids` của group Zalo đặt trên server, bản local còn rỗng; một lần deploy đẩy bản rỗng lên và kênh Zalo **im lặng tắt** — log vẫn sạch, cảnh báo cứ thế không ra khỏi server.

## Công cụ phụ

| | |
|---|---|
| `tools/run.sh {start\|stop\|restart\|status\|tail}` | quản lý tiến trình |
| `tools/lint_sql.sh` | chặn `PROMPT` kết thúc bằng `-`, `AS OF SCN` có subquery, `capture_message_number`, `DATA_DEFAULT_LENGTH` |
| `tools/zalo_find_chat.sh` | dò `chat_id` group Zalo từ `getUpdates` |
| `tools/cs1..cs8` | script Phase 0: credential store, `USERIDALIAS`, restart cuốn, tạo account, grant |

## Nguyên tắc không thương lượng

- **Không `sysdba`.** `ALTER INDEX REBUILD` và `GATHER_TABLE_STATS` chạy bằng chính chủ schema `ODS`.
- **Không có hộp nhập lệnh tự do.** `ggsci` chỉ nhận ~15 *intent* đã định nghĩa; blocklist là lớp thứ hai.
- **Mọi lệnh `ggsci` qua một hàng đợi tuần tự duy nhất** — ggsci không an toàn khi gọi song song.
- **Lag chỉ đọc từ `info all`**, không từ `info ... detail` (có dòng *Last Started* làm cột 4 thành chữ `Started`).
- **SCN khi verify chỉ là High Watermark** — không bao giờ Low WM hay `current_scn`. Không đọc được High WM thì **dừng, không verify**.
- **`AS OF SCN` dùng bind variable**, không subquery (`ORA-22818`).
- **Mọi SQL nằm trong file script**, không nhúng inline qua `ssh`.
- **Dòng `PROMPT` không kết thúc bằng `-`** (`SP2-0734`) — `lint_sql.sh` chặn.
- **Mọi script sinh ra lưu xuống `scripts/`** — công cụ không được là hộp đen.
- **Alias `@PMNV` vs `@BSHC` suy ra từ intent**, người dùng không chọn: cả 2 DB cùng `SERVICE_NAME=BSHC`.
- **Chống SQL injection 5 lớp**, quan trọng nhất là lớp 2: tên bảng người dùng chọn chỉ dùng làm **bind value** để tra catalog, rồi ghép vào SQL động bằng **chuỗi Oracle trả về**.

## Phase 2 — xong, đây là phase đầu tiên công cụ **ghi**

### Một đường ghi duy nhất

```
POST /action/arm   -> đo pre-flight, nạp đạn, trả fragment xác nhận
POST /action/fire  -> bắn cò (token 60s, L2 phải gõ tay tên)
```

Thêm một thao tác ghi mới = thêm **một dòng** vào bảng `OPS` trong `main.py`, và nó tự động được bọc đủ khung. Làm ngược lại — một endpoint riêng cho mỗi việc — thì sớm muộn sẽ có một endpoint quên một lớp.

Sáu `OPS` hiện có: `START` · `STOP` · `ADD_TRANDATA` · `PARAM_SAVE` (L1) · `VERIFY_SNAPSHOT` · `RESTART_ADD_TABLE` (L2).

### Ba mức, không phải hai

| Mức | Thao tác | Yêu cầu |
|---|---|---|
| **L0** đọc | `info all`, verify `LIVE`, xem log/param | đã login |
| **L1** hoàn tác được | start/stop, `add trandata`, ghi param | diff chính xác + lý do ≥10 ký tự |
| **L2** không hoàn tác | verify `SNAPSHOT_SCN`, restart có thứ tự | **toàn bộ** nghi thức bên dưới |

Nghi thức L2, thiếu một là không mở được nút: **nhập lại** tên đối tượng (máy chủ đối chiếu chuỗi này với đối tượng sắp bị tác động; ô nhập **cho dán** từ 2026-10-15, theo yêu cầu người dùng) · pre-flight **PASS** · lý do bắt buộc · **token hết hạn 60 giây** · audit ghi **trước** khi thực thi.

Token 60 giây không phải để chống kẻ tấn công — ai vào được UI thì đã có SSH. Nó để chống **chính người dùng**: mở tab xác nhận lúc 9h, đi họp, 11h quay lại bấm tiếp trong khi trạng thái hệ thống đã khác hẳn. `fire` **đo lại pre-flight** ngay trước khi chạy và từ chối nếu kết quả đã đổi.

L1 và L2 đều chỉ cần role `OPERATOR`. Cả 2–5 DBA đều có SSH và root trên server nên dùng role để chặn họ là nghi thức rỗng; chốt an toàn thật ở đây là **nghi thức + audit**. `ADMIN` chỉ giữ những gì thuộc về chính công cụ: audit, tài khoản.

### Pre-flight — nơi công cụ tạo ra giá trị lớn nhất

Công cụ **làm tăng tần suất** thao tác nguy hiểm — đó là mục đích — mà không tự động làm tăng dung lượng đĩa. Dự án tạo ra gia tốc mà không tạo ra phanh. Làm tay thì mỗi lần `TRUNCATE` có người đọc `df -hP /` trước; đặt sau một nút thì ma sát, sự chú ý, và việc chỉ một người biết cách làm **biến mất cùng lúc**.

Mọi thao tác ghi đều qua 3 check chung (số liệu giám sát phải mới, `/` đo **lại** tại chỗ chứ không dùng số của tick trước, Manager RUNNING) cộng check riêng theo `op`. FAIL thì **nút không mở được**, chỉ có "xem chi tiết" — **không có checkbox "tôi hiểu rủi ro"**.

Check đáng chú ý nhất là `table_khop_map` của `START`: start một Extract đang có nhiều dòng `TABLE` hơn số dòng `MAP` của Replicat cặp đôi là **mất dữ liệu im lặng**.

### Param editor

`backup cp -p có timestamp → ghi nguyên tử (temp + os.replace) → checkprm → FAIL thì **tự restore**`. Mọi lần sửa vào bảng `param_version` kèm diff; bản đã áp lưu thêm vào `scripts/`.

Hai luật tuyệt đối: **không bao giờ** ghi param của process đang RUNNING (process chỉ đọc param lúc khởi động, nên sửa lúc đang chạy là tạo ra thay đổi có hiệu lực ở thời điểm không ai đoán được), và **giảm số dòng `MAP` bị chặn** vì đó là mất dữ liệu im lặng.

Guard nội dung chặn 5 thứ: `USERID ... PASSWORD` dạng rõ (0/7 của Phase 0 không được thoái lui) · bỏ `USERIDALIAS` · `MEGABYTES` trên **tham số** `EXTTRAIL` · `HANDLECOLLISIONS` **toàn cục** · giảm `MAP`/`TABLE`.

### Verify chế độ chuẩn

```
stop extract -> chờ drain (rba = kích thước file trail VÀ lag 00:00:00)
-> stop replicat -> đọc High WM -> so sánh -> start lại (replicat trước, extract sau)
```

Luồng được start lại **trong khối `finally`** — kể cả khi verify lỗi hoặc job bị huỷ. Một luồng production dừng sau một job đo đạc thì hỏng nặng hơn hẳn việc không đo được.

Vòng chờ drain **không bao giờ** chờ `Low WM = High WM`: hai mốc đó không gặp nhau, vòng chờ sẽ chạy hết số lần rồi báo thất bại trên một hệ hoàn toàn bình thường.

### Test

`python tests/run_all.py` — **74 ca, không cần pytest**, không cần Oracle/ggsci/SQLite. Một bộ test phải cài thêm gói mới chạy được thì sẽ không ai chạy. Deploy đẩy cả `tests/` lên server.

Parser ggsci test bằng **fixture là output thật đã ghi lại**, không mock bằng tay — gồm ca `info all` vs `info ... detail` để khoá vĩnh viễn cạm bẫy 26.

## Ba lỗi chỉ lộ ra khi chạy thật — 2026-10-14

Đáng ghi lại vì cả ba **không thể phát hiện bằng đọc code**:

| Lỗi | Hậu quả nếu không sửa |
|---|---|
| `trail_file()` đoán **6 chữ số** (`ht000005`) trong khi bản 21.3 ghi **9** (`ht000000005`) | `exists()` False → `trail_bytes` None → `drained` **không bao giờ** True → `wait_drain` chạy hết thời gian chờ trên hệ bình thường → `quiesce()` start lại Extract và báo thất bại. **Verify chế độ chuẩn sẽ luôn lỗi.** |
| `detail()` hardcode `ALIAS_TARGET` cho **cả** Extract lẫn Replicat | Vi phạm đúng luật của chính dự án. Cả 2 DB cùng `SERVICE_NAME=BSHC` nên nhìn màn hình không phát hiện ra. Đã tách thành `alias_for(process)` |
| `drain_state()` không nói **vì sao** chưa drain | Vòng chờ hết giờ mà không biết tại sao — đúng chỗ đã ẩn lỗi trên. Giờ có trường `vi_sao` |

Cả ba đã có test hồi quy. Bài học: ghi cạm bẫy vào tài liệu không ngăn được tái phạm; **đối chiếu với output thật trên server** mới ngăn được.

### Lỗi thứ tư, cùng họ — phát hiện khi chạy quiesce thật

`drain_state()` đo trên **Extract** thay vì trên **Replicat**. RBA của Extract bằng kích thước file trail **ngay khi nó dừng flush**, nên điều kiện "rba = kích thước trail" đúng ngay lập tức và **không nói gì** về việc Replicat đã apply hết trail chưa. Tệ hơn: lag của một Extract **đã dừng** đóng băng ở giá trị lúc dừng (`00:00:01`) và **không bao giờ** về `00:00:00`.

Hậu quả đo được: vòng chờ chạy **290 giây không nhúc nhích** trên một hệ hoàn toàn bình thường, và sẽ chạy đến hết 870 giây rồi abort.

Điều kiện đúng, đúng như runbook: bên **đọc** phải đọc hết bên **ghi** đã ghi — (1) Replicat đang đọc đúng file trail cuối cùng Extract đã ghi, (2) `rba` của Replicat = kích thước file đó, (3) lag của **Replicat** = `00:00:00`.

### Lỗi thứ năm: `ast.parse()` không bắt được `await` trong hàm sync

Một đoạn code bị `str.replace()` chèn vào **cả hai** chỗ khớp, trong đó một chỗ là hàm sync. `ast.parse()` cho qua, `compile()` mới báo. App deploy lên **không start được**, 13 endpoint trả `000`.

`deploy.sh` giờ chạy `compile()` trên mọi file `.py` **và** `tests/run_all.py` trước khi `scp`.

## Phase 3 + 4 — xong: ba wizard

| Wizard | Bước | L2 | Dùng khi |
|---|---|---|---|
| `ADD_TABLE` | 23 | 5 | bảng **chưa có `MAP`**, thêm vào luồng đang chạy |
| `RESYNC_TABLE` | 15 | 4 | bảng **đã có `MAP`** nhưng dữ liệu lệch |
| `NEW_STREAM` | 13 | 5 | tạo Extract + Replicat **mới** (đường bấm tay từng bước) |
| `LUONG_MOI` | 29 | 9 | tạo luồng mới **trọn bộ**: hạ tầng + bảng đích + nạp dữ liệu + tự đăng ký vào `conf/app.toml` |

### State ở server, không ở client

Không phải tiện nghi mà là **bắt buộc đúng nghiệp vụ**: có bước nạp dài hàng chục phút và bước chờ `Low WM` vượt `MAXSCN`. State ở client mất trắng khi tunnel đứt hoặc DBA F5. State ở server thì hôm sau mở lại wizard vẫn ở bước 8, và **một DBA khác tiếp được việc của người trước**.

Mỗi bước có 4 phần cố định: *điều kiện vào → xem trước lệnh → chạy → kiểm chứng*. **Không bước nào chạy khi bước trước chưa PASS** — đó là luật của engine, không phải quy ước của từng bước. Và **một luồng chỉ một wizard** cùng lúc.

Bước ghi của wizard đi qua **đúng** khung an toàn Phase 2 (`arm` → `fire`). Không có khung an toàn thứ hai: một hệ có hai khung an toàn thì sớm muộn sẽ có một khung thiếu một lớp.

### Hai chốt không thể bỏ qua

**`ADD_TABLE` — thứ tự là hằng số.** `stop replicat` (bước 5) trước `restart extract` (bước 7–9). Thứ tự nằm trong **danh sách bước**, và danh sách bước là hằng số trong code — UI không có chỗ đảo.

**`RESYNC_TABLE` — bước 1 "bảng có THỰC SỰ lệch?" không bỏ qua được.** Mục 0 của runbook tồn tại vì sai lầm này đã xảy ra thật: nạp lại một bảng không hề lệch. Engine biến nó thành *không thể* bỏ qua. Đã thử trên `TT_MA_NT`: nguồn 1.174 = đích 1.174, checksum trùng → bước 1 **FAIL**, `cur_step` vẫn là 1, mọi bước sau vẫn `CHO`.

Khác `ADD_TABLE`: `RESYNC` **giữ index online** (không mark UNUSABLE), ghi `rows_before_truncate` **trước** khi xoá, và verify **hai lần**.

### Script nạp — ba lớp chặn

Sinh ra `scripts/wz<id>_nap_<bảng>.sql`, lưu xuống đĩa để DBA soi lại hoặc chạy tay — **công cụ không được là hộp đen**.

1. **đích phải RỖNG** — nạp vào bảng có sẵn là nhân bản dữ liệu
2. **SCN nạp ≥ `SCN_FLOOR`** — `RAISE_APPLICATION_ERROR`; nạp ở SCN cũ hơn floor là **bỏ lọt** dữ liệu mà `HANDLECOLLISIONS` không cứu được
3. **fallback conventional** — `INSERT /*+ APPEND */` **không** được `HANDLECOLLISIONS` bảo vệ, gặp `ORA-00001` là abend; script tự thử lại kiểu thường

### `NEW_STREAM` — lớp khoanh vùng lệnh

Đây là luồng **nguy hiểm nhất**: nó `REGISTER` một capture process vào **DB nguồn production**.

Nhóm lệnh `REGISTER`/`UNREGISTER`/`ADD`/`DELETE` `EXTRACT|REPLICAT|EXTTRAIL|CHECKPOINTTABLE` **không chặn được** — wizard cần chính chúng. Luật thay thế, chặt hơn chặn thuần:

1. chỉ chạy **bên trong** một phiên `NEW_STREAM` đang mở, ở đúng bước
2. chỉ tác động lên tên **chính phiên đó tạo ra**
3. **6 process + 3 trail + 3 checkpoint table đang chạy là bất khả xâm phạm**

Thứ tự kiểm **không được đảo**: lớp 3 trước lớp 2. Nếu đảo thì một phiên khai `cho_phep={"RHT"}` sẽ xoá được Replicat thật. Đã test: cố tình khai `RHT`/`ETHT`/`ETPMNV`/`OGG_BSH.RHT` vào danh sách cho phép → **vẫn bị từ chối**.

Thứ tự lệnh cũng là hằng số, hai chỗ dễ đảo nhất và cả hai đã từng sai: `REGISTER EXTRACT` **trước** `ADD EXTRACT`; `ADD CHECKPOINTTABLE` **trước** `ADD REPLICAT` (`GLOBALS` rỗng nên không có `CHECKPOINTTABLE` mặc định mà cú pháp lại bắt buộc chọn).

**Hai việc công cụ không làm, có chủ ý:** không tự chạy `ALTER SYSTEM SET streams_pool_size` (chỉ kiểm tra hai đầu, báo đỏ, sinh lệnh cho DBA chạy tay), và **không tự đặt tên** — DBA gõ, công cụ kiểm trùng.

### `LUONG_MOI` — tạo luồng mới trọn bộ, tự đăng ký (2026-10-15)

Ô thứ ba của **Thao tác nhanh**. 29 bước, ghép hạ tầng của `NEW_STREAM` với phần đưa dữ liệu vào của `ADD_TABLE`, cộng một bước mới: **`DANG_KY_CAU_HINH`** (bước 14).

**Ba điều của thứ tự này không được đảo**, và mỗi điều có một cách hỏng riêng:

1. `XAC_NHAN_KEYCOLS` (3) trước `TAO_PARAM` (8) — cột khoá **nhúng thẳng** vào dòng MAP sinh ra ở bước 8, và không bước nào sửa lại dòng đó.
2. `KIEM_BANG_DICH` (6) trước `REGISTER_EXTRACT` (10) — bẫy thiếu quyền phải nổ **trước** khi đăng ký capture vào DB nguồn.
3. `DANG_KY_CAU_HINH` (14) trước `START_EXTRACT` (15) — từ đó trở đi các handler dùng chung đọc `cfg.streams[...]` và `gg.PROCESSES`.

**Cố ý KHÔNG có** `THEM_MAP`/`THEM_TABLE`/`STOP_EXTRACT`: file tham số sinh ở bước 8 **đã có sẵn** cả hai dòng, chạy lại là nhân đôi dòng MAP. Cửa sổ bảo trì cũng không tồn tại — Replicat mới toanh, đã dừng sẵn suốt đợt nạp.

**`SCN_FLOOR` là handler riêng**: `_b_scn_floor` dùng chung đọc High Watermark của Replicat, mà Replicat chưa từng chạy thì không in mốc đó. Ngưỡng đúng ở đây là **`register_scn`** — SCN lúc đăng ký capture.

**Ghi `conf/app.toml` (`app/toml_ghi.py`)** — chỉ THÊM văn bản, không serialise lại: lưu bản cũ có đóng dấu thời gian → ghi nguyên tử (`tmp` cùng thư mục + `fsync` + `os.replace`) → **đọc lại và đối chiếu từng giá trị** của các luồng cũ; lệch một chữ thì trả file về bản cũ và dừng. Chạy lại được (khối trùng y hệt thì không ghi lần hai). Mốc discard cũng được ghi, và phải chèn **ngay sau** dòng `[discard_baseline]` — append cuối file thì khoá rơi vào bảng cuối cùng (`[notify.zalo_bot]`) và sinh cảnh báo discard giả vĩnh viễn.

**Hai danh sách tên, hai mục đích** (`app/adapters/gg.py`): `PROCESSES` là app được phép **điều khiển**; `BAT_KHA_XAM_PHAM` là app **không được tạo/xoá/đăng ký**. Tên của một phiên đang mở nằm ở danh sách đầu và **không** ở danh sách sau — nếu không thì chính chức năng dọn sạch của phiên bị lớp 3 chặn.

**Luật đặt tên theo vai trò** (`kiem_ten_moi`, đặt **sau** lớp bất khả xâm phạm — thứ tự lớp không đảo): Extract **phải** bắt đầu bằng `ET`, Replicat **phải** bắt đầu bằng `R`. Không phải quy ước thẩm mỹ: `alias_for()`, `start_process()` và `stop_process()` suy ra vai trò từ ký tự đầu của tên, nên một Replicat tên `ETX` sẽ bị chạy bằng `start extract` và dblogin vào **DB nguồn**. Chặn từ lúc đặt tên, vì đổi tên một process đã tạo thì phải xoá đi tạo lại. Sáu quy trình đang chạy đều theo đúng luật này.

**Hỏng đâu dọn đó** (yêu cầu người dùng): bộ lái tự chạy `rollback_new_stream` ngay khi một bước không đạt, ở **bất kỳ** bước nào sau khi đã chốt tên. Hiện trường trong DB của công cụ được giữ nguyên trước khi dọn, có một dòng audit `AUTO_ROLLBACK`, và **không bao giờ tự dọn lần hai**. Đánh đổi đã biết: lệnh dọn chạy `UNREGISTER EXTRACT … DATABASE` lên chính DB nguồn, và hỏng ở nửa sau thì mất cả công nạp.

### Rollback của `NEW_STREAM`

Sáu lệnh dọn, thứ tự cố định, và **`UNREGISTER EXTRACT ... DATABASE` là bước không thể bỏ qua**: `DELETE EXTRACT` mà quên nó thì để lại capture **mồ côi** ở DB nguồn, vẫn giữ archive log và đào redo vô ích **mãi mãi**. Tương tự `DELETE REPLICAT` mà quên `dblogin` trước thì để lại inbound server `OGG$<R>` mồ côi **vẫn giữ streams pool**. Nếu bước bắt buộc nào lỗi, rollback **báo đỏ** và in ra 2 câu SQL để kiểm tay.

## Ba mục đã vá — 2026-10-14 (sau rà soát tồn đọng)

### 1. Backup SQLite — `tools/backup_db.py`, cron 01:30 hằng ngày

Dùng `Connection.backup()` của Python, **không** dùng `cp`: `cp` một file SQLite đang ở chế độ WAL trong lúc app đang ghi sẽ cho ra bản sao **có thể hỏng** — WAL nằm ở file khác (`-wal`) và checkpoint có thể đang dở dang. Server này không có `sqlite3` CLI nên cũng không dùng `.backup` của CLI được.

Backup **cả** `logs/audit-YYYYMM.jsonl`: chuỗi hash audit nằm ở **hai** nơi và sự trùng lặp đó là có ý — một hệ audit mà kẻ tấn công xoá được bằng cùng đặc quyền thì không phải audit. Backup chỉ lấy DB thì mất nửa ý nghĩa của việc trùng lặp.

**Không** backup `conf/secrets.ini`: nhân bản mật khẩu ra thêm một chỗ với bảo đảm yếu hơn là giảm an toàn, không phải tăng.

Mỗi lần backup đều `PRAGMA integrity_check` trên **bản sao** trước khi coi là thành công — một bản backup chưa bao giờ mở thử thì không phải backup. Giữ 30 bản, nén gzip (~1 MB/bản).

### 2. Index UNUSABLE/REBUILD + phát hiện rủi ro `ORA-01555`

`ADD_TABLE` từ 20 lên **22 bước**: thêm `INDEX_UNUSABLE` (bước 12) và `INDEX_REBUILD` (bước 15), và `START_REPLICAT` chuyển xuống bước 17 — Replicat dừng suốt cửa sổ nạp nên đích đóng băng, đúng thứ tự runbook đã chạy 8 lô. Lô BC đo được: nạp **nhanh ×2,3–3,8** và tránh index phình +64%.

**Chỉ** mark index GLOBAL NONUNIQUE. **Giữ index UNIQUE ở trạng thái VALID** — chúng là thứ duy nhất bắt được dòng trùng *ngay* trong lúc nạp; mark hết cho nhanh rồi phát hiện trùng sau khi đã nạp 100 triệu dòng thì không còn đường lui nào ngoài `TRUNCATE` làm lại.

`REBUILD ... PARALLEL 8` đặt **độ song song cố định** cho index nên mọi truy vấn về sau sẽ lấy kế hoạch song song. Script tự chạy `NOPARALLEL` ngay sau — cái bẫy rất dễ bỏ qua.

**Quyết định chia lô**: ước thời gian nạp bằng **biên dưới** của tốc độ đã đo (15.000 dòng/giây, trong khoảng 11k–100k) rồi so với `tuned_undoretention` ở nguồn. Vượt 70% thì **chặn** kèm ba đường đi thật. Lô 4 nạp 64 phút đã ăn `ORA-01555` + `ORA-02063` thật.

Công cụ **cố ý không tự chia lô**. Chia lô đúng phải phân tích `high_value` của từng partition — một cột `LONG`, đã từng gây `ORA-00932`. Viết trình phân tích đó mà không có bảng nào để thử là đúng cách làm đã sinh ra 5 lỗi trong phiên xây dựng.

Script nạp giờ có **bốn** lớp chặn, trong đó lớp 2b **đọc lại** `tuned_undoretention` ngay trước khi nạp: giá trị đó **dao động mạnh** (đo được 900 → 2253 → 21241 → 27528 giây chỉ trong vài ngày) và script có thể chạy nhiều giờ sau khi sinh.

### 3. Job runner detach thật — `app/jobs.py`

`subprocess.Popen(..., start_new_session=True)` đặt con vào **session mới** nên nó không nhận tín hiệu gửi cho process group của uvicorn.

**Mật khẩu không đi qua argv.** `sqlplus user/pw@dsn` để mật khẩu lộ trong `ps` cho mọi user trên máy. Thay vào đó `tools/job_connect.py` đọc `secrets.ini` (0600) rồi in câu `connect` ra stdout, pipe vào `sqlplus -S /nolog` — mật khẩu chỉ tồn tại trong một pipe.

**Chính tiến trình job cập nhật trạng thái** qua `tools/job_done.py`, không phải app. Để app ghi thì lại quay về đúng vấn đề ban đầu. `job_done.py` coi là thất bại khi `rc != 0` **hoặc** log có dòng `ORA-`: `WHENEVER SQLERROR EXIT FAILURE` có thể bị một khối `EXCEPTION` bắt mất, lúc đó sqlplus trả 0 trong khi script thất bại.

**Bước wizard có thể CHỜ một job rời ra**: handler trả `cho_job`, bước giữ trạng thái `DANG`, UI poll `/wizard/<id>/job`. Và `kiem_job()` chỉ cho PASS sau khi đã chạy `_xn_<tên_bước>` để **kiểm chứng trạng thái cuối trên DB** — mã thoát 0 không đủ để kết luận việc đã xong đúng.

**Diễn tập thật**: job #6 spawn 09:53:43, restart app giữa lúc chạy, job **giữ `RUNNING`**, tự kết thúc 09:54:59 với `SUCCEEDED` và `result_json`. Mật khẩu 0 lần trong log.

Lỗi phát hiện trong chính lần diễn tập đầu: `_reconcile_jobs()` đánh dấu `KILLED` **oan** cho job detach đang chạy. Nó được viết khi job còn chạy trong tiến trình web. Hậu quả thật nếu không sửa: UI báo `KILLED` suốt một giờ trong khi đợt nạp vẫn chạy, **và** pre-flight `khong_co_job_khac` cho phép một đợt nạp thứ hai khởi động. Giờ `jobs.con_song()` kiểm **hai** điều — pid còn tồn tại **và** cmdline còn chứa `job_<id>.sh` — vì chỉ kiểm pid thì gặp PID reuse sẽ kết luận sai.

### Quyền trên schema ODS — ba đường đi, theo thứ tự quyền từ hẹp đến rộng

`ALTER INDEX`, `GATHER_TABLE_STATS` và `TRUNCATE` cần quyền của **chủ schema** `ODS`. `GGADM_LOAD` cố ý **không** có `ALTER ANY INDEX`/`ANALYZE ANY`.

1. **`ODS.GGADM_API`** — package definer-rights, `tools/cs10_ods_index_api.sql` (chạy một lần bằng sysdba). `GGADM_LOAD` có đúng 4 ý định trên 73 bảng, **không** quyền `ANY` nào, **không** giữ mật khẩu chủ schema. Phạm vi **tự giới hạn** ở các bảng mà `GGADM_LOAD` đã có quyền `INSERT` — đọc `user_tab_privs_made` nên không cần đồng bộ danh sách tay. Không procedure nào `DROP` được gì.
2. **khối `[ods]`** trong `secrets.ini` — chạy được, nhưng công cụ **giữ mật khẩu chủ schema**, tức giữ cả khả năng `DROP` bất kỳ object nào trong 3.600 object của `ODS`. Tính chất *"dù web app bị chiếm quyền hoàn toàn, kẻ tấn công không `DROP` được bảng nào"* **không còn đúng**.
3. **hướng dẫn chạy tay** — và **không tin** vào việc "đã chạy": lần bấm lại, bước sẽ **đo lại** trạng thái trên DB và chỉ cho PASS nếu số đo đúng.

Hiện đang ở đường **2**. `ODS` vốn không có `CREATE SESSION` (chỉ role `RESOURCE`) — cấu hình **đúng** cho một schema owner; role `CONNECT` được thêm vào để mở đường này. Nên chạy `cs10` rồi `REVOKE CONNECT FROM ODS` để về đường 1.

## `ADD_TABLE` chạy thật lần đầu — `HT_MA_KVUC` vào luồng HT, 2026-10-14

**23/23 bước PASS, phiên HOÀN TẤT.** Verify cuối: 3.355 = 3.355 dòng, checksum trùng khít `7162391230926`, 14 cột. Cửa sổ bảo trì (RHT dừng) **~9 phút**.

Bảng: `KT_BSH.HT_MA_KVUC` → `ODS.HT_MA_KVUC`, 3.355 dòng, 14 cột, PK thật `MA_KVUC`, `SCN_FLOOR = 163.406.205`, `MAXSCN = 163.531.599`.

Registry giờ **74 bảng, cả 74 đủ cả 3 nguồn, 0 lệch**. `TABLE 52/19/3`, `MAP 52/19/3`.

### Ba lỗ hổng lộ ra và đã vá

Lần chạy thật đầu tiên của một luồng 23 bước lộ ba thứ mà không lần đọc code nào tìm ra:

**1. Không có đường đăng ký bảng mới vào registry.** Registry là allowlist lớp 5 chống SQL injection, và `registry.sync()` chỉ gộp từ 3 nguồn (`TABLE`/`MAP`/`prepared`). Một bảng chưa thuộc luồng nào không có trong nguồn nào → `wizard.tao()` từ chối, và **không có cách nào** đưa nó vào.

→ Thêm op `REGISTRY_ADD` (L1, có audit): kiểm bảng **có thật** ở `KT_BSH` rồi insert với cả 3 cờ bằng 0. Và `sync()` giờ xoá `note` khi bảng đủ 3 nguồn — bản ghi đăng ký buộc phải có `note` để không bị câu đánh dấu stale ghi đè, nhưng ghi chú *"ứng viên mới, chưa vào luồng"* thành **sai** khi bảng đã vào luồng.

**2. Không tạo được bảng đích.** `ODS.HT_MA_KVUC` chưa tồn tại; bước `KIEM_BANG_DICH` từ chối đúng nhưng bế tắc — mỗi lần thêm bảng phải chạy `gen_target_ddl.py` ngoài công cụ rồi sửa tay.

→ Thêm `app/ddl.py` + bước `TAO_BANG_DICH` (L2, bước 3). Sinh DDL từ metadata nguồn: cột + `NOT NULL` + PK + index + grant. Hai thứ **cố ý không mang sang**: `DEFAULT` (GG cung cấp đủ giá trị mọi cột) và `FK`/`CHECK` — **một FK ở đích sẽ làm Replicat ABEND** khi thứ tự apply khác thứ tự phát sinh ở nguồn, và thứ tự đó không được bảo đảm. Kiểu lạ (LOB/LONG/XMLTYPE) thì **từ chối** và chỉ sang `gen_target_ddl.py`.

Xử lý đúng `CHAR_USED='C'`: nguồn khai `VARCHAR2(40)` byte = **10 ký tự** trong AL32UTF8, nên DDL sinh `VARCHAR2(10 CHAR)`. Mang sai đơn vị thì cột ngắn hơn nguồn và `ORA-12899` lúc apply.

**3. Công cụ tạo ra một bảng mà chính nó không đọc được.** DDL chỉ grant cho `GGADM_LOAD`. Đợt nạp 3.355 dòng **thành công** (job #9, `MAXSCN` đã ghi) nhưng bước `NAP` vẫn bị **FAIL** vì `_xn_nap` chạy bằng `GGADM_RO` → `ORA-00942`.

→ `ddl.sinh_ddl` giờ grant `SELECT` cho `GGADM_RO` **bắt buộc**, không phải tuỳ chọn. Thiếu nó thì verify, đếm dòng và bước kiểm chứng đều `ORA-00942`.

Và `_b_nap` giờ **vào lại được**: nếu đã có job nạp thành công của chính bước đó, nó **không nạp lại** mà đi thẳng sang kiểm chứng. Trước đó chạy lại bị lớp chặn 1 từ chối *"bảng đích không rỗng"* — đúng, nhưng bế tắc.

### Lỗi thứ tư: cảnh báo discard nổ sai mỗi lần bảo trì

Pre-flight `LOAD` **chặn** đợt nạp với lý do `RHT.dsc lệch 94 byte → đang có bản ghi bị bỏ`. Đọc file ra thì 94 byte đó là:

```
Oracle GoldenGate Delivery for Oracle process stopped, group RHT : 2026-10-14 10:17:01
```

GG ghi **một dòng banner khi Replicat start và một dòng nữa khi nó stop**. "Mốc rỗng" 118/113/115 byte chỉ đúng khi Replicat chạy **liên tục** từ lúc file được tạo. Nghĩa là **mỗi lần stop/start bình thường đều làm cảnh báo `discard_lech_moc_rong` nổ** — và nó vừa chặn oan đúng bước `NAP`.

Đây là lỗi nghiêm trọng hơn vẻ ngoài: một cảnh báo nổ sai mỗi lần bảo trì là cách chắc chắn nhất để DBA học cách bỏ qua nó, mà đây lại là cảnh báo **duy nhất** bắt được việc Replicat bỏ bản ghi im lặng.

→ `discard_info()` giờ **đếm số dòng KHÁC banner**, không so kích thước. File rỗng chỉ chứa banner; bất kỳ dòng nào khác là có chuyện. `delta_bytes` giữ lại chỉ để hiển thị. Luật cảnh báo, pre-flight, dashboard và trang log đều đã đổi. 3 ca test hồi quy dùng đúng nội dung thật của `RHT.dsc`.

### Chứng cứ

- 7 process RUNNING lag 0 · `TABLE 52/19/3` · `MAP 52/19/3` · 0/7 mật khẩu dạng rõ · 0 abend
- Dòng MAP cuối: `MAP KT_BSH.HT_MA_KVUC, TARGET ODS.HT_MA_KVUC, KEYCOLS (MA_KVUC), COLMAP (USEDEFAULTS);` — `HANDLECOLLISIONS` **đã bỏ** sau khi Low WM 163.628.078 vượt MAXSCN
- Cả 3 discard file về mốc rỗng, **0 dòng khác banner**
- Index đích: `HT_MA_KVUC_P` UNIQUE VALID + 2 nonunique VALID, **degree=1** (`NOPARALLEL` đã chạy), **0 index UNUSABLE** trong cả schema ODS
- `num_rows=3355`, `last_analyzed` đúng thời điểm
- Registry **74/74 đủ 3 nguồn, 0 lệch**, `KEYCOLS` tường minh 74/74, `HANDLECOLLISIONS` 0
- 74/74 test · audit **130 record, hash chain liền mạch** · `param_version` 3 lần, `checkprm` PASS cả 3, 0 lần phải tự restore
- 15 job: 11 SUCCEEDED, **3 FAILED** — ba job đó là ba lần tôi thử lại bước `NAP` và bị lớp chặn 1 từ chối đúng
