# 15 – Telegram bot nhận lệnh review

> Ngoài spec gốc, theo yêu cầu người dùng (2026-10-24). Mục tiêu: techlead/dev ra lệnh review ngay trong Telegram, kết quả về cùng chat và vẫn lưu trong DB để tổng hợp theo tuần/tháng/dự án.

## Mô hình
- Mỗi **dự án** (`business_projects`) có một bot; tuỳ chọn ghim bot vào **một repo** của dự án (bot "sub project"). Ràng buộc duy nhất `(business_project_id, project_id)`.
- Token bot mã hoá at-rest (Fernet, như token GitLab), kiểm tra bằng `getMe` khi lưu, không bao giờ trả về API.
- Scheduler poll `getUpdates` mỗi `TELEGRAM_POLL_INTERVAL_S` (mặc định 5 s), khoá advisory theo bot (giữ trên một kết nối riêng trong suốt lượt poll) nên nhiều scheduler không xử lý trùng. Lỗi của một bot ghi vào `last_error`, không chặn bot khác. Tra cứu GitLab trong lệnh dùng client timeout 10 s, 1 lần thử lại; Telegram 429 với `retry_after` > 5 s không chờ mà để tick sau thử lại. Logger `httpx` ở mức WARNING và bộ lọc log che `bot<token>` nên token không bao giờ vào log.
- Lệnh review đi qua đúng pipeline worker (`review_mr_job` / `review_commit_job`): điểm, finding, chi phí vào `reviews`/`review_findings`, tính vào `metrics_user_period` / `metrics_team_period` như review từ webhook.
- `telegram_requests` lưu ai yêu cầu, chat nào, mục nào; khi mọi mục xong (hoặc quá 3 giờ) bot trả kết quả về chat và đánh dấu `notified`; Telegram từ chối gửi (4xx: bot bị kick, chat không còn) hoặc quá hạn → `failed` kèm `error`.

## Danh tính và quyền
- Telegram user là một `accounts` kind `telegram` (external_key = telegram user id), chỉ được tạo khi `/link` thành công (ai cũng nhắn được cho bot nên người lạ không được ghi vào DB). Người chưa gắn → bot hướng dẫn `/link MÃ`.
- Mã liên kết: `POST /me/telegram/link-code`, 6 ký tự, dùng một lần, sống 15 phút (Redis).
- **Quyền review (2026-11-02, chung với dashboard, `Principal.may_review_work`)**: mỗi người chỉ review được commit / MR của **chính mình**; **người quản lý dự án** (grant `manage` trên dự án của bot) và **admin** review được của mọi người, kể cả tác giả chưa gắn người. Lead / manager của team **không** còn quyền review người khác (vẫn xem được kết quả của người mình lead như trước). Quyền xem (`/score`, `/report`, `/critical`) không đổi. `/sync` vẫn cho lead / quản lý dự án / admin.
- Kiểm tra ở mọi lối vào: `user=` / tên người trong lệnh (thành viên gõ tên người khác → từ chối ngay, không tra cứu, không gọi LLM), `id=` (theo tác giả), danh sách nút (thành viên chỉ thấy việc của mình), mọi lần bấm nút (nút "của ai?" của người quản lý trong nhóm bị thành viên bấm → vẫn chỉ ra danh sách của thành viên đó), và lần cuối khi bấm **Review đã chọn**.
- Mỗi người tối đa `TELEGRAM_COMMANDS_PER_10MIN` lệnh review/sync trong 10 phút; mỗi lệnh `today`/`open` tối đa `TELEGRAM_MAX_TARGETS` mục (bảo vệ ngân sách LLM).
- `allowed_chat_ids` (tuỳ chọn): bot chỉ trả lời trong các chat đó.
- Audit: `telegram.bot.*` (admin), `telegram.link`, `telegram.unlink`, `telegram.link_code`, `telegram.review`, `telegram.sync`.

## Lệnh
Dạng ưu tiên (2026-10-26): các cặp `key=value`, không cần đúng thứ tự, chỉ `type` là bắt buộc.

```
/review type=mr|merge-request|commit [id=<iid|sha|link>] [repo=<gợi ý>] [user=<gợi ý>] [range=today|7d]
/review type=mr id=412 repo=global-api      # MR !412 của …cloud-saas-global-api
/review type=commit user=tamtd              # commit gần nhất của tamtd
/review type=commit user=tamtd range=7d     # commit 7 ngày qua của tamtd (tối đa TELEGRAM_MAX_TARGETS)
/review type=mr user=an                     # MR gần nhất của An; bỏ user = của bạn
```

- **Viết tắt**: `repo` khớp đường dẫn, hậu tố, chuỗi con, rồi theo token ở cuối tên (`global-api` → `vihat/cloud-saas-global-api`); `user` khớp `@username`, email, phần trước `@` của email (`tamtd`), một phần tên, hoặc tài khoản git/GitLab đã gắn. Còn nhiều kết quả → **LLM chọn** (`app/telegram/resolve.py`: một lời gọi nhỏ, không tool, timeout 20 s không thử lại để không nghẽn poller, chỉ gửi danh sách tên trong phạm vi bot và trong phạm vi người gửi được xem, cần độ tin cậy ≥ 0.6); không chọn được → bot liệt kê và hỏi lại. Không có LLM (provider lỗi) → luôn hỏi lại.
- `range` chỉ dùng với `type=commit`.
- **Review lại** (2026-11-01): `again=1` (mặc định không) cho phép lấy cả mục đã có review `done` cùng sha; mỗi mục thành một lần review mới (`force`, `expected_round` như nút ↻ của danh sách chọn), lần cũ giữ trong lịch sử. Dùng được với `id=`, `user=` (mục gần nhất), danh sách chọn (mở sẵn chế độ ↻) và `all=1`. Chỉ người quản lý dự án/admin (như nút ↻); mục đang `queued`/`running` không bao giờ bị xếp lại.
- Dạng cũ bên dưới vẫn dùng được.

### Chọn bằng nút (không cần mở GitLab)
- Luồng theo vai trò (2026-11-02), ít bước nhất có thể:
  - **Thành viên**: `/review` → **📝 Commit của tôi** / **🔀 MR của tôi** → (chọn repo nếu dự án có nhiều repo; repo có commit mới nhất của bạn đứng đầu) → danh sách **chỉ gồm việc của bạn**, commit lấy trên **mọi nhánh** (việc của mình thường nằm ở nhánh tính năng; `branch=` hoặc nút 🌿 để chỉ xem nhánh review) → tick → **Review đã chọn**. Không có bước "của ai?", không có nút 👤, không có dòng 🚫.
  - **Người quản lý dự án / admin**: `/review` → **Commit gần đây** / **MR đang mở** → repo (repo có commit mới nhất đứng đầu) → **của ai?**: 🙋 Của tôi, 👥 Mọi người, rồi từng người (người được cấp quyền dự án và người đã commit trong dự án 90 ngày qua, ai commit gần nhất trước; tối đa 12 nút, quá thì gõ `user=`) → danh sách trên nhánh review (nút 🌿 để xem mọi nhánh; nút 👤 để đổi người).
- `type=commit` không `id=` và không `user=` (hoặc có `range=`) → danh sách commit (thành viên: của mình trên mọi nhánh; quản lý: của mọi người trên nhánh review, `branch=` để đổi; GitLab trước, DB khi GitLab lỗi), tối đa 40 mục, 8 mục/trang, bỏ merge commit; mỗi mục ghi tiêu đề, tác giả, thời gian và trạng thái: ⬜ chọn được, ✅ đã review (kèm verdict/điểm), ⏳ đang review, ↻ lần trước lỗi, 🚫 không được review (chỉ còn là chốt chặn phòng thủ). `type=mr` không `id=` → danh sách MR đang mở tương tự. `user=` lọc danh sách theo người (thành viên: chỉ chính mình); `all=1` bỏ qua bước chọn và xếp hàng tất cả.
- Bàn phím inline: nút số để tick, **Chọn hết**, **Review đã chọn (n)**, **Trước/Sau**, **Huỷ**. Trạng thái chọn lưu Redis (`tg:pick:<token>`, 30 phút); chỉ người gọi lệnh bấm được (người khác được báo); hết hạn → báo gõ lại. Bấm **Review đã chọn** → kiểm tra quyền theo tác giả từng mục, enqueue như lệnh thường (`trigger=telegram`, `telegram_requests` để trả kết quả về chat), sửa tin nhắn thành danh sách đã xếp hàng và gỡ nút. Mọi lần bấm đều được `answerCallbackQuery` để tắt vòng xoay.
- Poller nhận thêm `callback_query` trong `getUpdates`; lỗi khi xử lý một lần bấm chỉ hiện thông báo cho người bấm, không chặn bot.

| Lệnh | Ý nghĩa | Quyền |
|---|---|---|
| `/review mr <iid\|link> [repo]` | Review một MR (head hiện tại lấy từ GitLab) | tác giả / quản lý dự án / admin |
| `/review commit <sha\|link> [repo]` | Review một commit | tác giả / quản lý dự án / admin |
| `/review last [repo] [người]` | Commit gần nhất của một người | như trên |
| `/review lastmr [repo] [người]` | MR gần nhất của một người | như trên |
| `/review today [repo] [người...]` | Commit hôm nay (REPORT_TZ) của một/nhiều người, bỏ merge commit và commit đã có review | quản lý dự án / admin cho người khác |
| `/review open [repo]` | MR đang mở chưa có review: của bạn (quản lý dự án / admin: mọi người) | ai cũng dùng |
| `/status` | 5 review gần nhất của bạn, số yêu cầu đang chờ | ai cũng dùng |
| `/critical [repo]` | Finding critical/high đang mở trong phạm vi bạn xem được | theo quyền xem |
| `/score [người] [week\|month]` | Chỉ số kỳ gần nhất (Quality Index, điểm TB, critical/high, fix rate) | theo quyền xem |
| `/report [user=…\|all] [time=…] [repo=…]` | Báo cáo review theo khoảng thời gian (xem dưới) | theo quyền xem chỉ số cá nhân |
| `/repos`, `/sync [repo]`, `/whoami` (vai trò và quyền review), `/link <mã>`, `/unlink`, `/help` | tiện ích | `/sync` lead / quản lý dự án / admin |

Repo: đường dẫn GitLab hoặc hậu tố (`api`, `omicrm/api`). Người: `@username` GitLab, email, một phần tên, hoặc `me`. Viết tắt: `/mr 12`, `/commit abc`, `/today`, `/last`, `/open`.

Mục đã có review `done` cùng sha → bot trả kết quả ngay, không xếp hàng lại; `failed`/`skipped` → chạy lại (như `POST /reviews/{id}/rerun`).

### Kết quả MR và file phân tích (2026-11-02)
- Chỉ áp dụng cho **merge request được yêu cầu review từ Telegram** (tin nhắn kết quả của `telegram_requests`); commit và review từ nguồn khác giữ dòng ngắn như cũ.
- Mỗi MR trong tin kết quả ghi: kết luận, điểm, **mức cao nhất** (🔴 Critical / 🟠 High / 🟡 Medium / 🔵 Low / ✅ không có lỗi), số finding **cả bốn mức** (kể cả 0) theo đúng cách đếm của kết luận (bỏ finding bị bác bỏ / báo nhầm; finding chưa kiểm chứng ghi riêng, không tính điểm), link **⬇ Tải kết quả phân tích** và link trang review. Cuối tin có chú giải bốn mức (một lần).
- File phân tích: một file HTML tự chứa (`app/telegram/mr_report.py`: CSS nội tuyến, không script, không gọi ra ngoài, sáng/tối theo máy) gồm thông tin MR, kết luận, điểm, mức cao nhất, bảng **các mức nghĩa là gì** (theo rubric), từng finding theo mức (file:dòng, nhóm, trạng thái kiểm chứng, mô tả, gợi ý sửa, đoạn diff đã lưu ≤ 40 dòng); finding không tính điểm xếp cuối.
- Link ký sẵn (người dùng chọn: mở trên điện thoại không cần đăng nhập): `<DASHBOARD_URL>/api/v1/review-reports/<review id>.<hết hạn>.<chữ ký>`, HMAC-SHA256 với khoá dẫn xuất từ `SESSION_SECRET` (không dùng được làm phiên đăng nhập), sống `TELEGRAM_REPORT_LINK_HOURS` (mặc định 24). **Ai có link đều tải được** cho tới khi hết hạn (chuyển tiếp tin nhắn là chuyển quyền tải); hết hạn → 410, sai chữ ký → 404, review không phải MR hoặc chưa `done` → 404. Phản hồi: `Content-Disposition: attachment`, `Cache-Control: no-store`, CSP `default-src 'none'`, `X-Robots-Tag: noindex`. Mỗi lần tải ghi audit `telegram.report_download`.

### `/report` (2026-11-01)
- `/report user=tamtd time=04-2026,10-2026`; mọi phần tuỳ chọn, không cần thứ tự, từ không có `=` được hiểu là thời gian nếu đọc được, không thì là người (`/report tamtd 04-2026`).
- Thời gian: một mốc hoặc hai mốc nối bằng `,` / `..` / `~` (từ đầu mốc đầu đến cuối mốc sau), hoặc `from=` / `to=`. Mốc: tháng `04-2026` `4/2026` `2026-04`; ngày `15-04-2026` `15/04/2026` `2026-04-15`; năm `2026`; quý `q2-2026`; `today`, `yesterday`, `week`, `lastweek`, `month`, `lastmonth`, `year`, `7d` (và tiếng Việt: hôm nay, tuần, tháng trước…). Bỏ trống = tháng này. Cuối khoảng cắt về hôm nay (REPORT_TZ); tối đa 366 ngày; khoảng ở tương lai bị từ chối.
- Nội dung (một người): số review MR/commit, dòng code, số repo, điểm TB có trọng số LOC, điểm thấp nhất, tỉ lệ APPROVE, verdict, finding theo mức (cách đếm như điểm), cờ "cần hỗ trợ", theo repo (≤ 6), nhóm lỗi hay gặp (≤ 5), xu hướng theo tuần (≤ 62 ngày) hoặc theo tháng, link trang Thành viên với cùng khoảng. Cùng số liệu với `GET /members/{id}/evaluation` (`app/scoring/evaluation.py`), nhưng ranh giới đúng ngày, không làm tròn theo tuần.
- `user=all`: tổng hợp và từng người (điểm thấp trước, ≤ 15 người) trong phạm vi người gửi được xem.
- Chỉ tính review thuộc dự án của bot (hoặc repo được ghim / `repo=`). Quyền như chỉ số cá nhân trên dashboard: bản thân, lead của người đó, admin. Xem người khác hoặc `all` ghi audit `telegram.report`.

## API
- Admin: `GET/POST /telegram/bots`, `PATCH/DELETE /telegram/bots/{id}`, `POST /telegram/bots/{id}/test` (getMe), `GET /telegram/bots/{id}/requests`.
- Cá nhân: `GET /me/telegram`, `POST /me/telegram/link-code`, `DELETE /me/telegram/{account_id}`.

## Vận hành
- Tạo bot với @BotFather; với nhóm tắt Group Privacy (`/setprivacy` → Disable) để bot đọc lệnh.
- Không cần mở cổng vào: bot poll ra ngoài (`https://api.telegram.org`, đổi bằng `TELEGRAM_API_BASE` nếu đi qua proxy).
- Đổi token: Admin → Telegram → Đổi token (offset update được đặt lại); thu hồi token cũ với @BotFather.
