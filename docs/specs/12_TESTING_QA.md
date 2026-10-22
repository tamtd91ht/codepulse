# 12 – Testing & QA

## Các tầng test
| Tầng | Công cụ | Phạm vi |
|---|---|---|
| Unit | pytest | scoring, parser, identity matching, chunking |
| Integration | pytest + testcontainers (Postgres, Redis) | collector → DB, worker review end-to-end với LLM mock |
| Contract | respx fixtures | GitLab API responses |
| LLM eval | `make eval` | precision/recall reviewer trên bộ diff mẫu |
| E2E | Playwright (nếu có web app) | dispute flow, dashboard |

## Dữ liệu mẫu
- [ ] `tests/fixtures/gitlab/*.json` – response thật đã ẩn danh
- [ ] `tests/eval/cases/<id>/{diff.patch, expected.json}` – tối thiểu:
  - 5 lỗi security (SQLi, hard-coded secret, thiếu authz, XSS, path traversal)
  - 5 bug logic, 3 performance, 3 maintainability, 4 diff sạch (expected 0 finding critical/high)
- [ ] `FakeLLMProvider` trả kết quả xác định cho integration test

## Ngưỡng chấp nhận
- [ ] Coverage tổng ≥ 75%, `scoring/` & `review/` ≥ 80%
- [ ] Eval: precision critical+high ≥ 0.8, recall ≥ 0.7
- [ ] Không finding nào trỏ dòng ngoài diff (test hậu xử lý)

## CI (GitLab CI)
- [ ] Stages: lint → test → eval (manual hoặc khi đổi `review/`) → build image
