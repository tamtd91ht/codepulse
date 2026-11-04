# 22. Chấm điểm theo bộ tiêu chí có trọng số

> Yêu cầu người dùng ngày 2026-11-03, lấy từ tài liệu DPI (phần "đánh giá chấm điểm theo trọng số"): "xây dựng một bộ tiêu chí trên giao diện web rồi gán cho dự án; một dự án có thể có một hoặc nhiều bộ tiêu chí; khi review, kết hợp với bộ tiêu chí để chấm điểm theo bộ tiêu chí đó và trọng số của nó".
>
> Các quyết định đã chốt (người dùng đồng ý đề xuất, cùng ngày):
> - **Q1.** Điểm của mỗi thành phần được tính bằng công thức từ các finding thuộc thành phần đó. AI chỉ xếp mỗi finding vào một thành phần, không tự cho điểm.
> - **Q2.** Một dự án có thể có nhiều bộ tiêu chí, trong đó đúng **một bộ chính**. Bộ chính dùng cho chỉ số và báo cáo.
> - **Q3.** Bộ tiêu chí có **phiên bản**. Mỗi điểm số ghi lại phiên bản đã dùng để tính.
> - **Q4.** Điểm 0–100 hiện có (spec 08) và kết luận merge giữ nguyên. Điểm theo tiêu chí là phần **thêm vào**.
>
> Spec này khác đề xuất DPI ở một điểm: đây là điểm **của một thay đổi** được review, không phải điểm tổng xếp hạng một người. Nguyên tắc D1 của spec 20 vì vậy vẫn giữ.

## 1. Khái niệm

- **Bộ tiêu chí** (criteria set) gồm tên, mô tả và một danh sách **thành phần** (component). Mỗi thành phần có:
  - `key`: slug bất biến, ví dụ `security`;
  - `name`: tối đa 60 ký tự;
  - `description`: tối đa 500 ký tự. AI đọc mô tả này để xếp finding vào thành phần;
  - `weight`: số nguyên %, tổng các thành phần = 100;
  - `categories`: các loại finding thuộc thành phần này khi AI không xếp.
- Các loại finding (`app/review/schema.Category`): `security`, `bug`, `performance`, `maintainability`, `testing`, `style`, `docs`. **Mỗi loại thuộc đúng một thành phần của bộ.** Nhờ vậy mọi finding luôn có chỗ thuộc về, điểm tính được cả cho review cũ, và không có finding nào lọt khỏi điểm.
- Mỗi bộ có từ 2 đến 12 thành phần. Tuỳ chọn `apply_caps` (mặc định bật): điểm cuối cũng bị chặn trần như điểm review, tối đa 50 khi có critical và tối đa 80 khi có high. Lý do: một critical bảo mật không thể ra 90 điểm chỉ vì thành phần Bảo mật có trọng số nhỏ.
- **Phiên bản:**
  - Sửa `components` hoặc `apply_caps` sẽ tạo phiên bản mới, bất biến.
  - Sửa tên hoặc mô tả của bộ thì **không** tạo phiên bản mới.
  - Bộ chỉ xoá được khi chưa từng được dùng. Bộ đã dùng thì **lưu trữ** (archive).
- **Gán cho dự án:** một dự án có 0..n bộ, nếu có thì đúng một bộ chính. Thứ tự hiển thị theo `position`.

## 2. Chấm điểm (`app/scoring/criteria.py`, hàm thuần)

- **Xếp finding vào thành phần**, với mỗi bộ đang áp dụng:
  1. `finding.criteria[str(set_id)]`, tức thành phần AI chọn khi review, nếu là một `key` hợp lệ của phiên bản đó;
  2. nếu không, thành phần chứa `finding.category`.
- **Điểm thành phần c:** `review_score(findings thuộc c, loc_reviewed, weights, rubric_version, min_conf)`. Đây đúng là công thức của spec 08/19 (trọng số theo mức độ, hệ số theo cỡ thay đổi, finding đã xác minh, chặn trần), chỉ khác là chạy trên tập con. Thành phần không có finding được 100 điểm.
- **Điểm cuối:** `round(Σ weight_c × score_c / 100, 2)`. Nếu bật `apply_caps`, điểm cuối không vượt trần critical/high theo đúng quy tắc của `review_score`.
- Hàm trả về `breakdown`: với mỗi thành phần gồm key, tên, trọng số, điểm, mức phạt, và danh sách finding id **cùng nguồn xếp** (`ai` hoặc `category`).
- **Finding không được tính** (`false_positive`, `rejected`, chưa xác minh dưới ngưỡng) bị loại theo đúng quy tắc của `review_score`.

## 3. Dữ liệu (migration Alembic)

| Bảng | Cột |
|---|---|
| `criteria_sets` | `id`, `name` (unique), `description`, `archived` bool, `current_version_id` (FK, nullable khi mới tạo), `created_by`, timestamps |
| `criteria_set_versions` | `id`, `set_id` FK (cascade), `version` int (unique theo set), `components` JSONB, `apply_caps` bool, `note`, `created_by`, `created_at`. Bất biến |
| `project_criteria_sets` | `business_project_id` FK (cascade), `set_id` FK (restrict), `is_primary` bool, `position` int. PK (bp, set). Ràng buộc: tối đa một `is_primary` cho mỗi dự án (unique index có điều kiện) |
| `review_criteria_scores` | `review_id` FK (cascade), `set_id` FK (cascade), `version_id` FK, `score` Numeric(5,2), `breakdown` JSONB, `computed_at`. PK (review, set) |
| `review_findings.criteria` | JSONB, nullable: `{ "<set_id>": "<component key>" }` do AI xếp |

## 4. Khi review

- **Prompt:** nếu dự án của repo có bộ tiêu chí, thêm một khối `TIÊU CHÍ` liệt kê mỗi bộ (`set_id`, tên) và các thành phần (`key`, tên, mô tả). Yêu cầu AI điền trường tuỳ chọn `criteria: {set_id: key}` cho mỗi finding. Áp dụng cho cả review thường lẫn deep review. Khối này là **dữ liệu**, được redact, và dữ liệu không đóng được thẻ của prompt.
- **Kiểm tra đầu ra:** bỏ các set id hoặc key lạ.
- Khi review xong, tính và lưu `review_criteria_scores` cho mọi bộ của dự án, bằng phiên bản hiện tại của từng bộ.
- **Tính lại:** `rescore_review` (khiếu nại, đóng hoặc hạ mức finding, xác minh) tính lại cả điểm theo tiêu chí, **giữ phiên bản đã lưu**.
- Có job `recompute_criteria_job(bp_id, set_id, since)` tính lại cho các review đã xong của dự án, bằng **phiên bản hiện tại**. Job chạy khi gán bộ mới cho dự án, hoặc khi người dùng bấm "Tính lại". Review cũ chưa có `criteria` do AI xếp thì xếp theo loại finding.

## 5. Quyền (spec 21, thêm vào danh mục và bộ có sẵn bằng migration dữ liệu)

| key | Nghĩa | Mặc định |
|---|---|---|
| `criteria.manage` | Tạo, sửa, nhân bản, lưu trữ bộ tiêu chí | flag; chỉ admin |
| `criteria.assign` | Gán bộ tiêu chí cho dự án, tính lại | project/all; QLDA project, QLT all |

Xem điểm theo tiêu chí đi theo `review.view`: ai xem được review thì xem được điểm của nó.

## 6. API (`/api/v1`)

| Method | Path | Mô tả |
|---|---|---|
| GET | `/criteria/meta` | Các loại finding (key, nhãn tiếng Việt), giới hạn (2–12 thành phần, độ dài), **mẫu có sẵn** (§8) |
| GET | `/criteria-sets?archived=` | Danh sách: tên, mô tả, archived, phiên bản hiện tại (thành phần), số dự án đang dùng, có phải bộ chính ở đâu |
| GET | `/criteria-sets/{id}` | Chi tiết, kèm danh sách phiên bản (version, note, người tạo, ngày) |
| GET | `/criteria-sets/{id}/versions/{version}` | Một phiên bản (để so sánh) |
| POST | `/criteria-sets` | `{name, description, components, apply_caps, from_template?}` → 201 |
| PUT | `/criteria-sets/{id}` | `{name, description, components, apply_caps, note}`. Tạo phiên bản mới nếu thành phần hoặc `apply_caps` đổi |
| POST | `/criteria-sets/{id}/clone` | `{name}` |
| POST | `/criteria-sets/{id}/archive`, `/unarchive` | Bộ đang là bộ chính ở một dự án thì không lưu trữ được (409) |
| DELETE | `/criteria-sets/{id}` | Chỉ khi chưa từng dùng (chưa có điểm nào, chưa gán dự án nào) |
| POST | `/criteria/preview` | `{components, apply_caps, business_project_id, limit≤20}` → với tối đa 20 review gần nhất của dự án: điểm hiện có, điểm theo bộ đang soạn, breakdown. Không lưu gì |
| GET | `/business-projects/{bp}/criteria-sets` | Các bộ của dự án (thứ tự, bộ chính) |
| PUT | `/business-projects/{bp}/criteria-sets` | `{sets: [{set_id, is_primary}]}`, thay cả danh sách. Gán thêm bộ thì xếp hàng job tính lại |
| POST | `/business-projects/{bp}/criteria-sets/{set_id}/recompute` | `{since?}` → 202 |
| GET | `/reviews/{id}` | Thêm `criteria_scores: [{set_id, set_name, version, is_primary, score, breakdown}]` |
| GET | `/criteria/summary?business_project_id&project_id&user_id&from&to&set_id` | Trung bình điểm cuối và từng thành phần, có trọng số theo LOC như `avg_score`, trên các review trong phạm vi `review.view`; thêm xu hướng theo tuần |

**Kiểm tra đầu vào** (trả 422 kèm `detail` chỉ rõ trường lỗi):
- `key` theo `^[a-z][a-z0-9_]{1,30}$` và không trùng;
- tên không trống;
- trọng số là số nguyên 1–100, tổng = 100;
- mỗi loại finding thuộc đúng một thành phần;
- số thành phần trong khoảng 2–12.

Mọi thay đổi ghi audit `criteria_set.*` và `project.criteria_sets`.

## 7. UI/UX: màn cấu hình tiêu chí (yêu cầu "thật chuẩn")

Vị trí: **Admin › Bộ tiêu chí**, một tab của Admin.

1. **Danh sách bộ:**
   - Thẻ gọn cho mỗi bộ: tên, mô tả một dòng, thanh xếp chồng thể hiện trọng số từng thành phần (màu cố định theo thứ tự, có tooltip), số dự án đang dùng, nhãn "Bộ chính ở N dự án", phiên bản.
   - Lọc "đang dùng / lưu trữ", ô tìm.
   - Nút **Tạo bộ** mở hộp chọn: *bắt đầu từ mẫu* (§8) hoặc *trống*, hoặc nhân bản bộ có sẵn.
2. **Trình soạn bộ** (trang hoặc ngăn kéo rộng, không cuộn kiểu landing):
   - **Xem và sửa tách riêng** (người dùng 2026-11-04): bấm một bộ có sẵn là **chế độ Xem**, chỉ đọc (mô tả, phiên bản, trần, thanh trọng số, mỗi thành phần một dòng gồm tên, mã, trọng số, mô tả và các loại finding; Xem trước vẫn ở bên phải). Nút **Sửa** (chỉ ai có `criteria.manage`) mở trình soạn bên dưới; **Huỷ** bỏ thay đổi và về chế độ Xem; lưu xong cũng về chế độ Xem. Bộ mới mở thẳng trình soạn. "Có thay đổi chưa lưu", hỏi khi rời trang và Ctrl+S chỉ có trong chế độ Sửa. So sánh "đã đổi" dùng dạng chuẩn hoá (cắt khoảng trắng, loại finding đã sắp xếp), nên mở bộ mà không sửa gì không bao giờ báo phải lưu.
   - **Bên trái, các thành phần.** Mỗi dòng có tay kéo để sắp xếp, tên, mô tả (textarea gọn, đếm ký tự), và trọng số (thanh trượt + ô số, bước 1).
   - **Thanh tổng trọng số** luôn hiện trên cùng: phần đã dùng, phần còn lại, và báo đỏ khi tổng khác 100. Có nút **Chia đều**, và nút **Chuẩn hoá về 100** (giữ tỉ lệ).
   - **Ma trận loại finding × thành phần:** mỗi loại finding là một hàng, chọn một thành phần (radio). Loại chưa được gán được tô đỏ, và nút Lưu bị khoá cho tới khi đủ.
   - **Bên phải, Xem trước:**
     - chọn dự án, xem 10–20 review gần nhất: điểm hiện có, điểm theo bộ đang soạn, mức chênh lệch;
     - bấm vào một review để xem breakdown từng thành phần và các finding gây trừ điểm;
     - xem trước được cập nhật lại khi sửa, có debounce.
   - **Lỗi** hiện ngay tại trường (key trùng, tên trống…), có tóm tắt lỗi ở đầu. Lưu bằng phím tắt Ctrl+S. Rời trang khi chưa lưu thì hỏi xác nhận.
   - **Lưu:** nếu thành phần hoặc trần thay đổi, hiện hộp xác nhận *"Tạo phiên bản N+1"* kèm ô ghi chú phiên bản và tuỳ chọn *"Tính lại điểm cho các dự án đang dùng"*.
   - **Tab Phiên bản:** lịch sử phiên bản; so sánh hai phiên bản (thành phần thêm, bớt, đổi trọng số).
   - **Tab Dự án đang dùng:** danh sách dự án, bộ chính, nút Tính lại.
3. **Gán cho dự án:** ở Admin › Dự án (và trang dự án cho QLDA):
   - mục "Bộ tiêu chí": thêm hoặc bỏ bộ (hộp chọn có tìm), chọn bộ chính (radio), kéo để đổi thứ tự;
   - xác nhận khi bỏ bộ chính.
4. **Hiển thị điểm:**
   - **Trang review:** thẻ "Điểm theo tiêu chí". Bộ chính hiện trước, bộ khác ở tab nhỏ. Hiện điểm cuối lớn, rồi mỗi thành phần một hàng gồm tên, trọng số, thanh điểm và số finding. Bấm vào hàng thì mở danh sách finding (nguồn xếp: AI hay loại finding). Có tooltip giải thích công thức và trần.
   - **Kết quả gửi đi** (người dùng 2026-11-04): mọi nơi trả kết quả một review đã xong đều kèm điểm theo tiêu chí (`criteria_results`, `app/scoring/criteria_store.py`: các bộ đã lưu điểm cho review, bộ chính trước, tối đa 3), mỗi bộ một dòng `📐 <tên bộ> v<n>: <điểm>/100 (<thành phần> <điểm> · …)`:
     - Telegram: tin kết quả commit và MR, câu trả lời "đã được review" và `/status`;
     - file phân tích MR tải từ Telegram: bảng "Điểm theo tiêu chí" (thành phần, trọng số, điểm) cho từng bộ;
     - webhook thông báo (spec 16): trường `review.criteria_scores` trong JSON và dòng điểm trong tin Slack/Teams.
     Dự án chưa gán bộ nào hoặc review chưa có điểm theo bộ: không có dòng nào (không đổi gì so với trước).
   - **Trang thành viên và trang dự án:** khối "Theo tiêu chí": điểm trung bình từng thành phần (bar ngang), xu hướng điểm cuối theo tuần, chọn bộ nếu dự án có nhiều bộ.
5. **Chung:**
   - Dùng hệ thống tab và nút hiện có;
   - hỗ trợ dark mode bằng biến CSS;
   - hiển thị đúng ở màn hình điện thoại (cột xếp chồng);
   - đủ trợ năng: nhãn, focus, bàn phím cho kéo thả (nút lên/xuống thay kéo thả);
   - toàn bộ chữ tiếng Việt;
   - có trạng thái rỗng kèm hướng dẫn.

## 8. Mẫu có sẵn (chỉ là gợi ý ban đầu, không phải bộ trong DB)

| Mẫu | Thành phần (trọng số, loại finding) |
|---|---|
| Chuẩn chung | Đúng chức năng 30 (bug); Bảo mật 25 (security); Hiệu năng 10 (performance); Dễ bảo trì 20 (maintainability, style); Kiểm thử 10 (testing); Tài liệu 5 (docs) |
| Bảo mật cao | Bảo mật 45 (security); Đúng chức năng 25 (bug); Kiểm thử 15 (testing); Chất lượng mã 15 (performance, maintainability, style, docs) |
| Sản phẩm mới | Đúng chức năng 40 (bug, testing); Bảo mật 20 (security); Tốc độ & hiệu năng 15 (performance); Chất lượng mã 25 (maintainability, style, docs) |

## 9. Giai đoạn

| Giai đoạn | Nội dung |
|---|---|
| **22.1** | Spec; model và migration; `app/scoring/criteria.py` (hàm thuần) với test; validator thành phần; mẫu |
| **22.2** | API §6; quyền §5 (danh mục và migration dữ liệu cho bộ có sẵn); prompt và kiểm tra đầu ra; lưu điểm khi review; tính lại; job; xem trước; summary; test |
| **22.3** | UI §7 |

Mỗi giai đoạn: lint, mypy, toàn bộ test, build frontend; commit và push.
