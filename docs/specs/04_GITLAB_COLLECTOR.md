# 04 – GitLab Collector

## Pattern (học từ DevLake)
`collect` (gọi API → `raw_gitlab`) → `extract` (raw → tool layer) → `convert` (tool → domain). Mỗi bước idempotent, chạy lại được.

## GitLab API dùng (v4)
| Dữ liệu | Endpoint |
|---|---|
| Project | `GET /projects/:id` |
| Commits | `GET /projects/:id/repository/commits?since=&with_stats=true&all=true` |
| Commit diff | `GET /projects/:id/repository/commits/:sha/diff` |
| MRs | `GET /projects/:id/merge_requests?updated_after=&state=all` |
| MR diffs | `GET /projects/:id/merge_requests/:iid/diffs` (fallback `/changes` cho bản cũ) |
| MR commits | `GET /projects/:id/merge_requests/:iid/commits` |
| MR notes | `GET /projects/:id/merge_requests/:iid/notes` |
| MR approvals | `GET /projects/:id/merge_requests/:iid/approvals` |
| Pipelines | `GET /projects/:id/pipelines?updated_after=` |
| Users | `GET /users/:id` |
| Đăng comment | `POST /projects/:id/merge_requests/:iid/notes` |

Header: `PRIVATE-TOKEN: <token>`. Token cần scope `read_api` (+ `api` nếu đăng comment).

## Yêu cầu
- [ ] Client có phân trang (`X-Next-Page` hoặc keyset), retry exponential cho 429/5xx, tôn trọng `RateLimit-*` header.
- [ ] **Backfill**: lần đầu lấy N ngày (`BACKFILL_DAYS`, mặc định 90).
- [ ] **Incremental**: dùng `projects.last_synced_at` với `updated_after`/`since`; chồng lấn 1 giờ để không sót.
- [ ] Tính `first_review_at` = note đầu tiên không phải system và không phải của tác giả MR.
- [ ] Tính `review_rounds` = số lần có push mới sau một note review.
- [ ] Bỏ qua merge commit khi tính LOC của người dùng (`is_merge=true`).
- [ ] Lọc file sinh tự động / vendor theo `IGNORE_PATHS` (vd: `*.lock`, `dist/**`, `vendor/**`, `*.min.js`, migrations auto-gen).

## Webhook
- [ ] Endpoint `POST /webhooks/gitlab`, xác thực header `X-Gitlab-Token` (so sánh constant-time).
- [ ] Sự kiện: `Push Hook`, `Merge Request Hook` (action: open, update, merge), `Pipeline Hook`.
- [ ] Trả 200 ngay, đẩy job vào queue; lưu `X-Gitlab-Event-UUID` để chống xử lý trùng.
- [ ] MR `update` có `oldrev` khác → đánh dấu review cũ `superseded`, enqueue review mới.
- [ ] Script/CLI tự đăng ký webhook cho project: `codepulse projects register-webhook <id>`.

## Checklist test
- [ ] Unit test client với mock (`respx`), có case phân trang & 429
- [ ] Fixture JSON thật (đã ẩn danh) cho commit, MR, notes, pipeline
- [ ] Test idempotent: chạy convert 2 lần không nhân đôi dữ liệu
