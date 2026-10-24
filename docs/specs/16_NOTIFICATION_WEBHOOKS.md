# 16 – Webhook thông báo kết quả review

> Ngoài spec gốc, theo yêu cầu người dùng (2026-10-25). Khác với webhook GitLab ở `04` (GitLab gọi CodePulse khi có MR/commit mới): đây là chiều **ra**, CodePulse POST kết quả review tới một endpoint do admin đặt (kênh chat, service nội bộ, công cụ cảnh báo).

## Mô hình
- Bảng `notification_webhooks`: tên, URL (http/https), `format` (`json` | `slack` | `teams`), secret mã hoá Fernet (tuỳ chọn, dùng ký HMAC), `events`, phạm vi (`business_project_id` và/hoặc `project_id`, cả hai NULL = mọi repo), `enabled`, tình trạng gửi (`last_delivery_at`, `last_status_code`, `last_error`, `failures`, `deliveries`).
- Phạm vi khớp khi: webhook ghim đúng repo; hoặc webhook theo dự án và repo thuộc dự án đó; hoặc webhook không ghim gì.
- Sự kiện: `review.done` (review hoàn tất), `review.severe` (hoàn tất và có ≥1 finding critical/high), `review.failed` (review lỗi: GitLab, LLM, hết ngân sách). Review `skipped` (quá lớn, không có file review được) không phát sự kiện. Mỗi review gửi **một** thông báo tới mỗi webhook: nếu webhook đăng ký `review.severe` và review có critical/high thì gửi `review.severe`, nếu không thì `review.done`.
- Luồng: `review_mr_job` / `review_commit_job` (worker) commit review xong → nếu có webhook trong phạm vi thì enqueue `deliver_review_job(review_id)` (job id `notify-review-<id>`, timeout 3 phút) → job dựng payload và POST tới từng webhook. Review từ webhook GitLab, backfill, Telegram hay chạy lại đều đi qua đây.
- Gửi: timeout 10 s, 3 lần thử với backoff 2 s / 4 s cho 408/425/429/5xx và lỗi mạng; 4xx khác là lỗi cuối. Không giữ transaction DB trong lúc gọi HTTP; mỗi lần gửi ghi sổ bằng một UPDATE atomic (an toàn khi nhiều worker). `failures` đếm lỗi liên tiếp (kể cả **Gửi thử**); tới 20 thì webhook tự tắt (`last_error` nói rõ, và được giữ nguyên cho tới khi bật lại), admin bật lại trên dashboard (reset bộ đếm). Tối đa 12 webhook nhận một review (job timeout 10 phút).
- Trùng lặp: job id `notify-review-<id>` chỉ gộp các job đang chờ; một review `failed` bị chạy lại (webhook GitLab gửi trùng, rerun) có thể thông báo hai lần. Endpoint nên idempotent theo `review.id` + `X-CodePulse-Delivery`.

## Payload
Header: `Content-Type: application/json`, `User-Agent: CodePulse-Webhook/1.0`, `X-CodePulse-Event`, `X-CodePulse-Delivery` (uuid), `X-CodePulse-Timestamp` (Unix giây), `X-CodePulse-Signature: sha256=<HMAC-SHA256(secret, timestamp + "." + body)>` khi có secret (ký trên body thô, mọi định dạng).

Định dạng `json`:
```json
{
  "event": "review.severe",
  "sent_at": "2026-10-25T09:00:00+00:00",
  "review": {
    "id": 120, "status": "done", "target_type": "mr", "label": "vihat/omicrm/api !412",
    "reviewed_sha": "…", "score": 72.5, "loc_reviewed": 180, "summary": "…", "model": "…",
    "rubric_version": "v1", "error": null, "finished_at": "…",
    "findings": {"critical": 1, "high": 0, "medium": 2, "low": 1, "info": 0, "total": 4},
    "top_findings": [{"severity": "critical", "category": "security", "title": "…", "file_path": "app/x.py", "line_start": 12}],
    "url": "https://codepulse.company.vn/reviews/120",
    "web_url": "https://gitlab.company.vn/vihat/omicrm/api/-/merge_requests/412"
  },
  "project": {"id": 3, "path_with_namespace": "vihat/omicrm/api"},
  "business_project": {"id": 1, "name": "OmiCRM"},
  "author": {"user_id": 5, "name": "Nguyễn Văn An"}
}
```
`top_findings` tối đa 5 (critical trước, rồi theo độ tin cậy). Không gửi email, diff hay đoạn code (xem 11); `review.error` chỉ là loại lỗi (`gitlab`, `llm`, …), chi tiết (URL nội bộ, stderr) chỉ xem trên dashboard. Với Slack/Teams, mọi chuỗi lấy từ dữ liệu (tên dự án, tiêu đề finding, đường dẫn file) được escape `& < >`.

`slack`: `{"text": "<markdown>", "mrkdwn": true}`; `teams`: MessageCard với cùng nội dung. Dòng đầu: `*[Dự án] repo !iid — Review xong: 72/100*`, rồi số finding theo mức độ, LOC, tác giả, tối đa 5 finding và link xem review.

Sự kiện `ping` (nút **Gửi thử** trên dashboard): cùng cấu trúc với dữ liệu mẫu, `review.id = 0`.

## API (admin, audit `webhook.*`)
- `GET /webhooks`, `POST /webhooks`, `PATCH /webhooks/{id}` (secret `""` = bỏ secret; `enabled: true` reset bộ đếm lỗi), `DELETE /webhooks/{id}`, `POST /webhooks/{id}/test`.
- Secret không bao giờ trả về (`has_secret`), không vào audit log.

## Bảo mật và vận hành
- Chỉ admin cấu hình; URL chỉ nhận http/https và không được chứa `user:password@` (dùng secret). Endpoint nội bộ là bình thường (CodePulse thường chạy trong mạng công ty), nên không chặn địa chỉ private; admin chịu trách nhiệm về URL mình nhập. Audit log chỉ ghi host, không ghi URL đầy đủ (URL Slack/Teams tự nó là bí mật).
- Endpoint nên xác minh `X-CodePulse-Signature` (so sánh constant-time) **và** từ chối `X-CodePulse-Timestamp` lệch quá vài phút (chống replay), trả 2xx nhanh; xử lý nặng nên làm bất đồng bộ.
- Redis không sẵn sàng khi review xong → thông báo của review đó bị bỏ (review vẫn lưu); log `could not queue review notification`.
