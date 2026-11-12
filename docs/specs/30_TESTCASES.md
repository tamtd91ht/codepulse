# 30 – Testcase (quản lý testcase + AI nối testcase với code)

> Ngoài spec gốc, theo yêu cầu người dùng (2026-11-12): "về nội dung testcase liệu codepulse có thể
> hỗ trợ quản lý phần này không" → phân tích 4 mức; người dùng chốt: "đang dùng Google Sheet, làm
> mức 2 + mức 4 đi". Người dùng giao toàn quyền quyết định (memory "decisions-delegated"); mọi mục
> **[Quyết định]** do agent chọn, người dùng xem lại khi duyệt trên web.

## 1. Phạm vi

- **[Đã chốt]** Mức 2, kho testcase có cấu trúc: mỗi testcase là một bản ghi (mã, chức năng,
  tiền điều kiện, các bước, dữ liệu test, kết quả mong đợi, ưu tiên, loại, nhãn, Jira, trạng thái),
  có lịch sử sửa. Nhập từ Google Sheet / .xlsx / .csv, xuất .xlsx / .csv, lọc, tìm, độ phủ.
- **[Đã chốt]** Mức 4, AI nối testcase với code: sinh testcase từ tài liệu dự án / Jira / link /
  văn bản; rà soát chất lượng một bộ testcase; gợi ý testcase cần test lại theo MR (và theo đợt
  release); review AI nhắc khi thay đổi logic mà không có test tự động.
- **[Đã chốt]** Không làm mức 3 (vòng chạy test, pass/fail theo đợt, giao người test). File xuất có
  sẵn cột "Kết quả thực tế / Trạng thái chạy / Người test / Ngày test" để đội tiếp tục chạy test
  trên Google Sheet như hiện tại.
- **[Quyết định]** Chỉ đọc Google Sheet (qua link chia sẻ công khai, như tài liệu dự án §16), không
  ghi ngược lên Google (không có tài khoản Google của người dùng). Đồng bộ hai chiều ghi `IDEAS.md`.
- Ngoài phạm vi (IDEAS): tự dò file test tự động trong repo để nối với testcase; số liệu testcase
  trong DPI; quyền riêng `tests.*`.

## 2. Quyền **[Quyết định]**

Dùng lại quyền tài liệu dự án (spec 29 §5): `docs.view` xem testcase của dự án mình thấy,
`docs.edit` (scope `project` / `all`) thêm, sửa, nhập, xoá, chạy AI. Lý do: testcase là tri thức
dự án như tài liệu, cùng nhóm người đọc/viết; thêm khoá quyền mới phải cập nhật mọi bộ quyền đã lưu.
Khi QA cần quyền tách riêng thì thêm `tests.view` / `tests.edit` (IDEAS).

**[Quyết định]** Danh sách MR ở "Cần test lại" và "Testcase liên quan" (tiêu đề, nhánh, số file,
testcase ghép) hiện cho mọi người xem được dự án: tester cần biết MR nào chạm vào đâu, dù MR là của
ai. Kết quả review vẫn theo từng tác giả (spec 09): link sang trang review chỉ hiện khi người xem
được xem việc của tác giả đó (`can_see_work`). Chạy phân tích AI cho MR cần `docs.edit`.

## 3. Dữ liệu

### 3.1 Bộ testcase `test_suites`

Một bộ = một nhóm testcase (thường là một tab của Google Sheet: "Đăng nhập", "Thanh toán"…).

| Cột | Ghi chú |
|---|---|
| `business_project_id` | dự án gốc (nơi tạo; chỉ dự án gốc xoá được bộ) |
| `name` (≤ 200), `description` | |
| `source_url`, `source_sheet` | link Google Sheet và tên tab đã nhập (để "Nhập lại") |
| `position`, `archived_at` | |
| `created_by`, `updated_by`, timestamps | |

`test_suite_links (suite_id, business_project_id)`: **[Quyết định]** một bộ dùng chung nhiều dự án,
giống tài liệu (spec 29 §17). Ví dụ bộ OMICX dùng cho cả BE, FE và Mobile. Thêm dự án cần quyền
sửa ở dự án đó.

### 3.2 Testcase `test_cases`

| Cột | Ghi chú |
|---|---|
| `suite_id` | xoá bộ thì xoá testcase |
| `code` (≤ 40) | duy nhất trong bộ; trống khi nhập thì tự cấp `TC-0001`… |
| `section` (≤ 200) | chức năng / màn hình / nhóm trong bộ |
| `title` (≤ 500) | bắt buộc |
| `preconditions`, `steps`, `test_data`, `expected`, `notes` | văn bản (Markdown nhẹ, xuống dòng giữ nguyên), mỗi ô ≤ 10 000 ký tự |
| `priority` | `critical` / `high` / `medium` / `low` (mặc định `medium`) |
| `kind` | `manual` / `automated` |
| `automation` (≤ 500) | file test tự động (đường dẫn trong repo, có thể kèm `::tên test`) |
| `tags` JSONB | nhãn tự do: `smoke`, `regression`, `api`… (≤ 20, mỗi nhãn ≤ 40) |
| `jira_keys` JSONB | `ABC-123` (≤ 20) |
| `code_paths` JSONB | vùng code testcase phủ: glob trên đường dẫn file của repo thuộc dự án, ví dụ `src/payment/**`, `**/checkout*.ts` (≤ 20) |
| `status` | `draft` (nháp, AI sinh mặc định) / `approved` (đã duyệt) / `obsolete` (lỗi thời, không gợi ý nữa) |
| `source` | `manual` / `import` / `ai` |
| `revision` | số lần sửa, tăng mỗi lần lưu |
| `search` | văn bản gộp đã bỏ dấu (mã, chức năng, tiêu đề, bước, kết quả, nhãn, Jira) để tìm |
| `position`, `created_by`, `updated_by`, timestamps | |

`test_case_revisions`: mỗi lần sửa / nhập lại làm thay đổi testcase lưu ảnh chụp **trước khi
sửa** (`snapshot` JSONB các trường ở trên, `number`, `changed_by`, `changed_at`, `via`: editor /
import / ai). Xem lịch sử, khôi phục một bản cũ.

### 3.3 Lượt AI `test_ai_runs`

Một bảng cho ba loại việc (`kind`): `generate` (sinh testcase), `audit` (rà soát bộ), `impact`
(testcase cần test lại cho một MR). Cùng vòng đời như bản nháp tài liệu (spec 29 §12):
`queued → fetching → writing → ready | failed`, `ready → applied | discarded`; token
`requested_at` chống lượt cũ ghi đè; chi phí audit `testcase.ai.cost`, tính vào ngân sách LLM tháng.

| Cột | Ghi chú |
|---|---|
| `business_project_id`, `suite_id` (generate: bộ nhận testcase; audit: bộ được rà) | |
| `merge_request_id` (impact) | |
| `input` JSONB | generate: `doc_ids`, `jira_keys`, `urls`, `files` (văn bản dán / file đã trích), `instruction`, `count`; audit: `instruction` |
| `sources` JSONB | nguồn đã đọc (giống bản nháp tài liệu) |
| `result` JSONB | generate/audit: `cases` đề xuất, `issues` (audit), `summary`; impact: `cases` [{id, reason, confidence}], `missing` (testcase nên có thêm), `summary` |
| `status`, `error`, `model`, `cost_usd`, `applied_count` | |
| `requested_at`, `requested_by`, `created_by`, `created_at`, `started_at`, `finished_at` | |

Giới hạn: **[Quyết định]** 20 lượt AI testcase / người / 24 giờ (thử lại tính), 3 lượt đang chạy
cùng lúc / người, dừng khi hết ngân sách LLM tháng.

## 4. Nhập từ Google Sheet / Excel / CSV

Luồng hai bước, như người dùng đang quen với Sheet:

1. **Đọc** (`POST /testcases/import/read`): link Google Sheet (đọc bản xuất .xlsx, giữ link trong ô
   như spec 29 §16) hoặc file .xlsx / .csv tải lên (≤ 10 MB). Trả về mỗi tab: tên, dòng tiêu đề tìm
   được, cách ghép cột đoán được, 5 dòng mẫu, số dòng, và toàn bộ dòng (≤ 2 000 dòng × 30 cột/tab).
2. **Ghép cột và nhập** (`POST /testcases/import`): người dùng chọn tab nào nhập, vào bộ nào (bộ mới
   theo tên tab, hoặc bộ có sẵn), sửa cách ghép cột nếu đoán sai, rồi nhập.

**[Quyết định]** Đoán cột theo tên tiêu đề (bỏ dấu, không phân biệt hoa thường), tìm trong 10 dòng
đầu dòng nào khớp ≥ 2 cột:

| Trường | Tiêu đề nhận (ví dụ) |
|---|---|
| mã | ID, Mã, Mã TC, TC ID, Test case ID |
| chức năng | Chức năng, Module, Màn hình, Nhóm, Hạng mục, Feature |
| tiêu đề | Tên testcase, Tiêu đề, Mô tả, Mục đích, Kịch bản, Test case, Title, Summary, Scenario |
| tiền điều kiện | Điều kiện, Tiền điều kiện, Precondition |
| các bước | Các bước, Bước thực hiện, Thao tác, Steps |
| dữ liệu test | Dữ liệu, Dữ liệu test, Test data |
| kết quả mong đợi | Kết quả mong đợi / mong muốn / dự kiến, Expected |
| ưu tiên | Ưu tiên, Mức độ, Priority |
| loại | Loại, Type, Automation, Tự động |
| nhãn | Nhãn, Tag, Labels |
| Jira | Jira, Story, Issue, Ticket, Yêu cầu |
| ghi chú | Ghi chú, Note, Comment |

- Tiêu đề đúng như file xuất của CodePulse (§5) được nhận trước mọi luật đoán, nên xuất ra rồi nhập
  lại giữ đủ mọi trường; cột "Bộ testcase", "Lý do cần test lại" bỏ qua.
- Cột thuộc việc chạy test (Kết quả thực tế, Trạng thái/Status pass-fail, Người test, Ngày test) bỏ
  qua: CodePulse không làm mức 3.
- Chỉ các trường **có cột được ghép** được ghi: nhập lại một sheet thiếu cột (ví dụ không có cột Ưu
  tiên, Jira) không xoá giá trị đã sửa trong CodePulse.
- Cột "Loại" chứa Auto/Manual là loại; chứa Smoke/Regression… thì thành nhãn.
- Ký tự điều khiển (NUL, C0 trừ tab/xuống dòng) bị bỏ khi lưu; một lần nhập tối đa 300 000 ô /
  20 triệu ký tự.
- Cột "chức năng" trống thì lấy giá trị dòng trên (Google Sheet hay gộp ô theo chiều dọc).
- Dòng không có tiêu đề bỏ qua (đếm lại báo người dùng). Dòng chỉ có chức năng mà không có gì khác
  coi là dòng nhóm: đổi chức năng cho các dòng sau.
- Ưu tiên: `critical|blocker|p0|rất cao|khẩn cấp` → critical; `high|cao|p1` → high;
  `low|thấp|p3|p4` → low; còn lại medium. Loại: `auto|automation|automated|tự động|yes|x` →
  automated, còn lại manual. Jira: các mã dạng `ABC-123` trong ô.
- **Nhập lại** cùng bộ: testcase trùng mã được cập nhật (lưu bản trước vào lịch sử nếu khác), mã mới
  thêm vào; dòng không có mã ghép theo chức năng + tiêu đề, lần lượt (hai dòng trùng tên không sinh
  bản sao mỗi lần nhập); testcase không còn trong sheet **không bị xoá** (báo số lượng để người dùng tự đánh dấu
  lỗi thời).

## 5. Xuất

`GET /testcases/export?business_project_id=&suite_id=&format=xlsx|csv` (và các bộ lọc của danh
sách; danh sách cần test lại: `/testcases/regression/export`). Mỗi bộ một tab, cột: Mã, Chức năng,
Tiêu đề, Tiền điều kiện, Các bước, Dữ liệu test, Kết quả mong đợi, Ưu tiên, Loại, Nhãn, Jira, Trạng
thái, Ghi chú, Test tự động, Vùng code, rồi 4 cột trống: **Kết quả thực tế, Trạng thái chạy, Người test, Ngày test**. Tải lên Google Drive
là chạy test được ngay; nhập lại file này cũng được (tiêu đề khớp bảng §4). Ô .xlsx ghi kiểu chữ
(không bao giờ thành công thức); ô CSV bắt đầu bằng `= + - @` có thêm `'` đứng trước (bỏ đi khi nhập
lại).

## 6. AI sinh testcase (`generate`)

- Nguồn (≤ 5 mục, như tổng hợp tài liệu): tài liệu dự án (bản hiện tại), mã Jira (tiêu đề issue đã
  đồng bộ), link (web / GitLab của dự án, cùng bộ tải an toàn), văn bản dán, file tải lên (.docx,
  .pdf, .xlsx…, trích chữ qua `/docs/drafts/extract`).
- Kèm mã + tiêu đề các testcase đang có trong bộ để AI không sinh trùng.
- Yêu cầu tuỳ chọn (≤ 1 000 ký tự) và số lượng mong muốn (mặc định 15, tối đa 40).
- Kết quả: danh sách testcase đề xuất đủ trường (§3.2, không có `code_paths` trừ khi nguồn là code)
  + `summary` + `gaps` (điều nguồn chưa nói rõ). Người dùng xem, bỏ chọn / sửa nhanh, rồi "Thêm N
  testcase" vào bộ: trạng thái `draft`, nguồn `ai`, mã tự cấp.
- Văn bản nguồn là dữ liệu, không phải lệnh (prompt nói rõ, giống spec 29 §12).

## 7. AI rà soát bộ testcase (`audit`)

- Đầu vào: toàn bộ testcase chưa lỗi thời của bộ (≤ 400 testcase, ≤ 120 000 ký tự).
- Kết quả: `issues` [{codes, kind: duplicate | unclear | missing_expected | missing_steps |
  wrong_priority | other, message}] và `cases` đề xuất bổ sung (trường hợp biên, lỗi, quyền, dữ
  liệu rỗng…). Thêm đề xuất vào bộ như §6. Vấn đề chỉ là gợi ý: người dùng tự sửa testcase.
- Không cần AI: màn hình đã gắn cờ testcase thiếu bước / thiếu kết quả mong đợi (kiểm tra tại chỗ).

## 8. Testcase cần test lại theo MR

### 8.1 Ghép không cần AI (luôn có, rẻ)

Một MR của repo thuộc dự án X ghép với testcase (chưa lỗi thời) của các bộ thuộc X (gốc hoặc liên
kết) khi:

- trùng mã Jira (`merge_requests.jira_keys` ∩ `test_cases.jira_keys`); hoặc
- một file MR thay đổi (`commit_files` của các commit thuộc MR) khớp một glob trong `code_paths`
  (`**` mọi tầng, `*` / `?` trong một tầng, mẫu không có `/` khớp tên file ở mọi thư mục; khớp bằng
  quy hoạch động, không backtrack; tối đa 12 ký tự đại diện mỗi mẫu); hoặc
- file MR thay đổi chính là file `automation` của testcase.

Mỗi testcase ghép kèm lý do ("Jira OMX-12", "src/payment/a.ts khớp src/payment/**").

### 8.2 Phân tích bằng AI (bấm khi cần)

`impact`: AI nhận tiêu đề MR, nhánh, mã Jira (kèm tiêu đề issue), **danh sách đường dẫn file đổi
kèm số dòng thêm/xoá** (không gửi nội dung code) và danh mục testcase của dự án (mã, chức năng, tiêu
đề, nhãn, Jira, code_paths; ≤ 600 testcase, ưu tiên những testcase ghép §8.1 và cùng chức năng),
trả về testcase nên test lại (lý do, độ tin cậy cao/trung bình/thấp) và testcase còn thiếu (đề xuất,
thêm vào bộ như §6). Repo cấm gửi ra LLM ngoài (`allow_external_llm = false`) thì không chạy.

**[Quyết định]** Không tự chạy AI cho mọi MR (tốn chi phí); bấm "Phân tích bằng AI" trên trang review
của MR hoặc trên tab "Cần test lại". Kết quả lưu, xem lại không tốn thêm.

### 8.3 Tab "Cần test lại" (danh sách cho đợt release)

Chọn dự án, khoảng thời gian MR được merge (mặc định 14 ngày) và nhánh đích (tuỳ chọn): liệt kê MR
đã merge kèm testcase ghép (§8.1 + kết quả AI gần nhất §8.2), và **danh sách gộp** testcase cần test
lại (không trùng, sắp theo ưu tiên, mỗi testcase ghi MR nào kéo nó vào). Xuất ra .xlsx để chạy test
trên Google Sheet.

### 8.4 Trên trang review

Review của MR có thẻ "Testcase liên quan": testcase ghép §8.1, kết quả AI §8.2 nếu có, nút "Phân tích
bằng AI".

## 9. Review AI nhắc thiếu test

**[Quyết định]** Khi thay đổi có dòng thêm vào file mã nguồn (không phải cấu hình, tài liệu, giao
diện tĩnh) mà không thêm/sửa file test nào (đường dẫn có `test/`, `tests/`, `__tests__/`, `spec/`,
hoặc tên `*_test.*`, `*.test.*`, `*.spec.*`, `test_*.py`, `*Test.java`, `*Tests.cs`…), prompt review có
thêm khối GỢI Ý KIỂM THỬ: liệt kê tối đa 10 file mã nguồn đổi nhiều nhất và dặn mô hình chỉ nêu **một**
finding `testing` mức tối đa `low` khi logic mới có rủi ro thật mà không có test; không nêu cho thay
đổi nhỏ/hiển nhiên. Không đổi luật chấm điểm: category `testing` vốn có từ trước, khối này chỉ là
lời dặn trong prompt (không có hậu xử lý riêng). Bật/tắt bằng
`REVIEW_TEST_HINT` (mặc định bật). `review.basis["tests"]` ghi số file nguồn/test đã thấy.

## 10. API

| Method | Path | Ghi chú |
|---|---|---|
| GET | `/testcases/projects` | dự án xem được + số bộ / testcase + quyền sửa |
| GET/POST | `/testcases/suites` | danh sách bộ của dự án / tạo bộ |
| PATCH/DELETE | `/testcases/suites/{id}` | đổi tên, mô tả, lưu trữ / xoá (dự án gốc) |
| PUT | `/testcases/suites/{id}/projects` | dự án dùng chung bộ (§3.1) |
| GET | `/testcases` | lọc: `business_project_id`, `suite_id`, `q`, `priority`, `status`, `kind`, `section`, `tag`, `problem` (thiếu bước/kết quả); phân trang |
| POST | `/testcases` | tạo |
| GET/PATCH/DELETE | `/testcases/{id}` | chi tiết (kèm lịch sử) / sửa (`revision` để chống ghi đè: khác → 409) / xoá |
| POST | `/testcases/bulk` | đổi trạng thái / ưu tiên / chuyển bộ / xoá nhiều testcase |
| POST | `/testcases/{id}/restore/{number}` | khôi phục bản trong lịch sử |
| GET | `/testcases/stats` | độ phủ: tổng, theo trạng thái/ưu tiên/loại, % tự động, thiếu bước/kết quả, Jira có/không testcase (issue của dự án 90 ngày gần nhất) |
| POST | `/testcases/import/read`, `/testcases/import` | §4 |
| GET | `/testcases/export` | §5 |
| GET/POST | `/testcases/ai` | lượt AI của dự án / tạo (generate, audit, impact) |
| GET/DELETE | `/testcases/ai/{id}` | chi tiết / bỏ |
| POST | `/testcases/ai/{id}/retry`, `/testcases/ai/{id}/apply` | chạy lại / thêm testcase đề xuất đã chọn |
| GET | `/testcases/impact/mr/{mr_id}` | §8.1 + AI gần nhất cho một MR |
| GET | `/testcases/regression` | §8.3 |

## 11. Giao diện

Menu **Testcase** (sau Tài liệu dự án). Trang `/testcases`, hai tab:

- **Testcase**: chọn dự án; cột trái các bộ (số testcase, nút thêm bộ, Nhập từ Google Sheet, menu
  "…": đổi tên, dự án dùng chung, xuất, rà soát bằng AI, xoá); bên phải thẻ độ phủ, ô tìm + bộ lọc,
  bảng testcase (mã, chức năng, tiêu đề, ưu tiên, loại, trạng thái, Jira, cờ thiếu), chọn nhiều để
  duyệt / đổi ưu tiên / xoá; bấm một dòng mở bảng sửa bên cạnh (đủ trường, lịch sử, khôi phục). Nút
  **Sinh bằng AI** (nguồn: tài liệu dự án, Jira, link, dán văn bản, file) và thẻ các lượt AI.
- **Cần test lại**: §8.3.

Trang review của MR: thẻ "Testcase liên quan" (§8.4).

## 12. Kiểm thử

- Đọc sheet: đoán cột, dòng tiêu đề không ở dòng 1, gộp ô chức năng, ưu tiên/loại, Jira, link trong
  ô, CSV UTF-8 có BOM.
- Nhập lại: cập nhật theo mã, lịch sử, không xoá.
- Quyền: không `docs.edit` → 403; dự án không thấy → 404; bộ dùng chung.
- Ghép MR: Jira, glob, automation; testcase lỗi thời không ghép.
- AI: prompt chứa nguồn trong thẻ, kết quả sai dạng → failed; áp dụng tạo testcase `draft`/`ai`.
- Review: khối GỢI Ý KIỂM THỬ có khi đổi mã nguồn không có test, không có khi có file test.
