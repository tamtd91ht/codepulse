# 10 – Dashboard & Reports

## Lựa chọn `[DECISION]`
- **A. Grafana** (giống DevLake): nhanh, đọc thẳng PostgreSQL, phù hợp v1.
- **B. Web app riêng** (Next.js + API): UX tốt hơn, có dispute flow, phân quyền chi tiết.
- Khuyến nghị: v1 Grafana cho lead/manager + trang web tối thiểu cho dispute; v2 chuyển sang B.
- **Đã chốt (Phase 0): B. Web app Next.js.**

## Các màn hình
### 1. Team Overview (tuần/tháng)
- [ ] KPI tiles: Quality Index TB, tổng critical/high, defect density, build success rate (kèm delta kỳ trước)
- [ ] Bảng member: avatar, reviews, avg score, critical, high, density, quality index, xu hướng (sparkline)
- [ ] Stacked bar: số finding theo severity theo tuần
- [ ] Top category lỗi & top file/module "nóng"

### 2. Member Detail
- [ ] Line chart avg score & quality index theo tuần (12 tuần)
- [ ] Heatmap category × tuần
- [ ] Danh sách MR/commit gần đây + điểm + link GitLab
- [ ] Finding đang mở (critical/high trước)
- [ ] Điểm mạnh / cần cải thiện (AI tóm tắt từ finding của kỳ)

### 3. Critical Board
- [ ] Mọi finding critical/high `open`, lọc theo project/team/người, thời gian tồn tại

### 4. Review Detail
- [ ] Summary, điểm, findings với code excerpt, nút Dispute

### 6. Thành viên (đánh giá theo người; người dùng 2026-10-27, ngoài spec gốc)
- [x] Khoảng thời gian 7/30/90 ngày hoặc tuỳ chọn; lọc team, dự án, repo; tìm tên; chỉ người có review / cần hỗ trợ
- [x] KPI: người có review, review (MR/commit/LOC), điểm TB theo LOC, tỉ lệ approve, critical, high (bấm mở Critical Board)
- [x] Biểu đồ điểm TB theo người (thấp nhất trên cùng) + kết luận cả nhóm
- [x] Bảng: review, điểm TB (+ thấp nhất), kết luận (thanh chia theo verdict), critical/high/khác, lỗi/100 LOC, xu hướng tuần, review gần nhất; dòng đỏ khi có critical hoặc điểm TB < 65
- [x] Trang Member Detail thêm thẻ **Đánh giá trong kỳ**: tóm tắt, kết luận, loại lỗi, theo tuần, theo repo

### 5. Admin
- [ ] Projects, connections, unmapped accounts, rubric/weights, chi phí LLM theo tháng

## Báo cáo định kỳ
- [ ] Template Jinja2 → Markdown/HTML
- [ ] **Weekly** (thứ Hai 08:00): tổng quan team, top cải thiện, top critical chưa xử lý, member cần hỗ trợ
- [ ] **Monthly** (ngày 1): xu hướng 4 tuần, so sánh tháng trước, category lỗi phổ biến → gợi ý chủ đề training
- [ ] Gửi qua Email (SMTP), Slack/Teams webhook – cấu hình theo team
- [ ] Báo cáo cá nhân gửi riêng cho từng dev (chỉ dữ liệu của họ)

## Ví dụ khung Weekly Report
```markdown
# Weekly Code Quality – Team Backend – Tuần 43/2026
| Chỉ số | Tuần này | Tuần trước | Δ |
|---|---|---|---|
| Quality Index | 82.4 | 79.1 | +3.3 |
| Critical | 1 | 3 | -2 |
## Critical/High chưa xử lý
- [critical] SQL injection – app/auth/login.py:42 – @nguyenvana – 3 ngày
## Ghi nhận
- @tranthib: avg score 91, 0 high
## Gợi ý coaching
- 40% finding thuộc "testing" → workshop viết unit test
```
