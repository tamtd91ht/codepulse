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
