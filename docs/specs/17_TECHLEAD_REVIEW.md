# 17 – Review theo góc nhìn techlead: quy tắc dự án, verdict, review theo yêu cầu

> Ngoài spec gốc, theo yêu cầu người dùng (2026-10-25), lấy ý từ quy trình `/review-mr-dev` của dự án omicx: reviewer đọc diff như techlead duyệt merge của member, đối chiếu quy tắc của chính dự án, kết luận rõ merge được hay không, và nói MR làm gì, đúng hướng không.

## 1. Quy tắc review theo dự án / repo
- `business_projects.review_rules` (markdown ≤ 12.000 ký tự) và `projects.review_rules` (riêng cho repo) sửa ở Admin → Dự án & Repo → ngăn chi tiết dự án → **Quy tắc review**.
- `projects.review_rules_paths`: danh sách file **trong repo** (cách nhau dấu phẩy, ≤ 10 file, không `..`), đọc ở **default branch** của repo (`GET repository/files/:path/raw?ref=<default_branch>`), mỗi file cắt ở 6.000 ký tự. Không đọc tại commit đang review: tác giả có thể sửa file quy tắc trong cùng MR để "tự duyệt"; file quy tắc nằm trong diff thì bị bỏ qua và prompt ghi chú điều đó. Ví dụ omicx: `.claude/rules/critical/tenant-isolation.md, .claude/rules/critical/money-safety.md`.
- Ghép theo thứ tự dự án → repo → file, mỗi phần giữ tiêu đề (`## Quy tắc dự án X`, `## Quy tắc repo Y`, `## <path>`), tổng ≤ 20.000 ký tự, đưa vào khối "Team rules" của prompt. Prompt vẫn coi khối này là dữ liệu không tin cậy (không đổi được format output).
- Finding vi phạm một quy tắc ghi tiêu đề quy tắc vào `review_findings.rule_id` (cột có sẵn) → báo cáo được "dự án X vi phạm tenant isolation bao nhiêu lần".
- Không nạp được file (GitLab lỗi, file không có) → review vẫn chạy, chỉ thiếu phần đó; log warning.

## 2. Rubric v2 (techlead)
- `RUBRIC_VERSION = "v2"`; trọng số điểm giữ nguyên v1 (`WEIGHTS_BY_RUBRIC["v2"]`). Review của sha đã có kết quả v1 vẫn giữ; yêu cầu review lại sẽ tạo review v2 mới (theo nguyên tắc "một review là cuối cùng với sha + rubric của nó").
- Bốn câu hỏi theo thứ tự ưu tiên: an toàn (authz, tenant/ownership, injection, SSRF, secret, PII) → đúng và toàn vẹn dữ liệu khi concurrency / lỗi một phần (transaction, idempotency, race, tiền) → chịu tải (N+1, truy vấn không giới hạn, blocking trên request path, thiếu timeout/retry/circuit breaker, cache không TTL) → rõ ràng, bảo trì được. Severity chấm theo tác động production.
- Output thêm `intent` (không chấm điểm): `summary`, `matches_intent` (đúng với mô tả MR không), `altitude` (over / right / under), `concerns` (≤ 5 quan ngại thiết kế không vi phạm rule). Nhiều chunk → gộp: summary đầu tiên, concerns hợp nhất, matches_intent = AND, altitude lấy mức xấu nhất. Lưu `reviews.intent` (JSONB), hiện trên trang review, comment MR và payload webhook.

## 3. Verdict và risk score
- `reviews.verdict` ∈ `approve | fix_required | block`, `reviews.risk_score = critical×10 + high×3 + medium×1`, tính từ finding không bị lead kết luận false positive. Có critical → BLOCK; có high → FIX REQUIRED; còn lại APPROVE. Cùng thang với quy trình thủ công của techlead; điểm 0–100 (spec 08) vẫn là chỉ số so sánh theo thời gian.
- Tính khi review `done`; tính lại khi duyệt khiếu nại (finding thành false_positive) và khi `metrics recompute`. Migration `a7c3e9b1d2f4` backfill cho review cũ.
- Hiện ở: trang review (KPI "Kết luận merge"), trang dự án và tổng quan (KPI "Tỉ lệ APPROVE" + số approve/fix/block, tính trực tiếp từ `reviews`), comment MR, kết quả Telegram, payload webhook (`review.verdict`, `review.risk_score`, `review.intent`), bộ lọc `GET /reviews?verdict=`.

## 4. Ai yêu cầu review
- `reviews.trigger` ∈ `webhook | dashboard | telegram | backfill | rerun`, `reviews.requested_by_user_id` (SET NULL khi xoá người; người không còn tồn tại lúc job chạy → NULL). Truyền qua kwargs của job RQ (`review_mr_job(..., trigger=, requested_by=)`); khi một review `failed`/`skipped` được chạy lại, dòng cũ nhận trigger/người yêu cầu mới. Dùng để sau này tách "review tự động" với "review được yêu cầu" khi đánh giá người.

## 5. Review theo yêu cầu (dashboard)
Trang **Yêu cầu review** (`/reviews/request`, mọi người đăng nhập):
1. Chọn dự án → repo (chỉ repo đang theo dõi; repo tắt review bị mờ) → branch (gõ để tìm trên GitLab, mặc định default branch).
2. Chọn người (không chọn = mọi người bạn được review) và khoảng thời gian (hôm nay / 3 / 7 / 14 / 30 ngày / tuỳ chọn).
3. **Xem commit**: danh sách lấy thẳng từ GitLab (`GET /repos/{id}/commits?ref&since&until&user_id…`, ≤ 200), kèm tác giả đã gắn người, ±LOC, trạng thái review (verdict + điểm, trong hàng đợi, lỗi, bỏ qua) và lý do không review được (merge commit, đã review, ngoài quyền).
4. Chọn commit (mặc định chọn các commit review được, tối đa `REVIEW_BATCH_MAX`) → **Review N commit** → `POST /repos/{id}/review-commits {ref, shas}` (≤ `REVIEW_BATCH_MAX`, mặc định 50): server lấy lại từng commit từ GitLab (client request-path: timeout 10 s, 1 lần thử lại; GitLab lỗi khác 404 → dừng, trả 502, phần đã xếp hàng giữ nguyên), tạo tài khoản tác giả/dòng `commits` nếu chưa có (không ghi đè), kiểm tra quyền theo tác giả, bỏ merge commit và sha đã có review `queued|running|done|skipped`, **ghi sẵn dòng `reviews` trạng thái `queued`** (worker `_claim` tiếp quản) rồi enqueue `review_commit_job(trigger="dashboard", requested_by=…)`. Trang tự làm mới mỗi 10 giây khi còn review đang chạy.
- Quyền (giống Telegram, `Principal.may_review_author`): thành viên review việc của mình; lead thêm người mình lead và commit chưa gắn người; admin mọi người. Commit của người ngoài phạm vi không được liệt kê (đếm vào `hidden`); bộ lọc người không mở rộng được phạm vi.
- API phụ: `GET /repos` (repo đang theo dõi + dự án), `GET /people` (người trong phạm vi), `GET /repos/{id}/branches?search=`. Lỗi GitLab trả 404/502 chung, không lộ URL nội bộ. Audit `project.review_commits` (ref, số queued/skipped).
- Review vẫn tốn ngân sách LLM như review tự động; hết ngân sách → review `failed` với lý do, chạy lại được.

## Không làm (và vì sao)
- Không fan-out nhiều agent chuyên biệt theo loại file như omicx: chi phí gấp nhiều lần; thay bằng quy tắc theo repo/đường dẫn trong mục 1.
- Không cho LLM đọc cả repo (tầng "deep review") trong lượt này: cần quyết định riêng về bảo mật (hiện CLI chạy không tool, cwd rỗng để diff không thể prompt-inject). Ghi ở IDEAS.
