# 21. Phân quyền theo bộ quyền (permission sets)

> Yêu cầu người dùng 2026-11-03: "sinh ra bộ quyền, gán bộ quyền đó cho member (trừ admin là full quyền); bộ quyền theo các chức năng, gồm cả xem, xoá, sửa, chạy review…; làm một lần nhưng dùng được về sau".
> Quyết định, do người dùng giao chọn phương án tối ưu:
> - mỗi quyền có **phạm vi**;
> - bộ quyền gán **toàn hệ thống**, còn phạm vi dự án vẫn theo quyền `view`/`manage` dự án;
> - làm theo ba giai đoạn A, B, C, mỗi giai đoạn commit riêng.

## 1. Vì sao

Trước spec này, quyền được **ngầm suy** từ dữ liệu: admin, vai trò `lead`/`manager` trong team, quyền `view`/`manage` dự án, cờ `manage_all`. Có 149 endpoint, trong đó 90 cái chỉ admin dùng được, 72 cái tự kiểm tra bên trong. Mỗi lần đổi quyền lại phải sửa rải rác. Spec này đưa quyền về **một danh mục** và **một chỗ kiểm tra**. Admin tạo, sửa và gán bộ quyền mà không cần sửa code.

## 2. Mô hình

**Quyền = chức năng + hành động + phạm vi.**

- **Danh mục quyền** (`app/permissions/catalog.py`) nằm trong code. Mỗi quyền gồm:
  - `key` dạng `nhóm.hành_động`, ví dụ `dispute.decide`;
  - nhãn tiếng Việt và nhóm chức năng (để hiển thị);
  - loại: `flag` (có hoặc không) hoặc `scoped` (có phạm vi);
  - các phạm vi hợp lệ;
  - cờ `sensitive` cho quyền chạm tới dữ liệu cá nhân.
  
  Thêm chức năng mới chỉ cần thêm một dòng vào danh mục.
- **Phạm vi** (với quyền `scoped`), từ hẹp đến rộng:

| Phạm vi | Nghĩa |
|---|---|
| `own` | việc của chính mình |
| `team` | thêm người trong team mình lead hoặc manage (`lead_members` và người trong `managed_teams`) |
| `project` | thêm mọi người trong các dự án mình có quyền `manage` |
| `all` | mọi người trong các dự án mình truy cập được (admin: tất cả) |

  Dự án nào một người **truy cập được** vẫn do `project_grants` (`view`/`manage`) và `manage_all` quyết định. Phạm vi chỉ nói **trong đó** người ấy làm được tới đâu.

- **Bộ quyền** gồm bảng `permission_sets` (tên, mô tả, `builtin`, `auto_role`) và bảng `permission_set_items` (`set_id`, `permission`, `scope`).
- **Gán bộ quyền:** bảng `user_permission_sets` (`user_id`, `set_id`). Quyền hiệu lực của một người là **hợp** các bộ của họ; mỗi quyền lấy phạm vi rộng nhất. Không có quyền "cấm".
- **Bộ có sẵn tự gán theo vai trò** (`auto_role`): người có vai trò tương ứng tự động có bộ đó, không cần gán tay. Admin có thể sửa nội dung bộ có sẵn: sửa bộ "Lead" thì mọi lead đổi theo. Bộ tự tạo thì gán tay.

| Bộ có sẵn | Tự gán cho |
|---|---|
| Thành viên | mọi người đang hoạt động |
| Lead | người có vai trò `lead` trong một team |
| Manager | người có vai trò `manager` trong một team |
| Quản lý dự án | người có quyền `manage` ở ít nhất một dự án |
| Quản lý tổng | `users.manage_all` |

- **Admin** luôn có mọi quyền với phạm vi `all`, không qua bộ quyền.

## 3. Quy tắc cứng: không bộ quyền nào ghi đè

1. Không tự duyệt việc của mình: khiếu nại, yêu cầu đóng finding.
2. Người gửi yêu cầu mới được rút yêu cầu; tác giả thay đổi mới được khiếu nại hoặc gửi yêu cầu đóng.
3. Spec 20 P1–P8: không có điểm tổng cho từng người trên Luồng làm việc; tên người hiển thị theo `can_see_work`.
4. Ngân sách LLM và cổng `allow_external_llm` áp dụng với mọi người, kể cả admin.
5. Phải luôn còn ít nhất một admin đang hoạt động.

Quyền `sensitive` (xem đánh giá hoặc hồ sơ tháng của người khác, xem điểm lưu sẵn) cần xác nhận khi cấp, và mọi thay đổi bộ quyền đều ghi `audit_log` (`permission_set.*`).

## 4. Danh mục quyền (bản 1)

Trong cột Mặc định: **TV** = Thành viên, **L** = Lead, **M** = Manager, **QLDA** = Quản lý dự án, **QLT** = Quản lý tổng. Cột này tái tạo đúng hành vi trước spec 21.

| key | Chức năng | Loại / phạm vi | Mặc định |
|---|---|---|---|
| `overview.view` | Tổng quan dự án | flag | TV |
| `insights.view` | Luồng làm việc | flag | L, M, QLDA, QLT |
| `members.view` | Trang Thành viên (danh sách) | flag | L, M, QLDA, QLT |
| `evaluation.view` | Đánh giá từng người, heatmap | own/team/project/all · sensitive | TV own; L team; QLDA project; QLT all |
| `metrics.person` | Điểm tuần lưu sẵn của một người | own/team/all · sensitive | TV own; L team |
| `metrics.team` | Chỉ số team, hotspot, leaderboard, báo cáo | team/all | L team; M team |
| `review.view` | Kết quả review, finding | own/team/project/all | TV own; L team; QLDA project; QLT all |
| `review.request` | Yêu cầu review commit | own/project/all | TV own; QLDA project; QLT all |
| `review.rerun` | Chạy lại review | own/team/project/all | TV own; L team; QLDA project; QLT all |
| `review.author` | Gắn tác giả cho review | project/all | QLDA project; QLT all |
| `finding.close_request` | Gửi yêu cầu đóng finding của mình | own | TV |
| `finding.close_decide` | Chạy review / từ chối / xác nhận yêu cầu đóng | team/project/all | L team; M team; QLDA project; QLT all |
| `finding.lower_far` | Hạ critical thẳng xuống low | team/project/all | M team; QLDA project; QLT all |
| `finding.close_manual` | Đóng / mở lại finding thủ công | all | (chỉ admin) |
| `dispute.raise` | Khiếu nại finding của mình | own | TV |
| `dispute.decide` | Duyệt khiếu nại | team/project/all | L team; QLDA project; QLT all |
| `dispute.ai_check` | Nhờ AI phân tích khiếu nại | team/project/all | L team; QLDA project; QLT all |
| `evidence.view` | Hồ sơ tháng | own/team/all · sensitive | TV own; L team |
| `evidence.edit_self` | Viết phần của mình trong hồ sơ | own | TV |
| `evidence.lead` | Nhận xét, chốt, bản nháp AI của hồ sơ | team/all | L team |
| `knowledge.view` | Tiền đề review của repo | flag | TV |
| `knowledge.edit` | Làm mới, duyệt, sửa Tiền đề review; hồ sơ dự án; repo .claude dùng chung | project/all | QLDA project; QLT all |
| `gitlab_members.import` | Tra cứu, nhập người từ GitLab | project/all | QLDA project; QLT all |
| `telegram.self` | Liên kết Telegram của mình | flag | TV |
| `assistant.use` | Trợ lý Stetho | flag | M |
| `admin.connections` | Kết nối GitLab | flag | — |
| `admin.projects` | Dự án và repo: tạo, gom, theo dõi, sync, webhook, cấp quyền dự án | flag | — |
| `admin.people` | Người, team, tài khoản Git | flag | — |
| `admin.llm` | LLM, token, chi phí | flag | — |
| `admin.review_settings` | Cấu hình review | flag | — |
| `admin.insights` | Đồng bộ Insights, cấu trúc repo bằng AI | flag | — |
| `admin.notifications` | Webhook, bot Telegram | flag | — |
| `admin.commands` | Chạy command .claude | flag | — |
| `admin.purge` | Xoá dữ liệu | flag | — |
| `admin.permissions` | Tạo, sửa, gán bộ quyền | flag | — |

Dấu "—" nghĩa là không bộ có sẵn nào có quyền đó: chỉ admin, hoặc bộ do admin tạo.

## 5. Kiểm tra quyền

- `Principal.perms: dict[str, Scope | True]` được tính **một lần mỗi request** trong `load_principal`. Admin không cần tính: luôn đủ quyền.
- `Principal.can(key) -> bool` và `Principal.scope(key) -> Scope | None`.
- `Principal.covers(key, user_id, bp_id) -> bool` trả lời "có quyền `key` trên việc của `user_id` trong dự án `bp_id`". Hàm này thay các kiểm tra rải rác như `can_see_work`, `lead_members`, `_may_decide`.
- Endpoint khai báo bằng `Depends(require("key"))`. Với quyền `scoped`, endpoint còn gọi `covers(...)` trên đối tượng cụ thể.
- `/auth/me` trả `permissions` (key → phạm vi). Frontend dùng `can("key")` cho menu và nút bấm. Các cờ cũ (`insights`, `members_page`, `command_runs`) được giữ trong giai đoạn chuyển tiếp, rồi bỏ ở giai đoạn C.

## 6. Giai đoạn

| Giai đoạn | Nội dung | Hành vi |
|---|---|---|
| **A** | Danh mục; 3 bảng + migration (seed các bộ có sẵn đúng cột Mặc định); `Principal.perms/can/scope/covers`; `require()`; `/auth/me.permissions`; test resolve | **không đổi** |
| **B1** | Review, finding, đóng finding, khiếu nại (`reviews`, `commits` (phần review), `closures`, `disputes`, `activity`, `review_reports`) dùng `require`/`covers` | không đổi (test hiện có giữ nguyên) |
| **B2** | Insights, đánh giá, chỉ số, hồ sơ tháng (`insights`, `evaluation`, `metrics`, `overview`, `evidence`) | không đổi |
| **B3** | Tri thức, GitLab members, Telegram, assistant, commands (`knowledge*`, `claude_repo`, `members`, `telegram`, `assistant`, `commands`, `commits` phần hồ sơ) | không đổi |
| **B4** | Admin (`admin`, `llm`, `settings`, `purge`, `notifications`): `AdminPrincipal` → `require("admin.*")` | không đổi (mặc định chỉ admin) |
| **C** | API quản lý bộ quyền (`/admin/permission-sets`, gán người, xem trước quyền hiệu lực); trang Admin › Phân quyền (ma trận chức năng × phạm vi, nhân bản, gán hàng loạt, cảnh báo quyền nhạy cảm); frontend chuyển sang `can()` | admin bắt đầu tuỳ biến |

Mỗi giai đoạn: lint, mypy, toàn bộ test qua, rồi commit và push. Giai đoạn B thêm **test ma trận**: với mỗi bộ có sẵn, kết quả cho phép/từ chối của các endpoint chính phải đúng như trước.
