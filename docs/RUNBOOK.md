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
make eval      # ~ $2 cho 23 case (3 case fp-* đo precision khi bằng chứng nằm ở dòng ngữ cảnh); ngưỡng precision ≥ 0.8, recall ≥ 0.7 cho critical+high
```

Eval tự sinh theo dự án (spec 19 §5): mỗi invariant của tri thức review đang hoạt động được sinh một diff vi phạm và một diff sạch (cache ở `tests/eval/generated/<project>/<invariant>-<sha8>/`, chỉ sinh lại khi statement đổi), rồi review với bộ rule trong prompt. Báo cáo recall theo invariant và precision trên critical+high ghi vào `tests/eval/results/knowledge-<project>-<thời điểm>.json`:
```sh
make eval-knowledge PROJECT=7          # gọi LLM thật: sinh case (lần đầu) + review
make eval-knowledge PROJECT=7 FAKE=1   # kiểm tra harness, không tốn tiền
python -m app.review.eval --project 7 --knowledge knowledge.json   # tri thức từ file thay vì DB
```
Chạy lại khi đổi rubric, prompt, model hoặc tri thức review; xoá thư mục case trong `tests/eval/generated/` để sinh lại.

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

## 13. Review sâu (hồ sơ dự án + đối chứng trong repo)

- Bật mặc định khi `LLM_PROVIDER=claude_code`. Worker cần đủ đĩa tạm cho bản sao repo (tối đa `DEEP_REVIEW_MAX_ARCHIVE_MB`, mặc định 300 MB, giải nén có thể gấp vài lần) và CLI `claude` đã đăng nhập như lượt nhanh.
- Chi phí: mỗi review thêm một lượt gọi có tool (bị chặn bởi `DEEP_REVIEW_MAX_TURNS` và `DEEP_REVIEW_TIMEOUT_S`) và, mỗi 7 ngày một lần cho mỗi repo, một lượt gọi dựng hồ sơ. Theo dõi ở Admin → LLM & Chi phí; hết ngân sách tháng thì lượt sâu tự bỏ, lượt nhanh vẫn chạy.
- Tắt: cờ **Sâu** ở Admin → Dự án & Repo (theo repo) hoặc `DEEP_REVIEW=false` (toàn hệ thống). `DEEP_REVIEW_MIN_LOC=50` để chỉ review sâu các diff đáng kể.
- Hồ sơ dự án: xem ở trang dự án; sai hoặc cũ → Admin → ngăn chi tiết dự án → **Làm mới hồ sơ** (job `profile-<id>`). Hồ sơ dựng từ tài liệu của repo; repo không có README/CLAUDE.md sẽ có hồ sơ mỏng (`confidence` thấp) — thêm tài liệu vào repo là cách cải thiện đúng nhất.
- Sự cố: `reviews.deep.error = checkout` (archive quá lớn / GitLab lỗi), `LLMError` (CLI timeout, lỗi API), `ValueError` (output không đúng schema). Review vẫn `done` với kết quả lượt nhanh; xem cột *mode* để biết.

## 14. Đo trước/sau khi đổi luồng review (spec 19 §5)

Lấy số liệu trên DB thật trước khi đổi luồng (mốc), rồi so lại sau 2 tuần pilot. Hai lệnh:
```sh
codepulse review-metrics --since 2026-10-01 --until 2026-10-15 --out before.json        # --project <id> để giới hạn một repo; --md in bảng Markdown
codepulse review-metrics-compare before.json after.json --md                           # delta từng metric, theo nhóm mode/rubric
```
Review được gom theo `mode/rubric_version` (ví dụ `fast/v2`, `deep/v2`, `agent/v3`), chỉ tính review `done`/`superseded` có `finished_at` trong khoảng (`--until` loại trừ; bỏ trống = đến hết hôm nay). Ý nghĩa từng metric:

- `cost_usd`, `duration_s`, `loc_reviewed`: p50/p95 chi phí một review, thời gian chạy (`finished_at − started_at`), số dòng đã review.
- `turns`: p50/p95 số lượt tool của lượt có tool (`deep.turns`); nhóm `fast` không có.
- `ignored_verifications.sum` / `.share_gt0`: tổng số kiểm chứng bị bỏ qua và tỉ lệ review có ít nhất một (`deep.ignored_verifications`).
- `findings_per_review.<severity>`: số finding trung bình mỗi review theo severity; `verified.<nhãn>`: phân phối `confirmed/downgraded/rejected/unverified/skipped` (trống nếu DB chưa có cột).
- `dispute_acceptance.<severity>.rate`: tỉ lệ khiếu nại được chấp nhận trên số khiếu nại đã quyết (cao = reviewer nhiễu ở severity đó).
- `drift.mean_abs_score_diff`, `drift.verdict_change_rate`: cùng sha review ≥ 2 vòng, lệch điểm tuyệt đối trung bình và tỉ lệ đổi verdict giữa hai vòng liên tiếp (đo độ ổn định; nhóm là của vòng sau).

Quy trình A/B: chọn 30 commit/MR tiêu biểu (đủ repo, đủ cỡ diff). Chạy luồng cũ (mode `fast`/`deep`) rồi chạy v2 trên **cùng sha** bằng nút *Review lại* (force) ở dashboard; mỗi lần chạy là một vòng mới nên `drift` đo thẳng được chênh lệch cũ–mới. Lấy `review-metrics` cho khoảng ngày chạy luồng cũ (`before.json`) và khoảng chạy v2 (`after.json`), so bằng `review-metrics-compare`, rồi chỉnh trần (`REVIEW_AGENT_MIN_LOC` — sửa được ở Admin › Cài đặt review, số lượt, ngân sách) theo kết quả; eval chạy lại như mục 8.

## 15. Chạy command `.claude` từ dashboard (Admin › Chạy command)

Service `command-worker` (`python -m app.workers commands`) chạy riêng với worker review. Nó và `api` mount chung volume `command-runs` tại `COMMAND_RUNS_DIR` (mặc định `/tmp/codepulse-runs`): worker ghi file, api gửi file cho người tải. Image cần `git` (đã thêm vào Dockerfile).

Mỗi lần chạy: clone nhánh đã chọn (`COMMAND_CLONE_DEPTH` commit, 0 = toàn bộ; token GitLab đi qua header của git, không nằm trong URL hay `.git/config`), chép repo `.claude` dùng chung của dự án vào `.claude/` của repo (file của repo được giữ khi trùng), xoá file khớp `SENSITIVE_PATHS`, bỏ mọi symlink (git clone với `core.symlinks=false`), chỉ giữ `permissions` và `model` trong `.claude/settings*.json` của repo (bỏ `env`, `apiKeyHelper`… để token Claude không bị chuyển hướng), bỏ `.git` trừ khi `allowed-tools` của command cho chạy git (lịch sử git còn chứa file nhạy cảm đã xoá), rồi chạy `claude -p "/<command> <tham số>"` trong thư mục đó. Claude Code tự mở rộng command như ở máy local (`$ARGUMENTS`, `allowed-tools`, `model`, CLAUDE.md, rules, skills). Khác local: chế độ quyền `acceptEdits` (Bash/web chỉ khi `allowed-tools` của command hoặc `.claude/settings.json` cho phép), hooks tắt, không MCP, có trần thời gian/lượt/chi phí. Command dùng chung chạy được không cần repo: khi đó thư mục làm việc là bản clone của repo `.claude` dùng chung.

File trả về = mọi file trong `CODEPULSE_OUTPUT_DIR` + file command tạo/sửa trong bản clone (dưới `workspace/`), tối đa `COMMAND_MAX_OUTPUT_MB`. Bản clone xoá ngay khi chạy xong; file xoá sau lần tải đầu tiên, hoặc sau `COMMAND_FILE_TTL_HOURS` (scheduler xếp job dọn mỗi giờ vào hàng đợi `commands`; job này cũng đánh `failed` lần chạy mà worker chết giữa chừng). DB chỉ giữ ai chạy gì, sha, trạng thái, chi phí, tên file. Chi phí tính vào `MONTHLY_LLM_BUDGET_USD`; hết ngân sách thì lần chạy bị từ chối. Các giới hạn sửa ở tab Chạy command (ghi đè `.env`).

**Container riêng mỗi lần chạy:** worker không bao giờ tự chạy `claude` và cũng không gọi Docker. Nó chuẩn bị bản clone trong thư mục tạm riêng (không nằm trên volume chung), rồi nhờ **`command-gate`** (`app/commands/gate.py`) tạo container. `command-gate` là service duy nhất giữ `/var/run/docker.sock`, chạy `network_mode: none`, và chỉ nhận lệnh qua unix socket trên volume `command-gate`, volume mà ngoài nó chỉ `command-worker` mount.

Gate tự dựng cấu hình container, không nhận cấu hình từ bên gọi:
- image `codepulse-app:latest`, lệnh `python -m app.commands.inside`, user `codepulse`;
- env chỉ gồm token Claude và `CODEPULSE_OUTPUT_DIR`;
- không mount, `CapDrop ALL`, `no-new-privileges`, không privileged;
- giới hạn RAM (`COMMAND_MEMORY_MB`), CPU (`COMMAND_CPUS`), 512 tiến trình;
- mạng `codepulse-commands`.

Gate chỉ thao tác (đẩy tar, start, log, wait, kill, lấy file kết quả, xoá) trên container mang nhãn `codepulse.command_run` của nó.

Trong lúc chạy, worker theo dõi container: bấm Huỷ, quá hạn, hoặc ghi quá `COMMAND_DISK_MB` (mặc định 5 GB) thì container bị kill. Sau đó worker lấy ra đúng một file thường trong giới hạn dung lượng (đọc theo luồng), đặt tên do worker chọn, rồi xoá container cùng mọi tiến trình command để lại. Job dọn hằng giờ xoá luôn container của lần chạy đã kết thúc mà worker chết giữa chừng bỏ lại.

**Giới hạn đĩa thật (bắt buộc):** đo dung lượng định kỳ không chặn được việc tạo hàng triệu file rỗng (cạn inode) hay `fallocate` cả ổ trong vài giây. Hai việc đó làm đầy ổ của máy chủ, kéo theo PostgreSQL. Vì vậy gate đặt `StorageOpt size=COMMAND_DISK_MB` cho mỗi container. Docker chỉ hỗ trợ việc này khi data root (`/var/lib/docker`) dùng overlay2 trên **XFS mount với `pquota`**. Cách làm trên VPS: gắn một ổ hoặc phân vùng riêng, `mkfs.xfs`, mount vào `/var/lib/docker` với tuỳ chọn `pquota` (thêm vào `/etc/fstab`), rồi khởi động lại Docker. Lúc khởi động, gate thử tạo một container có quota. Nếu Docker từ chối, gate **từ chối mọi lần chạy** và lý do hiện ở lần chạy trên dashboard. `COMMAND_ALLOW_NO_DISK_QUOTA=1` (đặt cho service `command-gate`) tắt yêu cầu này, chỉ nên dùng khi data root của Docker đã nằm trên một filesystem riêng, tách khỏi OS, PostgreSQL và Redis. Quota XFS chỉ giới hạn dung lượng, không giới hạn số inode, nên kể cả khi có quota vẫn nên đặt `/var/lib/docker` trên ổ riêng, tách khỏi OS, PostgreSQL và Redis: một command tạo hàng chục triệu file rỗng khi đó chỉ ảnh hưởng tới Docker.

**Mạng `codepulse-commands`:** gate tạo mạng này khi khởi động, kiểu bridge với interface `cp-commands`, tắt liên lạc giữa các container. Container ra internet được (tới Anthropic). Để chặn nó vào mạng nội bộ và vào chính máy chủ, chạy một lần trên host và lưu lại bằng `iptables-save`, `netfilter-persistent` hoặc tương đương:

```sh
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10; do
  iptables -I DOCKER-USER -i cp-commands -d "$net" -j DROP
done
iptables -I INPUT -i cp-commands -j DROP   # PostgreSQL/Redis/dashboard trên chính host
```

Nếu DNS của host nằm trong dải nội bộ, thêm `iptables -I DOCKER-USER -i cp-commands -d <dns> -p udp --dport 53 -j ACCEPT` trước các luật DROP. Ngoài ra Redis và PostgreSQL vẫn phải có mật khẩu; `startup_check` cảnh báo khi URL thiếu mật khẩu. Lúc khởi động, gate chạy một container thăm dò thử mở kết nối tới gateway của mạng (chính máy chủ) ở các cổng 22, 80, 443, 3000, 5432, 6379, 8000. Kết nối được cổng nào, kể cả khi bị "connection refused" (vì như vậy là máy chủ có trả lời, tức là thiếu luật DROP), thì gate từ chối mọi lần chạy cho tới khi áp luật iptables rồi khởi động lại gate (`docker compose restart command-gate`; gate tự kiểm tra lại mỗi giờ và sau mỗi lần khởi động; thăm dò không chạy được hoặc không thấy gateway cũng bị coi là chưa cô lập). `COMMAND_ALLOW_OPEN_NETWORK=1` tắt kiểm tra này.

Lưu ý vận hành:
- Image `codepulse-app:latest` phải có sẵn trên máy (compose build nó). `.dockerignore` là allowlist (`app/`, `migrations/`, `alembic.ini`, `pyproject.toml`), nên không có file lạ nào lọt vào image mà command chạy.
- Token Claude vẫn nằm trong container, nên command có Bash có thể gửi nó ra ngoài. Theo lựa chọn hiện tại, rủi ro này được chấp nhận.
- Chi phí do CLI trong container báo về có thể bị chính command giả mạo; trần `COMMAND_MAX_BUDGET_USD` vẫn giới hạn chi tiêu thật.
- `COMMAND_SANDBOX=local` (chỉ dùng ở máy dev hoặc trong test) cho worker tự chạy CLI; `startup_check` ghi lỗi khi thấy giá trị này.

**Tắt và gỡ bỏ:**
- Tắt nhanh, không đổi code: đặt `COMMAND_RUNS_ENABLED=false` trong `.env.local` rồi `docker compose up -d api command-worker`. Tab biến mất, mọi route `/admin/command(-run)s` trả 404, lần chạy còn đang chờ bị đánh dấu thất bại khi worker lấy ra. Lần đang chạy dở thì huỷ trên dashboard trước khi tắt, hoặc chờ nó xong. Bật lại: `true` và restart như trên.
- Gỡ hẳn (revert commit `feat(commands)`), theo đúng thứ tự:
  1. Khi code cũ còn chạy: `docker compose run --rm api alembic downgrade a7c3e9d1f2b4` (xoá bảng `command_runs`, tức lịch sử chạy command; dữ liệu review không bị đụng). Nếu đã có migration mới chồng lên thì không downgrade được nữa: viết migration mới xoá bảng.
  2. Deploy code đã revert, `docker compose up -d --remove-orphans` (gỡ `command-gate`, `command-worker`).
  3. `docker volume rm <project>_command-runs <project>_command-gate`, `docker network rm codepulse-commands`, xoá container sót (`docker ps -aq --filter label=codepulse.command_run | xargs -r docker rm -f`), gỡ các luật iptables của `cp-commands` ở trên.
