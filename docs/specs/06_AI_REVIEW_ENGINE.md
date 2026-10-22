# 06 – AI Review Engine

## Đơn vị review
- **MR** (mặc định): review diff tổng MR so với target branch tại `head_sha`.
- **Commit** trực tiếp lên default branch (không qua MR): review diff commit.
- Bỏ qua nếu: không còn file sau khi lọc `IGNORE_PATHS`, hoặc diff > `MAX_REVIEW_LOC` (mặc định 1500) → status `skipped` + lý do "MR quá lớn" (vẫn ghi nhận vào metric PR Size).

## Chia nhỏ diff (chunking)
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
- [ ] Kiểm tra `file_path`/`line` có nằm trong diff; nếu không → hạ thành `info` hoặc loại bỏ.
- [ ] Loại finding `confidence < MIN_CONFIDENCE` (mặc định 0.5).
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
