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
| POST | `/disputes/{id}/ai-check` | Nhờ AI phân tích lại khiếu nại đang chờ (202, job RQ; kết quả ở `ai_*` của `GET /disputes`); AI không quyết định. 409 khi đã duyệt, đang chạy, repo `allow_external_llm = false` hoặc hết ngân sách tháng | lead của người khiếu nại / admin |
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
| GET | `/members/evaluation?from&to&team_id&business_project_id&project_id` | Mỗi người trong phạm vi: số review hoàn tất, điểm TB theo LOC, kết luận, finding theo mức, xu hướng tuần (tính trực tiếp từ `reviews`, tối đa 366 ngày, mặc định 30 ngày; bỏ người dùng không hoạt động như số liệu đã lưu; `mrs`/`commits` là số lượt review, MR review nhiều vòng tính nhiều lần) | **từ lead trở lên** (2026-11-03; developer và người chỉ `view` dự án: 403, xem mình ở `/members/{id}/evaluation`); manager: chỉ dòng của chính mình; lead: team mình; quản lý dự án: mọi người trong dự án; admin: tất cả |
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
| GET | `/insights/flow?business_project_id=&project_id=&period=week\|month\|range&start=&end=` | Luồng làm việc cấp dự án/repo: MR đã merge, 4 chặng (median/p75), merge không review, cỡ MR, xu hướng, MR đang chờ phản hồi, pipeline; `confidence`, `data_as_of` | **từ lead trở lên** (admin, lead/manager team, quản lý dự án; thành viên 403), trong dự án được cấp (admin: cả repo chưa gán); tên tác giả theo `can_see_work` |
| GET | `/insights/flow/mrs?…&stage=&only=unreviewed\|xl&limit=&offset=` | Danh sách MR tạo nên số liệu, chậm nhất trước | như trên |
| GET | `/insights/review-load?…` | Ai review MR của ai (đếm MR, không đếm comment), độ tập trung | như trên |
| GET | `/insights/knowledge?business_project_id=&project_id=&end=&limit=` | Rủi ro tri thức: thành phần (bản đồ thành phần, spec 20 §4.9b) trong `KNOWLEDGE_WINDOW_DAYS`, bus factor theo số lần chạm file có phai dần, một người nắm / người nắm đã vắng / cần gắn account; `nodes`: cây repo › module › thành phần › package con, `counted` (cảnh báo, đếm một lần ở cấp cao nhất) / `inherited` / `why` (§4.9c), người nắm (tên theo `can_see_work`) | như trên |
| GET | `/insights/components?project_id=` | Cấu trúc một repo: thành phần (tên, loại, số file, test, đường dẫn mẫu), nguồn auto/llm, trạng thái lần gom bằng AI | xem được repo |
| POST / DELETE | `/admin/insights/components/{project_id}` | Xếp hàng AI gom thành phần theo nghiệp vụ (202; chỉ đường dẫn, không mã nguồn; 409 khi đang chạy, `allow_external_llm = false`, hết ngân sách) / quay về bản tự động (204) | admin |

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
| POST | `/evidence/{user_id}/{yyyy-mm}/ai-draft` | Bản nháp nhận xét bằng AI từ snapshot đã chốt (không gửi mã nguồn); mỗi câu phải dẫn MR/finding/bằng chứng, câu không dẫn bị bỏ; chặn khi hết ngân sách hoặc có repo `allow_external_llm=false`; audit `evidence.ai_draft` kèm chi phí (tính vào ngân sách tháng) | lead trực tiếp, admin; hồ sơ đã gửi |
| POST | `/admin/insights/backfill` `{project_id?}` | Lấp dữ liệu Insights cho repo đã sync trước Phase 9: dựng lại từ `raw_gitlab` rồi sync đầy đủ (202, hàng đợi); audit `insights.backfill` | admin |
| GET | `/admin/insights/backfill?project_id=` | Tiến trình lấp dữ liệu Insights theo repo (queued, reconverting, syncing, done, failed; lỗi; thời điểm) | admin |

## Bổ sung (bộ tiêu chí chấm điểm, docs/specs/22 §6, giai đoạn 22.2, 2026-11-03)

Lỗi kiểm tra thành phần trả 422, `detail` dạng FastAPI: `[{loc: ["body", "components", i, "key"], msg, type}]` (lỗi cả bộ: `loc` = `["body", "components"]`). Tên trùng (không phân biệt hoa thường): 409.

| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/criteria/meta` | `{categories: [{key, label}], limits: {min_components, max_components, name_max (60, tên thành phần), description_max (500, mô tả thành phần), key_pattern, component_name_max, component_description_max, weight_min, weight_max, weight_total, set_name_max (100), set_description_max, note_max, preview_max}, templates: [{key, name, description, components}]}` | đã đăng nhập |
| GET | `/criteria-sets?archived=` | `[{id, name, description, archived, used, current_version: {id, version, components, apply_caps, note, created_at}\|null, version, version_id, components, apply_caps, projects_count, primary_count, projects: [{id, name, is_primary}] (chỉ dự án người xem thấy), created_at, updated_at}]`. `used`: đã có điểm hoặc đang gán dự án (khi đó chỉ lưu trữ, không xoá) | `criteria.manage` hoặc `criteria.assign` |
| GET | `/criteria-sets/{id}` | như một dòng ở trên, thêm `versions: [{id, version, note, apply_caps, created_at, created_by, created_by_name}]` (mới nhất trước) | như trên |
| GET | `/criteria-sets/{id}/versions/{version}` | `{id, set_id, version, components, apply_caps, note, created_at, created_by, created_by_name}` | như trên |
| POST | `/criteria-sets` `{name, description?, components?, apply_caps=true, from_template?}` | 201, chi tiết bộ. Có `from_template` mà không gửi `components`/`description`: lấy của mẫu. Phiên bản 1. Audit `criteria_set.create` | `criteria.manage` |
| PUT | `/criteria-sets/{id}` `{name, description, components, apply_caps, note?}` | Chi tiết bộ kèm `new_version`; phiên bản mới chỉ khi `components` (kể cả thứ tự) hoặc `apply_caps` đổi. Audit `criteria_set.update` | `criteria.manage` |
| POST | `/criteria-sets/{id}/clone` `{name}` | 201, bộ mới v1 với thành phần của phiên bản hiện tại. Audit `criteria_set.clone` | `criteria.manage` |
| POST | `/criteria-sets/{id}/archive`, `/unarchive` | Chi tiết bộ; 409 nếu đang là bộ chính ở một dự án. Audit `criteria_set.archive\|unarchive` | `criteria.manage` |
| DELETE | `/criteria-sets/{id}` | 204; 409 nếu đã có điểm hoặc đang gán dự án. Audit `criteria_set.delete` | `criteria.manage` |
| POST | `/criteria/preview` `{components, apply_caps, business_project_id, limit≤20, set_id?}` | `{business_project_id, items: [{review_id, title, target, project, author_user_id, author, finished_at, loc_reviewed, findings_count, score, criteria_score, delta, capped, breakdown, findings: [{id, severity, category, title}]}]}` trên các review xong gần nhất (trong phạm vi `review.view`). `set_id`: dùng cách AI đã xếp cho bộ đó. Không ghi gì | `criteria.manage` |
| GET | `/business-projects/{bp}/criteria-sets` | `[{set_id, name, description, archived, is_primary, position, version, components, apply_caps}]` theo `position` | xem được dự án |
| PUT | `/business-projects/{bp}/criteria-sets` `{sets: [{set_id, is_primary}]}` | Thay cả danh sách (thứ tự = `position`); có bộ thì đúng một bộ chính (422); bộ lưu trữ không gán mới được (422); hai yêu cầu cùng lúc: 409. Bộ mới gán: xếp job `recompute_criteria_job` (không xếp được: 503, danh sách vẫn đã lưu). Trả như GET. Audit `project.criteria_sets` | `criteria.assign` trên dự án |
| POST | `/business-projects/{bp}/criteria-sets/{set_id}/recompute` `{since?: date}` | 202 `{status: "queued", set_id, since}` (since: đầu ngày theo REPORT_TZ). Audit `criteria_set.recompute` | `criteria.assign` trên dự án |
| GET | `/reviews/{id}` | thêm `criteria_scores: [{set_id, set_name, version, is_primary, score, breakdown: {capped, components: [{key, name, weight, score, penalty, findings: [{id, source: ai\|category}]}]}}]`, bộ chính trước; mỗi finding thêm `criteria` | `review.view` |
| GET | `/criteria/summary?business_project_id&project_id&user_id&from&to&set_id` | `{sets: [{set_id, name, is_primary}], set_id, set_name, version, reviews, loc, score, components: [{key, name, weight, current, score, findings}], weeks: [{week_start, score, reviews}]}`; trung bình có trọng số theo LOC, trên review xong trong phạm vi `review.view`; không có `set_id`: bộ chính của dự án (của `business_project_id`, hoặc dự án của `project_id`); không có dữ liệu: `reviews` = 0, `components` và `weeks` rỗng; dự án không thấy: 404 | `review.view` |

## Bổ sung (token cho Claude Code / MCP, docs/specs/26 §2, người dùng 2026-11-05)

Các endpoint dưới đây chỉ nhận phiên dashboard (cookie/JWT phiên); token `cpat_…` không dùng được ở đây (401), nó chỉ dùng ở `/mcp`. `TokenOut` = `{id, name, prefix, scopes, expires_at, last_used_at, revoked_at, created_at, active}` (không bao giờ có hash hay token).

| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/me/tokens` | `[TokenOut]` của chính mình, mới nhất trước (kể cả đã thu hồi / hết hạn; `active` = chưa thu hồi và chưa hết hạn) | đăng nhập |
| POST | `/me/tokens` `{name (1–80), scopes = ["findings:read", "findings:close_request"], days = 90 (30\|90\|180\|365)}` | 201, `TokenOut` + `token` (`cpat_…`, chỉ hiện lần này). `findings:read` luôn có; scope lạ, hạn khác, tên rỗng/dài: 422; đã có 10 token còn hiệu lực: 409. Audit `api_token.create` (không ghi token) | đăng nhập |
| POST | `/me/tokens/{id}/revoke` | `TokenOut`; token của người khác hoặc không có: 404; thu hồi lại: trả như cũ, không audit thêm. Audit `api_token.revoke` | đăng nhập (chủ token) |
| GET | `/admin/users/{user_id}/tokens` | `[TokenOut]` của người đó (404 nếu không có người) | admin |
| POST | `/admin/tokens/{id}/revoke` | `TokenOut`; audit `api_token.revoke` kèm `by_admin` | admin |

## Bổ sung (báo cáo Claude Code / MCP, docs/specs/26 §13, người dùng 2026-11-05)

Bộ lọc chung: `from`, `to` (ngày, theo `REPORT_TZ`; mặc định 30 ngày đến hôm nay, tối đa 366 ngày), `business_project_id`, `project_id`, `team_id`, `user_id`, `severity` (`critical`, `high` hoặc cả hai cách dấu phẩy). Mọi dữ liệu giới hạn theo phạm vi `report.mcp` của người xem (người: `person_scope`; yêu cầu đóng và backlog: `work_scope` trên review). Tiêu đề finding / lý do của bước kiểm chỉ hiện khi `review.view` phủ review đó. Tỉ lệ 0–1 hoặc `null` khi mẫu số 0. Không cộng vào DPI hay điểm review.

| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/mcp-report/summary` | `{period, previous, filters, kpis:{calls, active_users, requests, first_pass_rate, median_hours_to_close, prev:{…cùng khoá, kỳ trước cùng độ dài}}, members:[…], total:{…}, note}`; mỗi dòng theo docs/specs/26 §13.5. Audit `mcp_report.read` | `report.mcp` |
| GET | `/mcp-report/requests?…&page` | `{items, total, page, page_size: 50}`: yêu cầu đóng qua MCP trong kỳ, mới nhất trước (`attempt` = lần gửi thứ mấy cho finding đó) | `report.mcp` |
| GET | `/mcp-report/calls?…&tool&ok&page` | `{items, total, page, page_size: 50}`: nhật ký gọi tool (`mcp_calls`) | admin |
| GET | `/mcp-report/export.xlsx?…` | Excel: sheet "Theo thành viên" và "Yêu cầu đóng qua MCP". Audit `mcp_report.export` | `report.mcp` |

## Bổ sung (thêm repo vào dự án bằng link GitLab hoặc ID, người dùng 2026-11-12)

- `POST /projects` nhận `repo` = link GitLab (link repo, link MR/nhánh/commit, clone URL https hoặc ssh), đường dẫn `group/repo` hoặc project ID; `connection_id` tuỳ chọn: link tự chọn kết nối theo host (kể cả GitLab chạy dưới đường dẫn con), ID/đường dẫn dùng kết nối duy nhất, có nhiều kết nối thì phải chọn (422 kèm lý do). Vẫn nhận `external_id` + `connection_id` như cũ.
- `business_project_id` gán repo vào dự án ngay khi thêm; cùng luật §3 của spec 21 (admin.projects được uỷ quyền không đưa repo vào dự án mình có quyền).
- Repo đã có: không lỗi, chuyển vào dự án và theo dõi lại nếu đã bỏ, trả 200 `existed: true` (audit `project.add_existing`); repo mới trả 201 (audit `project.create`). Sync đầu tiên được xếp hàng ở server; webhook vẫn gọi riêng `POST /projects/{id}/webhook`.
- Giao diện: form tạo dự án có ô repo (tuỳ chọn, mỗi dòng một repo), tạo xong mở luôn drawer của dự án; drawer dự án có nút "Thêm repo từ GitLab" (dự án chưa có repo thì form hiện sẵn); tab Thêm › Repo nhận nhiều link/ID.
