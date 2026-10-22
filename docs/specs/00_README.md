# CodePulse – Bộ spec cho AI Agent xây dựng tool đánh giá chất lượng code

> Tên tạm: **CodePulse**. Ý tưởng lấy từ Apache DevLake (collector → domain layer → metrics → dashboard), bổ sung **AI code review có chấm điểm & phân loại severity** theo từng member.

## Mục tiêu một câu
Tự động thu thập commit/MR từ GitLab, cho AI review từng thay đổi, chấm điểm + phân loại lỗi (critical/high/medium/low/info), gắn về từng member, rồi tổng hợp báo cáo theo tuần/tháng.

## Thứ tự đọc & thực hiện (BẮT BUỘC theo thứ tự)

| # | File | Nội dung | Trạng thái |
|---|---|---|---|
| 0 | `AGENTS.md` | Quy tắc làm việc cho AI agent | [ ] |
| 1 | `01_PRD.md` | Yêu cầu sản phẩm, phạm vi, tiêu chí thành công | [ ] |
| 2 | `02_ARCHITECTURE.md` | Kiến trúc, tech stack, cấu trúc thư mục | [ ] |
| 3 | `03_DATA_MODEL.md` | Schema database (DDL) | [ ] |
| 4 | `04_GITLAB_COLLECTOR.md` | Thu thập dữ liệu GitLab (API + webhook) | [ ] |
| 5 | `05_IDENTITY_TEAM_MAPPING.md` | Gộp tài khoản → người → team | [ ] |
| 6 | `06_AI_REVIEW_ENGINE.md` | AI review: rubric, prompt, JSON output | [ ] |
| 7 | `07_STATIC_ANALYSIS.md` | (Tuỳ chọn) Semgrep/SonarQube bổ trợ | [ ] |
| 8 | `08_SCORING_AGGREGATION.md` | Công thức điểm, tổng hợp tuần/tháng | [ ] |
| 9 | `09_API.md` | REST API nội bộ | [ ] |
| 10 | `10_DASHBOARD_REPORTS.md` | Dashboard & báo cáo định kỳ | [ ] |
| 11 | `11_SECURITY_PRIVACY.md` | Bảo mật, quyền, quyền riêng tư | [ ] |
| 12 | `12_TESTING_QA.md` | Chiến lược test, dữ liệu mẫu | [ ] |
| 13 | `13_DEPLOYMENT_OPS.md` | Docker, cấu hình, vận hành | [ ] |
| 14 | `14_TASKS_CHECKLIST.md` | **Checklist tổng theo phase – agent tick khi xong** | [ ] |

## Cách dùng với AI agent
1. Copy toàn bộ thư mục này vào `docs/specs/` của repo mới.
2. Prompt khởi động gợi ý:
   > "Đọc `docs/specs/AGENTS.md` và `docs/specs/00_README.md`, sau đó thực hiện lần lượt các task trong `14_TASKS_CHECKLIST.md`. Sau mỗi task: chạy test, tick checkbox, ghi chú vào `PROGRESS.md`. Dừng lại hỏi tôi khi gặp mục đánh dấu `[DECISION]`."
3. Mỗi phase xong → review thủ công trước khi cho agent làm phase tiếp.

## Ánh xạ ý tưởng từ DevLake
| DevLake | CodePulse |
|---|---|
| Plugin GitLab (collector/extractor/converter) | `collector/gitlab` 3 bước: raw → tool layer → domain |
| Domain layer (`commits`, `pull_requests`…) | Bảng chuẩn hoá trong `03_DATA_MODEL.md` |
| Team Configuration (`users`, `accounts`, `teams`) | `05_IDENTITY_TEAM_MAPPING.md` |
| SonarQube plugin (`cq_issues`) | `07_STATIC_ANALYSIS.md` |
| Metrics PR Review Time, PR Size, Build Success Rate | `08_SCORING_AGGREGATION.md` |
| Grafana dashboards | `10_DASHBOARD_REPORTS.md` |
| Blueprint / Pipeline (lịch thu thập) | Scheduler trong `02_ARCHITECTURE.md` |
