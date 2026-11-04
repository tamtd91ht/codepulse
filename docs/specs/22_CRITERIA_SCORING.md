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

## 10. Chấm lại điểm theo tiêu chí, có bộ lọc (người dùng 2026-11-04)
> "Chấm điểm lại toàn bộ theo tiêu chí đang được gán cho dự án; có cảnh báo và nút xác nhận; lọc theo dự án, repo, thời gian, user…; căn cứ kết quả review đã chạy so với tiêu chí hiện tại, không review lại; riêng kết quả quá sơ sài thì cho phép review lại; dùng Claude để tổng hợp cho chính xác."

- **Ở đâu**: Admin › Dự án & Repo › tab **Tiêu chí chấm** (và chỗ nào hiện khối gán bộ cho QLDA), nút **Chấm lại điểm…**. Quyền: `criteria.assign` trên dự án; tuỳ chọn review lại cần thêm `review.rerun` trên dự án.
- **Bộ lọc**: bộ tiêu chí (mặc định mọi bộ của dự án), repo của dự án, người (nhiều), khoảng thời gian (theo ngày `REPORT_TZ`, bỏ trống = mọi lúc). Chỉ review `done`.
- **Cách tính** (không gọi LLM): mỗi review được chấm lại từ **finding đã có** theo **phiên bản hiện tại** của từng bộ (`store_scores`), ghi đè điểm cũ. Finding đã được model xếp vào một thành phần (`criteria[set_id]`) thì giữ chỗ đó; chưa xếp thì đi theo loại lỗi. Điểm review 0–100 và kết luận merge không đổi.
- **Tuỳ chọn 1, Claude xếp finding** (mặc định bật trong hộp thoại):
  - Với mỗi review có finding **chưa được xếp hợp lệ** vào bộ, gọi Claude một lượt nhỏ, không có tool. Đầu vào là các thành phần (mã, tên, mô tả) và finding (mức, loại, file, tiêu đề + nội dung ≤ 400 ký tự, đã redact, ghi rõ là dữ liệu, không phải chỉ dẫn).
  - Chỉ nhận mã thành phần thuộc bộ; kết quả ghi vào `review_findings.criteria`, nên lần chấm sau không gọi lại.
  - Mỗi lượt gọi xếp tối đa 60 finding; review có nhiều hơn thì gọi thêm lượt.
  - Chi phí ghi vào audit `criteria.rescore.cost` **theo từng lô 100 review, trong cùng transaction với kết quả xếp**, nên job chết giữa chừng vẫn đã ghi phần đã tiêu. Khoản này tính vào ngân sách LLM tháng.
  - Trước mỗi lượt gọi, đọc lại mức đã tiêu của tháng (gồm các job khác). Chạm ngân sách thì dừng gọi; phần còn lại đi theo loại lỗi, còn điểm vẫn được chấm.
  - Dừng gọi khi: 5 lượt lỗi liên tiếp (Claude không trả lời), hoặc chạm **1.500 lượt gọi mỗi lần chạy** (khoảng 30 USD; muốn hơn thì thu hẹp bộ lọc rồi chạy tiếp).
  - **[Quyết định]** Nội dung finding (do model viết, có thể trích code của tác giả) gửi dạng JSON trong thẻ, ghi rõ là dữ liệu, không làm theo chỉ dẫn bên trong; chỉ nhận id finding và mã thành phần hợp lệ.
- **Tuỳ chọn 2, review lại kết quả quá sơ sài** (mặc định tắt):
  - **[Quyết định]** "Sơ sài" = thay đổi ≥ 30 dòng mà không có finding nào và nhận xét < 40 ký tự, ở rubric hiện tại, và là vòng mới nhất. Review theo rubric cũ **không** tính (Chạy lại cũng từ chối: cần yêu cầu review mới).
  - Review lại thật (vào hàng `reviews`), như nút Chạy lại: `force` + `expected_round` (một vòng mới, vòng cũ giữ lịch sử), cùng dạng job id `…-rerun-<review>-<vòng>` nên hiện trong hàng đợi đang chạy.
  - Vòng mới mang trigger `criteria_rescore` và **không bao giờ bị coi là sơ sài nữa**, nên một thay đổi thật sự không có lỗi chỉ trả tiền review lại một lần.
  - MR chỉ review lại khi còn mở và chưa có commit mới; repo phải đang theo dõi và bật review. Không review lại được thì đếm là "bỏ qua".
- **Xem trước (dry run, không ghi gì)**, trả về:
  - số review, số finding;
  - theo từng bộ: chưa có điểm / đang ở phiên bản cũ / sẽ đổi điểm (≥ 0,5) / mức đổi trung bình;
  - số finding chưa được xếp, ở bao nhiêu review, và ước tính chi phí Claude;
  - số review sơ sài theo lý do, có vài ví dụ.
- **Cảnh báo và xác nhận**: hộp thoại cảnh báo điểm tiêu chí của N review sẽ bị ghi đè (và chi phí Claude, số review chạy lại nếu có). Phải tick "Tôi hiểu…" thì nút **Chấm lại N review** mới bật. API bắt buộc `confirm: true`.
- **Giới hạn**: tối đa 20.000 review mỗi lần xem trước hoặc chạy (quá thì 422, yêu cầu thu hẹp bộ lọc). Truy vấn theo lô 1.000 id.
- **Chạy nền**:
  - Job `rescore_job` chạy trên hàng `reviews` (việc LLM, không chặn sync ở hàng `default`).
  - Mỗi dự án một lượt một lúc: khoá Redis `SET NX` lấy khi bấm, job nhả khi xong; đang chạy thì trả 409.
  - Kết quả giữ 1 ngày; lỗi thì trả về dòng lỗi cuối.
- **Tiến trình** (người dùng 2026-11-04: "cần show tiến trình để biết có chạy hay không, tiến độ ra sao"):
  - Job ghi tiến độ vào meta của job RQ **sau từng review và trước từng lượt gọi Claude** (tối đa mỗi 1,5 giây, luôn ghi khi đổi pha): pha (`scoring` / `classifying` / `saving` / `finished`), số review đã chấm / tổng, lượt gọi Claude, lượt lỗi, chi phí, số finding đã xếp, số review lại đã xếp hàng, lúc bắt đầu, lúc cập nhật gần nhất.
  - `GET …/criteria-rescore` trả thêm: vị trí trong hàng đợi `reviews` khi đang chờ, `stale` (đã chạy mà 5 phút không có bước nào: worker có thể đã dừng), giờ máy chủ (để tính thời gian đã chạy / còn lại không lệch đồng hồ).
  - **Cạnh nút "Chấm lại điểm"** luôn có một nhãn trạng thái (dù hộp thoại đóng): đang chờ (n việc phía trước), đang chấm `x/y review · %` kèm thanh nhỏ, "không thấy tiến triển", lỗi, hoặc "đã chấm lại N review · giờ xong". Bấm nhãn mở thẳng phần tiến độ.
  - **Hộp thoại, bước Tiến độ**: các bước (Chờ worker → pha hiện tại → Xong), thanh %, đã chạy, còn lại ước tính (theo tốc độ, khi đã xong ≥ 10%), lượt gọi Claude và chi phí, finding đã xếp, review lại đã xếp hàng, "cập nhật … trước"; cảnh báo khi `stale`. Xong thì hiện kết quả và nút "Chấm lại lượt mới".
  - Commit theo lô 100 review, tiến độ ở `job.meta`; hộp thoại hỏi lại mỗi 2 giây, đóng đi job vẫn chạy.
  - Kết quả: số review đã chấm, số finding Claude đã xếp, số lượt gọi và chi phí, có dừng vì ngân sách không, số review đã xếp hàng chạy lại / bỏ qua.
  - Audit: `criteria.rescore` (lúc bấm), `criteria.rescore.done`.
- API:
  - `POST /business-projects/{bp}/criteria-rescore/preview` với `{set_ids, project_id, user_ids, from, to}`;
  - `POST /business-projects/{bp}/criteria-rescore` với `{…, classify, rereview_thin, confirm: true}` → 202;
  - `GET /business-projects/{bp}/criteria-rescore` (trạng thái lượt gần nhất).
  - Code: `app/scoring/criteria_rescore.py`, `app/workers/criteria_jobs.py`, `app/api/routers/criteria_rescore.py`, `dashboards/web/components/CriteriaRescore.tsx`.

