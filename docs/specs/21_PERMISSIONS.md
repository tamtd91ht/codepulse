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
| `metrics.team` | Chỉ số team, hotspot, leaderboard, báo cáo | own/team/all · team gồm cả team manage | TV own (chỉ số team của mình; leaderboard và báo cáo cần team trở lên); L team; M team |
| `review.view` | Kết quả review, finding | own/team/project/all | TV own; L team; QLDA project; QLT all |
| `review.request` | Yêu cầu review commit | own/project/all | TV own; QLDA project; QLT all |
| `review.rerun` | Chạy lại review (tốn ngân sách LLM) | project/all | QLDA project; QLT all |
| `review.author` | Gắn tác giả cho review | project/all | QLDA project; QLT all |
| `finding.close_request` | Gửi yêu cầu đóng finding của mình | own | TV |
| `finding.close_decide` | Chạy review / từ chối / xác nhận yêu cầu đóng | team/project/all · team gồm cả team manage | L team; M team |
| `finding.lower_far` | Hạ critical thẳng xuống low | team/project/all · team gồm cả team manage | M team |
| `finding.close_manual` | Đóng / mở lại finding thủ công | all | (chỉ admin) |
| `dispute.raise` | Khiếu nại finding của mình | own | TV |
| `dispute.decide` | Duyệt khiếu nại | team/project/all | L team |
| `dispute.ai_check` | Nhờ AI phân tích khiếu nại | team/project/all | L team |
| `evidence.view` | Hồ sơ tháng | own/team/all · sensitive | TV own; L team |
| `evidence.edit_self` | Viết phần của mình trong hồ sơ | own | TV |
| `evidence.lead` | Nhận xét, chốt, bản nháp AI của hồ sơ | team/all | L team |
| `knowledge.view` | Tiền đề review của repo | flag | TV |
| `knowledge.edit` | Làm mới, duyệt, sửa Tiền đề review | project/all | QLDA project; QLT all |
| `knowledge.shared` | Repo .claude dùng chung, làm mới hồ sơ dự án | flag | — |
| `gitlab_members.import` | Tra cứu, nhập người từ GitLab | flag | — |
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

## 7. Giai đoạn C: API và màn Phân quyền

Mọi endpoint cần `admin.permissions` (mặc định chỉ admin):

| Endpoint | Việc |
|---|---|
| `GET /admin/permissions/catalog` | danh mục theo nhóm chức năng (nhãn, phạm vi, flag, sensitive), nhãn phạm vi |
| `GET /admin/permission-sets` | các bộ kèm items và số người (bộ có sẵn: người có vai trò đó, trừ admin; bộ tự tạo: người được gán còn hoạt động) |
| `POST /admin/permission-sets` | tạo; `from_set_id` để nhân bản (lấy items và mô tả của bộ nguồn) |
| `PATCH /admin/permission-sets/{id}` | tên, mô tả, items (thay cả danh sách) |
| `DELETE /admin/permission-sets/{id}` | chỉ bộ tự tạo |
| `PUT /admin/permission-sets/{id}/users` | thay người được gán; chỉ bộ tự tạo |
| `GET /admin/users/{id}/permissions` | xem trước quyền hiệu lực và bộ nào cho mỗi quyền |

Quyết định (người dùng giao chọn):
- Bộ có sẵn: sửa được items và mô tả; không đổi tên, không đổi `auto_role`, không xoá, không gán tay.
- Quyền `sensitive` cần `confirm_sensitive=true` khi **cấp mới hoặc nới phạm vi** (tạo, sửa, gán người mới), không thì 422 kèm danh sách. Nhân bản không cần, vì bản sao chưa cấp cho ai.
- Người giữ `admin.permissions` mà không phải admin: không cấp quyền mình không có (hoặc rộng hơn mình), không cấp `admin.permissions`, không thêm quyền vào bộ mình đang giữ, không tự gán mình.
- Không request nào tạo ra admin: admin vẫn chỉ là `ADMIN_EMAILS`; body không nhận `builtin`, `auto_role` (trường lạ bị 422).
- Mỗi thay đổi ghi `audit_log`: `permission_set.create|update|delete|assign`.
- Màn Admin › Phân quyền: hai tab con "Bộ quyền" (danh sách + ma trận) và "Quyền hiệu lực". Menu và nút ở các trang khác chuyển sang `can()` ở bước sau.

## 8. Chốt sau giai đoạn B (2026-11-03)

Giai đoạn B tìm thấy những chỗ bản 1 của §4 vượt quá hành vi cũ. Các bộ có sẵn được chỉnh lại theo bảng §4 hiện tại (migration `f8a0b2c4d6e8`), và các chốt tạm bị bỏ:

1. **Quản lý dự án và Quản lý tổng** chưa từng duyệt yêu cầu đóng hay khiếu nại, cũng chưa từng hạ critical xuống low. Bốn quyền đó không còn trong hai bộ này. Chốt `TEAM_AT_MOST` bị bỏ.
2. **Chạy lại review** luôn cần quản lý dự án, vì tốn ngân sách LLM. Phạm vi hợp lệ chỉ còn project/all. Danh sách "có thể chạy lại" vẫn mở cho mọi người, mỗi review tự trả lời có chạy được hay không.
3. **Phạm vi "team" cho từng quyền** (cờ `managed_team` trong danh mục):
   - Phạm vi "team" mặc định chỉ gồm người mình **lead**.
   - Chỉ những quyền manager vốn đã có mới tính cả người của team mình **manage**: `finding.close_decide`, `finding.lower_far`, `metrics.team`.
   - Nhờ vậy, người vừa là lead vừa là manager không thấy dữ liệu cá nhân của team mình chỉ manage (Phase 0).
   - Muốn một manager duyệt khiếu nại thì cấp phạm vi `all`, tức trong các dự án họ được xem.
4. **Thành viên** xem được chỉ số tổng hợp của chính team mình (`metrics.team` = own). Bảng xếp hạng và báo cáo tuần cần phạm vi team trở lên.
5. **Ba màn trước chỉ admin dùng** (repo .claude dùng chung, làm mới hồ sơ dự án, nhập người từ GitLab) có quyền riêng: `knowledge.shared` và `gitlab_members.import`. Không bộ có sẵn nào có hai quyền này. Không còn kiểm tra admin cứng, nên bộ tự tạo cũng mở được.
6. **`/auth/me`**: `insights` và `members_page` lấy từ `can("insights.view")` và `can("members.view")`. Bộ tự tạo nào mở hai trang này thì menu cũng hiện theo.
7. **Một khác biệt chấp nhận được:** endpoint có `require` trả 403 ngay khi thiếu quyền. Trước đây nó có thể trả 404 vì không thấy repo, hoặc 422 vì body sai. Không làm lộ thông tin gì.
