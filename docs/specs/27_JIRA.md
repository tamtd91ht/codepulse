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
- **[Quyết định]** (user 2026-11-05) Cấu hình được trên web ở **Admin › Jira** (và DPI › Jira),
  thẻ **Kết nối**: URL, PAT, CA nội bộ (PEM), bật/tắt tích hợp, chu kỳ, số ngày lấy lùi,
  timeout. Biến môi trường thành tuỳ chọn:
  - Thứ tự ưu tiên từng giá trị: giá trị lưu trên web, nếu không có thì biến môi trường
    (`app.jira.settings.connection()` → `JiraConnection`). Không cần khởi động lại.
  - Giá trị thường lưu ở `app_settings["jira_connection"]` (JSON: `base_url`, `ca_pem`,
    `interval_min` 1–1440, `backfill_days` 1–3650, `timeout_s` 1–300, `enabled`, mặc định
    `true`). Token lưu ở `stored_secrets` tên `jira_token`, mã hoá bằng `APP_ENCRYPTION_KEY`
    như token Claude (`source = "paste"`); không bao giờ trả về API, ghi log hay audit.
  - Kiểm tra: `base_url` là http(s), không kèm tài khoản, `?`, `#`, tối đa 300 ký tự (thiếu
    scheme thì thêm `https://`), không nhận `localhost` / `*.localhost`, loopback
    (127.0.0.0/8, ::1, kể cả dạng ::ffff:127.x) hay link-local (169.254.0.0/16, fe80::/10).
    `http://` được nhận nhưng màn hình cảnh báo token đi dạng rõ. `ca_pem` phải đọc được bằng
    `ssl.create_default_context(cadata=…)`, tối đa 100 KB; token là ASCII in được
    (`^[\x21-\x7e]+$`), tối đa 1000 ký tự.
  - CA: PEM lưu trên web, nếu không có thì `JIRA_CA_BUNDLE`, nếu không có thì CA hệ thống.
  - "Đã cấu hình" = có URL và token đang hiệu lực, và tích hợp bật. Tắt thì ngừng sync, dữ
    liệu giữ nguyên, nguồn task mặc định về GitLab (nếu chưa chọn).
  - **[Quyết định]** (review 2026-11-05) Token gắn với máy chủ, luật nằm trong resolver
    (`connection()`), không chỉ ở từng request. Máy chủ = origin `scheme://host:port`.
    - Token web chỉ dùng khi origin của URL đang hiệu lực trùng origin lúc lưu token
      (`app_settings["jira_connection"].token_origin`, ghi cùng token). Lưu token khi chưa có
      URL thì 422.
    - `JIRA_TOKEN` chỉ dùng khi origin của URL đang hiệu lực trùng origin của
      `JIRA_BASE_URL`. `JIRA_BASE_URL` trống thì `JIRA_TOKEN` không bao giờ đi kèm URL web.
    - Không token nào khớp thì không có token (chưa cấu hình), `connection.token_other_server
      = true`, màn hình báo "token hiện có thuộc máy chủ khác".
    - `PUT /connection` làm một kết nối đang có token thành không có token vì lý do này (URL
      máy chủ khác mà không kèm token, hoặc `token: ""` khi `JIRA_TOKEN` thuộc máy chủ khác)
      thì 422, không ghi gì. `POST /test` với URL máy chủ khác mà không kèm token cũng 422.
  - **Dùng lại biến môi trường** xoá mọi giá trị đã lưu trên web. Token web giải mã không được
    (sai key) thì coi như chưa lưu: `JIRA_TOKEN` áp dụng nếu cùng máy chủ, `connection`
    có `token_unreadable: true` và màn hình nhắc nhập lại; cảnh báo ghi log một lần mỗi
    process (không mỗi tick).
  - Không tạo được client (ví dụ CA lỗi) thì sync ghi `last_error` cho mọi dự án bật.
  - Tick: đưa job vào hàng đợi lỗi thì xoá `jira:sync:due` (tick sau thử lại) và báo lỗi.
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
  - Lần đầu: `n = số ngày lấy lùi × 1440` (web, nếu không thì `JIRA_BACKFILL_DAYS`).
- Mốc `jira_projects.synced_until` chỉ tiến lên khi cả dự án sync xong. Lỗi thì ghi
  `last_error` và lần sau làm lại.
- Mỗi dự án sync dưới `pg_try_advisory_xact_lock(0x7A, jira_projects.id)`: một lần sync khác
  của dự án đang chạy (scheduler và **Đồng bộ ngay**) thì dự án được bỏ qua (`skipped: busy`,
  không ghi `last_error`).
- **[Quyết định]** (2026-11-05, vì cấu hình đổi được trên web lúc đang chạy) Scheduler có job
  `jira-tick` chạy mỗi phút (`app.jira.sync.tick`): đọc lại kết nối, chỉ khi đã cấu hình và
  `SET jira:sync:due NX EX <chu kỳ × 60>` thành công mới đưa `app.jira.sync.sync_all_job` vào
  hàng đợi. Nhờ vậy mỗi chu kỳ chỉ một lần, kể cả khi scheduler khởi động lại. Chu kỳ lưu mới
  ngắn hơn thì thời gian chờ hiện tại rút xuống theo. Khoá advisory ở trên vẫn chặn hai lần sync
  cùng dự án. Admin có nút **Đồng bộ ngay**.
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
| web_url | `<URL Jira đang hiệu lực>/browse/<KEY>` |

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
  - Mặc định là `jira` khi Jira đã cấu hình (kết nối đang hiệu lực, §2), còn lại `gitlab`.
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

- **Admin › Jira** và **DPI › Jira** (cùng một màn hình; tab Admin hiện cho admin và người có
  `dpi.admin`):
  - **[Quyết định]** (2026-11-05) thẻ **Kết nối** là form: URL Jira, Personal Access Token (ô
    mật khẩu; trạng thái "Đã lưu (cập nhật …)" / "Đang dùng biến môi trường" / "Chưa có"; để
    trống là giữ; **Xoá token đã lưu**), CA nội bộ (PEM, tuỳ chọn), bật/tắt tích hợp, chu kỳ
    (phút), số ngày lấy lùi lần đầu, timeout (giây). Mỗi ô ghi giá trị đến từ web hay biến môi
    trường. Nút **Kiểm tra kết nối** thử giá trị đang nhập (chưa lưu), một nút chính **Lưu**,
    **Dùng lại biến môi trường** (hỏi xác nhận). Chưa cấu hình thì các thẻ còn lại thay bằng
    lời nhắc điền Kết nối trước;
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

Tiền tố `/dpi/jira`, quyền `dpi.admin` (admin có mọi quyền).

| Method | Path | Việc |
|---|---|---|
| GET | `/dpi/jira` | Cấu hình, nguồn task, mapping, dự án đã đăng ký kèm trạng thái sync; khối `connection` |
| PUT | `/dpi/jira/connection` | Lưu kết nối trên web (chỉ các khoá gửi lên); trả `connection` |
| DELETE | `/dpi/jira/connection` | Xoá giá trị đã lưu trên web, biến môi trường áp dụng lại |
| POST | `/dpi/jira/test` | Gọi serverInfo + field: phiên bản, custom field tìm được; body tuỳ chọn để thử giá trị chưa lưu |
| GET | `/dpi/jira/remote-projects` | Dự án trên Jira (key, name) để chọn |
| PUT | `/dpi/jira/projects` | Đăng ký, cập nhật dự án (key, name, enabled, business_project_id) |
| PUT | `/dpi/jira/mapping` | Lưu bảng quy đổi |
| PUT | `/dpi/jira/source` | `gitlab` \| `jira` |
| POST | `/dpi/jira/sync` | Đưa job sync vào hàng đợi (202) |

Mỗi thay đổi đều ghi audit.

- **[Quyết định]** (2026-11-05) `connection` = `{base_url, base_url_source, token_set,
  token_source, token_updated_at, token_unreadable, token_other_server, ca_set, ca_source,
  interval_min, backfill_days, timeout_s,
  enabled, configured, sources: {interval_min, backfill_days, timeout_s, enabled}, env:
  {base_url, token, ca_bundle}}`; mỗi `*_source` là `web` | `env` | `null`. Không bao giờ có
  token hay nội dung PEM. `configured`, `base_url`, `interval_min` ở mức trên là giá trị đang
  hiệu lực.
- `PUT /connection` body `{base_url?, token?, ca_pem?, interval_min?, backfill_days?,
  timeout_s?, enabled?}`: khoá không gửi thì giữ; `""` (base_url, token, ca_pem) hoặc `null`
  (số, enabled) xoá giá trị web. Token chỉ ghi. Sai thì 422 (thông báo `<khoá>: …`). Audit
  `dpi.jira.connection` ghi danh sách khoá đã đổi (`changed`), `base_url`, `enabled`, không
  ghi token hay PEM. `DELETE` ghi `dpi.jira.connection.reset`.
- `POST /test` body tuỳ chọn `{base_url?, token?, ca_pem?}`: khoá thiếu lấy giá trị đang hiệu
  lực; không lưu gì; chạy được cả khi tích hợp đang tắt. Không có URL/token nào thì 409.

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
