# ggadmin — công cụ quản trị Oracle GoldenGate (web)

Quản trị hệ GoldenGate 21.3 Classic tại BSH: 3 luồng, 73 bảng, ~374,8 triệu bản ghi.
Thay cho việc SSH vào gõ `ggsci` bằng tay, và **code hoá 3 runbook** đã chứng minh đúng qua 8 lô.

Kế hoạch đầy đủ: `C:\Users\User\.claude\plans\fizzy-sniffing-snail.md`
Tài liệu nghiệp vụ gốc: `D:\code\ora_gg\*.md`

## Chạy ở đâu

| | |
|---|---|
| Server | `oracle@172.16.33.90` — OL 8.10, glibc 2.28, 16 vCPU, 31 GB RAM |
| Thư mục | `/u01/app/ggadmin/` — **toàn bộ nằm trong đây, `rm -rf` là gỡ sạch** |
| Runtime | CPython 3.12 standalone trong `py/` — **không đụng Python hệ thống (3.6.8)**, không cần sudo |
| Web | `uvicorn --host 127.0.0.1 --port 8099 --workers 1` |
| Truy cập | SSH tunnel: `ssh -N -L 8099:127.0.0.1:8099 oracle@172.16.33.90` rồi mở `http://127.0.0.1:8099` |
| DB | `oracledb` **thin mode** → không cần thư viện Oracle, process web tách hoàn toàn khỏi `$ORACLE_HOME` |

Vì sao SSH tunnel chứ không mở port: `firewalld` đang bật và `oracle` không có sudo, nên **không mở được port nào**; mà công cụ này sửa được param file và dừng được Replicat production, nên không phơi nó ra LAN là an toàn hơn. SSH lo luôn phần mã hoá và xác thực lớp một.

## Triển khai

```powershell
# 1. Tren may Windows co internet
.\tools\make_wheels.ps1      # 21 wheel manylinux cho py3.12 -> wheelhouse\
.\tools\fetch_python.ps1     # CPython 3.12 standalone, co verify SHA-256 upstream
```

```bash
# 2. Day len server
scp vendor-runtime/cpython-3.12*.tar.gz oracle@172.16.33.90:/tmp/
scp -r wheelhouse requirements.txt tools/spike.sh oracle@172.16.33.90:/u01/app/ggadmin/

# 3. Chay spike - chung minh kien truc TRUOC khi viet tinh nang
ssh oracle@172.16.33.90 'mkdir -p /u01/app/ggadmin/logs; \
  bash /u01/app/ggadmin/spike.sh /tmp/cpython-3.12.15+20261009-x86_64_v2-unknown-linux-gnu-install_only.tar.gz \
  65cd75518588c2c0247aefa2dd48da01302b166c2a9aac63621115b23889058a'
```

Nhị phân tải về **không commit** vào git. `wheelhouse/MANIFEST.txt` và
`vendor-runtime/MANIFEST.txt` giữ release tag + SHA-256 để tải lại y nguyên.

## Kết quả spike — 2026-10-13, PASS toàn bộ

| Mục | Kết quả |
|---|---|
| CPython standalone | **3.12.15**, `RPATH=$ORIGIN/../lib` (relocatable thật), 217 MB, chạy trên glibc 2.28 |
| stdlib cần dùng | `sqlite3` **3.53.1** · `ssl` · `hashlib` · `tomllib` · `asyncio` · `subprocess` — đủ cả |
| Wheel offline | 21 wheel / 11 MB, `pip install --no-index --dry-run` **PASS** → wheelhouse đầy đủ, **không cần `gcc`** |
| `oracledb` | 3.4.2, **thin mode = True** |
| Kết nối DB đích | OK qua alias `BSHC` trong `tnsnames.ora`. `BSHC` = Oracle **19c EE**, `READ WRITE`, `PRIMARY`, **`NOARCHIVELOG`** |
| `ggsci` qua subprocess | rc=0, trả đúng **7 dòng** process, tất cả RUNNING lag ~0. Dùng `env` dựng riêng, không kế thừa env cha |
| Cạm bẫy 26 | Đã khẳng định cột 4 của `info all` là lag dạng `hh:mm:ss` → chốt luật "chỉ đọc lag từ `info all`" |
| Account `ODS` | **OPEN** → thiết kế "không dùng sysdba" chạy được |
| `GGADM_RO` / `GGADM_LOAD` | **chưa tồn tại** → Phase 0 việc 3 |
| `streams_pool_size` | 2048 MB **cả hai đầu** |
| `dircrd` | **rỗng** → credential store chưa lập |
| Mật khẩu dạng rõ trong param | **6 / 7 file** → mục tiêu cuối Phase 0 là **0** |

Hai hiệu chỉnh so với giả định ban đầu: server **có** ra được internet (vẫn giữ
cách cài offline vì nó tái lập được và kiểm toán được), và DB đích là **19c**
chứ không phải 21c như GoldenGate.

## Nguyên tắc không thương lượng

- **Không `sysdba`.** `ALTER INDEX REBUILD` và `GATHER_TABLE_STATS` chạy bằng chính chủ schema `ODS`.
- **Không có hộp nhập lệnh tự do.** `ggsci` chỉ nhận ~15 *intent* đã định nghĩa, tham số validate theo enum.
- **Mọi SQL nằm trong file script**, không nhúng inline qua `ssh` (nháy kép trong Oracle là identifier → `ORA-00904`).
- **Dòng `PROMPT` không kết thúc bằng `-`** → `SP2-0734`.
- **Lag chỉ đọc từ `info all`**, không từ `info ... detail` (bắt được chữ `Started`).
- **Mọi script sinh ra đều lưu xuống `scripts/`** — công cụ không được là hộp đen.
- **Alias `@PMNV` vs `@BSHC` suy ra từ intent**, người dùng không được chọn: cả 2 DB cùng `SERVICE_NAME=BSHC` nên prompt GGSCI hiện `@BSHC` cho cả hai.

## Việc còn lại của Phase 0

1. Tạo `GGADM_RO` + `GGADM_LOAD` với quyền tối thiểu.
2. Lập GG credential store (`dircrd` đang rỗng) → đổi 6 param file sang `USERIDALIAS`, xoá mật khẩu dạng rõ. **Cần cửa sổ bảo trì** vì phải restart process.
