# 11 – Security & Privacy

## Bảo mật kỹ thuật
- [x] Token GitLab & webhook secret mã hoá at-rest (Fernet), không log ra – `app/crypto.py`; lỗi/log/API không bao giờ chứa token
- [x] Webhook: xác thực `X-Gitlab-Token`, giới hạn IP nguồn (tuỳ chọn), chống replay bằng Event UUID – constant-time, `WEBHOOK_ALLOWED_IPS`, Redis SET NX 7 ngày
- [x] Token GitLab dùng tài khoản bot riêng, scope tối thiểu (`read_api`; `api` chỉ khi đăng comment) – hướng dẫn trong `docs/RUNBOOK.md`
- [x] HTTPS bắt buộc; CORS chỉ domain dashboard – TLS ở reverse proxy, HSTS + cookie Secure khi APP_BASE_URL là https, CORS = DASHBOARD_URL
- [x] Dependency scan (`pip-audit`) trong CI – và `npm audit` cho dashboard

## Dữ liệu gửi cho LLM
- [x] `[DECISION]` Được phép gửi code ra LLM bên ngoài không? Nếu có: dùng gói API cam kết không train trên dữ liệu → Có: Claude Code CLI, code được gửi tới Anthropic
- [x] Cờ `projects.allow_external_llm=false` → bắt buộc dùng provider local – v1 chưa có provider local: project này **không được review** (status `skipped`), quyết định người dùng 2026-10-22
- [x] Trước khi gửi: chạy Gitleaks/regex để **che secret** trong diff (thay bằng `***REDACTED***`) – regex trong `app/review/prepare.py`
- [x] Không gửi file trong `SENSITIVE_PATHS` (vd: `**/secrets/**`, `.env*`, `*.pem`)

## Quyền riêng tư & công bằng (quan trọng khi đánh giá người)
- [x] Thông báo minh bạch cho team: tool đo gì, dùng vào việc gì – `docs/USER_GUIDE.md` (lead gửi cho team trước pilot)
- [x] Developer xem được toàn bộ dữ liệu về mình + quyền dispute
- [x] Không công khai bảng xếp hạng cá nhân cho toàn team (chỉ lead) – `[DECISION]` → đã chốt: chỉ lead
- [x] Điểm là **tín hiệu coaching**, không phải căn cứ duy nhất cho đánh giá hiệu suất (ghi rõ trên dashboard) – chân mọi trang, báo cáo và comment MR
- [x] Data retention: xoá `code_excerpt` sau `RETENTION_DAYS` (mặc định 365), giữ metric tổng hợp – job hằng ngày 03:00, xoá cả raw_gitlab cũ
- [x] Audit log truy cập dữ liệu cá nhân – bảng `audit_log`
