# 09 – REST API

Base: `/api/v1`. Auth: SSO (OIDC với GitLab) – đã chốt ở Phase 0. OpenAPI tự sinh tại `/docs`.

## Endpoints
| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| POST | `/webhooks/gitlab` | Nhận webhook | token webhook |
| GET/POST | `/connections` | Quản lý kết nối GitLab | admin |
| GET/POST/PATCH | `/projects` | Bật review, đăng comment, external LLM | admin |
| POST | `/projects/{id}/sync` | Chạy collect thủ công | admin, lead |
| POST | `/projects/{id}/backfill-reviews?from=&to=` | Review lại lịch sử | admin |
| GET | `/reviews?user_id=&project_id=&business_project_id=&from=&to=&severity=&status=&mode=fast|deep` | Danh sách review (`mode=deep`: review sâu, gồm `agent` và `deep` cũ) | theo phạm vi |
| GET | `/reviews/{id}` | Chi tiết + findings | theo phạm vi |
| POST | `/reviews/{id}/rerun` | Review lại | lead |
| POST | `/reviews/rerun` | Review lại nhiều kết quả (tối đa 100 id/lần), báo lý do từng cái không chạy được | lead |
| GET | `/reviews/rerunnable?` (cùng bộ lọc với `/reviews`) | Mọi review khớp bộ lọc mà người xem được chạy lại ("chọn tất cả", tối đa 500, mới nhất trước) | lead |
| GET | `/findings?severity=critical&status=open` | Danh sách finding | theo phạm vi |
| POST | `/findings/{id}/dispute` | Dev khiếu nại | chủ finding |
| POST | `/disputes/{id}/decide` | Lead duyệt | lead |
| GET | `/metrics/users/{id}?period=week&from=&to=` | Chuỗi metric cá nhân | bản thân, lead |
| GET | `/metrics/teams/{id}?period=month` | Metric team | lead, manager |
| GET | `/leaderboard?team_id=&period=` | Bảng tổng hợp team | lead, manager |
| GET | `/reports/{period}/{start}?format=md|html` | Báo cáo | lead, manager |
| CRUD | `/users`, `/teams`, `/accounts` | Identity | admin |
| GET | `/identity/unmapped` | Account chưa gắn người | admin |

## Phân quyền (RBAC)
| Role | Phạm vi xem |
|---|---|
| developer | Dữ liệu của chính mình + tổng hợp ẩn danh của team |
| lead | Toàn bộ thành viên team mình |
| manager | Các team cấp dưới |
| admin | Tất cả + cấu hình |

### Phân quyền theo dự án (bổ sung ngoài spec, người dùng yêu cầu 2026-10-28)
- Admin = email nằm trong `ADMIN_EMAILS` (`.env`): toàn quyền, không cần cấp quyền dự án.
- **Quản lý tổng** (`users.manage_all`, người dùng yêu cầu 2026-11-02): dưới admin, trên mọi role khác — như `manage` trên **mọi** dự án (cả dự án tạo sau) mà không cần cấp quyền từng dự án; không vào Admin, không thấy repo chưa gán dự án. Họ thấy mọi người (số liệu vẫn chỉ tính việc trong dự án), nhưng bản thân không tính là "người trong dự án" của ai (không hiện trong danh sách người của manager khác) trừ khi có commit/MR ở đó; ô bật tắt ẩn với admin. **Chỉ admin** cấp và gỡ (kể cả Quản lý tổng cũng không tự cấp/gỡ được), ở Admin › Người & Team, có hộp xác nhận (`PATCH /users/{id}` `{manage_all}`, audit `user.update`); `GET /auth/me` trả `manage_all`.
- Người khác chỉ thấy các dự án (`business_projects`) được admin cấp quyền trong bảng `project_grants`; dự án không được cấp coi như không tồn tại (404), repo chưa gán dự án chỉ admin thấy.
- Hai mức trong một dự án:

| Mức | Được làm |
|---|---|
| `view` (Xem) | Số liệu dự án; review/finding theo luật role ở trên **và** thuộc dự án (dev: của mình; lead: người trong team mình); yêu cầu review **chỉ việc của mình** (2026-11-02: lead không review được người khác) |
| `manage` (Quản lý) | Như `view` + review/finding của mọi người trong dự án (cả tác giả chưa gắn người); yêu cầu review cho bất kỳ ai; chạy lại review; làm mới, duyệt, sửa Tiền đề review. **Xem đủ mọi người trong dự án** (2026-11-02): trang thành viên (`/people/{id}`, `/members/evaluation`, `/members/{id}/evaluation`, heatmap), danh sách người lọc (`/people`), Đóng finding và Khiếu nại của dự án — số liệu chỉ tính việc trong dự án họ được xem |

- "Người trong dự án" = được cấp quyền dự án đó, hoặc tác giả (đã gắn người) của commit/review trong repo của dự án (`app/api/access.py` `project_people`).
- Không mở cho quản lý dự án: điểm tuần lưu sẵn (`/metrics/users/{id}`, leaderboard, báo cáo team) vì gộp mọi dự án của một người; quyết định khiếu nại / yêu cầu đóng finding (vẫn theo lead/manager team); cấu hình Admin.

- Review của chính mình luôn xem được. Bot Telegram phục vụ một dự án: người dùng phải được cấp quyền dự án đó.
- API (admin): `GET`/`PUT /business-projects/{id}/grants` (PUT thay cả danh sách, audit `business_project.grants`); `GET /auth/me` trả `projects: {id: role}`.

## Checklist
- [ ] Pagination cursor-based, filter theo ngày dùng ISO-8601
- [ ] Mọi endpoint có test quyền (403 khi sai role)
- [ ] Audit log cho thao tác admin & quyết định dispute

## Bổ sung (docs/specs/17 – review theo góc nhìn techlead)
| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/repos` | Repo đang theo dõi kèm dự án | đăng nhập |
| GET | `/people` | Người trong phạm vi của người gọi | đăng nhập |
| GET | `/repos/{id}/branches?search=` | Branch từ GitLab | đăng nhập |
| GET | `/repos/{id}/commits?ref&since&until&user_id&limit` | Commit gần đây kèm trạng thái review (ẩn người ngoài phạm vi) | đăng nhập |
| POST | `/repos/{id}/review-commits` | Xếp hàng review các commit đã chọn (≤ REVIEW_BATCH_MAX) | theo tác giả: mình; quản lý dự án / admin mọi người |
| GET | `/reviews?verdict=&trigger=` | Lọc thêm theo kết luận merge và nguồn yêu cầu | như cũ |
| PATCH | `/business-projects/{id}` `review_rules`, `/projects/{id}` `review_rules`, `review_rules_paths` | Quy tắc review | admin |

## Bổ sung (đánh giá theo thành viên, người dùng 2026-10-27)
| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/members/evaluation?from&to&team_id&business_project_id&project_id` | Mỗi người trong phạm vi: số review hoàn tất, điểm TB theo LOC, kết luận, finding theo mức, xu hướng tuần (tính trực tiếp từ `reviews`, tối đa 366 ngày, mặc định 30 ngày; bỏ người dùng không hoạt động như số liệu đã lưu; `mrs`/`commits` là số lượt review, MR review nhiều vòng tính nhiều lần) | developer, manager: chỉ dòng của chính mình; lead: team mình; admin: tất cả |
| GET | `/members/{id}/evaluation?from&to` | Một người: tóm tắt, theo repo, kết luận, loại lỗi, theo tuần | bản thân, lead, admin |
| GET | `/reviews?…&business_project_id=&status=done&status=failed` | Thêm lọc theo dự án và trạng thái (lặp tham số); `from`/`to` là ngày trọn vẹn theo REPORT_TZ; mỗi dòng có `author` | theo phạm vi |

## Bổ sung (chạy command `.claude`, người dùng 2026-10-30)
| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/admin/commands?project_id=&ref=` hoặc `?business_project_id=` | Command trong `.claude/commands` của repo ở nhánh `ref` (mặc định nhánh mặc định) và của repo `.claude` dùng chung của dự án; command dùng chung trùng tên bị đánh dấu `overridden` (bản của repo được chạy) | admin |
| POST | `/admin/command-runs` `{project_id \| business_project_id, ref, source: repo\|shared, command, arguments}` | Xếp hàng một lần chạy (202) trên hàng đợi `commands`; một lần chạy đang chờ/chạy mỗi repo (409); repo tắt review → 409 (không gửi code ra LLM); audit `command.run` | admin |
| GET | `/admin/command-runs`, `/admin/command-runs/{id}?after=` | Lịch sử; chi tiết kèm log trực tiếp (từ mục `after`), kết quả cuối, danh sách file | admin |
| POST | `/admin/command-runs/{id}/cancel` | Huỷ (đang chờ: ngay; đang chạy: ở sự kiện kế tiếp) | admin |
| — | `COMMAND_RUNS_ENABLED=false` | Mọi route ở bảng này trả 404; `/auth/me` có `command_runs: false` (dashboard ẩn tab) | — |
| GET | `/admin/command-runs/{id}/download` | File duy nhất hoặc `.zip` của mọi file; tải một lần rồi xoá khỏi server (lần sau 410); audit `command.download` | admin |

## Bổ sung (Engineering Insights, docs/specs/20 §8–9, người dùng 2026-11-03)
| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/insights/flow?business_project_id=&project_id=&period=week\|month\|range&start=&end=` | Luồng làm việc cấp dự án/repo: MR đã merge, 4 chặng (median/p75), merge không review, cỡ MR, xu hướng, MR đang chờ phản hồi, pipeline; `confidence`, `data_as_of` | được cấp dự án (admin: cả repo chưa gán); tên tác giả theo `can_see_work` |
| GET | `/insights/flow/mrs?…&stage=&only=unreviewed\|xl&limit=&offset=` | Danh sách MR tạo nên số liệu, chậm nhất trước | như trên |
| GET | `/insights/review-load?…` | Ai review MR của ai (đếm MR, không đếm comment), độ tập trung | như trên |
| GET | `/insights/knowledge?business_project_id=&project_id=&end=&limit=` | Rủi ro tri thức: module (N cấp thư mục) trong `KNOWLEDGE_WINDOW_DAYS`, bus factor, một người nắm / người nắm đã vắng, người nắm (tên theo `can_see_work`) | như trên |

## Bổ sung (hồ sơ thành viên & nhật ký khiếu nại, người dùng 2026-11-03)
| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/disputes?pending=false&user_id=` | Mọi khiếu nại một người đã gửi (trang thành viên), kèm thời điểm, người duyệt, finding (mức, loại, file:dòng, trạng thái); chỉ những khiếu nại người xem vốn được thấy | như `/disputes` |
| GET | `/users/{id}` | Hồ sơ một người (Admin › Người & Team) | admin |
| PATCH | `/users/{id}` `{full_name, primary_email, active, manage_all}` | Sửa tên, email (trùng → 409; admin không tự đổi email ra khỏi ADMIN_EMAILS → 409); audit `user.update` kèm `previous_email` | admin |
| PUT | `/users/{id}/avatar` (body = bytes ảnh) | Thay ảnh đại diện: PNG/JPEG/WebP nhận theo nội dung file, ≤ `AVATAR_MAX_KB`; mỗi người một file `<id>.<ext>` trong `AVATAR_DIR`; audit `user.avatar` | admin |
| DELETE | `/users/{id}/avatar` | Xoá ảnh; audit `user.avatar_remove` | admin |
| GET | `/users/{id}/avatar` | Ảnh đại diện (`nosniff`, cache riêng 1 ngày); mọi `UserOut` có `avatar_url` kèm phiên bản | đăng nhập |

## Bổ sung (hồ sơ bằng chứng, docs/specs/20 §6, task 9.10, 2026-11-03)
| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/evidence/{user_id}` | Các tháng đã có hồ sơ và trạng thái | bản thân, lead trực tiếp, admin (D4) |
| GET | `/evidence/{user_id}/{yyyy-mm}` | Hồ sơ một tháng (chưa lưu thì tính trực tiếp), kèm `can` theo §6.2b; xem hồ sơ người khác ghi audit `evidence.view` | như trên |
| PATCH | `/evidence/{user_id}/{yyyy-mm}` `{self_notes, lead_notes, goals_next}` | Sửa phần được phép ở trạng thái hiện tại (409 nếu không) | như trên |
| POST | `/evidence/{user_id}/{yyyy-mm}/{refresh\|submit\|return\|review\|share}` | Làm mới số liệu / gửi / trả lại / đã xem / chốt sau 1:1; audit `evidence.<hành động>` | bản thân: refresh, submit; lead, admin: return, review, share (không với hồ sơ của chính mình) |
| POST/DELETE | `/evidence/{user_id}/{yyyy-mm}/items[/{item_id}]` | Thêm / xoá bằng chứng (chỉ người thêm xoá được) | như trên |
| GET | `/insights/dora?business_project_id=&project_id=&period=&start=&end=` | DORA cấp repo/dự án (20 §4.10): deploy production, lead time, tỉ lệ thay đổi lỗi, khôi phục, xu hướng, thay đổi lỗi gần nhất | như `/insights/flow` |
| PATCH | `/projects/{id}` `{production_environment}` | Môi trường production của repo cho DORA ("" = tự nhận theo tier/tên, D2) | admin |
| — | `/insights/dora` thêm khối `incidents` (9.9) | Sự cố mới, đã đóng, đang mở, sự cố / deploy, thời gian khôi phục theo incident, 10 sự cố mới nhất | như trên |
