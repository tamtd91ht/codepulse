# AGENTS.md – Quy tắc cho AI agent

## Nguyên tắc chung
- [ ] Đọc `00_README.md` và file spec liên quan **trước** khi code task đó.
- [ ] Làm **từng task một** theo `14_TASKS_CHECKLIST.md`; không nhảy phase.
- [ ] Mục có nhãn `[DECISION]` → dừng, hỏi người dùng, không tự quyết.
- [ ] Không tự thêm tính năng ngoài spec. Ý tưởng mới ghi vào `IDEAS.md`.
- [ ] Mọi thay đổi schema phải qua migration (không sửa DB tay).

## Chất lượng code
- [ ] Python 3.12, type hints đầy đủ, `ruff` + `mypy --strict` pass.
- [ ] Mỗi module có unit test; coverage tối thiểu 80% cho `scoring/` và `review/`.
- [ ] Không hard-code secret; đọc từ biến môi trường (xem `13_DEPLOYMENT_OPS.md`).
- [ ] Log dạng JSON có `request_id` / `job_id`.
- [ ] Hàm gọi API ngoài (GitLab, LLM) phải có retry + timeout + rate limit.

## Quy trình mỗi task
1. Đọc spec → viết test trước (nếu hợp lý).
2. Implement.
3. Chạy `make lint test`.
4. Tick checkbox trong `14_TASKS_CHECKLIST.md`.
5. Ghi 1–3 dòng vào `PROGRESS.md`: đã làm gì, quyết định gì, còn gì.
6. Commit: `feat(<module>): <mô tả>` (Conventional Commits).

## Cấm
- Không gửi source code ra LLM bên ngoài nếu `LLM_PROVIDER=local` hoặc repo nằm trong `REVIEW_EXCLUDE_PROJECTS`.
- Không lưu toàn bộ nội dung file nguồn vào DB; chỉ lưu diff hunk liên quan finding (tối đa 40 dòng).
- Không xoá dữ liệu lịch sử review; chỉ đánh dấu `superseded`.
