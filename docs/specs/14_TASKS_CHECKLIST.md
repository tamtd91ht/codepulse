# 14 – Master Task Checklist

> Agent tick `[x]` khi xong + ghi `PROGRESS.md`. Mỗi phase kết thúc bằng **Demo checkpoint** – dừng chờ người dùng xác nhận.

## Phase 0 – Chốt quyết định `[DECISION]`
- [x] LLM provider & có được gửi code ra ngoài không (01, 11) → Claude Code CLI (`claude -p`) chạy trên worker; code được gửi tới Anthropic
- [x] Review theo MR / cả commit trực tiếp (01) → cả MR và commit push thẳng lên `main`
- [x] Dashboard: Grafana hay web app (10) → web app Next.js
- [x] Auth: JWT nội bộ hay SSO GitLab (09) → SSO GitLab (OIDC)
- [x] Ai xem được điểm cá nhân (11) → chỉ bản thân + lead
- [x] Trọng số severity & luật điểm sàn critical (08) → giữ trọng số mặc định; có critical → score ≤ 50

## Phase 1 – Nền tảng (≈ 1 tuần)
- [x] 1.1 Khởi tạo repo, cấu trúc thư mục, Makefile, Docker Compose (02)
- [x] 1.2 Config bằng pydantic-settings, `.env.example` (13)
- [x] 1.3 Migration toàn bộ schema (03)
- [x] 1.4 Mã hoá secret, logging JSON, healthcheck
- [x] 1.5 CI: lint + test
- [x] **Demo**: `make up` chạy, `/healthz` OK, DB có đủ bảng

## Phase 2 – Thu thập dữ liệu (≈ 1–2 tuần)
- [x] 2.1 GitLab client: phân trang, retry, rate-limit (04)
- [x] 2.2 Collect → raw: commits, MRs, MR diffs/commits/notes, pipelines
- [x] 2.3 Extract/convert → domain; tính `first_review_at`, `review_rounds`
- [x] 2.4 Backfill + incremental sync qua scheduler
- [x] 2.5 Webhook endpoint + dedupe + enqueue
- [x] 2.6 Identity: auto-link, import CSV, trang unmapped (05)
- [x] 2.7 CLI: connections, projects, register-webhook, sync, identity import
- [ ] **Demo**: sync 1 project thật, xem commit/MR gắn đúng người – bỏ qua theo quyết định người dùng (2026-10-22)

## Phase 3 – AI Review (≈ 2 tuần)
- [x] 3.1 `LLMProvider` + 1 provider đã chọn + `FakeLLMProvider` (06)
- [x] 3.2 Lọc file, che secret, chunking diff + lấy context
- [x] 3.3 Prompt + rubric v1 + JSON schema + parser/repair
- [x] 3.4 Hậu xử lý: kiểm tra dòng, confidence, fingerprint dedupe
- [x] 3.5 Worker review MR/commit, trạng thái, superseded khi MR update
- [x] 3.6 Theo dõi token/chi phí + ngân sách tháng
- [x] 3.7 Đăng comment tổng lên MR (bật theo project)
- [x] 3.8 Bộ eval + `make eval` đạt ngưỡng (12)
- [ ] **Demo**: mở MR test có lỗi SQLi → nhận comment critical trong ≤ 2 phút

## Phase 4 – Chấm điểm & Tổng hợp (≈ 1 tuần)
- [x] 4.1 Công thức review_score + test bảng (08)
- [x] 4.2 Metric quy trình: review time, MR size, rounds, build success
- [x] 4.3 Aggregator tuần/tháng cho user & team (upsert, timezone)
- [x] 4.4 Fix rate qua fingerprint
- [x] 4.5 Quality index + MIN_SAMPLE + delta kỳ trước
- [x] 4.6 CLI recompute
- [ ] **Demo**: bảng metric 4 tuần gần nhất có số liệu hợp lý

## Phase 5 – API, Dashboard, Báo cáo (≈ 2 tuần)
- [x] 5.1 REST API + RBAC + audit log (09)
- [x] 5.2 Dispute flow (API + UI tối thiểu) → recompute
- [x] 5.3 Dashboard: Team Overview, Member Detail, Critical Board, Admin (10)
- [x] 5.4 Báo cáo weekly/monthly (Markdown/HTML) + gửi Email/Slack
- [x] 5.5 Báo cáo cá nhân riêng cho từng dev
- [ ] **Demo**: nhận báo cáo tuần thật qua Slack/email

## Phase 6 – Mở rộng (tuỳ chọn)
- [ ] 6.1 Semgrep / Gitleaks / SonarQube (07)
- [ ] 6.2 Inline discussion trên MR cho critical/high
- [ ] 6.3 AI tóm tắt "điểm mạnh / cần cải thiện" mỗi tháng cho từng dev
- [ ] 6.4 Collector GitHub (implement interface `Collector`)
- [ ] 6.5 Chỉ số DORA (deploy frequency, lead time, CFR, MTTR) như DevLake

## Phase 7 – Hardening & Go-live
- [x] 7.1 Checklist bảo mật & privacy hoàn tất (11)
- [x] 7.2 Monitoring + cảnh báo + backup (13)
- [x] 7.3 Runbook + tài liệu người dùng
- [ ] 7.4 Pilot 1 team 2 tuần → thu feedback, chỉnh rubric/trọng số
- [ ] 7.5 Rollout toàn bộ team

## Phase 8 – Tri thức review theo dự án & luồng review v2 (19)
- [ ] 8.1 Bảng `review_knowledge` + `review_knowledge_edits`, model, migration (19 §3.4)
- [ ] 8.2 `app/review/knowledge.py`: schema RK, tầng tài liệu (biên dịch không tool, theo lô), gộp hai cấp dự án/repo, trần token, áp sửa tay (19 §3.1–3.3)
- [ ] 8.3 Job `build_knowledge_job` + API refresh/versions/approve/edits + audit; review đầu tiên tự dựng; đánh dấu cũ theo sha (19 §3.4–3.5)
- [ ] 8.4 Dashboard: thẻ Tiền đề review + nút Rà soát lại dự án + trang chi tiết RK (nguồn, tắt/sửa, Đã bỏ, Mâu thuẫn, Duyệt) (19 §3.5)
- [ ] 8.5 Review nạp RK thay trích `.claude` (`claude_kit` chỉ còn fallback khi chưa có RK); `signals.py` chọn checks theo diff; basis.knowledge (19 §4.1)
- [ ] 8.6 Eval tự sinh theo dự án `eval --project` + chạy lại eval chung trên rubric hiện tại (19 §5)
- [ ] **Demo**: bấm Rà soát lại trên 1 repo omicx → xem RK, review 1 commit thấy rule_id trỏ về RK
- [ ] 8.7 Luồng v2: checkout một lần, [1] review agentic với RK, [1'] dự phòng không tool, chunk 60k (19 §4.1–4.2)
- [ ] 8.8 [2] Kiểm chứng độc lập: cột `verified`/`verification`, bằng chứng = dòng không đổi, `rejected` giữ dòng (19 §4.3)
- [ ] 8.9 Rubric v4, điểm chỉ tính finding đã kiểm chứng, verdict `needs_human`; comment MR/Telegram/webhook/UI hiện mức mới (19 §4.4–4.5)
- [ ] 8.10 Vận hành: transaction tách ba, RateLimiter Redis, đổi tên `DEEP_REVIEW*` → `REVIEW_AGENT*`, Admin chọn model từng bước (19 §4.6, §8)
- [ ] 8.11 Thống kê theo rule (chết/nhiễu), tab So với bản trước, tầng thăm dò mã, tự biên dịch theo cờ (19 §3.2, §5)
- [ ] 8.12 Đo trước/sau trên DB thật (chi phí, thời gian, ignored_verifications, dispute, lệch điểm) và chỉnh trần (19 §5)
- [ ] **Demo**: cùng 30 commit chạy luồng cũ và v2 → precision theo severity, chi phí/review, lệch điểm khi review lại
