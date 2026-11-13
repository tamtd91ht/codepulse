# 06 – AI Review Engine

## Đơn vị review
- **MR** (mặc định): review diff tổng MR so với target branch tại `head_sha`.
- **Commit** trực tiếp lên default branch (không qua MR): review diff commit.
- Bỏ qua nếu: không còn file sau khi lọc `IGNORE_PATHS`, hoặc diff > `MAX_REVIEW_LOC` (mặc định 1500) → status `skipped` + lý do "MR quá lớn" (vẫn ghi nhận vào metric PR Size).

## Chia nhỏ diff (chunking)
> **Thay bởi spec 19 (2026-10-27):** chỉ chunk khi diff vượt `REVIEW_CHUNK_TOKENS` (60.000 token); repo được checkout một lần và ngữ cảnh ±30 dòng đọc từ đĩa thay vì gọi `file_raw` từng file. Xem 19 §4.1–4.2.

- [ ] Nhóm theo file; mỗi chunk ≤ `MAX_TOKENS_PER_CHUNK` (vd 12k token).
- [ ] Mỗi chunk kèm ngữ cảnh: tiêu đề + mô tả MR, ngôn ngữ, ±30 dòng quanh hunk (lấy qua `GET /projects/:id/repository/files/:path/raw?ref=`).
- [ ] Gộp kết quả các chunk → 1 review; dedupe finding theo `fingerprint`.

## Rubric (version hoá – `rubric_version`, vd `v1`)
| Category | Kiểm tra |
|---|---|
| security | Injection, secret hard-code, authz thiếu, deserialization, SSRF, XSS |
| bug | Logic sai, null/undefined, race condition, xử lý lỗi thiếu, off-by-one |
| performance | N+1 query, vòng lặp nặng, thiếu index/caching rõ ràng, memory leak |
| maintainability | Hàm quá dài/phức tạp, trùng lặp, đặt tên kém, coupling |
| testing | Thay đổi logic không có test, test không assert |
| style | Vi phạm convention của team (đọc từ `CONVENTIONS.md` của repo nếu có) |
| docs | API public thiếu docstring, thay đổi breaking không ghi chú |

## Định nghĩa severity
| Severity | Định nghĩa | Ví dụ |
|---|---|---|
| **critical** | Có thể gây mất dữ liệu, lỗ hổng bảo mật khai thác được, sập production | SQL injection, lộ secret, xoá dữ liệu sai điều kiện |
| **high** | Bug chắc chắn xảy ra trong luồng chính, hoặc rủi ro bảo mật đáng kể | Null pointer ở luồng chính, thiếu kiểm tra quyền |
| **medium** | Bug ở edge case, vấn đề hiệu năng rõ, thiếu test cho logic quan trọng | N+1 query, không xử lý timeout |
| **low** | Maintainability, code smell | Hàm 150 dòng, magic number |
| **info** | Gợi ý, style nhỏ | Đổi tên biến |

## Output JSON (bắt buộc – validate bằng Pydantic, dùng tool/JSON mode của provider)
```json
{
  "summary": "Tóm tắt 2-3 câu về thay đổi và chất lượng",
  "score": 78,
  "score_breakdown": {"security": 90, "bug": 70, "performance": 85, "maintainability": 75, "testing": 60},
  "findings": [
    {
      "severity": "critical",
      "category": "security",
      "file_path": "app/auth/login.py",
      "line_start": 42, "line_end": 45,
      "title": "SQL injection trong truy vấn đăng nhập",
      "message": "Chuỗi SQL được nối trực tiếp từ input người dùng.",
      "suggestion": "Dùng parameterized query: cursor.execute(sql, (username,))",
      "confidence": 0.92
    }
  ]
}
```

## Prompt (khung)
```
SYSTEM: Bạn là senior code reviewer. Chỉ báo vấn đề có thật, dựa trên diff được cung cấp.
Không bịa số dòng. Chỉ comment trên dòng được thêm/sửa (+). Dùng rubric và định nghĩa severity sau: {rubric}.
Nếu không chắc, hạ severity và giảm confidence. Trả về đúng JSON schema: {schema}.
USER: Repo: {project}. Ngôn ngữ: {lang}. MR: {title}\n{description}
Conventions: {conventions_md}
Diff:\n{diff_chunk}
Context:\n{context}
```

## Hậu xử lý
> **Thay bởi spec 19 (2026-10-27):** mỗi finding bắt buộc có `rule_id` và đi qua bước kiểm chứng độc lập cho ra `verified` (`confirmed`/`downgraded`/`rejected`/`unverified`/`skipped`); điểm chỉ tính finding `confirmed`, `downgraded`, `skipped` và `unverified` có confidence ≥ 0.7. Xem 19 §4.3–4.4.

- [ ] Kiểm tra `file_path`/`line` có nằm trong diff; nếu không → hạ thành `info` hoặc loại bỏ.
- [ ] Loại finding `confidence < MIN_CONFIDENCE` (mặc định 0.4 từ rubric v3, trước đó 0.5).
- [ ] **Không dùng `score` của LLM làm điểm cuối** – điểm cuối tính bằng công thức ở `08` (LLM score chỉ tham khảo, lưu ở `score_breakdown`).
- [ ] Lưu `model`, `rubric_version`, token, chi phí.

## LLM Provider abstraction
```python
class LLMProvider(Protocol):
    def review(self, prompt: ReviewPrompt) -> ReviewResult: ...
```
- [ ] `AnthropicProvider`, `OpenAIProvider`, `OllamaProvider`
- [ ] `temperature=0`, timeout 120s, retry 3 lần, JSON repair 1 lần nếu parse lỗi
- [ ] Giới hạn chi phí `MONTHLY_LLM_BUDGET_USD` – vượt thì tạm dừng và cảnh báo

## Đăng comment lên MR (nếu `post_mr_comment=true`)
- [ ] 1 comment tổng: điểm, đếm theo severity, top 5 finding.
- [ ] (Tuỳ chọn) discussion inline cho critical/high qua `POST /merge_requests/:iid/discussions` với `position`.
- [ ] Cập nhật comment cũ thay vì tạo mới khi review lại (lưu `note_id`).

## Đánh giá chất lượng reviewer (eval)
- [ ] Bộ 20–30 diff mẫu có lỗi đã biết trong `tests/eval/` (expected findings).
- [ ] Script `make eval` đo precision/recall theo severity – chạy mỗi khi đổi prompt/model.

## Tự review lại khi LLM hoạt động lại (yêu cầu 2026-11-04)

Cấu hình (Cài đặt review, sửa trên UI): `LLM_AUTO_RESUME` (mặc định tắt) và `LLM_PROBE_INTERVAL_MIN` (5–240, mặc định 30).

- **LLM lỗi** = lỗi của chính LLM (`llm: …`) do kết nối, timeout, HTTP 401/403/408/409/425/429/5xx, rate/usage limit, quota, quá tải, hết hạn đăng nhập. Không tính: câu trả lời hỏng (non-JSON, parse), hết ngân sách tháng, lỗi GitLab.
- Bảng một dòng `llm_health` (`ok | down | recovering`). Review gặp LLM lỗi được đánh dấu `reviews.llm_retry` và chuyển dòng `ok → down` bằng UPDATE có điều kiện: N review lỗi cùng lúc thì chỉ lần đầu mở "sự cố", các lần sau không đổi gì.
- Khi `down`: review mới không gọi LLM, kết thúc `failed` với lời "chờ LLM hoạt động lại" và cũng được đánh dấu chờ (không tốn token, không spam LLM).
- Scheduler tick 5 phút (`llm-watch`, max_instances=1); chỉ thử khi dòng `down` và tới `next_probe_at`, khoá `FOR UPDATE SKIP LOCKED` nên hai scheduler không thử cùng lúc. Thử = một lệnh rất nhỏ (timeout 60s, không retry). Lỗi → hẹn lần sau theo chu kỳ.
- Thử được → `recovering` và xếp **một** job `llm_resume_job` (job id theo sự cố). Job khoá dòng, chỉ chạy khi còn `recovering`, đặt `ok` rồi nhận review chờ theo lô bằng `UPDATE … RETURNING` (mỗi review chỉ được nhận một lần dù job chạy hai lần) và xếp lại như "Chạy lại" (trigger `rerun`, giữ người yêu cầu). Review không còn chạy được (repo tắt, vòng mới hơn, rubric đổi, MR đã merge/có push mới) giữ `failed` kèm lý do.
- Tắt cấu hình khi đang `down`: lần tick kế đóng sự cố, review chờ giữ `failed` (chạy lại tay).
- API: `GET /admin/llm-health`, `POST /admin/llm-health/probe` ("Kiểm tra ngay": thử ở tick kế), quyền `admin.review_settings`.

## Nhiều tài khoản Claude: mặc định + dự phòng (yêu cầu 2026-11-09)
Admin › LLM › Kết nối Claude giữ nhiều tài khoản (`llm_accounts`, token mã hoá như trước; token đơn lẻ cũ được migration chuyển thành tài khoản mặc định). Code: `app/review/llm_accounts.py`, `app/review/providers/failover.py`, `app/review/providers/account_errors.py`.
- [x] **Luôn dùng tài khoản mặc định**; dự phòng chỉ dùng khi mặc định không dùng được, theo thứ tự admin xếp; token trong `.env` là dự phòng cuối. *Lý do: yêu cầu người dùng; giữ một tài khoản giúp prompt cache trúng nhiều hơn.*
- [x] **Chỉ lỗi của chính tài khoản mới chuyển**: đăng nhập (401/403, token hết hạn), hạn mức (429, usage limit, credit), gói không có model review. Lỗi 5xx/quá tải/timeout/mạng giống nhau ở mọi tài khoản → không chuyển (tránh đổi qua đổi lại), giữ retry + "Tự review lại khi LLM hoạt động lại". CLI không retry lỗi đăng nhập/hạn mức gói/model (chuyển ngay, không mất ~14 s backoff); 429 ngắn hạn (`rate_limit_error`, không phải usage limit) vẫn được retry, hết retry thì nghỉ 1 phút rồi chuyển. Chỉ đọc lỗi API có cấu trúc (`claude reported an error …`), không bao giờ đọc stderr của CLI (có thể chứa diff) — một MR nhắc tới "rate_limit" hay "model" không được làm tài khoản bị tạm dừng.
- [x] **Quay lại mặc định không bằng ping định kỳ** mà ở lần gọi thật đầu tiên sau `retry_at` (lỗi đăng nhập/hạn mức trả về trong vài giây) → không tốn token cho việc kiểm tra. `retry_at`: hạn mức có giờ reset trong thông báo → đúng giờ đó; không có → `LLM_ACCOUNT_RETRY_MIN` (Admin › Cấu hình, 5–240, mặc định 15 phút), gấp đôi mỗi lần còn lỗi, tối đa 8×; lỗi đăng nhập → 8× (không tự khỏi; đăng nhập lại hoặc "Kiểm tra" xoá ngay); gói thiếu model → chờ tới khi đổi model hoặc admin kiểm tra lại. *Lý do: 15 phút cố định là thừa với hạn mức có giờ reset và vô ích với token đã thu hồi.*
- [x] **Giữ chung ngữ cảnh**: mỗi lần gọi là độc lập (không session, `--setting-sources ""`, không CLAUDE.md của tài khoản); mọi ngữ cảnh (rubric, tiêu chí, knowledge, diff) do CodePulse gửi. Mọi tài khoản bị **ghim cùng một model** (`LLM_MODEL` hoặc mặc định), không bao giờ hạ model; tài khoản không dùng được model bị bỏ qua. `reviews.llm_account` ghi tài khoản đã trả lời (hiện ở trang chi tiết review).
- [x] Tài khoản chuyển từ "dùng được" sang lỗi → thông báo trong app cho người đã thêm tài khoản đó (một lần mỗi đợt lỗi). Khi mọi tài khoản đều chờ, lỗi được coi là "LLM lỗi" → cơ chế tự review lại tiếp quản.
- [x] **HTTP 429 được đọc theo nội dung** (người dùng 2026-11-09: hạn mức gói cũng trả 429): có giờ reset (`|epoch`, ISO, `resets 3pm (Asia/Ho_Chi_Minh)`) hoặc lời về hạn mức gói (usage limit, 5-hour, weekly, Opus limit, "reached your … limit") → hết hạn mức, chuyển ngay; nói rõ là giới hạn theo phút (per-minute, tokens per, "resets in 30 seconds") → giới hạn ngắn; 429 không rõ loại → thử lại 1 lần, nghỉ 1 phút, nếu lặp lại ngay sau đó thì coi là hết hạn mức (lùi 15 phút, gấp đôi) và báo cho admin. *Lý do: không để tài khoản hết hạn mức bị thử mỗi phút, mỗi lần mất ~14 s retry.*
- [x] **Đọc tên lỗi của CLI trước lời báo** (người dùng 2026-11-13: tài khoản mặc định hết hạn mức nhưng review báo lỗi, không chuyển sang tài khoản dự phòng). Nguyên nhân: Claude CLI 2.1 đổi lời báo ("You've hit your Fable limit", "… monthly spend limit", "… team's shared budget", "Opus 5 requires usage credits", "You're out of usage credits", "Your seat type doesn't include usage", "Your usage allocation has been disabled"), thường không kèm HTTP 429, nên không được nhận là lỗi của tài khoản. Sửa: thông báo lỗi kèm `[api_error=…]` (tên lỗi của CLI, ví dụ `usage_limit_reached`, `model_requires_usage_credits`, `stored_login_refused`) và `[api_error_code=…]` (mã của server), chỉ nhận chuỗi định danh `[a-z][a-z0-9_]*`, không bao giờ là chữ của model. Tên đã biết quyết định loại lỗi (hạn mức / đăng nhập); mã server lạ có chữ usage_limit, quota, credit, budget, spend_limit, billing, overage → hạn mức, oauth/login/credential → đăng nhập. Các lời báo mới cũng được thêm vào bộ đọc chữ. Lỗi quá tải, mất kết nối, `max_output_tokens` vẫn không chuyển tài khoản.
- Nhiều worker cùng gặp một lỗi chỉ tính một lần (không đẩy backoff lên 8× ngay); một lần gọi dài bắt đầu trước khi tài khoản lỗi không xoá trạng thái lỗi đó khi xong. Đổi tài khoản mặc định được khoá (advisory lock) để hai admin thao tác cùng lúc không lỗi 500.
- API cũ `DELETE /admin/llm-token` giờ xoá tài khoản mặc định (dự phòng đầu tiên lên thay), không còn quay thẳng về token trong `.env` khi vẫn còn tài khoản dự phòng.
- Lưu ý: dùng tài khoản do công ty sở hữu (seat Team/Enterprise) hoặc API key làm dự phòng; gom nhiều gói cá nhân để vượt hạn mức có thể trái điều khoản của Anthropic.
