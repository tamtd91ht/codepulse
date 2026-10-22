# PROGRESS

## Phase 0 – Chốt quyết định (2026-10-22)
- Đã chốt: LLM = Claude Code CLI (`claude -p`) trên worker, code gửi tới Anthropic; review cả MR và commit push thẳng lên `main`; dashboard Next.js; auth SSO GitLab (OIDC); điểm cá nhân chỉ bản thân + lead xem; có critical → score ≤ 50, giữ trọng số mặc định.
- Ghi quyết định vào 01, 02, 08, 09, 10, 11, 14.
- Còn mở: (1) chưa có provider local → project `allow_external_llm=false` chưa review được; (2) `13_DEPLOYMENT_OPS.md` chưa có giá trị `LLM_PROVIDER` cho Claude Code CLI (xử lý ở task 3.1); (3) cần xác nhận gói Claude cho phép chạy CLI không giám sát trên server.

## Phase 1 – Nền tảng
- 1.1: skeleton `app/` theo 02, pyproject (ruff/mypy strict/pytest), Dockerfile, compose (postgres, redis, api, worker, scheduler; bỏ grafana vì đã chọn Next.js), Makefile up/down/lint/test (migrate/seed thêm ở 1.3). Máy dev chưa có `make` → chạy lệnh trực tiếp.
- 1.2: `app/config.py` (pydantic-settings, secret dạng SecretStr, list dạng CSV), `.env.example`; thêm `LLM_PROVIDER=claude_code` (mặc định) vào 13 theo quyết định Phase 0.
- 1.3: models SQLAlchemy cho 17 bảng của 03 (mọi bảng có created_at/updated_at, đủ 4 index), migration Alembic đầu tiên; `reviews` UNIQUE dùng NULLS NOT DISTINCT (mr_id/commit_sha luôn có 1 cột NULL nên UNIQUE thường không có tác dụng). Test tích hợp bằng testcontainers (tắt Ryuk do lỗi port trên Docker Desktop). `make seed` làm ở 1.4 vì cần mã hoá token.
- 1.4: `SecretBox` (Fernet, lỗi không lộ token), log JSON có request_id (middleware, header X-Request-ID) / job_id (worker RQ), `/healthz` (DB + Redis bắt buộc, số worker còn heartbeat chỉ báo cáo), healthcheck container cho api và worker, `app.seed` idempotent (`make seed`).
- 1.5: CI lint + test: GitHub Actions (repo đang ở GitHub) và `.gitlab-ci.yml` theo 12 (docker-in-docker cho testcontainers) – stage eval/build thêm ở 3.8/sau.
