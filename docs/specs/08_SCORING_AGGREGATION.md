# 08 – Scoring & Aggregation

## 1. Điểm của một review (0–100)
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
| `reviews_count` | số review `done` có `author_user_id` = user, `finished_at` trong kỳ |
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
