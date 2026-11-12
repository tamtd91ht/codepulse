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
- [x] 6.1 Semgrep / Gitleaks (07, phương án C, user 2026-11-12: gợi ý cho AI, chưa tính điểm); SonarQube để sau khi có
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
- [x] 8.1 Bảng `review_knowledge` + `review_knowledge_edits`, model, migration (19 §3.4)
- [x] 8.2 `app/review/knowledge.py`: schema RK, tầng tài liệu (biên dịch không tool, theo lô), gộp hai cấp dự án/repo, trần token, áp sửa tay (19 §3.1–3.3)
- [x] 8.3 Job `build_knowledge_job` + API refresh/versions/approve/edits + audit; review đầu tiên tự dựng; đánh dấu cũ theo sha (19 §3.4–3.5)
- [x] 8.4 Dashboard: thẻ Tiền đề review + nút Rà soát lại dự án + trang chi tiết RK (nguồn, tắt/sửa, Đã bỏ, Mâu thuẫn, Duyệt) (19 §3.5)
- [x] 8.5 Review nạp RK thay trích `.claude` (`claude_kit` chỉ còn fallback khi chưa có RK); `signals.py` chọn checks theo diff; basis.knowledge (19 §4.1)
- [x] 8.6 Eval tự sinh theo dự án `eval --project` + chạy lại eval chung trên rubric hiện tại (19 §5)
- [ ] **Demo**: bấm Rà soát lại trên 1 repo omicx → xem RK, review 1 commit thấy rule_id trỏ về RK
- [x] 8.7 Luồng v2: checkout một lần, [1] review agentic với RK, [1'] dự phòng không tool, chunk 60k (19 §4.1–4.2)
- [x] 8.8 [2] Kiểm chứng độc lập: cột `verified`/`verification`, bằng chứng = dòng không đổi, `rejected` giữ dòng (19 §4.3)
- [x] 8.9 Rubric v4, điểm chỉ tính finding đã kiểm chứng, verdict `needs_human`; comment MR/Telegram/webhook/UI hiện mức mới (19 §4.4–4.5)
- [x] 8.10 Vận hành: transaction tách ba, RateLimiter Redis, đổi tên `DEEP_REVIEW*` → `REVIEW_AGENT*` (19 §4.6, §8) — còn nợ: Admin chọn model từng bước
- [x] 8.11 Thống kê theo rule (chết/nhiễu), tab So với bản trước, tầng thăm dò mã, tự biên dịch theo cờ (19 §3.2, §5)
- [x] 8.12 Đo trước/sau trên DB thật (chi phí, thời gian, ignored_verifications, dispute, lệch điểm) và chỉnh trần (19 §5)
- [ ] **Demo**: cùng 30 commit chạy luồng cũ và v2 → precision theo severity, chi phí/review, lệch điểm khi review lại

## Phase 9 – Engineering Insights & hồ sơ bằng chứng (20) – người dùng yêu cầu 2026-11-03
- [x] 9.1 Spec 20 + mục lục 00 + checklist
- [x] 9.2 `app/insights/flow.py`, `pipelines.py`, `confidence.py` – hàm thuần + test bảng (20 §4.2–4.8, §7)
- [x] 9.3 API `/insights/flow`, `/insights/flow/mrs`, `/insights/review-load` + test quyền (20 §8–9)
- [x] 9.4 Dashboard `/insights`: Luồng làm việc, Tải review (20 §10)
- [x] 9.5 Bảng `commit_files` + converter + CLI `insights backfill-files` (20 §5.1)
- [x] 9.6 `knowledge.py` + API `/insights/knowledge` + tab Rủi ro tri thức (20 §4.9)
- [ ] **Demo**: một dự án thật – 4 chặng 8 tuần gần nhất, top MR chờ, bus factor
- [ ] 9.7 USER_GUIDE "Insights đo gì và không đo gì" + pilot 2–3 team (20 §12) – tài liệu xong (USER_GUIDE §6, §6a; `docs/PILOT.md`: chọn team, chuẩn bị, nhịp tuần, khảo sát, ngưỡng); còn việc tổ chức pilot thật
- [x] 9.8 Deployments collector + DORA (20 §4.10, §5.2; D2 đã chốt: tier production)
- [x] 9.9 Incident từ GitLab (20 §5.3; D3 cập nhật: bản tối thiểu, không Jira, không task)
- [x] 9.10 Hồ sơ bằng chứng: model, job draft, API, UI, audit (20 §6; D1 coaching, D4 bản thân + lead + admin)
- [x] 9.10b Hồ sơ: MR chạm module một người nắm, commit thẳng lên nhánh chính, cảnh báo account chưa gắn người (20 §6.2)
- [x] 9.12 Lấp dữ liệu Insights cho repo đã sync trước Phase 9 (tự chạy một lần, nút admin, CLI; RUNBOOK §16)
- [x] 9.11 Bản nháp AI cho hồ sơ (20 §6.4; thay 6.3)

## Phase 10 – Review qua MCP, tệp quy tắc, tài liệu dự án – người dùng yêu cầu 2026-11-11
- [x] 10.1 MCP `request_review` + `get_review_status`, scope `reviews:request`, giới hạn `mcp_reviews_per_day`, trigger `mcp` vào hàng ưu tiên; tách `queue_commit` dùng chung với trang Yêu cầu review (26 §22)
- [x] 10.2 Hướng dẫn › Tích hợp MCP mục 8 + lệnh `/review-push`; ô tick token; cấu hình hạn mức trong Admin › Cấu hình review (26 §22.5)
- [x] 10.3 Tệp quy tắc dự án: bảng `review_rule_files`, nhập .md/.txt/.doc/.docx nhiều file, đưa vào prompt như nguồn riêng; đọc .doc bằng thư viện chuẩn (17 §1b)
- [x] 10.4 Tài liệu dự án: model + migration + quyền `docs.view`/`docs.edit`, chuyển đổi 7 loại file (thêm .pdf bằng pypdf), phiên bản, tìm toàn văn không dấu, mẫu (29 §3–§9)
- [x] 10.5 Màn `/docs`: cổng 3 cột, tải nhiều file, trình soạn song song, lịch sử + so sánh + khôi phục, xem PDF gốc, độ đầy đủ, in (29 §5–§6)
- [x] 10.6 Tổng hợp từ link (AI): bản nháp tạo mới / bổ sung / nâng cấp theo mẫu chuẩn, link web và GitLab của repo trong dự án, duyệt + so sánh + sửa rồi lưu phiên bản nguồn `link`, nguồn của tài liệu (29 §12); nguồn là file tải lên (29 §12.8) hoặc văn bản dán (29 §12.9) hoặc ảnh chụp màn hình (29 §12.10)
- [x] 10.7 Ảnh trong tài liệu: dán / kéo thả / chọn ảnh trong trình soạn, `asset:<id>`, ảnh ngoài hiện thành link (29 §13); đổi cỡ ảnh (29 §13.1), kéo góc ảnh (29 §13.2), căn ảnh (29 §13.3), chữ chảy quanh ảnh (29 §13.4)
- [x] 10.8 Bảng trong trình soạn: chọn kích thước, thanh bảng (hàng, cột, căn cột, dàn thẳng), dán ô từ Excel (29 §14)
- [x] 10.9 Xoá tài liệu trên thanh công cụ (29 §15); giữ link khi nhập Google Docs/Sheets, .docx, .xlsx, web (29 §16); một tài liệu cho nhiều dự án (29 §17)
- [x] 10.10 Testcase: bộ + testcase + lịch sử + bộ dùng chung nhiều dự án, quyền theo `docs.*`, nhập Google Sheet / .xlsx / .csv (đoán cột, sửa cột, nhập lại theo mã), xuất .xlsx / .csv có cột chạy test, độ phủ (30 §3–§5, §10–§11)
- [x] 10.11 AI testcase: sinh từ tài liệu / Jira / link / văn bản / file, rà soát bộ, phân tích MR; testcase cần test lại theo MR (Jira, glob vùng code, file test tự động) + tab "Cần test lại" + thẻ trên trang review; khối GỢI Ý KIỂM THỬ trong prompt review (30 §6–§9)
- [x] 10.12 Mô hình TestRail (30 §13–§14): cây thư mục, template Steps + bước dùng chung, type / estimate / milestone, mã tự sinh C/R/T/P/M/SS, milestone, cấu hình, test plan, test run, ghi kết quả từng bước, lịch sử kết quả, nhập/xuất TestRail CSV, run từ Cần test lại
- [ ] **Demo**: push 2 commit → `/review-push` trong Claude Code → thông báo review xong trên web; nhập 3 file quy tắc vào một dự án → review sau ghi "Tệp quy tắc …" trong Căn cứ review; tải 1 Word + 1 PDF vào Tài liệu dự án, tìm không dấu, sửa và khôi phục phiên bản; dán 1 link wiki + 1 link README GitLab → bản nháp → so sánh → lưu

