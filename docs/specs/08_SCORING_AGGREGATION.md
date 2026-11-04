# 08 – Scoring & Aggregation

## 1. Điểm của một review (0–100)
> **Thay bởi spec 19 (2026-10-27):** từ rubric v4 chỉ tính finding đã kiểm chứng (`confirmed`, `downgraded` theo severity mới, `skipped`, `unverified` có confidence ≥ 0.7) và cap critical ≤ 50 / high ≤ 80 chỉ áp cho `confirmed`; review rubric < v4 giữ cách cũ khi recompute. Xem 19 §4.4.

```
penalty      = Σ weight[severity] × confidence   (finding status ≠ false_positive)
weight       = {critical: 25, high: 10, medium: 4, low: 1, info: 0}
size_factor  = max(1, loc_reviewed / 200)        # MR lớn được "chia đều" lỗi
review_score = clamp(100 − penalty / size_factor, 0, 100)
```
- Trọng số lưu trong config (`SCORING_WEIGHTS`), version hoá cùng `rubric_version`.
- Finding bị dispute được chấp nhận (`false_positive`) → tính lại điểm review.
- `[DECISION]` Có giới hạn tối đa điểm sàn khi có critical (vd: có critical → score ≤ 50)? → **Đã chốt: có**, review có ≥ 1 critical → `review_score ≤ 50`.

### Rubric v3 (người dùng chốt 2026-10-26)
Với v1/v2, MR 1000 LOC có 1 high + 2 medium vẫn được ~97 trong khi kết luận là FIX REQUIRED (penalty chia tuyến tính cho LOC/200). Từ `rubric_version = v3`:
```
weight       = {critical: 30, high: 15, medium: 5, low: 1.5, info: 0}
size_factor  = max(1, sqrt(loc_reviewed / 200))
review_score = clamp(100 − penalty / size_factor, 0, 100)
có critical  → ≤ 50;  có high → ≤ 80
```
- Review v1/v2 giữ nguyên cách tính (kể cả khi recompute sau dispute); điểm chỉ so sánh trong cùng rubric_version.
- Ví dụ: 400 LOC 1H+2M+3L 92.3 → 80; 800 LOC 5 medium 96.5 → 91.25; 300 LOC 6 low 97.6 → 95.6.

## 2. Chỉ số theo người / kỳ (tuần ISO, tháng)
| Metric | Công thức |
|---|---|
| `reviews_count` | số review `done` có `author_user_id` = user, **thời điểm thay đổi** trong kỳ: thời gian commit (review MR: lúc mở MR) nếu nằm trong 30 ngày trước `finished_at`, ngoài khoảng đó hoặc không rõ thì `finished_at` (`Review.counted_at`; người dùng đổi 2026-10-29, trước đó là `finished_at`). Review xong muộn cho thay đổi của kỳ trước làm kỳ đó được tổng hợp lại (`late_periods`). |
| `avg_score` | trung bình `review_score` **có trọng số theo LOC** |
| `critical/high/...` | đếm finding theo severity |
| `defect_density` | `weighted_penalty / (loc_changed / 100)` |
| `mr_count` | MR merged trong kỳ |
| `avg_review_time_h` | `first_review_at − created_at_src` (giờ) |
| `avg_mr_size` | additions + deletions |
| `avg_review_rounds` | trung bình `review_rounds` |
| `build_success_rate` | pipeline success / tổng pipeline trên MR của user |
| `fix_rate` | finding critical/high đã `fixed` / tổng (fixed = không còn xuất hiện ở review sau cùng fingerprint) |

## 3. Quality Index tổng hợp (0–100)
```
quality_index = 0.50 × avg_score
              + 0.20 × (100 − min(100, defect_density × 10))
              + 0.15 × build_success_rate
              + 0.15 × fix_rate × 100
```
- Chỉ hiển thị khi `reviews_count ≥ MIN_SAMPLE` (mặc định 3) – tránh kết luận từ dữ liệu quá ít.
- Hiển thị kèm **delta so với kỳ trước** và trung bình team.

## 4. Team
- Tổng hợp từ thành viên thuộc team **tại thời điểm** review (theo `team_members.joined_at/left_at`).
- Thêm: phân bố severity, top 5 category lỗi, top 5 file/module "nóng".

## 5. Job tổng hợp
- [ ] `aggregate(period_type, period_start)` – idempotent (upsert).
- [ ] Cron: tuần → 00:30 thứ Hai (tính tuần trước); tháng → 00:30 ngày 1.
- [ ] Recompute khi có dispute được duyệt hoặc đổi trọng số (CLI `codepulse metrics recompute --from --to`).
- [ ] Timezone báo cáo: `REPORT_TZ` (mặc định `Asia/Ho_Chi_Minh`).

## Test bắt buộc
- [ ] Bảng test case công thức (MR nhỏ có 1 critical, MR lớn nhiều low, không finding…)
- [ ] Test ranh giới tuần/tháng theo timezone
- [ ] Test recompute sau dispute

## Quyết định trên finding giữ nguyên qua các review sau (người dùng 2026-11-04)
> "Bên khiếu nại đã duyệt nhưng bên finding lại chưa đóng."

- **Nguyên nhân**: khiếu nại (và đóng finding) chỉ đổi đúng dòng finding được khiếu nại. Một push mới của MR, hoặc một lần review lại, báo **lại cùng lỗi đó thành một dòng mới** ở trạng thái mở; Critical Board và trang review hiện review mới nhất nên lỗi vẫn hiện như chưa đóng.
- **[Quyết định]** Quyết định "finding sai" (khiếu nại được chấp nhận, hoặc đóng là finding sai) và "chấp nhận rủi ro" (kèm mức đã hạ) áp dụng cho **cùng finding** (cùng fingerprint: file, loại, đoạn code, tiêu đề) trong **mọi review khác của cùng MR / cùng commit**:
  - lúc quyết định: các bản còn mở được đổi theo, review của chúng được tính lại kết luận merge, điểm, tuần và tháng (`app/review/status.carry_decision`, `app/scoring/rescore.rescore_copies`);
  - lúc review mới lưu kết quả: lỗi đã được quyết định trước đó giữ quyết định ấy (`decided_before`).
- "Đã sửa" không mang sang: lỗi bị báo lại nghĩa là chưa sửa. Khiếu nại bị từ chối cũng không đổi gì.
- Dữ liệu có từ trước: chạy một lần `python -m app.cli findings carry-decisions`.

## Thời điểm tính kỳ: thời gian commit / mở MR, không phải thời gian review (người dùng 2026-11-04)
> "Thời gian dùng cho tính toán, báo cáo, mọi thứ là thời gian commit … time review chỉ là thông tin"; "MR lấy thời gian mở MR cũng hợp lý"; "ví dụ commit tháng 6 nhưng tới tháng 11 mới yêu cầu review".
- `Review.counted_at` = thời điểm thay đổi được tạo: thời gian commit, hoặc lúc mở MR. **Bỏ** quy tắc cũ (2026-10-29) "commit cách lúc review quá 30 ngày thì tính theo lúc review": commit tháng 6 review tháng 11 vẫn tính vào tháng 6.
- Thời gian review chỉ còn dùng khi không biết thời gian thay đổi, hoặc thời gian commit nằm sau lúc review (đồng hồ sai / ngày bị sửa). Ngoài ra chỉ để hiển thị, và cho chi phí LLM theo tháng (tiền tiêu lúc review chạy).
- Tuổi finding đang mở (báo cáo tuần, Telegram, Critical Board) tính từ thời gian thay đổi.
- Sau khi deploy: tính lại số liệu đã lưu `python -m app.cli metrics recompute --from <ngày đầu> --to <hôm nay>`, và bấm "Chạy lại dữ liệu DPI".

