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
- Quyền đúng như dashboard (09, 11): thành viên review việc của mình; lead review người trong team mình lead; admin mọi người. Tác giả chưa gắn người → cần lead/admin. `/review open`, `/sync` chỉ lead/admin.
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
- `range` chỉ dùng với `type=commit`; `type=mr` không `id` = MR gần nhất của người đó.
- Dạng cũ bên dưới vẫn dùng được.

| Lệnh | Ý nghĩa | Quyền |
|---|---|---|
| `/review mr <iid\|link> [repo]` | Review một MR (head hiện tại lấy từ GitLab) | tác giả / lead / admin |
| `/review commit <sha\|link> [repo]` | Review một commit | tác giả / lead / admin |
| `/review last [repo] [người]` | Commit gần nhất của một người | như trên |
| `/review lastmr [repo] [người]` | MR gần nhất của một người | như trên |
| `/review today [repo] [người...]` | Commit hôm nay (REPORT_TZ) của một/nhiều người, bỏ merge commit và commit đã có review | lead cho người khác |
| `/review open [repo]` | MR đang mở chưa có review, của người mình lead (admin: mọi người) | lead / admin |
| `/status` | 5 review gần nhất của bạn, số yêu cầu đang chờ | ai cũng dùng |
| `/critical [repo]` | Finding critical/high đang mở trong phạm vi bạn xem được | theo quyền xem |
| `/score [người] [week\|month]` | Chỉ số kỳ gần nhất (Quality Index, điểm TB, critical/high, fix rate) | theo quyền xem |
| `/repos`, `/sync [repo]`, `/whoami`, `/link <mã>`, `/unlink`, `/help` | tiện ích | `/sync` lead/admin |

Repo: đường dẫn GitLab hoặc hậu tố (`api`, `omicrm/api`). Người: `@username` GitLab, email, một phần tên, hoặc `me`. Viết tắt: `/mr 12`, `/commit abc`, `/today`, `/last`, `/open`.

Mục đã có review `done` cùng sha → bot trả kết quả ngay, không xếp hàng lại; `failed`/`skipped` → chạy lại (như `POST /reviews/{id}/rerun`).

## API
- Admin: `GET/POST /telegram/bots`, `PATCH/DELETE /telegram/bots/{id}`, `POST /telegram/bots/{id}/test` (getMe), `GET /telegram/bots/{id}/requests`.
- Cá nhân: `GET /me/telegram`, `POST /me/telegram/link-code`, `DELETE /me/telegram/{account_id}`.

## Vận hành
- Tạo bot với @BotFather; với nhóm tắt Group Privacy (`/setprivacy` → Disable) để bot đọc lệnh.
- Không cần mở cổng vào: bot poll ra ngoài (`https://api.telegram.org`, đổi bằng `TELEGRAM_API_BASE` nếu đi qua proxy).
- Đổi token: Admin → Telegram → Đổi token (offset update được đặt lại); thu hồi token cũ với @BotFather.
