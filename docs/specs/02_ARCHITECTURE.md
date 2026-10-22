# 02 – Kiến trúc & Tech stack

## Sơ đồ
```
            ┌──────────── Webhook (Push/MR) ────────────┐
GitLab ─────┤                                           ▼
            └── REST API ◄── Collector (scheduled) ── Job Queue (Redis)
                                   │                     │
                                   ▼                     ▼
                           PostgreSQL ◄────── Review Worker ──► LLM Provider
                          (raw/tool/domain)        │
                                   ▲               └──► GitLab MR comment
                                   │
                    Aggregator (cron) ──► metrics_* tables
                                   │
                         API (FastAPI) ──► Web Dashboard / Grafana
                                   │
                         Reporter (cron) ──► Email / Slack / Teams
```

## Tech stack (mặc định – `[DECISION]` nếu muốn đổi)
| Thành phần | Lựa chọn |
|---|---|
| Ngôn ngữ | Python 3.12 |
| API | FastAPI + Pydantic v2 |
| DB | PostgreSQL 16, SQLAlchemy 2 + Alembic |
| Queue | Redis + RQ (hoặc Celery) |
| Scheduler | APScheduler trong service `scheduler` |
| LLM client | Lớp trừu tượng `LLMProvider` (Anthropic / OpenAI / Ollama) |
| Dashboard | Grafana (nhanh) **hoặc** Next.js (tuỳ biến) – `[DECISION]` |
| Đóng gói | Docker Compose |

## Cấu trúc thư mục
```
codepulse/
├── app/
│   ├── api/               # FastAPI routers
│   ├── collector/
│   │   ├── base.py        # Interface Collector (mở rộng GitHub sau)
│   │   └── gitlab/        # client.py, collect.py, extract.py, convert.py
│   ├── webhook/           # nhận & xác thực webhook
│   ├── identity/          # mapping account → user → team
│   ├── review/            # prompt, rubric, llm providers, parser
│   ├── static_analysis/   # (tuỳ chọn) semgrep/sonar
│   ├── scoring/           # công thức điểm, aggregator
│   ├── reporting/         # sinh báo cáo, notifier
│   ├── models/            # SQLAlchemy models
│   ├── workers/           # job handlers
│   └── config.py
├── migrations/
├── dashboards/            # Grafana JSON hoặc web app
├── tests/
├── docs/specs/            # bộ file .md này
├── docker-compose.yml
└── Makefile
```

## Luồng dữ liệu 3 lớp (học từ DevLake)
1. **Raw**: lưu nguyên JSON response API (`raw_gitlab_*`) → có thể chạy lại extract mà không gọi API.
2. **Tool layer**: bảng parse riêng GitLab (`gl_commits`, `gl_merge_requests`…).
3. **Domain layer**: bảng chuẩn hoá độc lập nguồn (`commits`, `merge_requests`…) – mọi logic chấm điểm chỉ đọc lớp này.

## Checklist
- [ ] Khởi tạo repo theo cấu trúc trên
- [ ] `docker-compose.yml`: postgres, redis, api, worker, scheduler, (grafana)
- [ ] `Makefile`: `up`, `down`, `migrate`, `lint`, `test`, `seed`
- [ ] `config.py` đọc env bằng pydantic-settings
- [ ] Healthcheck `/healthz` cho API và worker
