# 01 – Product Requirements (PRD)

## Bối cảnh
Team dùng GitLab (Cloud hoặc self-hosted). Cần biết: ai commit gì, chất lượng ra sao, lỗi nghiêm trọng nằm ở đâu, và xu hướng theo tuần/tháng để coaching.

## Người dùng
| Vai trò | Nhu cầu |
|---|---|
| Tech Lead | Xem điểm & lỗi của từng member, top lỗi critical/high cần xử lý |
| Developer | Nhận review AI ngay trên MR, xem điểm cá nhân & xu hướng |
| Engineering Manager | Báo cáo tuần/tháng theo team, so sánh kỳ trước |
| Admin | Cấu hình project, rubric, mapping người dùng |

## Yêu cầu chức năng
- [ ] **F1** Kết nối nhiều GitLab project (token + URL).
- [ ] **F2** Thu thập commit, MR, comment review, pipeline (backfill lịch sử + cập nhật tăng dần).
- [ ] **F3** Nhận webhook Push/MR để review gần real-time.
- [ ] **F4** AI review từng MR (ưu tiên) hoặc commit trực tiếp lên nhánh chính.
- [ ] **F5** Mỗi review trả về: điểm 0–100, danh sách finding (severity, category, file, line, message, suggestion).
- [ ] **F6** Gắn review/finding về **người** (không chỉ email Git) và **team**.
- [ ] **F7** Đăng tóm tắt review thành comment trên MR (bật/tắt theo project).
- [ ] **F8** Tổng hợp theo tuần/tháng: điểm TB, số finding theo severity, mật độ lỗi/100 LOC, xu hướng.
- [ ] **F9** Dashboard: team overview, chi tiết member, danh sách critical đang mở.
- [ ] **F10** Báo cáo định kỳ (Markdown/HTML) gửi email hoặc Slack/Teams.
- [ ] **F11** Developer có thể **dispute** 1 finding (false positive) → lead duyệt.
- [ ] **F12** Kèm metric quy trình: PR review time, PR size, review rounds, build success rate.

## Ngoài phạm vi (v1)
- GitHub/Bitbucket (thiết kế collector dạng plugin để mở rộng sau).
- Tự động sửa code.
- Xếp hạng lương/thưởng tự động.

## Tiêu chí thành công
- [ ] Review 1 MR ≤ 2 phút từ lúc webhook đến.
- [ ] ≥ 80% finding critical/high được lead đánh giá là đúng (đo qua dispute rate).
- [ ] Báo cáo tuần sinh tự động 08:00 thứ Hai.

## [DECISION] cần người dùng chốt
- [x] LLM provider: Claude API / OpenAI / model nội bộ (Ollama, vLLM)? → Claude Code CLI trên worker
- [x] Review theo MR hay cả commit trực tiếp lên `main`? → cả hai
- [x] Ai được xem điểm của người khác (chỉ lead hay cả team)? → chỉ lead
