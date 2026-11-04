# 24 – Thông báo trong ứng dụng (chuông, toast, lịch sử)

> Ngoài spec gốc, theo yêu cầu người dùng (2026-11-04): "bổ sung chức năng socket để bắn thông báo đi, các sự kiện sau cần có thông báo (chuông + toast): review xong, đóng finding, giải quyết khiếu nại, có khiếu nại mới", rồi "thông báo + lịch sử nhé". Các mục **[Quyết định]** do agent chọn theo uỷ quyền của người dùng.

Khác spec 16 (webhook gửi ra ngoài: Slack, Teams, hệ thống khác, cấu hình theo dự án): đây là thông báo **cho từng người trong dashboard**.

## 1. Sự kiện và người nhận
| Sự kiện | `kind` | Người nhận | Liên kết |
|---|---|---|---|
| Review xong | `review_done` | Tác giả thay đổi; người yêu cầu review (nếu có). | `/reviews/<id>` |
| Review lỗi | `review_failed` | Chỉ người yêu cầu review. | `/reviews/<id>` |
| Đóng finding | `finding_closed` | Tác giả thay đổi có finding; người gửi yêu cầu đóng nếu là người khác. | `/reviews/<id>#finding-<id>` |
| Khiếu nại mới | `dispute_new` | Người **được duyệt** khiếu nại đó (`dispute.decide` trên công việc của người gửi trong dự án của repo, như trang Duyệt khiếu nại): lead của team, người có bộ quyền / quyền dự án tương ứng, admin. | `/disputes` |
| Khiếu nại đã xử lý | `dispute_decided` | Người gửi khiếu nại. | finding trong trang review |

- Không bao giờ báo cho chính người làm hành động (`actor_id`), không báo cho tài khoản đã khoá.
- **[Quyết định]** Review do **backfill** hoặc **chấm lại theo tiêu chí** (spec 22 §10) chạy thì **không báo cho ai** (một lần backfill có thể là hàng trăm review, cho cả tác giả lẫn người bấm).
- Review lỗi giữa chừng (crash, hết thời gian) và yêu cầu không chạy được (repo bỏ theo dõi, không tìm thấy MR / commit…) cũng báo cho người yêu cầu, kèm lý do, khi RQ không thử lại nữa.
- "Đóng finding" gồm mọi đường đóng: yêu cầu đóng được kiểm là đã sửa, lead xác nhận đề xuất (chấp nhận rủi ro / finding sai), admin đóng tay, **tự đóng** khi review một thay đổi sau thấy lỗi đã sửa, và push mới của MR không còn báo finding cũ. Tự đóng gộp **một thông báo cho mỗi tác giả và mỗi review của họ** ("3 finding đã tự đóng nhờ api !12"), liên kết tới review của chính họ (review của thay đổi sửa lỗi có thể là của người khác, họ không xem được).
- Nội dung: tiêu đề ngắn ("Review xong: api !12"), một dòng mô tả (điểm, số critical / high, kết luận merge; tên finding; lý do khiếu nại), sắc thái (`good` / `warn` / `bad` / `info`) để tô màu.

## 2. Lưu trữ (lịch sử)
- Bảng `user_notifications` (migration `e5a7c9b1d3f5`): `user_id`, `kind`, `title`, `body`, `link`, `tone`, `actor_id`, `ref` (`review:12`, `finding:40`, `dispute:7`), `read_at`, `created_at`.
- Dòng được ghi **trong cùng transaction với sự kiện** (`app/notifications/inbox.py`): sự kiện bị rollback thì không có thông báo.
- **[Quyết định]** Giữ **180 ngày** (`INBOX_KEEP_DAYS`), xoá trong job dọn dữ liệu hằng ngày (`app/retention.py`).

## 3. Đẩy realtime
- **[Quyết định]** Dùng **Server-Sent Events** (`GET /api/v1/inbox/stream`), không dùng WebSocket:
  - dashboard chuyển `/api` sang API bằng rewrite HTTP của Next, đường này giữ được một response dài nhưng không nâng cấp WebSocket;
  - thông báo chỉ đi một chiều (máy chủ → trình duyệt);
  - `EventSource` tự nối lại, gửi `Last-Event-ID`; máy chủ gửi bù tối đa 50 thông báo bị lỡ. Sự kiện `ready` mang id mới nhất của người đó, nên mọi lần nối lại đều có mốc (kể cả khi chưa nhận thông báo nào); lần nối lại do dashboard tự làm (sau lỗi) gửi `?after=<id>`.
  - Publish dùng client Redis riêng có timeout 2 giây (không chặn request khi Redis treo). Dòng thêm trong SAVEPOINT bị rollback không được publish.
- Sau khi transaction của sự kiện commit, các dòng mới được publish lên Redis (kênh `codepulse:notifications`). Mỗi tiến trình API giữ **một** subscription và chuyển tới các tab đang mở của đúng người nhận. Worker (review, đóng finding) và API đều publish được.
- Mỗi stream sống tối đa 10 phút rồi trình duyệt nối lại (kiểm tra đăng nhập lại), ping mỗi 15 giây để proxy không cắt. Stream không giữ kết nối DB (xác thực bằng session ngắn riêng).
- Redis lỗi chỉ mất phần đẩy tức thời: dữ liệu vẫn ở DB; số chưa đọc được đọc lại khi nối lại và mỗi 3 phút.

## 4. API
- `GET /inbox?limit=&before=&unread=&kind=`: mới nhất trước, phân trang bằng `before` (id); trả `items`, `unread`, `next_before`.
- `GET /inbox/unread-count`.
- `POST /inbox/read` `{ids?}`: đánh dấu đã đọc một số hoặc tất cả; chỉ chạm dòng của chính mình.
- Không cần quyền riêng: ai đăng nhập cũng có hộp thư của mình và chỉ thấy của mình.

## 5. Giao diện
- **Chuông** ở chân thanh bên (máy tính) và thanh trên (điện thoại), có số chưa đọc (99+). Bấm mở bảng 15 thông báo gần nhất: chưa đọc nền xanh nhạt và chấm xanh; "Đánh dấu đã đọc tất cả"; bấm một dòng thì mở liên kết và đánh dấu đã đọc; "Xem tất cả thông báo" sang trang lịch sử. Esc hoặc bấm ra ngoài để đóng.
- **Toast** góc trên phải khi có thông báo mới: loại, tiêu đề, mô tả, viền trái theo sắc thái; tự ẩn sau 6 giây (dừng khi rê chuột / focus), tối đa 3 cái; bấm để mở.
- Thông báo đến thì các danh sách liên quan đang mở được tải lại (Khiếu nại, Đóng finding, Kết quả review, hoạt động review).
- **Trang Thông báo** (`/notifications`): lịch sử, lọc Tất cả / Chưa đọc và theo loại, "Tải thêm", "Đánh dấu đã đọc tất cả"; thông báo mới đến khi đang mở trang hiện ngay trên đầu.
