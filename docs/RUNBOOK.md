# CodePulse – Runbook vận hành

Dành cho người vận hành (admin/DevOps). Mọi lệnh `codepulse …` chạy trong container api:
`docker compose exec api codepulse …`.

## 1. Cài đặt lần đầu

1. **Tài khoản bot GitLab** (docs/specs/11): tạo user riêng, ví dụ `codepulse-bot`, thêm vào các project cần đo với quyền *Reporter*.
   Tạo Personal Access Token với scope tối thiểu:
   - `read_api` – đủ để thu thập và review;
   - thêm `api` **chỉ khi** bật comment lên MR (`post_mr_comment`).
2. **OAuth app cho đăng nhập dashboard**: GitLab → Admin → Applications (hoặc Group → Applications),
   scopes `openid email profile read_user`, redirect URI `https://<dashboard>/api/v1/auth/callback`.
3. **Cấu hình**: `cp .env.example .env`, điền ít nhất:
   - `POSTGRES_PASSWORD`, `DATABASE_URL`
   - `APP_ENCRYPTION_KEY` (Fernet key – **giữ bản sao an toàn**, mất key = mất mọi token đã lưu)
   - `SESSION_SECRET` (≥ 32 ký tự), `ADMIN_EMAILS` (lần đăng nhập đầu tiên của các email này tự tạo user,
     nếu GitLab xác nhận email; những người khác phải được import – bước 6)
   - `APP_BASE_URL` = `DASHBOARD_URL` = URL https của dashboard (dashboard phục vụ cả `/api`)
   - `GITLAB_OAUTH_URL`, `GITLAB_OAUTH_CLIENT_ID`, `GITLAB_OAUTH_CLIENT_SECRET`
   - `LLM_PROVIDER=claude_code`, `CLAUDE_CODE_OAUTH_TOKEN` = token từ `claude setup-token` (gói Claude),
     hoặc `LLM_API_KEY` = API key Anthropic
   - `MONTHLY_LLM_BUDGET_USD`, `SMTP_URL`, `SMTP_FROM`, `REPORT_CHANNELS` / `SLACK_WEBHOOK_URL`
4. **HTTPS**: đặt reverse proxy (nginx, Traefik…) phía trước service `web` (cổng 3000), terminate TLS,
   chuyển tiếp `X-Forwarded-For`. Không mở cổng 8000 (api) ra Internet; chỉ Prometheus nội bộ cần nó.
5. Khởi động: `docker compose up -d --build` (api tự chạy migration).
6. Đăng ký nguồn: trên dashboard **Admin → Kết nối GitLab** (URL + token bot) và **Projects**
   (GitLab project ID, tự đăng ký webhook và sync; hoặc Group ID/đường dẫn để thêm một lần mọi project của
   group kể cả subgroup – project tạo sau cần thêm lại), hoặc bằng CLI:
   ```sh
   codepulse connections add --name gitlab --url https://gitlab.company.vn --token <PAT>
   codepulse projects add <gitlab_project_id>        # in ra id CodePulse của project
   codepulse projects register-webhook <id>
   codepulse identity import --teams teams.csv --users users.csv --accounts accounts.csv
   codepulse projects sync <id>                       # backfill BACKFILL_DAYS ngày
   codepulse identity unmapped                        # tài khoản chưa gắn người → gắn ở Admin
   ```
   Định dạng CSV: docs/specs/05. Vai trò `lead` / `manager` đặt ở cột `role` của `users.csv`.

## 2. Thêm project

```sh
codepulse projects add <gitlab_project_id>
codepulse projects register-webhook <id>
codepulse projects sync <id>
```
Bật/tắt AI review, comment lên MR, cho phép gửi code ra LLM: dashboard → Admin → Dự án & Repo (bấm vào ô dự án để xem repo của nó, hoặc chuyển sang **Danh sách** để thao tác hàng loạt).
Project có `allow_external_llm = false` **không được review** ở v1 (chưa có provider local).

## 3. Xoay token / secret

| Thứ cần xoay | Cách làm |
|---|---|
| Token GitLab của bot | Tạo token mới trên GitLab → `codepulse connections rotate <connection_id> --token <PAT mới>` → thu hồi token cũ trên GitLab. (`codepulse connections list` để xem id.) |
| Webhook secret | `codepulse connections rotate <connection_id> --webhook-secret` (không truyền giá trị = sinh ngẫu nhiên) → xoá hook cũ trên GitLab → `codepulse projects register-webhook <id>` cho từng project. |
| `SESSION_SECRET` | Đổi trong `.env`, `docker compose up -d api` – mọi người phải đăng nhập lại. |
| `APP_ENCRYPTION_KEY` | **Không đổi trực tiếp.** Giải mã mọi token bằng key cũ, mã hoá lại bằng key mới trong cùng một transaction, rồi mới đổi `.env`. |
| `CLAUDE_CODE_OAUTH_TOKEN` / `LLM_API_KEY` | Chạy lại `claude setup-token` (token hết hạn sau ~1 năm), đổi trong `.env`, `docker compose up -d worker`. |

## 4. Chạy lại review

- Review `failed` / `skipped` (ví dụ do vượt ngân sách, lỗi LLM): dashboard → Review → hoặc
  `POST /api/v1/reviews/<id>/rerun` (lead/admin).
- Review `done` là cố định cho sha + rubric đó; push mới hoặc đổi rubric sẽ tạo review mới.
- Review lại lịch sử (tốn ngân sách LLM): `POST /api/v1/projects/<id>/backfill-reviews?from=YYYY-MM-DD&to=YYYY-MM-DD` (admin).
- Review kẹt ở `running` > 30 phút (worker chết giữa chừng) sẽ được nhận lại ở lần chạy sau.

## 5. Tính lại metric

Sau khi duyệt khiếu nại, metric tuần/tháng liên quan được tính lại ngay. Khi đổi `SCORING_WEIGHTS`
hoặc sửa dữ liệu tay:
```sh
codepulse metrics recompute --from 2026-09-01 --to 2026-10-31
```
Lệnh này chấm lại điểm review từ finding hiện tại rồi tổng hợp lại mọi tuần/tháng giao với khoảng ngày.

## 6. Giám sát và cảnh báo

- Prometheus scrape `http://api:8000/metrics` với `Authorization: Bearer $METRICS_TOKEN`
  (mẫu: `deploy/prometheus/prometheus.yml`), luật cảnh báo: `deploy/prometheus/alerts.yml`.
- `/healthz` của api: 200 khi DB + Redis ổn; số worker còn sống nằm trong `checks.workers`.

| Cảnh báo | Kiểm tra | Xử lý |
|---|---|---|
| `CodePulseWorkerDown` | `docker compose ps worker`, `docker compose logs worker` | `docker compose restart worker`; nếu crash lặp lại, xem traceback (log JSON có `job_id`). |
| `CodePulseQueueBacklog` | `docker compose exec worker rq info --url $REDIS_URL` | Tăng worker: `docker compose up -d --scale worker=3`. |
| `CodePulseLlmErrors` | log `claude call failed` / review `failed` với lỗi `llm: …` | Kiểm tra `CLAUDE_CODE_OAUTH_TOKEN` / `LLM_API_KEY`, hạn mức gói Claude / tài khoản Anthropic, phiên bản CLI. Chạy lại review lỗi sau khi sửa. |
| `CodePulseBudget80` | Admin → Chi phí LLM | Báo người phụ trách; đến 100% review tạm dừng (`failed: monthly LLM budget reached`) và có thể chạy lại khi tăng ngân sách. |

## 7. Backup và khôi phục

- Service `backup` dump Postgres mỗi ngày vào `./backups` (giữ `BACKUP_KEEP_DAYS`, mặc định 14).
  **Sao chép thư mục này ra ngoài máy chủ** (object storage, máy khác).
- Khôi phục:
  ```sh
  docker compose stop api worker scheduler
  docker compose exec -T postgres pg_restore --clean --if-exists -U codepulse -d codepulse < backups/codepulse-<ngày>.dump
  docker compose start api worker scheduler
  ```
- Ngoài DB, cần giữ an toàn `.env` (đặc biệt `APP_ENCRYPTION_KEY`).

## 8. Đánh giá reviewer (eval)

Chạy khi đổi prompt, rubric, model hoặc nâng phiên bản Claude Code CLI (`CLAUDE_CODE_VERSION` trong Dockerfile):
```sh
make eval      # ~ $2 cho 20 case; ngưỡng precision ≥ 0.8, recall ≥ 0.7 cho critical+high
```

## 9. Lưu trữ dữ liệu

Job 03:00 hằng ngày xoá `code_excerpt` của finding và dữ liệu thô GitLab cũ hơn `RETENTION_DAYS`
(mặc định 365). Review, finding (không có trích đoạn code) và metric tổng hợp được giữ lại.

## 10. Bot Telegram

1. Trên Telegram, chat với **@BotFather**: `/newbot`, đặt tên, lấy token. Nếu dùng trong nhóm: `/setprivacy` → chọn bot → **Disable** (bot mới đọc được lệnh trong nhóm), rồi thêm bot vào nhóm.
2. Dashboard **Admin → Telegram → Thêm bot**: tên, token, dự án (và một repo nếu chỉ muốn bot phục vụ repo đó), tuỳ chọn danh sách chat id được phép. Token được kiểm tra bằng `getMe` rồi mã hoá trong DB.
3. Scheduler poll Telegram mỗi `TELEGRAM_POLL_INTERVAL_S` giây (mặc định 5). Cột **Poll gần nhất** và **Trạng thái** cho biết bot sống hay lỗi (`last_error`). Nút kiểm tra gọi `getMe` lại.
4. Mỗi thành viên: **Của tôi → Telegram → Tạo mã liên kết**, gửi `/link MÃ` cho bot (mã sống 15 phút). Người chưa liên kết chỉ nhận hướng dẫn; không có gì được ghi vào DB cho tới khi `/link` thành công. Tài khoản Telegram đã liên kết có thể gỡ ở trang Của tôi hoặc bằng `/unlink`.
5. Đổi token: Admin → Telegram → biểu tượng bút → dán token mới; thu hồi token cũ với @BotFather. Xoá bot chỉ xoá cấu hình và lịch sử yêu cầu; review đã chạy vẫn giữ.
6. Sự cố thường gặp: `HTTP 401 Unauthorized` = token sai/đã thu hồi; `HTTP 409 Conflict` = một tiến trình khác đang `getUpdates` cùng token (ví dụ chạy hai môi trường với cùng bot); không trả lời trong nhóm = Group Privacy còn bật hoặc chat id không nằm trong danh sách cho phép.
7. Lệnh và quyền: xem `docs/specs/15_TELEGRAM_BOT.md`. Mọi lệnh review được ghi audit log (`telegram.review`) và tốn ngân sách LLM như review thường.

## 11. Webhook thông báo kết quả review

Khác với webhook GitLab (GitLab gọi CodePulse khi có MR/commit mới), webhook thông báo là chiều ra: CodePulse POST kết quả review tới endpoint của bạn. Chi tiết payload: `docs/specs/16_NOTIFICATION_WEBHOOKS.md`.

1. Dashboard **Admin → Webhook thông báo → Thêm webhook**: tên, URL, định dạng (`JSON` cho service của bạn, `Slack`/`Teams` cho incoming webhook của kênh chat), phạm vi (mọi dự án / một dự án / một repo), sự kiện (`Review xong`, `Có critical/high`, `Review lỗi`), secret tuỳ chọn (≥16 ký tự) để ký HMAC-SHA256.
2. Bấm **Gửi thử**: CodePulse gửi sự kiện `ping` với payload mẫu và báo mã HTTP endpoint trả về.
3. Gửi thật chạy trong worker (job `notify-review-<id>`) ngay sau khi review được lưu; endpoint cần trả 2xx trong 10 giây. 5xx/429/lỗi mạng được thử lại 3 lần; sau 20 lần lỗi liên tiếp webhook tự tắt, cột **Gửi gần nhất** cho biết lý do; bấm **Bật lại** sau khi sửa endpoint.
4. Xoá kết nối GitLab: chỉ xoá được khi không còn repo nào dùng nó (gỡ hẳn repo ở Dự án & Repo trước); đổi tên kết nối bằng biểu tượng bút ở tab Kết nối GitLab.

## 12. Quy tắc review theo dự án và review theo yêu cầu

- **Quy tắc review**: Admin → Dự án & Repo → bấm ô dự án → mục *Quy tắc review*: markdown ngắn gọn, mỗi quy tắc một gạch đầu dòng dưới một tiêu đề (tiêu đề là thứ finding sẽ trích dẫn). Với repo đã có sẵn file quy tắc (ví dụ `.claude/rules/critical/*.md`), nhập đường dẫn vào *File quy tắc trong repo* của repo đó: CodePulse đọc file ở default branch (không đọc tại commit đang review, để tác giả không sửa được quy tắc trong cùng MR). Giới hạn: 12.000 ký tự mỗi khối, 10 file, 6.000 ký tự mỗi file, tổng 20.000 ký tự vào prompt (tăng chi phí token tương ứng).
- **Rubric v2**: review cũ (v1) giữ nguyên điểm và verdict đã backfill; review lại một sha cũ tạo review v2 mới. Chạy `make eval` sau khi đổi prompt/model (bộ case trong `tests/eval/cases`).
- **Verdict**: BLOCK khi có critical, FIX REQUIRED khi có high, APPROVE còn lại; risk = critical×10 + high×3 + medium×1. Duyệt khiếu nại "không phải lỗi" làm verdict tính lại ngay; `codepulse metrics recompute` tính lại hàng loạt.
- **Review theo yêu cầu** (menu *Yêu cầu review*): tối đa `REVIEW_BATCH_MAX` (50) commit mỗi lần, danh sách tối đa `REVIEW_COMMITS_LIST_MAX` (200). Commit trên branch chưa sync vẫn review được (CodePulse tạo dòng commit từ GitLab). Tốn ngân sách LLM như review tự động; hết ngân sách → review `failed`, bấm chạy lại khi sang tháng.
