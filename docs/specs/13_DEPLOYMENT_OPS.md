# 13 – Deployment & Operations

## Docker Compose services
`postgres`, `redis`, `api`, `worker` (scale được), `command-worker` (chạy command `.claude`, mount chung volume `command-runs` với `api`; xem RUNBOOK §15), `scheduler`, `grafana` (tuỳ chọn), `ollama` (tuỳ chọn).

## Biến môi trường (`.env.example`)
```dotenv
DATABASE_URL=postgresql+psycopg://codepulse:***@postgres:5432/codepulse
REDIS_URL=redis://redis:6379/0
APP_ENCRYPTION_KEY=           # Fernet key
APP_BASE_URL=https://codepulse.company.vn

LLM_PROVIDER=claude_code      # claude_code|anthropic|openai|ollama
LLM_MODEL=
LLM_API_KEY=
MONTHLY_LLM_BUDGET_USD=200
MAX_REVIEW_LOC=1500
MAX_TOKENS_PER_CHUNK=12000
MIN_CONFIDENCE=0.5

BACKFILL_DAYS=90
SYNC_INTERVAL_MIN=30
IGNORE_PATHS=*.lock,dist/**,vendor/**,*.min.js,**/generated/**
SENSITIVE_PATHS=**/secrets/**,.env*,*.pem,*.key
ALLOWED_EMAIL_DOMAINS=company.vn

REPORT_TZ=Asia/Ho_Chi_Minh
SMTP_URL=
SLACK_WEBHOOK_URL=
RETENTION_DAYS=365
```

## Vận hành
- [x] Migration chạy tự động khi `api` khởi động (hoặc job riêng) – lệnh của service api
- [x] Backup Postgres hằng ngày – service `backup` (pg_dump, giữ 14 ngày)
- [x] Metrics Prometheus: queue length, review latency, LLM error rate, chi phí/ngày – `/metrics` (`app/monitoring.py`)
- [x] Cảnh báo: worker chết, queue > 100 job, LLM lỗi > 10%, vượt 80% ngân sách – `deploy/prometheus/alerts.yml`
- [x] Hàng đợi review chia làn theo dự án (**[Đã chốt]** user 2026-11-13: "khi 1 dự án nào đó review hàng loạt thì các dự án khác dù chỉ 1 yêu cầu review thôi cũng phải chờ dự án kia chạy xong"). **[Quyết định]** Mỗi hàng (`reviews_asked`, `reviews`) có `REVIEW_LANES` + 1 làn (mặc định 16; làn 0 = không thuộc dự án; dự án `x` → làn `1 + (x-1) mod REVIEW_LANES`, nên đặt ≥ số dự án); mọi review-worker nghe mọi làn và lấy **xoay vòng** trong cùng hàng (`app/workers/queues.py` `rotate`), thứ tự hai hàng giữ nguyên. Không chia cứng M/N worker cho mỗi dự án như gợi ý ban đầu: chia cứng thì khi chỉ một dự án có việc, worker của dự án khác ngồi không; xoay vòng đạt cùng mục tiêu (dự án khác chờ tối đa một review đang chạy) mà không phí công suất. Số review song song vẫn là `REVIEW_WORKERS`. Hàng cũ (không hậu tố) vẫn được phục vụ để không mất job lúc nâng cấp. Chi tiết vận hành: `docs/RUNBOOK.md` "Hàng đợi review".
- [x] Runbook `docs/RUNBOOK.md`: thêm project, xoay token, chạy lại review, recompute metric

## Cài đặt lần đầu
1. `cp .env.example .env` và điền giá trị
2. `make up && make migrate`
3. `codepulse connections add --url ... --token ...`
4. `codepulse projects add <gitlab_project_id>` + `register-webhook`
5. `codepulse identity import ...`
6. `codepulse projects sync <id>` → kiểm tra dashboard
