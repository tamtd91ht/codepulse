# PROGRESS

## Phase 0 – Chốt quyết định (2026-10-22)
- Đã chốt: LLM = Claude Code CLI (`claude -p`) trên worker, code gửi tới Anthropic; review cả MR và commit push thẳng lên `main`; dashboard Next.js; auth SSO GitLab (OIDC); điểm cá nhân chỉ bản thân + lead xem; có critical → score ≤ 50, giữ trọng số mặc định.
- Ghi quyết định vào 01, 02, 08, 09, 10, 11, 14.
- Còn mở: (1) chưa có provider local → project `allow_external_llm=false` chưa review được; (2) `13_DEPLOYMENT_OPS.md` chưa có giá trị `LLM_PROVIDER` cho Claude Code CLI (xử lý ở task 3.1); (3) cần xác nhận gói Claude cho phép chạy CLI không giám sát trên server.
