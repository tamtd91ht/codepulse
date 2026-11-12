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
  không vượt scope. Swagger ở `/api/v1/testcases/api-docs`, OpenAPI JSON ở
  `/api/v1/testcases/api-docs/openapi.json` (`/api/v1/docs` là API tài liệu dự án nên không dùng).
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
  - `repo` = `path_with_namespace` hoặc phần cuối của nó (không phân biệt hoa thường); phải là repo
    của dự án của bộ (gốc hoặc dùng chung), tên ngắn trùng nhiều repo thì bắt ghi đủ. Chỉ kiểm tra
    mục mới (mục đã lưu vẫn giữ); khôi phục revision không kiểm tra.
  - Vùng code hiệu lực của case = của bộ + chuỗi thư mục (gốc trước) + của case. Case ghi `!X` bỏ
    mục kế thừa đúng bằng `X`; `!glob` (không repo) bỏ glob đó ở mọi repo. Bộ / thư mục không nhận
    `!`. MR ghép theo repo của MR: mục `repo:` của repo khác không khớp.
  - API: `PATCH /testcases/suites/{id}` và `PATCH /testcases/sections/{id}` nhận `code_paths`;
    `GET /testcases/repos?business_project_id=` (gợi ý repo); `GET /testcases/issue-docs?key=`
    (tài liệu nhắc mã Jira); `GET /testcases/traceability[/export]?business_project_id=&milestone_id=
    &sprint=&label=&q=&days=&uncovered=&limit=&offset=` (Issue chưa có epic/sprint riêng: `sprint` =
    sprint / version đồng bộ ở `issues.milestone`, epic lọc theo nhãn). Ma trận phân trang theo
    issue (`limit` mặc định 200, tối đa 500) và trả `total`; `issues` / `covered` đếm mọi issue khớp
    bộ lọc (không giới hạn số case); kết quả gần nhất của case = lượt test có `tested_at` mới nhất
    (chưa test thì của run mới nhất). Export CSV đi hết các trang (tối đa 5 000 issue), ô bắt đầu
    bằng `= + - @`, tab, CR được thêm `'`. AI `kind: "links"` (`suite_id`, `section_id?`) chỉ gửi
    repo của dự án của lượt AI có `allow_external_llm` khác false, issue Jira và tiêu đề tài liệu
    của dự án đó; và `POST /testcases/ai/{id}/apply-links {items: [{id, jira_keys, code_paths}]}`
    (cộng thêm vào case qua `service.change`, mỗi case một revision `via=ai`); `generate` nhận
    `auto_docs: true` để tự thêm tài liệu nhắc mã Jira; mã Jira mà tài liệu nguồn nhắc (và có trong
    dự án) được gợi ý cho đề xuất.
  - "% issue Jira 90 ngày có testcase" tính trực tiếp mỗi lần gọi `/testcases/stats` (không cache).
- **X4 bot comment MR:** dùng cơ chế comment MR đã có của review (`post_mr_comment`).
- **Lịch gửi báo cáo (G4):** gửi qua Telegram / webhook đã có. Email / Slack ghi IDEAS nếu chưa có kênh.

### 15.3 Tiến độ

Ghi ở `PROGRESS.md` theo từng hạng mục.

### 15.4 G1 – quyết định **[Quyết định]**

Kết quả automation từ CI (`app/testcases/ci.py`, `app/api/routers/test_ci.py`, CLI `tools/codepulse_cli.py`).

- **API:** `POST /testcases/runs/import-results` (multipart: `file` + `business_project_id`,
  `suite_id?`, `run_name?`, `run_id?`, `milestone_id?`, `config_ids?` ("1,2"), `close_run?`,
  `mr_iid?`, `commit_sha?`, `branch?`, `project?` (đường dẫn repo GitLab, `$CI_PROJECT_PATH`),
  `auto_create?`) → 202 `{import_id, status}`; xử lý nền (RQ, hàng `default`).
  `GET /testcases/ci-imports/{id}` → tiến độ `{status, total, done, mapped, created_cases, skipped,
  unmapped[], run_id, run_key, error, ...}`; `GET /testcases/ci-imports?business_project_id=` → 20
  lượt gần nhất. Tối đa 20 MB, 5 000 test / báo cáo, 5 lượt chưa xong / dự án (429).
- **Quyền:** có `run_id` (run đang mở của dự án) → `tests.run` (hoặc `tests.manage`); không có →
  tạo run mới, cần `tests.manage`; `auto_create` cần thêm `tests.edit` và `suite_id`. Token chỉ có
  scope `results:write` gọi được endpoint này và `POST /testcases/attachments` (đính kèm từ CI,
  tối đa 2 GB / dự án, 60 file / phút / người; file không gắn kết quả nào bị xoá sau 24 giờ) (G11).
- **Định dạng** nhận theo nội dung (không theo tên file): JUnit XML (cả xUnit / TestNG kiểu JUnit),
  TestNG `testng-results.xml`, xUnit.net v2 XML, Playwright JSON reporter, Cucumber JSON. XML có
  `DOCTYPE` (nên có thể có ENTITY: "billion laughs", XXE) hoặc không phải UTF-8 bị từ chối (422).
- **Ghép:** (1) mã `C<id>` trong tên test, tag, annotation, property (`@C12`, `[C12]`, `C12:`,
  `test_C12_x`; một test có thể mang nhiều mã) của case còn hiệu lực trong một bộ của dự án;
  (2) `automation_id` = `classname.name` (Playwright: `file > describe.title`; Cucumber:
  `feature.scenario`), trong `suite_id` nếu có; (3) `auto_create`: case `kind=automated`,
  `type=automated`, `status=draft`, `source=import`, tiêu đề = tên test, thư mục
  "Automation > Unmapped"; còn lại liệt kê ở `unmapped`. Nhiều test vào cùng một case: Failed thắng.
- **Skipped:** không ghi kết quả (như TestRail: test vẫn được thêm vào run, giữ "Chưa chạy"), đếm
  riêng ở `skipped`. Failure / error / timedOut → Failed.
- **Không trùng:** một run có một kết quả CI cho mỗi test và mỗi commit. Gửi lại vào cùng run
  (`run_id`) cùng commit → cập nhật kết quả đó (các trạng thái đã thấy giữ ở `ci.attempts`); commit
  khác → thêm kết quả mới (lịch sử).
- **Kết quả CI:** `source=ci`, `elapsed_s` (làm tròn lên), `log` (message), `stack`, `attachments`
  (Playwright attachments, `[[ATTACHMENT|path]]` của JUnit: chỉ lưu đường dẫn, không lưu file),
  `ci = {commit_sha, branch, mr_iid, project, test_name, file, import_id, attempts}`. Failed bắn
  webhook `result.failed` (G11).
- **Flaky** (tính lại cho các case vừa có kết quả): pass và fail trên cùng commit (kể cả các lần
  retry của Playwright), hoặc đổi trạng thái ≥ 3 lần trong 10 kết quả CI gần nhất. Hiện "Flaky" ở
  test trong run và ở trang case.
- **"% tự động"** (`GET /testcases/stats` → `automated_ci`): case `automated` còn hiệu lực có kết
  quả CI trong 30 ngày.
- **Cần test lại của MR:** case có kết quả CI gửi kèm `mr_iid` = IID của MR (và `project` trùng repo
  của MR, nếu có gửi) hiện trong `GET /testcases/impact/mr/{id}` với lý do "CI: Failed (R12, abc12345)".
- **Hiệu năng:** nạp case theo mã và theo automation_id một lần, chèn / cập nhật kết quả hàng loạt
  theo lô 500 test (lưu tiến độ sau mỗi lô); 5 000 test (kèm tự tạo 5 000 case) < 30 s (có test).
- **Automation ID** sửa được trong form case (duy nhất trong bộ: trùng → 422; chuyển bộ mà trùng thì
  bỏ automation ID của case được chuyển).

Ví dụ GitLab CI:

```yaml
test:
  script:
    - pytest --junitxml=reports/junit.xml || true
    - python3 tools/codepulse_cli.py results upload --url "$CODEPULSE_URL"
        --token "$CODEPULSE_TOKEN" --project 8 --suite 3 --file reports/junit.xml
        --mr "$CI_MERGE_REQUEST_IID" --commit "$CI_COMMIT_SHA"
        --branch "$CI_COMMIT_REF_NAME" --repo "$CI_PROJECT_PATH" --wait
  artifacts:
    when: always
    reports:
      junit: reports/junit.xml
```

Không có Python: `curl -H "Authorization: Bearer $CODEPULSE_TOKEN" -F business_project_id=8
-F suite_id=3 -F commit_sha=$CI_COMMIT_SHA -F file=@reports/junit.xml
$CODEPULSE_URL/api/v1/testcases/runs/import-results`.

### 15.5 G3 – quyết định **[Quyết định]**

- `POST /testcases/tests/{id}/bug` tạo issue Jira: tiêu đề `[C12] <tiêu đề case> – Failed`, mô tả
  (Jira wiki) gồm bảng bước với kết quả mong đợi / thực tế, bình luận, log, môi trường (run, cấu hình,
  phiên bản, nhánh / commit / MR của CI), đính kèm và link về CodePulse. Mã bug lưu vào `defects` của
  kết quả được chọn, hoặc của kết quả failed mới nhất.
- `POST /testcases/tests/{id}/bug/link` gắn bug có sẵn (kiểm tra mã; nếu gọi được Jira thì kiểm tra
  issue có tồn tại). Run đã đóng: tạo / gắn bug trả 409, như ghi kết quả.
- Ghi lên Jira bật bằng `JIRA_WRITE_ENABLED` (mặc định tắt) hoặc công tắc trên Admin › Jira ›
  Kết nối (giá trị lưu trên web thắng biến môi trường). Người được giao gửi dạng `{"name": …}`
  (Jira Server / Data Center).
- Trạng thái bug: đọc bảng issue đã đồng bộ trước, issue chưa đồng bộ thì hỏi Jira trực tiếp và nhớ
  5 phút trong tiến trình. "Cần retest" (test của run đang mở, kết quả mới nhất failed, mọi bug đã
  Done) chỉ dựa trên dữ liệu đã đồng bộ / đã nhớ: liệt kê test không bao giờ gọi Jira.
- Cài đặt dự án (`GET/PUT /testcases/project-settings`, đọc `tests.view`, sửa `tests.manage`): chỉ
  người được giao mới ghi kết quả (G6), bắt buộc duyệt testcase (G7), nơi tạo bug Jira (project, loại
  issue, component, nhãn).

### 15.6 G6 – quyết định **[Quyết định]**

Code: `app/testcases/statuses.py`, `app/api/routers/test_status.py`, `components/TestRuns.tsx`.

- **Trạng thái:** có sẵn Passed, Failed, Blocked, Retest, Skipped (và Untested). "Xong" (không cộng
  vào thời gian còn lại): Passed, Failed, Blocked, Skipped; Retest và Untested chưa xong. Trạng thái
  tuỳ biến theo dự án (`tests.manage`, tối đa 20): mã slug tự sinh từ tên (không trùng mã có sẵn,
  `total`), màu theo token `good|bad|warn|info|muted|primary`, cờ "xong", thứ tự (sau các trạng thái
  có sẵn). Đã dùng thì không xoá (409), chỉ lưu trữ: kết quả cũ vẫn hiện tên, kết quả mới không chọn
  được (422). API: `GET /testcases/statuses?business_project_id=` → `{items, can_edit}`, `POST`,
  `PATCH|DELETE /testcases/statuses/{id}`. CI chỉ ghi trạng thái có sẵn.
- **Counts** giữ `untested, passed, failed, blocked, retest, total`, thêm `skipped` và một khoá cho
  mỗi trạng thái tuỳ biến đang có test.
- **Bước → kết quả:** có bước Failed thì kết quả Failed (kể cả khi chọn Passed; chọn Blocked / Retest /
  tuỳ biến thì giữ), `failed_step` = bước Failed đầu tiên; không có Failed mà có Blocked thì tương tự
  với Blocked; mọi bước Passed (hoặc Skipped) và không chọn trạng thái thì Passed.
- **Chỉ người được giao** (`assigned_only` ở cài đặt dự án): chỉ người được giao test / run, hoặc
  người có `tests.manage`, ghi kết quả (cả hàng loạt và tải đính kèm); `RunOut` có `assigned_only`,
  `can_record_all`.
- **Thời gian:** `RunOut.elapsed_total_s` = tổng `elapsed_s` các kết quả của run;
  `remaining_estimate_s` = tổng ước tính của test chưa xong + (test chưa xong không có ước tính) ×
  elapsed trung bình của các kết quả có elapsed trong run. `TestOut.elapsed_s` / `failed_step` lấy từ
  kết quả có trạng thái mới nhất.
- **Đính kèm:** `POST /testcases/attachments?business_project_id=&filename=` (thân là bytes, ≤ 10 MB;
  ảnh PNG / JPEG / WebP / GIF theo chữ ký; .pdf / .zip theo đuôi + chữ ký; .txt / .log theo đuôi,
  UTF-8, không có byte 0), trùng nội dung thì dùng lại; `GET /testcases/attachments/{id}` (`tests.view`
  trên dự án của tệp; `nosniff`, CSP sandbox, tệp không phải ảnh tải xuống). Kết quả nhận
  `attachment_ids` (≤ 20) và lưu `[{id, name, mime, size}]`.
- **Phím tắt** trong ngăn test (khi không ở ô nhập): P / F / B / R / S chọn trạng thái, J / → và K / ←
  sang test sau / trước, `?` hiện danh sách; Ctrl+Enter lưu và sang test sau (trong form).

### 15.7 G4 – quyết định **[Quyết định]**

Báo cáo (`app/testcases/reports.py`, `app/api/routers/test_reports.py`, tab "Báo cáo" ở
`components/TestcaseReports.tsx`). Tính trực tiếp mỗi lần gọi (không cache), vài truy vấn gộp.

- **API:** `GET /testcases/reports/{kind}` với kind = `summary | compare | coverage | defects |
  productivity | automation | readiness`; lọc `business_project_id` (bắt buộc), `suite_id`,
  `milestone_id` (run của milestone hoặc của plan thuộc milestone), `plan_id`, `config_id`,
  `branch` (`ci.branch` của kết quả), `from` / `to` (ngày theo REPORT_TZ, gồm cả hai đầu);
  `compare` nhận `run_ids` (2–10), `readiness` cần `milestone_id`, `min_pass_rate` (mặc định 95).
  `GET /testcases/reports/{kind}/export` → Excel; `GET /testcases/reports/options` → giá trị cho bộ lọc.
- **Hai tập số:** *test* = TestRunTest của các run khớp và trạng thái hiện tại (có kỳ: run tạo trước
  cuối kỳ và chưa đóng trước đầu kỳ; có nhánh: test có kết quả CI trên nhánh đó); *kết quả* =
  TestResult có trạng thái ghi trong kỳ (bình luận không tính). Trạng thái đếm chung `{status: n}`
  (gồm trạng thái tuỳ biến), "đã xong" = khác các trạng thái chưa xong (Untested, Retest, trạng thái tuỳ
  biến không tính là xong), cùng quy tắc với thời gian còn lại của run.
- **So sánh run:** run sắp theo thời gian tạo; "Hồi quy" khi passed → failed giữa hai run liên tiếp
  có chạy case đó, "Đã sửa" khi failed → passed.
- **Độ phủ:** issue Jira của dự án (`jira_issue_in`, không tính sub-task) có / chưa có case; theo
  loại issue (Jira chưa đồng bộ epic: epic xem ở Ma trận truy vết theo nhãn); theo thư mục: % case có
  kết quả trong kỳ và % case có kết quả mới nhất Passed; theo milestone.
- **Defect:** mã bug trong `defects` của kết quả; trạng thái lấy từ Issue đã đồng bộ (mở / đóng /
  chưa đồng bộ); "chặn release" = bug mở và test ghi nó vẫn Failed / Blocked.
- **Năng suất** (tín hiệu để kèm cặp, lời lẽ trung tính): case tạo, case được duyệt (một thay đổi
  chuyển case sang approved, theo revision), kết quả ghi tay, thời gian; kết quả CI là một số tổng.
  Người có `tests.manage` trên dự án thấy mọi người; người khác thấy dòng của mình và số tổng (cũng
  áp dụng cho bảng "Theo người ghi" của Tổng kết).
- **Automation:** % tự động theo tuần = case automated còn hiệu lực có kết quả CI trong 30 ngày tính
  đến cuối tuần / case còn hiệu lực đã có lúc đó; danh sách flaky; lượt CI = theo run và
  `ci.import_id`, tổng `elapsed_s`.
- **Sẵn sàng release:** từ test của các run thuộc milestone. No-go khi: chưa có test, % passed <
  ngưỡng, có test ưu tiên Rất cao / Cao đang Failed hoặc chưa chạy, có bug mở. Flaky và bug chưa đồng
  bộ là lưu ý, không chặn. Kèm pass rate theo ngày (dashboard milestone). AC: 5 000 kết quả ≤ 3 s (có
  test).
- **Export:** Excel (openpyxl) mỗi phần một sheet, mọi ô ghi dạng text. **PDF** = trang in của trình
  duyệt (nút "In / Lưu PDF", CSS in ẩn menu, bộ lọc); không dựng PDF ở server. **Link chia sẻ** = URL
  trang với bộ lọc trong query (`?tab=reports&report=…&r_period=…&r_ms=…`), người mở vẫn cần quyền.
- **Lịch gửi:** `GET/POST /testcases/report-schedules`, `PATCH/DELETE /testcases/report-schedules/{id}`,
  `POST …/{id}/send` (gửi ngay), `GET /testcases/report-schedules/webhooks` (webhook dùng được: của dự
  án hoặc chung, không gắn repo); cần `tests.manage`. Loại: summary, readiness (cần milestone),
  coverage, defects, automation; `params`: suite_id, milestone_id, plan_id, config_id, branch, days
  (mặc định 1 ngày / 7 ngày), min_pass_rate. Scheduler (`testcase-reports`, 5 phút) gửi lịch đến giờ
  (REPORT_TZ; hằng ngày, hoặc hằng tuần đúng thứ), kỳ báo cáo kết thúc hôm qua; `last_sent_at` cùng
  ngày → không gửi lại; hàng bị khoá (`FOR UPDATE SKIP LOCKED`) nên hai scheduler không gửi trùng. Gửi
  bằng đường webhook của G11 (`WebhookDelivery` sự kiện `testcase.report`, ký HMAC, thử lại 3 lần;
  Slack / Teams nhận một dòng `text`, JSON nhận cả số liệu). Telegram bot riêng, email, Slack native:
  IDEAS.

### 15.8 G5 – trường tuỳ biến **[Quyết định]**

- Bảng `test_fields` (một dự án): `key` (slug duy nhất trong dự án, tự sinh từ tên, không sửa sau khi
  tạo), `name`, `entity` (`case` | `result`), `type` (`text`, `textarea`, `number`, `date`,
  `dropdown`, `multiselect`, `checkbox`, `user`, `url`), `options` (dropdown / multi-select: danh
  sách nhãn), `required`, `default`, `suite_ids` (rỗng = mọi bộ), `templates` (rỗng = mọi template;
  `text` / `steps`), `position`, `archived_at`. Xoá = lưu trữ (giá trị giữ nguyên, ẩn khỏi form, lọc,
  cột); khôi phục được. Đọc `tests.view`, sửa `tests.manage`.
- Giá trị lưu ở `test_cases.custom` / `test_results.custom` (JSONB `{key: value}`): text ≤ 2 000 ký tự,
  textarea ≤ 20 000, number là số, date `YYYY-MM-DD`, dropdown một nhãn trong `options`,
  multi-select danh sách nhãn, checkbox bool, user là id người dùng đang hoạt động, url http(s).
  Khoá lạ bị bỏ; trường đã lưu trữ giữ giá trị cũ.
- Bắt buộc: chặn lưu ở API (422, nêu tên trường) khi tạo / sửa case (trường áp dụng cho bộ và template
  của case) và khi ghi kết quả có trạng thái (trường `result`). Nhập Sheet, AI, CI không bị chặn:
  thiếu thì điền giá trị mặc định nếu có. Giá trị mặc định điền khi tạo.
- `custom` của case vào revision (khôi phục được) và vào tìm kiếm (text, dropdown, multi-select).
- Lọc danh sách case: `f.<key>=<giá trị>` (dropdown / multi-select / checkbox / user: bằng; text:
  chứa). Cột: chọn hiện trên bảng testcase (lưu ở trình duyệt). Nhập Sheet: cột có tiêu đề trùng tên
  trường được đoán tự động. Xuất Excel: một cột mỗi trường đang dùng. API token: đọc / ghi như trường
  thường. Báo cáo tổng kết: lọc theo một trường dropdown của case (`field`, `value`).

### 15.9 G7 – vai trò và duyệt **[Quyết định]**

- Hai quyền mới: `tests.delete` (xoá testcase, bộ, thư mục) và `tests.approve` (duyệt testcase).
  `tests.edit` không còn gồm xoá. Migration chép `tests.edit` → `tests.delete` và `tests.manage` →
  `tests.approve` cho mọi bộ quyền đang có, nên không ai mất quyền.
- Năm bộ quyền mẫu (không phải builtin, admin sửa / gán như bộ thường; phạm vi "mọi dự án xem được",
  tức các dự án người đó là thành viên; vai trò khác nhau giữa các dự án thì tạo bộ riêng): **Testcase – Admin dự án** (mọi quyền tests.*), **Lead** (view, edit, run,
  delete, approve, manage), **Tester** (view, edit, run), **Developer** (view, run: ghi kết quả test
  được giao, tạo bug), **Guest** (view). Ma trận hiện trên trang Hướng dẫn.
- Duyệt: trạng thái case thêm `review` (Chờ duyệt). Khi `require_approval` của dự án bật: đặt
  `approved` (sửa, nhập, hàng loạt, khôi phục, AI) cần `tests.approve` (403); người sửa gửi duyệt
  (`draft` → `review`); `POST /testcases/{id}/approve` và `/reject` (`comment` bắt buộc khi từ chối,
  về `draft`) cần `tests.approve`; sửa nội dung một case `approved` đưa nó về `review`. Khi tắt: như
  cũ. Mỗi lần duyệt / từ chối ghi `test_case_reviews` (người, quyết định, bình luận, revision) và hiện
  trong lịch sử case. Hàng đợi "Chờ duyệt" là bộ lọc trạng thái `review`.

### 15.10 G10 – thao tác hàng loạt **[Quyết định]**

- `POST /testcases/bulk` thêm hành động: `tags` (thêm / bỏ), `kind`, `estimate`, `jira` (thêm mã),
  `custom` (đặt một trường), `copy` (sang bộ / thư mục khác, mã mới, trạng thái `draft`). Mọi hành
  động sửa ghi revision `bulk` và trả `batch_id`.
- `POST /testcases/replace` (`suite_id`, `find`, `replace`, `fields` ⊂ title / preconditions / steps /
  expected / notes / test_data, `case_sensitive`, `regex` (giới hạn 200 ký tự, timeout), `section_id?`,
  `preview`): xem trước trả từng case khớp với đoạn trước / sau (±80 ký tự quanh chỗ đổi; tối đa 500
  case, 10 chỗ đổi / case, 1 MB, quá thì `truncated`); áp dụng thay toàn bộ, ghi revision `bulk`, trả
  `batch_id`. Regex chạy bằng gói `regex` có timeout (tổng 2 s → 422), từ chối lặp lồng nhau, lặp liền
  nhau cùng một mẫu (`a*a*`, `.*.*`), tham chiếu ngược, cú pháp mờ.
- Undo: `test_bulk_ops` lưu mỗi lần sửa hàng loạt (người, dự án, loại, case_ids, revision trước, case
  tạo ra). `POST /testcases/bulk/{batch_id}/undo` (chính người làm hoặc `tests.manage`, trong 24 giờ):
  khôi phục revision trước cho case chưa bị sửa sau đó (case đã đổi thì bỏ qua và báo), xoá case đã
  copy chưa có kết quả. Xoá hàng loạt không undo được (vẫn hỏi xác nhận).
- AI phát hiện trùng: loại AI `duplicates` cho một bộ: nhóm case trùng / gần trùng kèm lý do và case
  nên giữ; áp dụng = chuyển case thừa sang `obsolete` (revision `ai`, có undo).
- 200 case một lần ≤ 5 s: một truy vấn khoá, revision ghi theo lô.

### 15.11 API giai đoạn 3 (G5, G7, G10)

Trường tuỳ biến (`app/api/routers/test_fields.py`, `app/testcases/fields.py`):

- `GET /testcases/fields?business_project_id=&entity=case|result&archived=false` →
  `[{id, key, name, entity, type, options, required, default, suite_ids, templates, position,
  archived_at}]` theo `position`.
- `POST /testcases/fields` `{business_project_id, entity, name, type, options?, required?, default?,
  suite_ids?, templates?}` → 201; `PATCH /testcases/fields/{id}` (mọi trường trừ `key`, `entity`,
  `type`); `DELETE /testcases/fields/{id}` → lưu trữ (204); `POST /testcases/fields/{id}/restore`;
  `PUT /testcases/fields/order` `{business_project_id, entity, ids}`.
- Case: `custom: {key: value}` trong body tạo / sửa và trong `CaseOut` / `CaseDetail`; danh sách
  `GET /testcases?...&f.<key>=<giá trị>`. Kết quả: `custom` trong body ghi kết quả (một / hàng loạt)
  và trong kết quả trả về.

Duyệt (`app/api/routers/test_approval.py`, `app/testcases/approval.py`):

- `POST /testcases/{id}/submit` (draft → review, `tests.edit`), `POST /testcases/{id}/approve`
  `{comment?}`, `POST /testcases/{id}/reject` `{comment}` (`tests.approve`) → `CaseDetail`.
  `GET /testcases/{id}/reviews` → `[{id, decision, comment, revision, user_id, user_name,
  created_at}]` mới nhất trước.
- `CaseDetail` thêm `can_delete`, `can_approve`, `approval_on`.

Hàng loạt (`app/api/routers/test_bulk.py`, `app/testcases/bulk.py`):

- `POST /testcases/bulk` `{ids, action, ...}`; hành động cũ: `delete`, `move` (`suite_id`), `section`
  (`section_id`), `milestone` (`milestone_id`), `status`, `priority`, `type`; mới: `tags`
  (`tags_add`, `tags_remove`), `kind`, `estimate` ("1h 30m"), `jira` (`jira_add`), `custom`
  (`field_key`, `value`), `copy` (`suite_id`, `section_id?`). → `{changed, batch_id, skipped}`.
- `POST /testcases/replace` `{suite_id, section_id?, find, replace, fields, case_sensitive, regex,
  preview}` → xem trước `{total, truncated, matches: [{case_id, key, title, changes: [{field,
  before, after}]}]}`; áp dụng `{changed, batch_id}`.
- `GET /testcases/bulk-ops?business_project_id=` → 20 lần gần nhất của mình `[{id, kind, summary,
  count, created_at, undone_at, can_undo}]`; `POST /testcases/bulk-ops/{id}/undo` → `{restored,
  deleted, skipped: [{case_id, key, reason}]}`.
- AI trùng: `POST /testcases/ai` `{kind: "duplicates", suite_id}`; kết quả `groups: [{keep,
  duplicates: [case_id], reason}]`; `POST /testcases/ai/{id}/apply-duplicates` `{groups: [{keep,
  duplicates}]}` → `{changed, batch_id}`.

### 15.12 G8 – BDD và Exploratory **[Quyết định]**

- Template thêm `bdd` và `exploratory` (cạnh `text`, `steps`).
- **BDD:** case lưu kịch bản ở `bdd` (Gherkin tiếng Anh: `Background`, `Scenario`, `Scenario Outline`
  + `Examples`, `Given / When / Then / And / But`, bảng dữ liệu `| ... |`, docstring `"""`, tag `@...`).
  Bộ phân tích tự viết (`app/testcases/gherkin.py`, không thêm thư viện), lỗi cú pháp → 422 nêu dòng.
  Khi chạy, mỗi dòng bước là một bước có kết quả riêng (như template Steps). `Scenario Outline`:
  bảng `Examples` thành bảng tham số của case (G13), `<biến>` được thay theo từng dòng.
- **Nhập .feature:** `POST /testcases/import/feature` (multipart `file` (≤ 2 MB, ≤ 50 000 dòng, ≤ 1 000
  scenario; nhiều file thì gửi nhiều lần), `suite_id`, `section_id?`): `Feature` thành thư mục (tạo nếu chưa có, theo tên), mỗi
  `Scenario` / `Scenario Outline` một case BDD (tiêu đề = tên scenario, tag `@C12` → cập nhật case C12
  nếu thuộc bộ, tag khác → nhãn, `Background` chép vào đầu kịch bản). Trả `{created, updated,
  sections, errors}`.
- **Xuất .feature:** `GET /testcases/export/feature?suite_id=&section_id?` → một file `.feature` mỗi
  thư mục có case BDD (zip nếu nhiều hơn một), mỗi scenario mang tag `@C<id>`; nhập lại file vừa xuất
  cho cùng kết quả (tương đương ngữ nghĩa).
- **AI chuyển đổi:** loại AI `convert` (`suite_id`, `case_ids`, `direction`: `to_bdd` | `to_steps`),
  đề xuất từng case, áp dụng ghi revision `ai` (có undo như G10).
- **Exploratory:** case có `charter` (sứ mệnh) và ước tính (timebox). Trong run: `POST
  /testcases/tests/{id}/session` `{action: start | pause | resume}` và `POST
  /testcases/tests/{id}/session/notes` `{kind: note | bug | question | idea, text}` (cần quyền ghi kết
  quả của test); phiên lưu ở `test_run_tests.session` `{started_at, elapsed_s, running, notes:[{at,
  offset_s, kind, text, user_id}]}`; ghi kết quả thì ghi chú phiên chép vào kết quả và thời gian chạy
  = thời gian phiên nếu không nhập.

### 15.13 G9 – Baseline **[Quyết định]**

- Bảng `test_baselines` (bộ, tên, mô tả, milestone?, người tạo, số case) và `test_baseline_cases`
  (case_id (null nếu case đã xoá), mã, đường dẫn thư mục, `snapshot` đầy đủ: nội dung đã mở bước dùng
  chung, tham số, trường tuỳ biến, vị trí). Baseline không sửa được, chỉ tạo / xoá.
- `POST /testcases/baselines` `{suite_id, name, description?, milestone_id?, section_ids?}`
  (`tests.manage`; case `obsolete` không vào); `GET /testcases/baselines?business_project_id=&suite_id=`;
  `GET /testcases/baselines/{id}` (kèm case); `DELETE /testcases/baselines/{id}` (`tests.manage`; run
  đã tạo từ baseline giữ nguyên nội dung).
- So sánh: `GET /testcases/baselines/compare?a=<id>&b=<id | current>&offset=&limit=` → `{added, removed,
  changed: [{key, title, fields: [{field, before, after}]}], same, total_added, total_removed,
  total_changed, offset, limit}`; mỗi loại phân trang (mặc định 200, tối đa 500), giá trị dài quá
  2 000 ký tự bị cắt.
- Run từ baseline: `POST /testcases/runs` thêm `baseline_id` (chọn case trong baseline như chọn trong
  bộ: tất cả, thư mục, bộ lọc, danh sách); test lấy nội dung từ baseline và **không** cập nhật theo
  case hiện tại kể cả khi run đang mở (`test_runs.baseline_id`).

### 15.14 G12 – Audit log **[Quyết định]**

- Ghi tự động bằng listener `before_flush` của SQLAlchemy (`app/audit_trail.py`): mỗi lần tạo / sửa /
  xoá suite, thư mục, case, bước dùng chung, run, plan, milestone, cấu hình, trường tuỳ biến, trạng
  thái, cài đặt dự án, baseline, bộ quyền và mục quyền, gán bộ quyền, token API → một dòng audit
  `{entity, id, op, before, after}` chỉ gồm các cột đổi (bỏ `search`, `updated_at`, giá trị dài cắt
  2 000 ký tự), kèm `business_project_id` (cột mới, có chỉ mục) và người làm (biến ngữ cảnh lấy từ
  phiên đăng nhập / token; job nền là null). Kết quả test không ghi (đã có lịch sử kết quả).
- Xem: `GET /testcases/audit?business_project_id=&actor_id=&entity=&action=&target=&q=&from=&to=
  &before_id=&limit=` (`tests.manage` của dự án; admin xem mọi dự án và các dòng không thuộc dự án như
  bộ quyền, token). Xuất: `GET /testcases/audit/export?...&format=xlsx|csv` (≤ 50 000 dòng).
- Giữ ≥ 1 năm: audit log không bị dọn theo `RETENTION_DAYS` (có test).

### 15.15 G13 – Tham số hoá **[Quyết định]**

- Case có `params` `{columns: [tên], rows: [[giá trị]]}` (≤ 20 cột, ≤ 200 dòng, giá trị ≤ 500 ký tự;
  tên cột chữ / số / `_`). Tiêu đề, tiền điều kiện, bước, kết quả mong đợi, dữ liệu test, kịch bản BDD
  dùng `{{tên}}` (BDD dùng thêm `<tên>`); biến không có cột → 422 khi lưu.
- Khi đưa vào run, mỗi dòng tham số là một test (`test_run_tests.param_row` = số dòng từ 1, `params` =
  giá trị); tiêu đề test thêm `[cột=giá trị, ...]`; nội dung hiển thị đã thay giá trị. Ràng buộc duy
  nhất của test đổi thành (run, case, dòng tham số). Case không có tham số: như cũ (`param_row` null).
- CI: `C12[3]` (hoặc `C12_3` trong tên test) → dòng 3; `C12` không chỉ dòng → mọi dòng của case nhận
  kết quả đó.
- Báo cáo đếm theo test (mỗi dòng một test); độ phủ theo case.
- Test chỉ giữ tên cột (thứ tự) và giá trị dòng của nó, không chép cả bảng; baseline giữ cả bảng (một
  bản mỗi case). Run đã có test của case khi case chưa có tham số: thêm lại không sinh các dòng.
  Giới hạn số test của run tính cả số dòng, kiểm tra trước khi dựng.

### 15.16 API giai đoạn 4 (G8, G9, G12, G13)

- Case (tạo / sửa / `CaseOut` / `CaseDetail` / revision / nhập Sheet / xuất): thêm `bdd`, `charter`,
  `params` `{columns, rows}`; `template` nhận `bdd`, `exploratory`. Nội dung test (`TestDetail.content`)
  là nội dung đã thay giá trị tham số; `TestOut` / `TestDetail` thêm `param_row`, `params`, `session`;
  `RunOut` thêm `baseline_id`, `baseline_name`.
- Tiện ích: `POST /testcases/bdd/check` `{text}` → `{ok, errors: [{line, message}], scenarios,
  outline_columns}` (soạn thảo kiểm tra trực tiếp).
- BDD: `POST /testcases/import/feature` (multipart `file`, `suite_id`, `section_id?`) →
  `{created, updated, sections, errors}`; `GET /testcases/export/feature?suite_id=&section_id=` →
  `.feature` hoặc `.zip`. AI: `POST /testcases/ai` `{kind: "convert", suite_id, case_ids, direction}`,
  kết quả `proposals: [{case_id, template, bdd?, steps_list?, preconditions?}]`, áp dụng `POST
  /testcases/ai/{id}/apply-convert` `{case_ids}` → `{changed, batch_id}`.
- Exploratory: `POST /testcases/tests/{id}/session` `{action}` và `POST
  /testcases/tests/{id}/session/notes` `{kind, text}` → `session`.
- Baseline: như §15.13; `BaselineOut` `{id, business_project_id, suite_id, suite_name, name,
  description, milestone_id, milestone_name, case_count, created_by, created_by_name, created_at}`;
  chi tiết thêm `cases: [{id, case_id, key, title, section, priority, type}]`; tạo run: `POST
  /testcases/runs` thêm `baseline_id` (kèm `section_ids` / `case_ids` / bộ lọc như run từ bộ).
- Audit: như §15.14; dòng `{id, at, actor_id, actor_name, action, entity, entity_id, target,
  business_project_id, changes: [{field, before, after}], details, request_id}`, phân trang bằng
  `before_id`; `GET /testcases/audit/facets?business_project_id=` → `{actions, entities, actors}`.
