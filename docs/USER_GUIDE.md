# CodePulse – Hướng dẫn sử dụng

> Lead gửi phần 1 cho cả team **trước khi bắt đầu pilot** (docs/specs/11: thông báo minh bạch).

## 1. CodePulse đo gì và dùng vào việc gì

**Đo gì**
- Mỗi merge request (và mỗi commit đẩy thẳng lên nhánh chính) được AI review theo một rubric cố định:
  bảo mật, lỗi logic, hiệu năng, khả năng bảo trì, kiểm thử, convention, tài liệu.
- Mỗi vấn đề (finding) có mức độ `critical / high / medium / low / info`. Điểm của một review (0–100)
  tính bằng công thức cố định từ các finding và kích thước thay đổi – **không phải** điểm do AI tự cho.
  Có ít nhất một lỗi critical thì điểm review tối đa 50.
- Kèm các chỉ số quy trình: thời gian chờ review, kích thước MR, số vòng review, tỉ lệ build thành công,
  tỉ lệ lỗi nghiêm trọng đã được sửa ở lần review sau.

**Gửi gì ra ngoài**
- Diff của thay đổi được gửi tới Anthropic (Claude) để review; secret trong code được che trước khi gửi;
  các file nhạy cảm (`.env*`, `*.pem`, `**/secrets/**`…) không bao giờ được gửi. Project nào không cho phép
  gửi code ra ngoài sẽ không được review.
- CodePulse chỉ lưu tối đa 40 dòng trích đoạn quanh mỗi finding, và xoá trích đoạn sau 365 ngày.

**Dùng vào việc gì**
- Là **tín hiệu coaching**: giúp lead thấy chỗ cần hỗ trợ, giúp team chọn chủ đề training.
- **Không** là căn cứ duy nhất để đánh giá hiệu suất, không tự động xếp hạng lương thưởng.

**Ai xem được gì**
| Bạn là | Bạn xem được |
|---|---|
| Developer | Toàn bộ dữ liệu của chính bạn; số liệu tổng hợp (ẩn danh) của team |
| Lead | Dữ liệu từng thành viên trong team mình |
| Manager | Số liệu tổng hợp của các team trực thuộc – không xem điểm từng người |
| Admin | Cấu hình hệ thống |

Mọi lần xem dữ liệu cá nhân của người khác đều được ghi vào nhật ký kiểm tra (audit log).

**Khi AI sai**: bạn khiếu nại (dispute) finding đó; lead của bạn duyệt. Nếu được chấp nhận, finding không còn
tính vào điểm và mọi chỉ số liên quan được tính lại ngay.

## 2. Developer

- Đăng nhập dashboard bằng tài khoản GitLab → trang **Của tôi**: điểm và Quality Index 12 tuần, loại lỗi theo
  tuần, MR/commit gần đây, finding đang mở.
- Trên MR (nếu project bật): một comment tổng hợp của CodePulse, được cập nhật mỗi lần bạn push.
- **Khiếu nại**: mở review → finding → *Khiếu nại* → ghi lý do. Theo dõi ở trang *Khiếu nại*.
- Mỗi thứ Hai bạn nhận email báo cáo cá nhân (chỉ dữ liệu của bạn).

## 3. Lead

- **Tổng quan**: mọi dự án trong một màn hình: số review, điểm trung bình, critical/high mới và đang mở, so với kỳ trước; bấm vào một dự án để xem theo repo, theo thời gian và loại lỗi.

- **Team**: chỉ số tuần/tháng so với kỳ trước, finding theo mức độ, loại lỗi và file "nóng",
  bảng thành viên với xu hướng 8 tuần. Quality Index chỉ hiện khi một người có từ 3 review trong kỳ.
- **Critical Board**: mọi finding critical/high còn mở, lọc theo project/người, cũ nhất trước.
- **Duyệt khiếu nại**: chấp nhận hoặc từ chối; bạn không thể tự duyệt khiếu nại của mình.
- Chạy lại review bị lỗi/bị bỏ qua từ trang review.
- Báo cáo tuần (thứ Hai 08:00) và tháng (ngày 1) qua email; kênh chat của team nhận bản không có điểm từng người.

## 4. Bot Telegram (nếu dự án của bạn có)

- Liên kết một lần: **Của tôi → Telegram → Tạo mã liên kết**, gửi `/link MÃ` cho bot. Gõ `/whoami` để kiểm tra.
- Ra lệnh trong chat riêng hoặc nhóm team:
  - `/review mr 123 api` – review MR !123 của repo api (dán link MR cũng được).
  - `/review commit a1b2c3d` – review một commit.
  - `/review last` / `/review lastmr` – commit / MR gần nhất của bạn.
  - Lead: `/review today an@company.vn binh@company.vn` (commit hôm nay của các bạn đó), `/review open` (MR đang mở chưa review), `/review last @username`.
  - `/status`, `/critical`, `/score` – xem nhanh review gần đây, lỗi nghiêm trọng đang mở, điểm kỳ gần nhất.
- Khi review xong, bot trả về cùng chat: điểm, số finding theo mức độ và link xem chi tiết trên dashboard.
- Quyền giống dashboard: bạn chỉ review được việc của mình; lead review được người trong team mình.

## 5. Đọc các chỉ số

| Chỉ số | Ý nghĩa |
|---|---|
| Điểm review | 100 trừ phần phạt theo mức độ × độ tin cậy, chia theo kích thước MR (mỗi 200 dòng) |
| Điểm TB | Trung bình có trọng số theo số dòng đã review |
| Mật độ lỗi | Phần phạt trên mỗi 100 dòng thay đổi |
| Fix rate | Tỉ lệ lỗi critical/high biến mất ở lần review sau của cùng MR |
| Quality Index | 50% điểm TB + 20% (mật độ lỗi thấp) + 15% build thành công + 15% fix rate; phần không có dữ liệu được bỏ qua |

Câu hỏi về cách tính hoặc dữ liệu của bạn: liên hệ admin CodePulse của công ty.
