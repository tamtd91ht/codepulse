# 27 – Jira làm nguồn task cho DPI

> Người dùng chốt ngày 2026-11-05. Bổ sung spec 25 (DPI): nguồn task chuyển từ GitLab Issues sang
> **Jira Data Center** nội bộ. Một Jira chung cho toàn công ty, mỗi đội là một dự án Jira con.
> CodePulse **chỉ đọc** Jira, không ghi gì lên đó.

## 1. Phạm vi

- **[Đã chốt]** Jira Data Center (self-hosted). Không hỗ trợ Jira Cloud ở đợt này.
- **[Đã chốt]** Jira là nguồn task của DPI (A1, A2, C2, C3, R4, lỗi production B2) và thay
  GitLab Issues. Dữ liệu GitLab Issues cũ không xoá.
- **[Đã chốt]** Chỉ đọc. Không tạo ticket, không đổi trạng thái, không comment.
- Ngoài phạm vi: tạo ticket từ finding, đồng bộ hai chiều, webhook Jira (ghi `IDEAS.md`).

## 2. Kết nối

- **[Quyết định]** Biến môi trường:
  - `JIRA_BASE_URL`: ví dụ `https://jira.company.vn`. Để trống thì tắt tích hợp.
  - `JIRA_TOKEN`: PAT của **tài khoản dịch vụ chỉ đọc** (quyền Browse projects trên các dự án
    cần lấy).
  - `JIRA_CA_BUNDLE`: CA nội bộ (tuỳ chọn).
  - `JIRA_SYNC_INTERVAL_MIN`: mặc định 15.
  - `JIRA_BACKFILL_DAYS`: lần sync đầu lấy lùi bao nhiêu ngày, mặc định 180.
  - `JIRA_TIMEOUT_S`: mặc định 20.
- Header `Authorization: Bearer <PAT>`. Mọi lần gọi có timeout, retry 3 lần với 5xx và lỗi
  mạng, tôn trọng `Retry-After` của 429 (trần 30 giây), và nghỉ tối thiểu 100 ms giữa hai lần
  gọi. Token không bao giờ ghi vào log hay trả về API.
- Endpoint dùng:
  - `GET /rest/api/2/serverInfo`: kiểm tra kết nối.
  - `GET /rest/api/2/field`: tìm id custom field theo tên (Story Points, Sprint, Flagged).
  - `GET /rest/api/2/project`: danh sách dự án cho Admin chọn.
  - `POST /rest/api/2/search`: lấy issue theo JQL, phân trang `startAt`/`maxResults=100`.

## 3. Đồng bộ

- **[Quyết định]** Polling theo JQL: `project = "<KEY>" AND issuetype != Epic AND
  updated >= -<n>m ORDER BY updated DESC`.
  - Mới nhất trước: issue được cập nhật trong lúc đang phân trang `startAt` nhảy lên đầu, nên
    trang sau chỉ lặp lại một issue (vô hại, upsert theo `id`) chứ không bỏ sót như `ASC`.
  - `n` là số phút kể từ mốc đồng bộ trước của dự án, cộng 5 phút chồng lấn.
  - Dùng thời gian tương đối để không phụ thuộc múi giờ của tài khoản dịch vụ.
  - Lần đầu: `n = JIRA_BACKFILL_DAYS × 1440`.
- Mốc `jira_projects.synced_until` chỉ tiến lên khi cả dự án sync xong. Lỗi thì ghi
  `last_error` và lần sau làm lại.
- Mỗi dự án sync dưới `pg_try_advisory_xact_lock(0x7A, jira_projects.id)`: một lần sync khác
  của dự án đang chạy (scheduler và **Đồng bộ ngay**) thì dự án được bỏ qua (`skipped: busy`,
  không ghi `last_error`).
- Scheduler đưa job `app.jira.sync.sync_all_job` vào hàng đợi mỗi `JIRA_SYNC_INTERVAL_MIN`
  phút. Admin có nút **Đồng bộ ngay**.
- Sync chạy khi đã cấu hình và có dự án bật, **bất kể nguồn task đang chọn**. Nhờ vậy dữ liệu
  có sẵn trước khi chuyển nguồn.
- Issue bị xoá trên Jira không phát hiện được qua search, nên vẫn giữ trong DB (ghi nhận).
- Issue chuyển sang dự án khác thì đổi key. `id` giữ nguyên nên cập nhật đúng dòng.

## 4. Chọn dự án Jira

- **[Đã chốt]** Admin chọn dự án Jira (theo key) trong Admin › DPI › Jira và gán mỗi dự án cho
  một **dự án CodePulse** (`business_projects`) để lọc báo cáo và phân quyền.
- Dự án Jira không gán dự án CodePulse thì chỉ admin thấy trong báo cáo cấp team.
- Bảng `jira_projects`: `key` (unique), `name`, `business_project_id`, `enabled`,
  `synced_until`, `last_synced_at`, `last_error`, `issues_count`.

## 5. Quy đổi trường (mặc định; Admin sửa được)

Cấu hình lưu ở `app_settings["jira_mapping"]` (JSON), xem `app/jira/settings.py`.

| DPI | Lấy từ Jira |
|---|---|
| state | `statusCategory.key == "done"` → `closed`, còn lại `opened` |
| closed_at | `resolutiondate`, không có thì `statuscategorychangedate`, không có nữa thì `updated` (chỉ khi done) |
| type | `issuetype.name` theo bảng: Story / New Feature → feature, Bug → bug, Improvement → refactor, Task → chore, Documentation → docs. Sub-task theo loại của issue cha. Loại khác → chore. |
| complexity | Story Points, xem §6 |
| prod_severity | Issue là lỗi production khi: là Bug có label thuộc `prod_labels` (mặc định `production`, `prod-bug`), hoặc issue type thuộc `prod_issue_types` (mặc định `Incident`). Mức lấy theo Priority: Highest / Blocker → P1, High / Critical → P2, còn lại → P3. |
| due_date | `duedate`, không có thì ngày kết thúc **sprint gần nhất** của issue, không có nữa thì `releaseDate` sớm nhất trong Fix Version |
| milestone | Tên sprint gần nhất, không có thì tên Fix Version đầu tiên |
| blocked | Đang Flagged (Impediment) → thêm label `blocked::impediment`. Status thuộc `blocked_statuses` (mặc định: tên chứa "Blocked") → thêm label `blocked::<tên status>`. Báo cáo R4 dùng lại cơ chế `blocked::<lý do>` sẵn có. |
| labels | `labels` của Jira, cộng label `blocked::…` như trên |
| time | `timeoriginalestimate`, `timespent` (giây) |
| assignee | `assignee` (Jira DC chỉ có một người được giao) |
| web_url | `<JIRA_BASE_URL>/browse/<KEY>` |

- **[Quyết định]** Không lấy Epic (JQL loại `issuetype != Epic`).

## 6. Story Points → độ phức tạp

- **[Đã chốt]** Trường Story Points, tên mặc định `Story Points`. Admin đổi được tên (ví dụ
  "Story point estimate"), hệ thống tự tìm custom field theo tên.
- Làm tròn lên mức gần nhất:

  | Story Points | Mức | Điểm |
  |---|---|---|
  | (0, 1] | XS | 1 |
  | (1, 2] | S | 2 |
  | (2, 3] | M | 3 |
  | (3, 5] | L | 5 |
  | > 5 | XL | 8 (trần) |

- Giá trị trống hoặc 0 thì không có độ phức tạp, và độ tin cậy A1 hạ như hiện tại.
- Story Points > 8 vẫn tính 8. Trang Sẵn sàng dữ liệu đếm số issue này với lời nhắc "nên tách
  issue".
- **[Đã chốt]** Sub-task có Story Points thì tính như issue thường. Sub-task không có Story
  Points thì **không tính trong DPI** (A1, A2, C2), vì điểm nằm ở story cha.

## 7. Người

- **[Quyết định]** Tài khoản Jira là `accounts.kind = 'jira_user'`, `external_key` = `key` của
  user Jira DC (ổn định khi đổi username), `display_name` = displayName.
- `issues.assignee_account_ids` chứa id tài khoản Jira, nên DPI tìm issue theo các tài khoản
  của người đó như với GitLab.
- Tự nối khi sync:
  - `emailAddress` của Jira trùng `users.primary_email`;
  - không được thì thử `<name>@<miền trong ALLOWED_EMAIL_DOMAINS>`.
  - Không bao giờ ghi đè liên kết sẵn có.
- Tài khoản lưu `accounts.email` (emailAddress) và `accounts.login` (name) của Jira. Cuối mỗi
  lần sync, mọi tài khoản `jira_user` chưa nối được thử nối lại theo hai giá trị này (người
  được thêm sau vẫn được nối, không cần issue của họ được cập nhật lại). Migration
  `f1b3d5e7a9c2`.
- Tài khoản chưa nối hiện ở danh sách "tài khoản chưa gán" của Admin và trên trang Sẵn sàng dữ
  liệu (% assignee đã nối).

## 8. Liên kết issue ↔ MR (C3, defect)

- **[Quyết định]** Collector GitLab tìm key dạng `[A-Z][A-Z0-9_]{1,9}-\d+` trong tiêu đề, mô tả
  và nhánh nguồn của MR, rồi lưu vào `merge_requests.jira_keys` (JSONB).
  - Sau số có thể là `_` (`feature/ABC-123_login`). Tên nhánh khớp không phân biệt hoa thường
    (`feature/abc-123-login`) và key được viết hoa.
  - MR convert trước khi có tính năng này (`jira_keys` NULL): job một lần
    `app.jira.backfill.backfill_keys_once_job` (scheduler đưa vào hàng đợi lúc khởi động,
    `app_settings["jira_keys_backfill_v1"]` ghi đã chạy) lấy lại key từ payload MR mới nhất
    trong `raw_gitlab` (hết raw thì từ tiêu đề và nhánh đã lưu), theo lô, rồi nối MR.
  - Không lưu mô tả (spec 25 §4.1).
  - Chỉ key thuộc dự án Jira **đã đăng ký** mới được nối, để loại `UTF-8`, `SHA-256`…
- Nguồn liên kết:
  - key có trong **tiêu đề hoặc tên nhánh** → `closes`. Quy ước: nhánh `feature/ABC-123-…` là
    việc của issue đó.
  - key chỉ có trong mô tả → `mention`.
- Nối ở hai chiều:
  - GitLab convert nối MR vừa cập nhật với issue Jira đã có;
  - Jira sync nối issue vừa cập nhật với MR đã có `jira_keys` chứa key.
- Quy ước đặt tên nhánh, commit và mẫu MR được ghi trong Hướng dẫn.

## 9. Nguồn task

- **[Quyết định]** `app_settings["dpi_task_source"]` = `gitlab` | `jira`.
  - Mặc định là `jira` khi đã có `JIRA_BASE_URL`, còn lại `gitlab`.
  - Admin đổi trong Admin › DPI › Jira.
- DPI chỉ đọc issue của nguồn đang chọn (`issues.source`), nên không tính trùng.
- Khi nguồn là `jira`, collector GitLab **ngừng lấy issue**. Dữ liệu cũ vẫn giữ, chuyển lại
  `gitlab` thì lấy tiếp.
  - Chuyển `jira` → `gitlab`: cửa sổ sync của các repo đã trôi qua mà không lấy issue, nên
    `PUT /dpi/jira/source` đưa vào hàng đợi một lần sync **đầy đủ** (`full=True`) cho mọi repo
    đang theo dõi.
- Báo cáo R4, lỗi production và trang Sẵn sàng dữ liệu đều theo nguồn đang chọn.

## 10. Lưu trữ

Migration `e8a0c2d4f6b9` (và `f1b3d5e7a9c2`: `accounts.email`, `accounts.login`, §7).
Downgrade `e8a0c2d4f6b9` xoá issue Jira (kéo theo `prod_defects`), nên từ chối khi còn lỗi
production của issue Jira có `rca_status` khác `pending`.

- `issues`:
  - `project_id` cho phép NULL;
  - thêm `source` (`gitlab` | `jira`, mặc định `gitlab`), `jira_project_id`, `key` (unique khi
    có), `parent_key`, `story_points` (numeric);
  - check: issue GitLab phải có `project_id`, issue Jira phải có `jira_project_id` và `key`.
- `id` của issue Jira = `10^15 + id Jira`, xem `app.jira.settings.JIRA_ID_OFFSET`. Cách này
  không trùng id issue GitLab và upsert theo `id` như cũ.
- `iid` của issue Jira = phần số của key.
- `merge_requests.jira_keys` (JSONB, index GIN).
- Bảng `jira_projects` (§4).
- Lỗi production của Jira dùng chung bảng `prod_defects`.

## 11. Quyền và phạm vi

- Cấu hình Jira cần quyền `dpi.admin`.
- Báo cáo cấp team và lỗi production lọc theo dự án CodePulse:
  - issue GitLab theo `projects.business_project_id` như cũ;
  - issue Jira theo `jira_projects.business_project_id`.
- AI gợi ý điểm (spec 25 §R6) gửi tiêu đề issue Jira như issue GitLab. Không có code.

## 12. Giao diện

- **Admin › DPI › Jira**:
  - trạng thái kết nối (đã cấu hình chưa, nút **Kiểm tra kết nối**: phiên bản Jira và các
    custom field tìm được);
  - nguồn task;
  - danh sách dự án Jira (bật/tắt, gán dự án CodePulse, lần sync gần nhất, lỗi, số issue);
  - nút **Đồng bộ ngay**;
  - bảng quy đổi trường (§5–6).
- **Sẵn sàng dữ liệu**: khi nguồn là Jira, thay cột issue theo repo bằng bảng theo dự án Jira:
  - issue đóng trong cửa sổ của trang (mặc định 30 ngày), % có Story Points, số issue > 8 điểm;
  - % assignee đã nối;
  - lần sync gần nhất và lỗi.

  Tiêu chí toàn công ty "issue có complexity ≥ 70%" tính trên Jira.
- Mã issue hiển thị là key Jira (`ABC-123`) thay cho `#iid`.
- **Hướng dẫn**: mục Jira (quy ước nhánh `feature/ABC-123-mo-ta`, tiêu đề MR có key, Story
  Points, cách đánh dấu lỗi production và blocked).

## 13. API

Tiền tố `/dpi/jira`, quyền `dpi.admin`.

| Method | Path | Việc |
|---|---|---|
| GET | `/dpi/jira` | Cấu hình, nguồn task, mapping, dự án đã đăng ký kèm trạng thái sync |
| POST | `/dpi/jira/test` | Gọi serverInfo + field: phiên bản, custom field tìm được |
| GET | `/dpi/jira/remote-projects` | Dự án trên Jira (key, name) để chọn |
| PUT | `/dpi/jira/projects` | Đăng ký, cập nhật dự án (key, name, enabled, business_project_id) |
| PUT | `/dpi/jira/mapping` | Lưu bảng quy đổi |
| PUT | `/dpi/jira/source` | `gitlab` \| `jira` |
| POST | `/dpi/jira/sync` | Đưa job sync vào hàng đợi (202) |

Mỗi thay đổi đều ghi audit.

## 14. Kiểm thử

- `mapping`: Story Points ↔ mức, loại issue, sub-task, prod-bug, priority, due date
  (issue / sprint / fix version), blocked, sprint dạng chuỗi (DC cũ) và dạng object.
- `client`: retry 5xx, 429 + Retry-After, timeout, phân trang, không log token.
- `sync`: upsert, nối tài khoản theo email, mốc chỉ tiến khi xong, lỗi ghi `last_error`, nối MR
  theo key, defect tạo và rút.
- DPI: facts chỉ đọc nguồn đang chọn; sub-task không điểm bị bỏ; R4 và defect lọc theo dự án
  CodePulse của Jira; key hiển thị.
- GitLab: `jira_keys` trích từ tiêu đề, mô tả và nhánh; collector ngừng lấy issue khi nguồn là
  Jira.
