# Kế hoạch: công cụ quản trị Oracle GoldenGate (web) — `ggadmin`

## Context

Hệ GoldenGate 21.3 Classic tại BSH đang được quản trị **hoàn toàn bằng tay qua SSH**: gõ `ggsci`, `grep` log, chạy SQL rời. Qua 8 lô đưa 73 bảng / 374,8 triệu bản ghi vào 3 luồng, cách làm này bộc lộ đúng một loại vấn đề: **quy trình thì đúng, nhưng thao tác dễ sai ở chi tiết**. Bằng chứng ghi trong chính tài liệu dự án:

- Lẫn `Low Watermark` với `High Watermark` → verify dương tính giả.
- Đọc lag từ `info replicat ... detail` → bắt được chữ `Started`, vòng chờ drain vô hiệu (cạm bẫy 26).
- Dòng `PROMPT` kết thúc bằng `-` → `SP2-0734`, **tái phạm 3 lần** dù đã ghi lại.
- Nhúng SQL inline qua `ssh` → đổi sang nháy kép → `ORA-00904`, **mắc 2 lần**.
- Đảo thứ tự stop `RHT` / restart `ETHT` sẽ gây **mất dữ liệu im lặng**: Replicat bỏ qua record không khớp `MAP` mà vẫn dời checkpoint — không discard, không log, không dấu vết.
- `HANDLECOLLISIONS` không bảo vệ `INSERT /*+ APPEND */` → `ORA-00001` oan (cạm bẫy 23).

Toàn bộ là **lỗi thao tác máy móc, code hoá được**. Mục tiêu không phải thay DBA mà là **biến 5 runbook đã viết thành phần mềm**: cái gì đã chứng minh đúng qua 8 lô thì không được phép gõ sai lần thứ 9.

Giá trị thứ hai: hiện **không ai biết luồng khoẻ hay không mà không SSH vào**. Không lịch sử lag, không cảnh báo, không đồ thị dung lượng. Vấn đề redo `log file switch (checkpoint incomplete)` 4.697 lần / 5 giờ chỉ phát hiện được vì tình cờ đi rà log.

### Đã chốt với người dùng

| Mục | Quyết định |
|---|---|
| Nơi chạy | **Trên chính server GG** `172.16.33.90`, user `oracle` |
| Giao diện | **Web trong browser**. Python chỉ là phần chạy ngầm, không phải CLI |
| Truy cập | **Bind `127.0.0.1` + SSH tunnel**, không cần root, không mở port |
| Người dùng | **2–5 DBA**, có login + audit log |
| Quyền | **Toàn quyền từ đầu** — start/stop, sửa param, add trandata, wizard |
| Bảng thiếu khoá | **Chặn lại, bắt xác nhận `KEYCOLS`** sau khi công cụ khảo sát |
| Đồng bộ lại | Phát hiện lệch + nạp lại cả bảng, **và chọn tay bất kỳ bảng nào** |
| **Tạo luồng mới** | **Có** — wizard tạo Extract + Replicat mới, kèm rollback có `UNREGISTER` |

### Ràng buộc cứng của server (đo thật 2026-10-13)

| | |
|---|---|
| OS | Oracle Linux 8.10, kernel UEK 5.15 |
| Python | **chỉ 3.6.8** (pip 9.0.3) — EOL 12/2021, FastAPI hiện đại cần ≥ 3.8 |
| Node | **không có** |
| `sudo -n` | **"a password is required"** → `oracle` không cài gói, không mở port |
| firewalld | **active** — lọc theo allowlist, **không** liên quan ngưỡng 1024, nên port cao cũng bị chặn. Nhưng `lo` thuộc zone `trusted` → **bind 127.0.0.1 chạy được ngay** |
| Tài nguyên | 16 vCPU, 31 GB RAM, **chỉ còn ~6 GB available** (SGA chiếm) |
| Đĩa `/` | ~495 GB, còn ~315 GB (37% dùng) — **nhưng số này hết hạn nhanh, phải đo lại trước mỗi quyết định** (cạm bẫy 29) |
| Mạng | giả định **không ra internet** → vendor mọi thư viện |

Ba ràng buộc đầu quyết định toàn bộ kiến trúc: **không cài gì vào hệ thống, không build gì trên server, không mở port nào.**

---

## Kiến trúc

```
May Windows cua DBA                        Server GG 172.16.33.90 (user oracle)
┌──────────────────┐                       ┌─────────────────────────────────────────┐
│ browser          │  ssh -N -L 8099:...   │ /u01/app/ggadmin/                       │
│ 127.0.0.1:8099   │◄──── SSH tunnel ─────►│   py/     CPython 3.12 standalone       │
└──────────────────┘                       │   app/    FastAPI + Jinja2              │
                                           │   web/    static + vendor (htmx/Alpine) │
                                           │   data/   ggadmin.db (SQLite, WAL)      │
                                           │   conf/   secrets.ini (0600)            │
                                           │   scripts/ moi script sinh ra deu luu   │
                                           │   logs/   app.log, audit-YYYYMM.jsonl,  │
                                           │           job_<id>.log                  │
                                           └─────────────────────────────────────────┘
                                              │ subprocess          │ oracledb THIN
                                              │ (env dung rieng)    │ (khong can lib Oracle)
                                              ▼                     ▼
                                        ggsci / sqlplus       BSHC (ODS) ──DBL_PMNV──► PMNV
```

**Toàn bộ nằm trong một thư mục. `rm -rf /u01/app/ggadmin` là gỡ sạch** — không rpmdb, không `/etc`, không systemd.

### Vì sao từng lựa chọn

| Quyết định | Lý do |
|---|---|
| **CPython 3.12 standalone** `scp` vào `py/` | Python hệ thống 3.6.8 quá cũ, không có sudo để `dnf`. Lấy asset `cpython-3.12.x-x86_64_v2-unknown-linux-gnu-**install_only**.tar.gz` (`astral-sh/python-build-standalone`): build trên glibc 2.17, OL8 có 2.28 → thừa điều kiện; `RPATH=$ORIGIN/../lib` nên relocatable thật; ~30 MB nén, ~250 MB cả venv. **Chọn 3.12 không chọn 3.13+** vì wheel manylinux đã chín; không có `gcc`/`python3-devel` và không xin được sudo để build từ source |
| **Không đụng Python hệ thống** | Nhiều script Oracle trên server có thể đang dùng `python3`. Thư mục riêng = không thể làm hỏng cái gì |
| **FastAPI + uvicorn `--workers 1`** | Đã có sẵn trên máy Windows nên dev/test offline được. **Bắt buộc 1 worker**: nhiều worker sẽ nhân bản poller và tranh ghi SQLite |
| **`oracledb` THIN mode** | Pure Python, **không cần thư viện Oracle nào** → tiến trình web hoàn toàn tách khỏi `$ORACLE_HOME`, hết lo xung đột `LD_LIBRARY_PATH` (`$ORACLE_HOME/lib` có `libssl`/`libcrypto` bản Oracle). Thin mode **không hỗ trợ `/ as sysdba`** — đây là **tính năng**, nó buộc công cụ không thể có sysdba |
| **Subprocess có `env` dựng riêng** | `ggsci`/`sqlplus` cần `ORACLE_HOME`, `ORACLE_SID`, `TNS_ADMIN`, `LD_LIBRARY_PATH`. **Không bao giờ kế thừa env tiến trình cha**, dựng dict mới mỗi lần. Đồng thời đây là cách khắc phục đúng cạm bẫy "`ssh host 'cmd'` không source `.bash_profile`" |
| **Jinja2 + htmx + Alpine + uPlot**, vendor sẵn | Không node trên server, không build step, **đọc được bằng mắt trên server** — với công cụ có đặc quyền thì "code đang chạy == code trong git" là thuộc tính an ninh thật. Loại React/Vite vì mất khả năng sửa tại chỗ lúc 2 giờ sáng và bus factor = 1 máy |
| **SQLite** (không ghi vào Oracle) | Trong stdlib. Và quan trọng hơn: **DB đích chính là đối tượng được giám sát** — ghi metric vào đó thì DB chết là mất luôn dữ liệu cần để chẩn đoán |
| **Bind 127.0.0.1 + tunnel** | Chạy ngay không cần root. Và SSH tunnel cho **xác thực mạnh miễn phí**: muốn vào UI thì trước hết phải có SSH được. Login trong app là lớp **thứ hai**, để phân biệt *ai*. Không cần TLS, không cần ai duyệt. Lưu ý `mgr.prm` hiện đã có `ACCESSRULE ... IPADDR *, ALLOW` — đừng lặp lại sai lầm đó ở tầng web |

### Quyết định quan trọng nhất: công cụ **không có sysdba**

Tôi đề xuất **không cấp sysdba cho công cụ**, trái với mặc định ban đầu. Mọi việc trong 2 runbook làm được bằng account thường:

| Account | Quyền | Dùng ở |
|---|---|---|
| `GGADM_RO` | `SELECT` trên các `DBA_*` cần thiết + `SELECT` trên `ODS.*` và `KT_BSH.*@DBL_PMNV` | 95% request: dashboard, verify `COUNT(*)`/`ORA_HASH`, mọi thao tác đọc |
| `GGADM_LOAD` | `SELECT` nguồn qua DB link; `INSERT`/`TRUNCATE` **chỉ** trên `ODS`; `ALTER TABLE ... LOGGING` | Chỉ initial load / re-sync |
| **`ODS`** (chủ schema) | sẵn có | `ALTER INDEX ... REBUILD` và `GATHER_TABLE_STATS` — **chủ schema luôn làm được trên object của mình**, nên không cần `ALTER ANY INDEX`/`ANALYZE ANY`, cũng không cần sysdba (đây là cách thay thế cho `ORA-01031` đã gặp, thay vì chạy sysdba như script hiện tại) |
| `OGG_BSH` | như hiện tại | Chỉ `ggsci` dùng, **qua credential store** |

Kết quả: dù web app bị chiếm quyền hoàn toàn, kẻ tấn công **không `DROP` được bảng nào, không ra khỏi schema `ODS`, không `shutdown` được DB**. Đây là phòng tuyến thật, mạnh hơn mọi blocklist. `startup`/`shutdown` **không có trong công cụ** — để nguyên runbook thủ công.

*Cần kiểm ở Phase 0: account `ODS` có mở (không `LOCKED`) và dùng được không. Nếu không, phương án dự phòng là grant object privilege tối thiểu cho `GGADM_LOAD`, vẫn không dùng sysdba.*

---

## Phase 0 — Dọn nền (~0,5–1 ngày, **trước khi viết code tính năng**)

1. **Spike 3 đường kết nối** — khử rủi ro chết người:
   - `scp` CPython standalone → `py/bin/python3 -V` chạy (kiểm glibc).
   - Wheelhouse offline: trên Windows `pip download -r requirements.txt -d wheelhouse --platform manylinux2014_x86_64 --python-version 3.12 --implementation cp --abi cp312 --only-binary=:all:`, `scp`, rồi `pip install --no-index --find-links=wheelhouse`. Pin **mọi** version + hash. Chạy `pip install --dry-run --no-index` trên server trước khi tin — `pip download` từ Windows **bỏ qua** dependency có marker platform-specific.
   - `ggsci` qua `subprocess` trả được `info all`; `oracledb` thin connect tới `BSHC`.
2. **GG credential store** — xoá mật khẩu dạng rõ khỏi `dirprm/*.prm`: `ADD CREDENTIALSTORE` (dir `dircrd` đã có, đang rỗng) → `ALTER CREDENTIALSTORE ADD USER OGG_BSH@PMNV ALIAS pmnv_ogg` (và `@BSHC`) → đổi param sang `USERIDALIAS`. App chỉ gửi `DBLOGIN USERIDALIAS pmnv_ogg`, **không bao giờ biết mật khẩu**. Cần cửa sổ bảo trì vì phải restart process.
3. Tạo `GGADM_RO`, `GGADM_LOAD`; kiểm account `ODS`.
4. Tạo cây thư mục `/u01/app/ggadmin/{py,app,web,data,conf,logs,scripts,backup}`.

Nếu chưa có cửa sổ bảo trì cho (2), Phase 1 tạm đọc mật khẩu từ `conf/secrets.ini` (`0600`, do DBA **gõ tay một lần**, **không** `grep` từ `*.prm`), và phải đóng nợ này trước Phase 2.

---

## Các module

### `app/adapters/gg.py` — bọc ggsci

Chạy `ggsci` qua `subprocess`, lệnh đưa qua **stdin**, `shell=False`, argv dạng list. **Mọi lệnh đi qua một hàng đợi tuần tự duy nhất** (`asyncio.Lock`) — ggsci không an toàn khi gọi song song, mà nhiều browser mở cùng lúc sẽ gọi song song.

**Allowlist ~15 "intent", không phải blocklist.** App **không bao giờ** gửi chuỗi do người dùng gõ tới ggsci; không có hộp nhập lệnh tự do, kể cả cho admin:

```
# L0-L1, proc thuoc enum cac process DANG TON TAI
INFO_ALL · INFO_DETAIL{proc} · STATS{proc,mode} · VIEW_REPORT{proc} · VIEW_PARAM{proc}
INFO_TRANDATA{schema,table} · ADD_TRANDATA{schema,table,keycols} · DBLOGIN{alias}
START{proc} · STOP{proc} · SEND_SHOWTRANS{proc} · CHECKPRM{file,type}
INFO_CHECKPOINTTABLE{tbl}

# L2, CHI trong phien wizard NEW_STREAM, va chi voi ten do phien do tao
ADD_CHECKPOINTTABLE{tbl} · REGISTER_EXTRACT{et} · ADD_EXTRACT{et} · ADD_EXTTRAIL{path,et,mb}
ADD_REPLICAT{r,trail,ckpt} · DELETE_REPLICAT{r} · DELETE_CHECKPOINTTABLE{tbl}
DELETE_EXTRACT{et} · UNREGISTER_EXTRACT{et}

proc ∈ {ETPMNV, ETHT, ETDTHU, RPMNVODS, RHT, RDTHU, MGR}   # enum co dinh, 7 gia tri
alias suy ra tu intent, nguoi dung KHONG duoc chon  (xem cam bay @PMNV vs @BSHC)
```

**Tham số chứa `\n` `\r` `;` `!` bị từ chối** — ggsci tách lệnh theo dòng, một `\n` trong tên bảng là **lệnh thứ hai**. Đây là injection nguy hiểm hơn SQL injection ở đây vì không ai nghĩ tới nó.

Code hoá đúng những cạm bẫy đã trả giá:

| Cạm bẫy | Cách xử lý |
|---|---|
| Lag từ `info ... detail` bắt được chữ `Started` | `get_lag()` **chỉ** parse `info all`; không có đường nào khác đọc lag |
| `MAP (TABLE) resolved` không in đủ số dòng lúc start | Kiểm bằng `grep -c '^TABLE '` + `checkprm` + ≥1 dòng `resolved`, **không** đếm số dòng resolved |
| `stats` không xác nhận được MAP mới | Đọc `dirrpt/*.rpt`, đúng như runbook ghi |
| Dòng nhiễu `post-quantum`/`store now`/`upgraded` | Lọc một chỗ duy nhất ở tầng adapter |
| Mật khẩu trong output | Redact `(PASSWORD|IDENTIFIED BY)\s+\S+` → `***` **trước khi** ghi log hay audit |

### `app/adapters/ora.py` — bọc Oracle

`oracledb` thin, 3 pool (`GGADM_RO`, `GGADM_LOAD`, `ODS`). Mật khẩu nạp **một lần lúc khởi động** từ `conf/secrets.ini` vào RAM; **không** log, **không** trả qua API, **không** ghi vào SQLite. Nguồn `PMNV` **chỉ** truy cập qua `@DBL_PMNV` — giữ đúng mô hình hiện tại, và **nguồn là read-only tuyệt đối** với công cụ này.

**Chống SQL injection — 5 lớp**, vì không thể bind *identifier* mà runbook buộc sinh SQL động (`AS OF SCN`, `TRUNCATE`, biểu thức `ORA_HASH` sinh từ `LISTAGG`, nạp theo partition):

1. **Schema là hằng số trong code** (`KT_BSH`, `ODS`). UI không có ô nhập schema.
2. **Allowlist từ catalog** — lớp quan trọng nhất: tên người dùng chọn chỉ dùng làm **bind value** để tra `dba_tables`; nếu có kết quả thì **ghép vào SQL động bằng chuỗi Oracle trả về**, không bao giờ bằng chuỗi người dùng gõ. Áp dụng y hệt cho partition (`dba_tab_partitions`) và cột (`dba_tab_columns`).
3. **Validate cú pháp trước khi tới DB**: `^[A-Z][A-Z0-9_$#]{0,29}$`; chặn `"` `'` `;` `-` khoảng trắng newline.
4. `DBMS_ASSERT.SQL_OBJECT_NAME()` / `ENQUOTE_NAME()` ở mọi chỗ sinh SQL động trong PL/SQL.
5. **Giới hạn nghiệp vụ**: tên bảng còn phải nằm trong **registry 73 bảng** (`managed_table`). Bảng tồn tại trong `ODS` nhưng ngoài phạm vi GG thì công cụ **không được chạm** — chặn cả tấn công lẫn tai nạn.

### `app/collector.py` — poller

Một task nền, 30 giây: `info all`, `info <proc> detail` (seqno/RBA/lag), `stat()` 3 trail + 3 discard file (so mốc rỗng 118/113/115), `df /`, room + **`maxbytes` còn lại của từng datafile**, `dba_capture`/`dba_apply`, số bảng prepared ở nguồn, ABEND/FATAL mới trong `ggserr.log`. **Một poller duy nhất cho mọi browser.** Trạng thái process chỉ ghi **khi thay đổi**, không ghi mỗi 30s.

### `app/alerts.py`

Ngưỡng lấy từ mốc thật của hệ, không bịa. Có `for_seconds` chống nhảy (phải vi phạm liên tục N giây mới nổ):

| Luật | Ngưỡng |
|---|---|
| Process không RUNNING | ngay |
| Lag Replicat | > 5 phút WARN, > 30 phút CRIT |
| Discard file lệch mốc rỗng | bất kỳ byte nào ≠ 118/113/115 |
| ABEND/FATAL mới | ngay |
| Room `BSH_DATA`/`BSH_INDEX` | < 10 GB |
| **Datafile sắp chạm trần 32 GB** (smallfile, block 8K) | > 30 GB — phải tính **từng datafile**, không chỉ `% free` của tablespace; tablespace báo "còn chỗ" trong khi datafile đã cụng trần là cái bẫy đã gặp (`ORA-03206`) |
| `df /` | < 15% trống |
| **Bảng capture mà không có `MAP`** | ngay — mục 9.1 của ops guide nói đây là cái bẫy số một của BSH |

### `app/verify.py`

Hai chế độ, **nói rõ khác biệt ngay trên UI**:

- **Nhanh (`LIVE`)** — không dừng gì. **Có thể dương tính giả** vì đích vẫn đang thay đổi. Để soi nhanh.
- **Chuẩn (`SNAPSHOT_SCN`)** — quiesce đúng quy trình: `stop extract` → chờ drain (**`RBA` = kích thước trail VÀ lag `00:00:00`**, không bao giờ chờ `Low WM = High WM` vì hai mốc không gặp nhau) → `stop replicat` → đọc **High** WM → so sánh → start lại. Không đọc được High WM thì **dừng, không verify**.

Công thức dùng lại đúng cái đã chạy 8 lô: `COUNT(*)` + `SUM(ORA_HASH(...))` với biểu thức sinh bằng `LISTAGG` hash từng cột nối `'|'` (độc lập NLS, chịu bảng rộng).

**Hai chi tiết bắt buộc:**
- Lưu `hash_expr_sha256` cùng kết quả. Param có `DDL INCLUDE MAPPED` → DDL thêm cột → biểu thức đổi → hash đổi **mà không phải vì dữ liệu lệch**. Không lưu biểu thức thì sẽ mất một buổi sáng truy một "sai lệch" không tồn tại — đúng cái bẫy mục 0 của runbook re-sync cảnh báo.
- `src_hash`/`tgt_hash` lưu **TEXT**. `SUM(ora_hash)` của bảng 256 triệu dòng vượt 2^53 → lưu số thực sẽ **mất chính xác và báo lệch giả**.

**Chống tự gây sự cố:** RAM available chỉ ~6 GB. Verify cả luồng `PM` (52 bảng) phải chạy **tuần tự**, parallel hạ xuống 2–4, **không bao giờ song song nhiều verify** — nếu không chính công cụ giám sát sẽ gây ra sự cố nó đang giám sát.

### `app/jobs.py` — job dài

Mọi tác vụ dài **không chạy trong HTTP request**: POST → tạo row `job` → spawn `nohup sqlplus ... > logs/job_<id>.log 2>&1 &` **detach khỏi uvicorn** → UI theo `job_id` qua SSE. Ba lợi ích: tunnel đứt job vẫn chạy; restart app job vẫn chạy; **DBA vẫn `tail -f` được bằng tay** đúng như đang quen.

### `app/wizard_newstream.py` — tạo luồng mới (Extract + Replicat)

Dự án đã làm việc này **2 lần**: `ETHT`/`RHT` (lô HT) và `ETDTHU`/`RDTHU` (lô BC). Cả hai lần đều thành công nhưng **lần đầu mắc 3 lỗi mà lần hai vẫn phải tra lại tài liệu mới tránh được**. Đây là luồng nguy hiểm nhất vì nó `REGISTER` một capture process ở **DB nguồn production**, và nếu dọn sai thì để lại capture mồ côi **đào redo vĩnh viễn và giữ archive log**.

Nguồn: `Ke-hoach-dua-14-bang-HT-vao-ETHT-RHT.md` (1.685 dòng, chi tiết nhất — có cả biên bản từng lệnh và 21 cạm bẫy) + `Ke-hoach-dua-3-bang-BC-vao-ETDTHU-RDTHU.md`.

**Thứ tự lệnh — không được đổi:**

```
0. TIEN DIEU KIEN (chi doc)
   info all: cac process cu RUNNING lag ~0
   KHONG ton tai dirprm/<et>.prm, dirprm/<r>.prm, dirdat/<xx>0000*
   dia / con > 10 GB
   streams_pool_size o CA HAI DAU  -> xem ghi chu ben duoi
1. ADD TRANDATA cho cac bang, tai @PMNV
2. Tao dirprm/<et>.prm + dirprm/<r>.prm  -> checkprm PASS CA HAI
3. ADD CHECKPOINTTABLE OGG_BSH.<R>                      @BSHC
4. REGISTER EXTRACT <ET> DATABASE SHARE AUTOMATIC       @PMNV   <- TRUOC add extract
   ADD EXTRACT <ET>, INTEGRATED TRANLOG, BEGIN NOW      @PMNV
   ADD EXTTRAIL /u01/app/ogg/dirdat/<xx>, EXTRACT <ET>, MEGABYTES 100
5. ADD REPLICAT <R>, INTEGRATED, EXTTRAIL .../<xx>, CHECKPOINTTABLE OGG_BSH.<R>   @BSHC
6. START EXTRACT <ET> -> cho lag ve ~0  (co the ~13 phut)
   START REPLICAT <R>  -> lag 0, .dsc dung moc rong
7. ... tu day chuyen sang wizard ADD_TABLE cho cua so nap
```

**Chín chốt mà công cụ phải chặn, không phải nhắc** — tất cả đều là lỗi đã xảy ra thật:

| Chốt | Vì sao |
|---|---|
| **`REGISTER EXTRACT` phải chạy TRƯỚC `ADD EXTRACT`** | Tài liệu GGSCI 21.3 nói rõ. Wizard cố định thứ tự, không cho đảo |
| **`ADD CHECKPOINTTABLE` phải trước `ADD REPLICAT`** | `GLOBALS` **rỗng** → `ADD REPLICAT` không có `CHECKPOINTTABLE` mặc định mà cú pháp lại bắt buộc chọn `CHECKPOINTTABLE` hoặc `NODBCHECKPOINT` |
| **Alias `@PMNV` vs `@BSHC` buộc vào từng intent** | **Cả 2 DB cùng `SERVICE_NAME=BSHC`** nên prompt GGSCI hiện `@BSHC` cho **cả hai** — nhìn màn hình không phân biệt được. `ADD TRANDATA`/`REGISTER` → `@PMNV`; `ADD CHECKPOINTTABLE`/`ADD REPLICAT` → `@BSHC`. Công cụ **không cho người dùng chọn alias**, nó suy ra từ intent |
| **`MEGABYTES` chỉ hợp lệ trên LỆNH `ADD EXTTRAIL`, không hợp lệ trên THAM SỐ `EXTTRAIL` trong `.prm`** | Đã gặp thật: `checkprm` báo `parameter [exttrail] has unrecognized keyword or extra value "MEGABYTES"`. Trình sinh param file **không bao giờ** ghi `MEGABYTES` vào `.prm` |
| **Param file Replicat phải có `MAP` từ lúc tạo**, và `ADD REPLICAT` chạy **trước** khi start Extract | Mô hình đã dùng cho cả 2 luồng mới |
| **Tiền điều kiện `streams_pool_size` ở CẢ HAI ĐẦU** | Bắt buộc cho integrated capture/apply. Công cụ **chỉ kiểm tra và sinh ra lệnh**, **không tự chạy** — xem ghi chú dưới |
| **Chờ Extract mới bắt kịp trước khi vào cửa sổ nạp** | `ETHT` cần **~13 phút** (lag 9 phút → 2 giây) vì phải đào redo từ SCN đăng ký tới hiện tại; nguồn sinh **~1,06 TB redo/ngày**. Wizard poll `info all`, **không `sleep` cố định** |
| **`OGG$CAP_<ET>` ở trạng thái `ENABLED` ngay sau `REGISTER`** | Checklist ban đầu dự kiến `DISABLED` và đã phải đính chính. Công cụ hiện đúng "bình thường", không báo động giả — capture đã đăng ký ở mức DB nhưng process còn STOPPED nên chưa đào redo thật |
| **Đọc watermark phải `dblogin` trước** | Không `dblogin` thì in `(requires database login)` — dễ tưởng là không có watermark |

**Rollback của wizard này là phần nguy hiểm nhất, phải code cứng đủ 2 lệnh dọn:**

```
dblogin userid OGG_BSH@BSHC ...     <- PHAI dblogin TRUOC
delete replicat <R>                 <- moi don luon inbound server OGG$<R>
delete checkpointtable OGG_BSH.<R>
dblogin userid OGG_BSH@PMNV ...
delete extract <ET>
unregister extract <ET> database    <- BAT BUOC
```

- **`DELETE EXTRACT` mà quên `UNREGISTER EXTRACT ... DATABASE`** → capture **mồ côi** vẫn giữ archive log và đào redo vô ích **mãi mãi**. Wizard coi `unregister` là một bước **không thể bỏ qua**, không phải tuỳ chọn.
- **`DELETE REPLICAT` mà quên `dblogin` trước** → inbound server `OGG$<R>` mồ côi **vẫn giữ streams pool**.

**Hai điều công cụ KHÔNG làm, có chủ ý:**

1. **Không tự chạy `ALTER SYSTEM SET streams_pool_size`.** Lệnh này tác động **DB nguồn production** và nằm trong danh sách chặn. Công cụ chỉ *kiểm tra* `v$sga_dynamic_components` hai đầu, báo đỏ nếu thiếu, và **sinh ra lệnh cho DBA chạy tay**. Pre-flight FAIL nếu streams pool chưa đủ → không đi tiếp được.
2. **Không tự đặt tên.** Tên process, tên trail prefix (**đúng 2 ký tự**, ví dụ `pm`/`ht`/`dt`), tên checkpoint table đều do DBA gõ, và công cụ **kiểm tra trùng** với 6 process + 3 trail + 3 checkpoint table đang có trước khi cho đi tiếp.

**Lọc log:** `QU_ERROR` **có chứa chuỗi `ERROR`** → `grep ERROR` thuần cho dương tính giả. Bộ lọc phải loại `QU_ERROR` ra, đúng như cạm bẫy đã ghi.

### `app/wizard_add.py` — thêm bảng vào luồng

Máy trạng thái 12 bước, **state ở server** (`wizard_run` + `wizard_step`). Đây không phải tiện nghi mà là **bắt buộc đúng nghiệp vụ**: có bước nạp dài hàng chục phút và bước chờ `Low WM` vượt `MAXSCN`; state ở client sẽ mất trắng khi tunnel đứt hoặc DBA F5. State ở server thì hôm sau mở lại wizard vẫn ở bước 8, và **một DBA khác tiếp được việc của người trước**.

Mã hoá từ `Quy-trinh-them-bang-vao-luong-GoldenGate.md` (887 dòng) + `Lenh-da-chay-4-bang-TBH-TT-vao-ETHT-RHT.md` (556 dòng). Mỗi bước có 4 phần cố định: **điều kiện vào → xem trước lệnh → chạy → kiểm chứng**. Không bước nào chạy khi bước trước chưa PASS.

Những chỗ công cụ **chặn**, không phải nhắc:

| Chốt | Hành vi |
|---|---|
| Bảng không có PK thật | Khảo sát, đề xuất `KEYCOLS`, **kiểm chứng duy nhất trên dữ liệu thật + đếm cột NULL**, báo đỏ nếu không có unique index (khi đó mỗi UPDATE là full scan), rồi **bắt xác nhận** mới đi tiếp |
| Thứ tự stop | **Bắt buộc `stop replicat` trước `restart extract`**. UI không cho đảo — đảo là mất dữ liệu im lặng |
| `SCN_FLOOR` | **Chỉ** từ `High Watermark CSN`, và **chỉ sau khi** Extract đã bắt kịp. Không nhận `Low WM` hay `current_scn` |
| Bỏ `HANDLECOLLISIONS` | App tự poll `info replicat detail`, **chỉ mở nút khi `Low WM` > `MAXSCN`** — đây là chỗ duy nhất dùng Low WM, và là chỗ wizard thắng làm tay rõ nhất vì loại hẳn khả năng bỏ quá sớm |
| Script nạp | Luôn sinh 3 lớp chặn: đích phải rỗng; mỗi SCN ≥ `SCN_FLOOR` (`RAISE_APPLICATION_ERROR`); fallback conventional nếu APPEND lỗi |
| Chia lô | Tự đọc `tuned_undoretention` để **quyết định** 1 SCN/bảng hay chia lô, thay vì để người dùng đoán |
| Index | Đề xuất mark index **GLOBAL nonunique** UNUSABLE rồi REBUILD; **giữ unique `*_P` VALID** |
| `delete trandata` | Enforce đúng thứ tự runbook: bỏ dòng `TABLE` khỏi `.prm` **trước**, rồi mới `DELETE TRANDATA` |

### `app/wizard_resync.py` — đồng bộ lại bảng đã có trong luồng

Mã hoá `Quy-trinh-dong-bo-lai-bang-da-lech.md` (728 dòng). Hai đường vào: từ kết quả verify báo lệch, **và chọn tay bất kỳ bảng nào trong 73 bảng** (yêu cầu phát sinh của người dùng).

Khác wizard thêm bảng, lấy đúng từ runbook:
- **Bước 0 "bảng có THỰC SỰ lệch?" là bắt buộc, không bỏ qua được** — chạy verify `SNAPSHOT_SCN` trước, chỉ khi `matched = 0` mới mở các bước sau. Mục 0 của runbook tồn tại vì sai lầm này đã xảy ra; wizard phải biến nó thành **không thể bỏ qua**, không phải một lời nhắc.
- Thêm `HANDLECOLLISIONS` vào `MAP` **đã tồn tại**, không thêm `MAP` mới.
- `TRUNCATE` đích, **ghi lại số dòng cũ trước khi xoá** (cột riêng `rows_before_truncate`).
- **Giữ index online, không mark UNUSABLE** (ngược wizard thêm bảng — runbook giải thích vì sao).
- **Verify 2 lần**: tại SCN snapshot trước khi start, và chặt hơn sau catch-up.

### `app/safety.py`

**3 mức, không phải 2** — vì mức giữa là nơi hầu hết thao tác thật nằm:

| Mức | Thao tác | Yêu cầu |
|---|---|---|
| **L0 — đọc** | `info all`, `stats`, verify, xem log | đã login |
| **L1 — hoàn tác được** | start/stop, `add trandata`, thêm dòng `TABLE`/`MAP` | modal hiện **diff chính xác** + lý do bắt buộc (≥10 ký tự) + 1 click |
| **L2 — không hoàn tác** | `TRUNCATE` + nạp lại, bỏ `HANDLECOLLISIONS`, `delete trandata`, mọi bước re-sync | (1) **gõ tay** tên bảng, ô nhập chặn `onpaste`; (2) **pre-flight PASS mới mở được nút**; (3) lý do bắt buộc; (4) token hết hạn sau **60 giây**; (5) ghi audit **trước** khi thực thi |

**Pre-flight L2 — nơi công cụ tạo giá trị lớn nhất:** `/` còn > 2× kích thước nguồn của bảng; **từng datafile** đích còn chỗ dưới trần 32 GB; SCN còn flashback được (`v$undostat`); Replicat **STOPPED thật** không phải `STOPPING`; param đã backup có timestamp và `checkprm` PASS; bảng đích không có session khác đang khoá.

FAIL → nút **không mở được**, chỉ có "xem chi tiết". **Không có checkbox "tôi hiểu rủi ro".** Muốn vượt thì làm tay theo runbook, có chủ ý.

**Blocklist là lớp phòng thủ thứ hai**, kiểm trên chuỗi **đã render xong**, ngay trước khi ghi vào stdin ggsci; khớp thì **raise + audit `SECURITY_BLOCK`**, không bỏ qua âm thầm:

```
GGSCI pha checkpoint: ALTER EXTRACT|REPLICAT * BEGIN NOW|<time>|EXTSEQNO|EXTRBA
  (EXTSEQNO/EXTRBA con nguy hon BEGIN NOW: nhay checkpoint la MAT DU LIEU IM LANG)
  CLEANUP * · DELETE TRACETABLE · DELETE RMTTRAIL · ADD RMTTRAIL
GGSCI tho: KILL * · STOP MGR · moi wildcard '*' trong ten process · SHELL · '!' · OBEY
  SEND ... FORCESTOP · SET EDITOR · EDIT PARAM · CREATE SUBDIRS
GGSCI trandata: DELETE TRANDATA cho bang ngoai 73 bang hoac con dong TABLE trong .prm
  ADD TRANDATA voi schema != KT_BSH
SQL: DROP TABLE|INDEX|PARTITION|USER|TABLESPACE|DATABASE LINK
  TRUNCATE tren schema != ODS · ALTER DATABASE|SYSTEM · SHUTDOWN|STARTUP
  GRANT|REVOKE|CREATE USER|ALTER USER ... IDENTIFIED BY
  DELETE FROM khong co WHERE · moi DML tren KT_BSH.* qua DB link
  EXECUTE IMMEDIATE / khoi BEGIN...END tuy y tu UI
He thong: rm -rf · moi ghi vao dirdat/ (sua trail = pha checkpoint)
  kill -9 · ggsci duoi user != oracle · shell=True (chan o code review/CI)
```

**Một nhóm lệnh không chặn được mà phải khoanh vùng** — vì wizard tạo luồng mới cần chính chúng: `REGISTER`/`UNREGISTER EXTRACT`, `ADD`/`DELETE EXTRACT`, `ADD`/`DELETE REPLICAT`, `ADD`/`DELETE EXTTRAIL`, `ADD`/`DELETE CHECKPOINTTABLE`. Luật thay thế, chặt hơn chặn thuần:

- Chỉ chạy được **bên trong một phiên `NEW_STREAM` đang mở**, ở đúng bước của phiên đó. Ngoài wizard thì bị từ chối như mọi lệnh trong blocklist.
- **Chỉ tác động lên process/trail/checkpoint table mà chính phiên đó đã tạo.** Tên nằm trong `wizard_run`; **6 process + 3 trail + 3 checkpoint table đang chạy là bất khả xâm phạm**, không có đường nào trong app chạm tới được.
- `UNREGISTER EXTRACT` chỉ xuất hiện ở bước rollback và **bắt buộc đi kèm** `DELETE EXTRACT` — không cho chạy lẻ, cũng không cho `DELETE` mà bỏ `UNREGISTER`.

Các lớp còn lại: **backup param trước mọi lần sửa** (`cp -p` → `checkprm` → nếu FAIL thì **tự restore**) và **không bao giờ ghi param của process đang RUNNING**; **mọi script sinh ra lưu xuống `scripts/`** để DBA soi lại hoặc chạy tay — công cụ không được là hộp đen; **khoá độc quyền** 1 wizard / 1 luồng; **tối đa 1 job load/re-sync trên toàn hệ** (lock trong SQLite).

### `app/audit.py`

Ghi **2 nơi, append-only**: SQLite `audit_log` (để query/báo cáo) **và** file `logs/audit-YYYYMM.jsonl` mở `O_APPEND`, `0600`. Lý do trùng lặp: *một hệ audit mà kẻ tấn công xoá được bằng cùng đặc quyền thì không phải audit*. Mỗi record có `prev_hash` = SHA-256 record trước → **hash chain**, kèm lệnh `verify-audit`.

**Audit cả L0** (lưu lượng nhỏ, và "ai xem checksum bảng nào lúc nào" là thông tin cần khi truy nguyên). **Redact mật khẩu trước khi ghi** — đây chính là cái bẫy mà `grep PW_SRC` trong script hiện tại đã mắc; không mang sang app.

### `app/auth.py`

`app_user` trong SQLite, `pbkdf2_hmac` (stdlib) hoặc argon2id nếu có wheel. Session cookie `HttpOnly` + `SameSite=Strict`. 3 vai `VIEWER`/`OPERATOR`/`ADMIN`, có `failed_count` + `locked_until`, `must_change_pwd`. 2–5 người nên không cần IdP.

### `web/` — UI

Vendor vào `web/static/vendor/` (~220 KB), **commit kèm SHA-256 trong `MANIFEST.txt`**: `htmx.min.js`, `alpine.min.js`, `uplot.iife.min.js` + css, `pico.min.css`. **Không CDN kể cả khi có internet** — công cụ không được gửi request ra ngoài khi DBA đang xử lý sự cố.

Phân vai rõ, trộn lẫn là nguồn gốc của mớ rối:
- **htmx** — mọi thứ liên quan dữ liệu server. Bảng tự refresh (`hx-trigger="every 15s"`, server render luôn `<tr>` — không JSON, không template client, không code render trùng lặp). Wizard từng bước = POST trả fragment bước kế. Log stream qua **SSE** (`tail -f` của `*.rpt`/`*.dsc`) — SSE đúng hơn WebSocket: một chiều, tự reconnect, qua tunnel không vấn đề.
- **Alpine** — chỉ UI state thuần client (modal, accordion, tab, disable nút khi chưa gõ đủ token). **Không gọi API.**
- **uPlot** — time-series. 47 KB (Chart.js ~200 KB) và vẽ hàng chục nghìn điểm không nghẽn — poll 30s × nhiều metric × nhiều ngày thì đúng là hàng chục nghìn điểm.

**Frontend phải chịu được mất kết nối** (tunnel đứt khi laptop sleep): banner "mất kết nối, đang thử lại", poll có exponential backoff, và **không bao giờ huỷ job đang chạy khi browser mất kết nối**.

Trang: Dashboard · Lịch sử (đồ thị) · 73 bảng (registry + lần verify cuối) · Verify · Wizard (3 loại: tạo luồng mới / thêm bảng / đồng bộ lại) · Param (đã mask, sửa có diff) · Log · Audit.

**Giảm đau cho SSH tunnel:** phát cho mỗi DBA một `gg-admin.cmd` 2 dòng (`ssh -N -L 8099:127.0.0.1:8099 oracle@172.16.33.90` rồi `start http://127.0.0.1:8099`), hoặc `Host gg-admin` + `LocalForward` trong `~/.ssh/config`.

---

## Chia giai đoạn

| Phase | Nội dung | Công sức |
|---|---|---|
| **0** | Spike 3 đường kết nối + credential store + 2 account + cây thư mục | ~0,5–1 ngày |
| **1** | Auth + audit + poller + dashboard + cảnh báo + lịch sử + verify `LIVE` + registry 73 bảng. **Không một lệnh ghi nào** | **1,0×** (~5–7 ngày) |
| **2** | Khung L1/L2 + pre-flight + start/stop + sửa param có cấu trúc + `add trandata` + job runner + verify `SNAPSHOT_SCN` | **1,5×** (~8–10 ngày) |
| **3** | Wizard `ADD_TABLE` + `RESYNC_TABLE` + pause/resume/bàn giao + rollback + báo cáo | **2,0×** (~10–14 ngày) |
| **4** | Wizard `NEW_STREAM` (tạo Extract + Replicat mới) + rollback có `UNREGISTER` | **0,8×** (~4–6 ngày) |

**Tổng ≈ 29–39 ngày-người. Dùng thật được sau Phase 1 (~1 tuần).**

`NEW_STREAM` để **sau** `ADD_TABLE` dù nghiệp vụ nó đi trước, vì ba lý do: nó **dùng lại** phần lớn bước của `ADD_TABLE` (trandata, param, nạp, verify) nên làm sau thì rẻ hơn nhiều; nó là luồng **tần suất thấp nhất** (2 lần trong cả dự án) nhưng **rủi ro cao nhất** vì `REGISTER` vào DB nguồn production; và nó cần khung L2 + rollback đã chạy ổn làm nền.

Quyền đã chốt là **toàn quyền từ đầu** — phase chỉ phản ánh **thứ tự xây**, không khoá quyền giả tạo. Phase 1 chỉ đọc không phải vì chính sách mà vì nó là nền của mọi thứ sau, và vì **phê duyệt triển khai một công cụ chỉ `SELECT` thì cực dễ**.

Riêng Phase 1 có một tính năng tự trả lại chi phí: đối chiếu tự động `TABLE` trong `*.prm` ↔ `MAP` ↔ `dba_capture_prepared_tables` → cảnh báo **"bảng capture mà không MAP"**.

---

## File sẽ tạo

Repo mới `D:\code\ggadmin`, **`git init`** (công cụ sửa được production thì phải có lịch sử thay đổi; `D:\code\ora_gg` hiện không phải git repo). Deploy sang `/u01/app/ggadmin`.

```
app/
  main.py            FastAPI, Jinja2, StaticFiles, bind 127.0.0.1, --workers 1
  config.py          doc conf/app.toml — KHONG chua mat khau
  db.py              SQLite + migration + PRAGMA (WAL, busy_timeout, foreign_keys)
  auth.py  audit.py  safety.py  jobs.py  collector.py  alerts.py  verify.py
  wizard_newstream.py  wizard_add.py  wizard_resync.py
  adapters/gg.py     allowlist intent + queue tuan tu + parser
  adapters/ora.py    3 pool thin + 5 lop chong injection
  adapters/osfs.py   df, stat trail/discard, tail log
  routers/*.py       dashboard, history, tables, verify, wizard, param, logs, audit
web/
  templates/*.html   Jinja2 (fragment cho htmx)
  static/app.js  static/charts.js  static/style.css
  static/vendor/{htmx,alpine,uplot,pico} + MANIFEST.txt (SHA-256)
tests/
  fixtures/          output ggsci THAT da ghi lai, khong mock bang tay
  test_gg_parse.py  test_verify_sql.py  test_safety.py  test_preflight.py
tools/
  make_wheels.ps1    tai wheel offline tren Windows
  deploy.sh          scp py/ + app/ + web/ sang server
requirements.txt     pin version + hash
conf/app.toml.example   conf/secrets.ini.example
```

**Tái sử dụng từ `C:\Users\User\Desktop\script_DDL\gen_target_ddl.py`** (612 dòng, cùng driver `oracledb`): `_rows()` (query → `list[dict]`, cột lowercase), `fetch_metadata()` / `fetch_columns()` / `fetch_indexes()` / `fetch_partition_info()`, `fmt_datatype()`, `_long_to_str()` (xử lý cột LONG — đã gặp `ORA-00932` khi so sánh `high_value`), và **mô hình `selftest()`**: tách render khỏi DB để test không cần kết nối. Dùng cho bước "sinh DDL bảng đích" trong wizard. Lưu ý file đó dùng `ALL_*` view; công cụ này dùng `DBA_*` qua `GGADM_RO`.

### Schema SQLite

`data/ggadmin.db`, `0600`, `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000; foreign_keys=ON`.

Bảng: `gg_process` · `managed_table` (registry 73 bảng = allowlist lớp 5) · `metric_sample` · `metric_hourly` · `process_status_event` · `alert_rule` · `alert_event` · `verify_run` · `wizard_run` · `wizard_step` · `job` · `app_user` · `audit_log` · `param_version`.

Ba quyết định schema đáng nói:
- **`metric_sample` long/narrow + `WITHOUT ROWID`**, PK `(scope, scope_key, metric, ts)`: thêm metric mới không cần migration, và "lag của `RPMNVODS` 7 ngày qua" là một range scan liền mạch — đúng pattern uPlot cần. Khối lượng ~138k row/ngày ≈ 12 MB/tháng; giữ 90 ngày raw + rollup giờ → file **dưới 200 MB vĩnh viễn**.
- **`src_hash`/`tgt_hash` là TEXT** (xem `verify.py`).
- **`wizard_run` lưu `snapshot_scn`, `trail_seqno`, `trail_rba`, `rows_before_truncate` thành cột riêng**, không nhét vào `ctx_json` — đó là những con số mà toàn bộ tính đúng đắn của 3 runbook phụ thuộc vào; chúng cần query được, index được, hiện được trên báo cáo. `kind ∈ {NEW_STREAM, ADD_TABLE, RESYNC_TABLE}`, và riêng `NEW_STREAM` thêm `created_extract`, `created_replicat`, `created_trail_prefix`, `created_ckpt_table`, `register_scn` — **đây chính là danh sách quyết định lệnh `DELETE`/`UNREGISTER` được phép chạm vào tên nào**, nên nó phải là cột, không phải JSON.

**Backup:** `sqlite3 ggadmin.db ".backup backup/ggadmin-$(date +%F).db"` trong cron của `oracle` — hot backup đúng cách, không cần dừng app; `gzip`, giữ 30 bản.

---

## Verify — nghiệm thu thế nào

**Phase 0**: `py/bin/python3 -V` in 3.12; `pip install --no-index` thành công; `import oracledb` + connect `BSHC` OK; `ggsci info all` qua subprocess OK; `checkprm` PASS sau khi đổi sang `USERIDALIAS`; `grep -c PASSWORD dirprm/*.prm` = 0.

**Phase 1**: mở `http://127.0.0.1:8099` qua tunnel thấy đúng 6 process RUNNING; **so sánh từng số trên dashboard với số gõ tay** — đây là bài kiểm quan trọng nhất của phase 1. Dừng thử `RHT` (ngoài giờ, có kế hoạch) → dashboard đổi trạng thái + cảnh báo nổ trong ≤ 30 giây. Sau 1 giờ có dữ liệu đồ thị. Đo RSS của uvicorn, phải ≈ 60–90 MB.

**Phase 2**: verify `SNAPSHOT_SCN` trên `ETHT`/`RHT` → **18/18 khớp**, và **High WM công cụ đọc phải trùng số đọc tay**. Verify `LIVE` trên `ETDTHU`/`RDTHU` → 3/3. Sửa param: diff đúng, `.bak_*` được tạo, `checkprm` PASS, thử một param sai cú pháp → **app tự restore**. Thử lệnh trong blocklist → **bị từ chối + có row `SECURITY_BLOCK`**. Thử pre-flight với đĩa giả lập thiếu → **nút không mở được**.

**Phase 3**: chạy wizard `ADD_TABLE` **trên một bảng nhỏ chọn làm bài thử** (dưới 10.000 dòng, chưa vào luồng), đối chiếu từng bước với `Lenh-da-chay-4-bang-TBH-TT-vao-ETHT-RHT.md`: **lệnh công cụ sinh ra phải giống lệnh đã chạy tay**. Sau đó chạy `RESYNC_TABLE` trên cùng bảng đó, verify 2 lần. Thử tắt tunnel giữa lúc nạp → **job vẫn chạy, mở lại wizard vẫn đúng bước**.

**Phase 4**: tạo **một luồng thử nghiệm dùng rồi xoá** (`ETTEST`/`RTEST`, trail `tt`, 1 bảng nhỏ) rồi **chạy rollback đầy đủ** và kiểm chứng đã dọn sạch: `dba_capture@DBL_PMNV` trở về **3 dòng** (không có capture mồ côi), không còn inbound server `OGG$RTEST` ở đích, `dirdat/tt*` đã xoá, `info all` trở về **7 dòng**. Đối chiếu từng lệnh wizard sinh ra với biên bản trong `Ke-hoach-dua-14-bang-HT-vao-ETHT-RHT.md`. Thử ép wizard chạy `DELETE EXTRACT ETHT` (process thật) → **phải bị từ chối + `SECURITY_BLOCK`**.

**Test tự động**: parser ggsci test bằng **fixture là output thật đã ghi lại**, không mock bằng tay — đặc biệt ca `info all` vs `info ... detail` để khoá vĩnh viễn cạm bẫy 26. `test_safety.py` phủ **toàn bộ** blocklist **và luật khoanh vùng của nhóm `ADD`/`DELETE`/`REGISTER`** — phải có ca "process thật bị từ chối" cho cả 6 process. `test_preflight.py` phủ mọi nhánh FAIL.

---

## Rủi ro

### Rủi ro lớn nhất — và nó không phải rủi ro kỹ thuật

Công cụ này **làm tăng tần suất thao tác nguy hiểm** (đó chính là mục đích: biến quy trình 16 phút thành vài click) mà **không tự động làm tăng dung lượng đĩa**. Dự án tạo ra gia tốc mà không tạo ra phanh.

Làm tay thì mỗi lần `TRUNCATE` là một quyết định có ý thức, có người đọc `df -hP /` trước. Đặt nó sau một nút trong browser thì ba lớp an toàn tự nhiên — ma sát, sự chú ý, và việc chỉ một người biết cách làm — **biến mất cùng lúc**.

**Kịch bản vỡ cụ thể**: một DBA mở wizard re-sync cho một bảng 6 GB nguồn, lúc đó trail đang phình vì Replicat luồng khác abend từ đêm trước. Initial load chạy, `/` đầy. Oracle không ghi được datafile → **cả DB `BSHC` chết**. Và vì `/` là **một LV duy nhất chứa đồng thời datafile Oracle, OGG home và trail file**, không có chỗ nào để xoay.

*Hiệu chỉnh so với bản thảo đầu:* `BSHC` **đang `NOARCHIVELOG`**, nhưng nó là **DB phái sinh** — mọi thứ trong `ODS` dựng lại được từ `PMNV` bằng chính runbook re-sync. Nên hậu quả không phải "mất dữ liệu vĩnh viễn" mà là **RTO tính bằng giờ tới một ngày**: nạp lại 374,8 triệu dòng ở tốc độ đã đo (11.000–100.000 dòng/giây) cộng rebuild index và verify. Đó là lý do tôi **không** đưa "bật ARCHIVELOG cho `BSHC`" thành điều kiện tiên quyết — nó đáng cân nhắc nhưng không phải cái chặn dự án này.

**Bốn biện pháp, theo thứ tự quan trọng:**

1. **Pre-flight dung lượng là chốt cứng, không override được, ngay từ dòng code đầu của Phase 2** — không để Phase 3. `/` còn > 2× kích thước nguồn, **và** từng datafile đích còn chỗ dưới trần 32 GB, **và** trail hiện tại dưới ngưỡng. FAIL thì nút không bấm được.
2. **Chốt cứng thứ hai: `/` dưới 25 GB free → app chuyển read-only toàn cục**, banner đỏ, mọi nút L1/L2 tắt, tự khôi phục khi đĩa thoáng lại.
3. **Giới hạn tỷ lệ**: tối đa **1 job load/re-sync trên toàn hệ cùng lúc**, và **1 lần/ngày** trừ khi ADMIN gỡ. Ngăn kịch bản "thấy dễ quá, re-sync liền 5 bảng".
4. Datafile đã được thêm trong phiên 2026-10-13 nên trần 32 GB **tạm thời không chặn** (`BSH_DATA` room 47.683 MB, `BSH_INDEX` 38.713 MB). Nhưng mỗi tablespace vẫn là smallfile → **vẫn phải cảnh báo theo từng datafile**, và nên hỏi sysadmin về việc chuyển sang bigfile.

### Rủi ro còn lại

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| **Wizard `NEW_STREAM` `REGISTER` capture vào DB nguồn production; rollback sai để lại capture mồ côi đào redo và giữ archive log vĩnh viễn** | Cao | `UNREGISTER` là bước không bỏ qua được; `DELETE`/`UNREGISTER` **chỉ** chạm tên do chính phiên đó tạo; nghiệm thu Phase 4 bắt buộc có bài tạo-rồi-xoá và kiểm `dba_capture` trở về đúng số dòng |
| Bug trong param editor hoặc wizard làm hỏng cả 3 luồng cùng lúc | Cao | Allowlist intent (không có lệnh tự do); blocklist lớp 2; xem trước mọi lệnh; backup + `checkprm` + tự restore; không ghi param process đang chạy; mọi script lưu ra đĩa |
| CPython standalone không chạy / wheel không cài offline được | Cao | **Spike Phase 0**. Dự phòng: `python3.6` + Flask 1.1.4 (loại, nợ kỹ thuật) hoặc xin `dnf module install python39` |
| RAM available chỉ ~6 GB, verify bảng lớn ngốn PGA | Trung bình | Verify tuần tự, parallel 2–4, không song song; đo RSS ở Phase 1 |
| Gọi ggsci song song gây lỗi lạ | Trung bình | Một hàng đợi tuần tự duy nhất |
| Logic watermark code sai → tin sai là đã đồng bộ | Trung bình | Dùng đúng luật đã chứng minh 8 lô; fixture thật; **đối chiếu số công cụ với số gõ tay** ở mỗi phase |
| Người dùng tin công cụ hơn mức nó đáng tin | Trung bình | UI nói rõ verify "nhanh" **có thể dương tính giả**; mọi kết quả hiện kèm SCN và thời điểm đo |
| Tunnel đứt giữa lúc chạy việc dài | Thấp | Job detach bằng `nohup`; state wizard ở server |

## Việc cần người khác

1. **Cửa sổ bảo trì** để restart 6 process sau khi chuyển sang `USERIDALIAS` (Phase 0 việc 2).
2. **DBA nguồn** — hai việc:
   - `PMNV` có ở `ARCHIVELOG` không, archive giữ bao lâu — quyết định wizard được phép dừng Extract tối đa bao lâu. **Hiện chưa đo**, cần biết trước khi wizard tự động dừng Extract.
   - Mỗi lần tạo luồng mới cần `ALTER SYSTEM SET streams_pool_size` ở **cả hai đầu**. Công cụ **không tự chạy** lệnh này (nó chạm DB nguồn production và nằm trong blocklist) — chỉ kiểm tra, báo đỏ và sinh lệnh cho DBA chạy tay.
3. **Bảo mật**: ngoài `dirprm/*.prm`, file `D:\code\ora_gg\.claude\settings.local.json` có mật khẩu Oracle **dạng rõ** trong allowlist. Nên xử lý, độc lập với dự án này.
4. **Sysadmin** (không chặn dự án): chuyển `BSH_DATA`/`BSH_INDEX` sang bigfile; và nếu sau này muốn bỏ tunnel thì xin reverse-proxy TLS qua nginx/httpd có `rich rule` giới hạn subnet tổ DBA — **đừng xin mở `8099/tcp` trần ra LAN**.

*Ghi chú: theo CLAUDE.md, tài liệu kiến trúc này sẽ được ghi vào Obsidian (`ora_gg - Công cụ quản trị GoldenGate`) ngay sau khi kế hoạch được duyệt — chưa ghi vì plan mode chỉ cho sửa file kế hoạch.*
