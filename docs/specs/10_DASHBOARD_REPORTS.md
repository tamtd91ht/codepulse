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
- [x] **[Quyết định]** (user 2026-11-06) Cột **Ngày commit** (ngày/tháng/năm, không giờ): thời điểm của thay đổi
  (`Review.counted_at`: giờ commit, MR là lúc mở MR), API trả `committed_at`. Bộ lọc **Ngày commit** dạng từ–đến
  (lịch `DateRangePicker` như Tổng quan, có "Mọi thời gian"): ngày theo giờ máy người xem, gửi `found_from`/`found_to`
  ([from, to) như link từ dashboard), giữ trên URL (`from`, `to`). Bỏ khoảng ngày thì về danh sách finding đang mở.
- [x] **[Quyết định]** (user 2026-11-06) Id finding ở cuối ô **Vấn đề** (sau tiêu đề và nhãn trạng thái), dạng ô nhỏ
  `#id` (`IdChip`, components/ui.tsx): chữ mono nhạt, viền mảnh; hover/focus hiện biểu tượng chép (màn hình cảm ứng luôn
  hiện); bấm chép số id (không có `#`), ô chuyển xanh "✓ Đã chép" 1,6 giây; không có Clipboard API (HTTP) thì chép qua
  textarea. Trang Đóng finding cũng đặt ô này ở cuối tiêu đề finding (user 2026-11-06), dòng phụ còn repo và file.
  Trang chi tiết review (mục Findings) cũng có ô này ở cuối dòng tiêu đề mỗi finding, sau nhãn trạng thái (user 2026-11-06).

### 3b. Đóng finding — trạng thái mỗi dòng
- [x] **[Quyết định]** (user 2026-11-06) Danh sách và trang finding phải nói cùng một điều. Mỗi dòng là một yêu
  cầu/lần đóng, nhưng ô **Trạng thái** hiện `shown_status` (app/models/review.py `shown_status`):
  - finding đang đóng (`fixed`, `accepted_risk`, `false_positive`) → **Đã đóng · <cách đóng của finding>**, dù yêu
    cầu này bị từ chối, đã rút hay đang chờ;
  - yêu cầu này đã đóng finding mà finding mở lại sau đó → **Mở lại**;
  - còn lại → trạng thái của yêu cầu.
  Khác với kết quả riêng của yêu cầu thì có dòng phụ "Yêu cầu này: Bị từ chối". Chi tiết ghi "Kết quả của yêu
  cầu" và "Finding hiện giờ". Bộ lọc Trạng thái lọc theo `shown_status` (SQL `_shown_status`), API trả `shown_status`,
  `shown_closed_as`.
- [x] Bước kiểm gặp finding đã đóng cách khác trước khi chạy: yêu cầu thành `cancelled` với lý do "Finding đã được
  đóng trước khi kiểm …: không cần nữa" (trước là `rejected`, dễ đọc thành bị từ chối); UI ghi "Không cần nữa".

### 4. Review Detail
- [ ] Summary, điểm, findings với code excerpt, nút Dispute

### 6. Thành viên (đánh giá theo người; người dùng 2026-10-27, ngoài spec gốc)
- [x] Khoảng thời gian 7/30/90 ngày hoặc tuỳ chọn; lọc team, dự án, repo; tìm tên; chỉ người có review / cần hỗ trợ
- [x] KPI: người có review, review (MR/commit/LOC), điểm TB theo LOC, tỉ lệ approve, critical, high (bấm mở Critical Board)
- [x] Biểu đồ điểm TB theo người (thấp nhất trên cùng) + kết luận cả nhóm
- [x] Bảng: review, điểm TB (+ thấp nhất), kết luận (thanh chia theo verdict), critical/high/khác, lỗi/100 LOC, xu hướng tuần, review gần nhất; dòng đỏ khi có critical hoặc điểm TB < 65
- [x] Trang Member Detail thêm thẻ **Đánh giá trong kỳ**: tóm tắt, kết luận, loại lỗi, theo tuần, theo repo

### 7. Bộ lọc thời gian dùng chung (người dùng 2026-11-09)
Thời gian chọn ở một màn (Tổng quan, Insights, Dự án, Team, Thành viên, Báo cáo thành viên, trang một thành viên, Tích hợp MCP, DPI, Minh chứng) là thời gian mọi màn khác mở ra; sửa ở đâu thì các màn sau dùng thời gian mới (`dashboards/web/lib/timeScope.ts`).
- [x] Lưu ở `localStorage` (`cp:time-scope`), các tab khác theo luôn; hết hạn sau 12 giờ không chọn lại — sang ngày làm việc mới mỗi màn về mặc định của nó. *Lý do: tuần/tháng cũ không bám lại người dùng hôm sau.*
- [x] Mỗi màn hiện thời gian chung theo cách của nó: tuần/tháng ở màn 7/30/90 ngày là "Tuỳ chọn" với các ngày đó; 7/30/90 ngày ở Tổng quan/Insights là "Khoảng ngày"; màn chỉ có tuần/tháng (Dự án, Team — API chưa nhận khoảng) hiện tuần (khoảng ≤ 7 ngày trong một tuần) hoặc tháng chứa ngày cuối, kèm dòng ghi chú; DPI/Minh chứng lấy tháng của ngày cuối. Chỉ thao tác chọn mới ghi đè thời gian chung; mở màn không đổi nó. Thời gian trên URL (link chia sẻ) khi vào trang trở thành thời gian chung.
- [x] Không áp dụng cho Critical Board, Đóng finding (hàng đợi việc — lọc ngầm sẽ giấu finding còn mở; link từ Tổng quan vẫn mang kỳ như trước), form Yêu cầu review và Chấm lại tiêu chí (là thao tác, không phải bộ lọc xem).

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
