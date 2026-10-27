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
| GET | `/reviews?user_id=&project_id=&from=&to=&severity=` | Danh sách review | theo phạm vi |
| GET | `/reviews/{id}` | Chi tiết + findings | theo phạm vi |
| POST | `/reviews/{id}/rerun` | Review lại | lead |
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
| POST | `/repos/{id}/review-commits` | Xếp hàng review các commit đã chọn (≤ REVIEW_BATCH_MAX) | theo tác giả: mình / người mình lead / admin |
| GET | `/reviews?verdict=&trigger=` | Lọc thêm theo kết luận merge và nguồn yêu cầu | như cũ |
| PATCH | `/business-projects/{id}` `review_rules`, `/projects/{id}` `review_rules`, `review_rules_paths` | Quy tắc review | admin |

## Bổ sung (đánh giá theo thành viên, người dùng 2026-10-27)
| Method | Path | Mô tả | Quyền |
|---|---|---|---|
| GET | `/members/evaluation?from&to&team_id&business_project_id&project_id` | Mỗi người trong phạm vi: số review hoàn tất, điểm TB theo LOC, kết luận, finding theo mức, xu hướng tuần (tính trực tiếp từ `reviews`, tối đa 366 ngày, mặc định 30 ngày; bỏ người dùng không hoạt động như số liệu đã lưu; `mrs`/`commits` là số lượt review, MR review nhiều vòng tính nhiều lần) | developer, manager: chỉ dòng của chính mình; lead: team mình; admin: tất cả |
| GET | `/members/{id}/evaluation?from&to` | Một người: tóm tắt, theo repo, kết luận, loại lỗi, theo tuần | bản thân, lead, admin |

