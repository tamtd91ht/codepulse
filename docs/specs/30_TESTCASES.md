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

## 2. Quyền

**[Đã chốt]** (user 2026-11-12: "bổ sung module testcase vào bộ quyền") Nhóm **Testcase** trong
bộ quyền, theo vai trò của TestRail (trước đó dùng chung quyền tài liệu dự án):

| Quyền | Phạm vi | Cho làm |
|---|---|---|
| `tests.view` | cờ | xem testcase, test run, test plan, milestone của dự án mình thấy |
| `tests.edit` | project / all | thiết kế testcase: thêm, sửa, nhập, xoá, thư mục, bước dùng chung, AI sinh / rà soát |
| `tests.run` | project / all | ghi kết quả cho mọi test của dự án (người được giao test / run luôn ghi được test của mình) |
| `tests.manage` | project / all | tạo, sửa, đóng, xoá run / plan, milestone, cấu hình; phân tích MR bằng AI |

Mặc định: Thành viên `tests.view`; Quản lý dự án đủ 4 quyền ở `project`; Quản lý tổng ở `all`.
Migration `c4e6a8b0d2f5`: mọi bộ quyền đang có `docs.view` được `tests.view`, có `docs.edit` ở
phạm vi nào được `tests.edit` / `tests.run` / `tests.manage` ở phạm vi đó (không ai mất quyền).

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

## 13. Mô hình TestRail (user 2026-11-12: "làm full luôn đi, mang hết những cái tối ưu của testrail qua đây, ngoài ra các mã nên tự động gen đỡ bắt người dùng nhập")

Thay quyết định §1 "không làm mức 3": CodePulse là **nơi gốc** của testcase và nơi chạy test;
Google Sheet / Excel / CSV chỉ còn là đường nhập/xuất (kể cả CSV đúng định dạng TestRail).

### 13.1 Mã tự sinh **[Quyết định]**

Người dùng không nhập mã nào. Mỗi đối tượng có ID cố định theo kiểu TestRail, hiện kèm tiền tố:

| Đối tượng | Mã |
|---|---|
| Testcase | `C<id>` (không đổi khi chuyển bộ / thư mục) |
| Test run | `R<id>` |
| Test (một testcase trong một run) | `T<id>` |
| Test plan | `P<id>` |
| Milestone | `M<id>` |
| Bước dùng chung | `SS<id>` |

Cột `code` cũ thành **"Mã gốc"** (chỉ đọc): mã của dòng trong sheet đã nhập, dùng để ghép khi nhập
lại. Mã `TC-0001` do CodePulse tự cấp trước đây bị bỏ (migration). Nhập lại một file có cột `ID` dạng
`C123` thì cập nhật đúng testcase đó.

### 13.2 Cây thư mục (Section)

`test_sections (suite_id, parent_id, name, description, position)`: thư mục lồng nhiều cấp trong một
bộ, sắp thứ tự, kéo thả testcase / thư mục để chuyển. Testcase có `section_id` (trống: gốc của bộ).
Xoá thư mục xoá cả thư mục con và testcase bên trong (như TestRail; màn hình hỏi lại, nêu số lượng).
Migration chuyển chuỗi "chức năng" cũ thành thư mục; khi nhập, `A > B > C` thành 3 cấp.

### 13.3 Trường của testcase

- **Template**: `text` (Tiền điều kiện, Các bước, Kết quả mong đợi dạng chữ) hoặc `steps` (bảng
  bước, **mỗi bước một kết quả mong đợi**, có thể chèn **bước dùng chung**). Nút "Tách thành các
  bước" chuyển dạng chữ đánh số sang bảng bước.
- **Type** (danh sách TestRail): Acceptance, Accessibility, Automated, Compatibility, Destructive,
  Functional (mặc định), Other, Performance, Regression, Security, Smoke & Sanity, Usability.
  Migration lấy từ nhãn cũ (smoke, regression, security, performance…).
- **Estimate**: thời gian ước tính, nhập `30s`, `5m`, `1h 30m` (số trơn = phút).
- **Milestone** (tuỳ chọn), **References** = mã Jira (đã có), Priority, Automation (đã có).
- Bỏ "Mã" khỏi form.

### 13.4 Bước dùng chung (Shared steps)

`test_shared_steps (business_project_id, title, steps)`: chuỗi bước dùng lại ("Đăng nhập bằng tài
khoản quản trị"). Testcase dạng `steps` chèn bằng `{"shared": id}`; sửa bước dùng chung là mọi
testcase dùng nó đổi theo. Không xoá được khi còn testcase dùng (báo số lượng).

### 13.5 Milestone, Cấu hình

- `test_milestones (business_project_id, name, description, start_on, due_on, completed_at)`: đợt
  release / sprint. Run, plan, testcase gắn milestone; trang milestone có tiến độ gộp các run.
- `test_config_groups` + `test_configs`: nhóm cấu hình (Trình duyệt: Chrome, Safari; Nền tảng:
  Android, iOS…). Plan chọn cấu hình → mỗi tổ hợp một run.

### 13.6 Test run, test, kết quả

- **Run** `test_runs`: tên, mô tả, milestone, người được giao mặc định, mã Jira, cấu hình, nguồn
  (`manual`, `regression`, `plan`), đóng (`completed_at`). Chọn testcase: cả bộ / theo thư mục /
  theo bộ lọc (ưu tiên, type, nhãn) / testcase đã tick / danh sách **Cần test lại** của một đợt
  (§8.3). Run để mở thì thêm / bớt testcase được.
- **Test** `test_run_tests`: một testcase trong run, trạng thái `untested` → kết quả mới nhất; người
  được giao. Khi run còn mở, test hiện nội dung **hiện tại** của testcase; khi đóng run, nội dung được
  chốt (ảnh chụp), sửa testcase sau đó không đổi run cũ. Testcase bị xoá: test giữ ảnh chụp.
- **Kết quả** `test_results`: Passed / Failed / Blocked / Retest (TestRail), bình luận, phiên bản
  (build), thời gian chạy, mã bug Jira (defects), giao lại người khác; testcase dạng bước ghi trạng
  thái + kết quả thực tế **từng bước**. Mọi kết quả giữ lịch sử; trạng thái test = kết quả mới nhất.
  Ghi kết quả hàng loạt (chọn nhiều test → Passed).
- **Plan** `test_plans`: nhóm nhiều run (mỗi lựa chọn testcase × tổ hợp cấu hình), tiến độ gộp, đóng
  plan đóng mọi run.
- Quyền: xem theo `docs.view`; tạo / sửa / đóng run, plan, milestone, cấu hình, bước dùng chung theo
  `docs.edit`; **ghi kết quả**: `docs.edit` hoặc người được giao test / run.
- Báo cáo: thanh tiến độ theo trạng thái (run, plan, milestone), lịch sử kết quả của từng testcase
  (trên trang testcase), xuất run ra Excel (trạng thái, người chạy, bình luận, defects).

### 13.7 Nhập / xuất kiểu TestRail

- Xuất thêm định dạng **TestRail CSV**: `ID, Title, Section, Template, Type, Priority, Estimate,
  References, Milestone, Preconditions, Steps, Expected Result, Steps (Step), Steps (Expected
  Result)`; testcase dạng bước viết mỗi bước một dòng (dòng sau để trống các cột của testcase), đúng
  kiểu "multi-row" của TestRail.
- Nhập nhận cả định dạng này (cột `Section` có `>` thành cây, dòng tiếp theo không có Title là bước
  tiếp theo) lẫn sheet cũ. Ô "Các bước" và "Kết quả mong đợi" đều đánh số cùng số bước thì thành bảng
  bước.

### 13.8 AI

Sinh / rà soát / đề xuất testcase theo dạng `steps` (mỗi bước có kết quả mong đợi), kèm type,
estimate, đường dẫn thư mục; áp dụng thì tạo thư mục còn thiếu.

## 14. API mô hình TestRail (hợp đồng giữa backend và giao diện)

Quy ước: mọi `key` do server sinh (`C<id>`, `R<id>`, `T<id>`, `P<id>`, `M<id>`, `SS<id>`).
`Person = {id, name}`. `Counts = {untested, passed, failed, blocked, retest, total}`. Thời gian ISO
8601, ngày `YYYY-MM-DD`. Hàm dùng chung: `app/testcases/content.py` (key, nhãn, đường dẫn thư mục,
mở rộng bước dùng chung, estimate, quyền ghi kết quả).

### 14.1 Testcase (`app/api/routers/testcases.py`)

- `CaseOut` (thay bản cũ): thêm `key` ("C12"), `code` (mã gốc từ sheet, có thể null), `section_id`,
  `section` (đường dẫn "A > B" hoặc null), `template` (`text|steps`), `steps_list` (thô:
  `[{content, expected} | {shared: id}]`), `steps_view` (đã mở rộng: `[{content, expected,
  shared_id?, shared_title?}]`), `type`, `estimate_s`, `estimate` ("1h 30m" | null), `milestone_id`;
  giữ `id, suite_id, title, preconditions, steps, expected, test_data, notes, priority, kind,
  automation, tags, jira_keys, code_paths, status, source, revision, problems, updated_at`.
- Đầu vào tạo / sửa: `section_id, title, template, preconditions, steps, expected, steps_list,
  test_data, notes, priority, type, kind, automation, tags, jira_keys, code_paths, status,
  milestone_id, estimate` (chuỗi, server parse). **Không nhận `code`.** PATCH vẫn kèm `revision`.
- `GET /testcases`: thêm bộ lọc `section_id` (gồm thư mục con), `type`, `milestone_id`; bỏ
  `section`. Trả `{items, total, tags, can_edit}`.
- `POST /testcases/bulk`: thêm `action` `section` (`section_id`, null = gốc của bộ), `type`
  (`type`), `milestone` (`milestone_id`).
- Thư mục:
  - `GET /testcases/sections?suite_id=` → `[{id, suite_id, parent_id, name, description, position,
    depth, path, cases (trực tiếp), total (gồm thư mục con)}]` theo thứ tự đọc cây (không tính case
    lỗi thời).
  - `POST /testcases/sections {suite_id, parent_id?, name, description?}`.
  - `PATCH /testcases/sections/{id} {name?, description?, parent_id?, position?}`: có `parent_id`
    (kể cả null) là chuyển chỗ; không cho vào chính nó / thư mục con của nó.
  - `DELETE /testcases/sections/{id}` → xoá cả thư mục con và case bên trong, trả
    `{sections, cases}` đã xoá.
- `GET /testcases/import/template?format=xlsx|testrail`: file mẫu để nhập.
- `GET /testcases/export`: thêm `format=testrail` (CSV nhiều dòng một case, §13.7).
- Nhập: thêm ánh xạ cột `type, estimate, template, step_content, step_expected, milestone`; `code`
  dạng `C123` = cập nhật case đó; `section` dạng `A > B` thành cây.
- AI (`/testcases/ai`): đề xuất có thêm `template: "steps"`, `steps_list`, `type`, `estimate`,
  `section` (đường dẫn).

### 14.2 Thiết lập (`app/api/routers/test_runs.py`)

- Milestone: `GET /testcases/milestones?business_project_id=` → `[{id, key, name, description,
  start_on, due_on, completed_at, runs, counts, can_edit}]`; `POST {business_project_id, name,
  description?, start_on?, due_on?}`; `PATCH /testcases/milestones/{id} {name?, description?,
  start_on?, due_on?, completed?: bool}`; `DELETE`.
- Cấu hình: `GET /testcases/configs?business_project_id=` → `[{id, name, configs: [{id, name}]}]`;
  `POST /testcases/config-groups {business_project_id, name}`; `PATCH|DELETE
  /testcases/config-groups/{id}`; `POST /testcases/configs {group_id, name}`; `PATCH|DELETE
  /testcases/configs/{id}`.
- Bước dùng chung: `GET /testcases/shared-steps?business_project_id=` → `[{id, key, title, steps:
  [{content, expected}], used_by, updated_at, can_edit}]`; `POST {business_project_id, title,
  steps}`; `PATCH /testcases/shared-steps/{id} {title?, steps?}`; `DELETE` (409 khi còn case dùng).
- Người giao được: `GET /testcases/people?business_project_id=` → `[Person]` (người của dự án và
  admin đang hoạt động).

### 14.3 Run, test, kết quả (`app/api/routers/test_runs.py`, `app/testcases/runs.py`)

- `Selection` (chọn case cho run / plan): `{suite_id?, section_ids?: [int], case_ids?: [int], all?:
  bool, priority?: [str], type?: [str], tags?: [str], regression?: {days: int, target_branch?:
  str}}`. Không lấy case lỗi thời; `section_ids` gồm thư mục con; `all` không có `suite_id` = mọi bộ
  của dự án; `regression` = danh sách Cần test lại §8.3.
- `RunOut = {id, key, business_project_id, plan_id, plan_name, milestone_id, milestone_name, name,
  description, config, config_ids, assigned_to: Person|null, refs, source, source_meta,
  completed_at, created_at, created_by: Person|null, counts, can_edit}`.
- `GET /testcases/runs?business_project_id=&state=open|completed|all&milestone_id=&plan_id=` →
  `[RunOut]` (mới trước).
- `POST /testcases/runs {business_project_id, name?, description?, milestone_id?, assigned_to?,
  refs?, selection}` → `RunOut` (tên mặc định "Test run <dd/mm/yyyy>" hoặc "Regression <n> ngày
  (<dd/mm>)"); không chọn được case nào → 422.
- `GET /testcases/runs/{id}` → `RunOut`; `PATCH /testcases/runs/{id} {name?, description?,
  milestone_id?, assigned_to?, refs?, add?: Selection, remove_test_ids?: [int]}` (run đã đóng →
  409); `POST /testcases/runs/{id}/close` (chốt ảnh chụp) → `RunOut`; `DELETE`.
- `GET /testcases/runs/{id}/tests?status=&assigned_to=&q=` → `{items: [TestOut], total}`;
  `TestOut = {id, key, run_id, case_id, case_key, title, section, priority, type, status,
  assigned_to, tested_at, estimate_s, defects, latest_comment}`.
- `GET /testcases/tests/{id}` → `TestDetail = TestOut + {content, results, run: {id, key, name,
  completed}, can_record, can_edit}`; `content = {case_key, title, section, template,
  preconditions, steps, expected, steps_list, test_data, notes, priority, type, estimate_s, refs,
  automation}` (xem `content.case_content`).
- `POST /testcases/tests/{id}/results {status?, comment?, version?, elapsed?: "1m 30s", defects?:
  [str], step_results?: [{status, actual}], assigned_to?: int|null}` → `TestDetail`;
  `ResultOut = {id, status, comment, version, elapsed_s, elapsed, defects, step_results,
  assigned_to, created_by, created_at}`.
- `POST /testcases/runs/{id}/results {test_ids, status?, comment?, version?, defects?,
  assigned_to?}` → `{changed}` (ghi hàng loạt).
- `GET /testcases/runs/{id}/export` → .xlsx.
- `GET /testcases/{case_id}/results` → `[{result: ResultOut, test_id, test_key, run_id, run_key,
  run_name, config, completed}]` (50 mới nhất): lịch sử kết quả của một case.

### 14.4 Plan

- `PlanOut = {id, key, name, description, milestone_id, milestone_name, completed_at, created_at,
  counts, runs, can_edit}`; `GET /testcases/plans?business_project_id=&state=` → `[PlanOut]`.
- `POST /testcases/plans {business_project_id, name, description?, milestone_id?, entries: [{name?,
  selection, config_ids?: [int], assigned_to?}]}` → `PlanDetail`: mỗi entry sinh một run cho mỗi
  tổ hợp cấu hình (tích Descartes theo nhóm của các `config_ids` đã chọn; không chọn → 1 run).
- `GET /testcases/plans/{id}` → `PlanDetail = PlanOut + {runs: [RunOut]}`; `PATCH
  /testcases/plans/{id} {name?, description?, milestone_id?, add_entries?}`; `POST
  /testcases/plans/{id}/close` (đóng mọi run); `DELETE`.

## 15. Nâng cấp ngang và vượt TestRail (user 2026-11-12)

> Nguồn: tài liệu "CODEPULSE_TESTCASE_UPGRADE_SPEC.md" (Google Docs, user gửi 2026-11-12: "nâng
> cấp testcase theo nội dung này đi"). Mã hạng mục G1–G13, X1–X9 giữ như tài liệu; làm theo lộ trình
> 5 giai đoạn của tài liệu, mỗi hạng mục một commit. Mục này ghi **hiện trạng đã có** (tài liệu viết
> từ phía giao diện, nhiều chỗ "[cần xác minh]") và **quyết định** khi tài liệu để ngỏ.

### 15.1 Hiện trạng các mục "[cần xác minh]"

| Mục | Đã có trước nâng cấp | Còn thiếu |
|---|---|---|
| G6 | Passed / Failed / Blocked / Retest / Untested, kết quả từng bước, giao người (test, run), elapsed, ghi hàng loạt, "Lưu & sang test sau" | Skipped, trạng thái tuỳ biến, đính kèm / dán ảnh, phím tắt, lọc "Của tôi", ước tính thời gian còn lại, cờ "chỉ người được giao" |
| G7 | Quyền `tests.view/edit/run/manage` trong bộ quyền (§2), kiểm tra ở backend | Vai trò dựng sẵn Tester / Developer / Guest, quyền duyệt case, cờ "bắt buộc duyệt" |
| G10 | Bulk: trạng thái, ưu tiên, type, milestone, chuyển bộ / thư mục, xoá; revision cho mỗi thay đổi | Sửa tags / jira / code_paths hàng loạt, sao chép, tìm-thay, gộp trùng bằng AI, Undo |
| G12 | `audit_logs` ghi hầu hết thao tác testcase | Giá trị trước/sau, màn xem / lọc / export |
| G3 | Ô "Bug (mã Jira)" trong kết quả (defects) | Tạo bug lên Jira, trạng thái bug, gợi ý retest |

### 15.2 Quyết định **[Quyết định]**

- **G11 token:** dùng lại token cá nhân `cpat_…` (spec 26), thêm scope `testcases:read`,
  `testcases:write`, `results:write`. Token có thể gắn một dự án (`business_project_id`): chỉ dùng
  được trong dự án đó. Bearer token chỉ mở `/api/v1/testcases/*`; quyền vẫn là của chủ token, và
  không vượt scope. OpenAPI ở `/api/v1/docs/openapi.json`, Swagger ở `/api/v1/docs`.
- **G11 webhook:** dùng lại webhook theo dự án (Admin › Webhook), thêm sự kiện `run.closed`,
  `result.failed`, `case.status_changed`, `plan.closed`. Payload ký HMAC-SHA256. Gửi lỗi tự thử
  lại 3 lần, rồi đánh dấu failed; xem và gửi lại được.
- **G1 CLI:** không phát hành được gói npm/pip từ repo này. CLI là một file Python không phụ thuộc
  (`tools/codepulse_cli.py`), kèm ví dụ GitLab CI dùng `python3` hoặc `curl`. Gói npm/pip ghi IDEAS.
- **G3 Jira ghi:** spec 27 chốt Jira chỉ đọc; tài liệu nâng cấp yêu cầu tạo bug. Cho phép ghi **chỉ
  để tạo issue / link**, bật bằng cấu hình riêng (`JIRA_WRITE_ENABLED` + token có quyền tạo issue),
  tắt mặc định; các phần khác của tích hợp Jira vẫn chỉ đọc.
- **G2 code_paths:** dạng mới `{repo}:{glob}`; dạng cũ không có `repo:` vẫn hiểu là mọi repo của dự
  án (không phá dữ liệu cũ). Thư mục và bộ có `code_paths` riêng, case con kế thừa.
- **X4 bot comment MR:** dùng cơ chế comment MR đã có của review (`post_mr_comment`).
- **Lịch gửi báo cáo (G4):** gửi qua Telegram / webhook đã có. Email / Slack ghi IDEAS nếu chưa có kênh.

### 15.3 Tiến độ

Ghi ở `PROGRESS.md` theo từng hạng mục.
