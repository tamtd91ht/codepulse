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
- [ ] 1.4 Mã hoá secret, logging JSON, healthcheck
- [ ] 1.5 CI: lint + test
- [ ] **Demo**: `make up` chạy, `/healthz` OK, DB có đủ bảng

## Phase 2 – Thu thập dữ liệu (≈ 1–2 tuần)
- [ ] 2.1 GitLab client: phân trang, retry, rate-limit (04)
- [ ] 2.2 Collect → raw: commits, MRs, MR diffs/commits/notes, pipelines
- [ ] 2.3 Extract/convert → domain; tính `first_review_at`, `review_rounds`
- [ ] 2.4 Backfill + incremental sync qua scheduler
- [ ] 2.5 Webhook endpoint + dedupe + enqueue
- [ ] 2.6 Identity: auto-link, import CSV, trang unmapped (05)
- [ ] 2.7 CLI: connections, projects, register-webhook, sync, identity import
- [ ] **Demo**: sync 1 project thật, xem commit/MR gắn đúng người

## Phase 3 – AI Review (≈ 2 tuần)
- [ ] 3.1 `LLMProvider` + 1 provider đã chọn + `FakeLLMProvider` (06)
- [ ] 3.2 Lọc file, che secret, chunking diff + lấy context
- [ ] 3.3 Prompt + rubric v1 + JSON schema + parser/repair
- [ ] 3.4 Hậu xử lý: kiểm tra dòng, confidence, fingerprint dedupe
- [ ] 3.5 Worker review MR/commit, trạng thái, superseded khi MR update
- [ ] 3.6 Theo dõi token/chi phí + ngân sách tháng
- [ ] 3.7 Đăng comment tổng lên MR (bật theo project)
- [ ] 3.8 Bộ eval + `make eval` đạt ngưỡng (12)
- [ ] **Demo**: mở MR test có lỗi SQLi → nhận comment critical trong ≤ 2 phút

## Phase 4 – Chấm điểm & Tổng hợp (≈ 1 tuần)
- [ ] 4.1 Công thức review_score + test bảng (08)
- [ ] 4.2 Metric quy trình: review time, MR size, rounds, build success
- [ ] 4.3 Aggregator tuần/tháng cho user & team (upsert, timezone)
- [ ] 4.4 Fix rate qua fingerprint
- [ ] 4.5 Quality index + MIN_SAMPLE + delta kỳ trước
- [ ] 4.6 CLI recompute
- [ ] **Demo**: bảng metric 4 tuần gần nhất có số liệu hợp lý

## Phase 5 – API, Dashboard, Báo cáo (≈ 2 tuần)
- [ ] 5.1 REST API + RBAC + audit log (09)
- [ ] 5.2 Dispute flow (API + UI tối thiểu) → recompute
- [ ] 5.3 Dashboard: Team Overview, Member Detail, Critical Board, Admin (10)
- [ ] 5.4 Báo cáo weekly/monthly (Markdown/HTML) + gửi Email/Slack
- [ ] 5.5 Báo cáo cá nhân riêng cho từng dev
- [ ] **Demo**: nhận báo cáo tuần thật qua Slack/email

## Phase 6 – Mở rộng (tuỳ chọn)
- [ ] 6.1 Semgrep / Gitleaks / SonarQube (07)
- [ ] 6.2 Inline discussion trên MR cho critical/high
- [ ] 6.3 AI tóm tắt "điểm mạnh / cần cải thiện" mỗi tháng cho từng dev
- [ ] 6.4 Collector GitHub (implement interface `Collector`)
- [ ] 6.5 Chỉ số DORA (deploy frequency, lead time, CFR, MTTR) như DevLake

## Phase 7 – Hardening & Go-live
- [ ] 7.1 Checklist bảo mật & privacy hoàn tất (11)
- [ ] 7.2 Monitoring + cảnh báo + backup (13)
- [ ] 7.3 Runbook + tài liệu người dùng
- [ ] 7.4 Pilot 1 team 2 tuần → thu feedback, chỉnh rubric/trọng số
- [ ] 7.5 Rollout toàn bộ team
