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
    cần lấy); với `JIRA_AUTH_TYPE=basic` là mật khẩu của tài khoản đó.
  - `JIRA_AUTH_TYPE`: `pat` (mặc định) | `basic`; `JIRA_USERNAME`: tài khoản dịch vụ khi
    `basic` (xem cách đăng nhập bên dưới).
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
- **[Quyết định]** (user 2026-11-05: Jira công ty là **Jira Server 8.0.2**, chưa có PAT vì PAT
  có từ 8.14) Hai cách đăng nhập, `auth_type`:
  - `pat` (mặc định): `Authorization: Bearer <PAT>`.
  - `basic`: `Authorization: Basic base64(username:password)`, chuỗi `username:password` mã hoá
    UTF-8 trước khi base64 (mật khẩu không ASCII dùng được). Dùng một **tài khoản dịch vụ
    riêng** chỉ có Browse Projects, mật khẩu mạnh không dùng nơi khác, chỉ qua HTTPS (màn hình
    cảnh báo đỏ với `http://`), đổi mật khẩu định kỳ.
  - `auth_type` và `username` (không bí mật; 1–200 ký tự in được, không có `:`, bỏ khoảng trắng
    hai đầu) lưu trong `app_settings["jira_connection"]` như các giá trị web khác; biến môi
    trường dự phòng `JIRA_AUTH_TYPE` (`pat` | `basic`, mặc định `pat`) và `JIRA_USERNAME`
    (`JIRA_USERNAME` có `:` thì bỏ qua và ghi cảnh báo).
  - Bí mật vẫn là **một** bí mật đã lưu (`stored_secrets` `jira_token`, mã hoá; env
    `JIRA_TOKEN`): với `basic` nó là mật khẩu. Mọi luật gắn máy chủ ở trên áp dụng y hệt cho mật
    khẩu (`token_origin`, `JIRA_TOKEN` chỉ cho origin của `JIRA_BASE_URL`, 422 khi đổi máy chủ
    mà dùng lại bí mật). Đổi `username` hay `auth_type` mà không nhập lại bí mật: được, vì bí mật
    vẫn chỉ đi tới máy chủ của nó; máy chủ mới luôn cần nhập lại.
  - Kiểm tra mật khẩu: 1–1000 ký tự, không ký tự điều khiển (Unicode `Cc`), giữ nguyên như gõ
    (không cắt khoảng trắng); PAT vẫn là ASCII in được. Bí mật được kiểm theo `auth_type` đang
    hiệu lực sau request. Chuyển `basic` → `pat` mà mật khẩu đang lưu không phải PAT hợp lệ thì
    422 (nhập PAT). `basic` mà không có username thì 422.
  - "Đã cấu hình" = URL + bí mật + bật + (username khi `basic`).
- Mọi lần gọi có timeout, retry 3 lần với 5xx và lỗi mạng, tôn trọng `Retry-After` của 429
  (trần 30 giây), và nghỉ tối thiểu 100 ms giữa hai lần gọi. Token và mật khẩu không bao giờ ghi
  vào log hay trả về API.
- **[Quyết định]** (2026-11-05) Đăng nhập bị từ chối thì **không bao giờ thử lại** (Jira
  Server khoá tài khoản sau vài lần sai bằng CAPTCHA): mọi 401, và 403 có
  `X-Seraph-LoginReason` chứa `AUTHENTICATION_DENIED` / `AUTHENTICATED_FAILED` hoặc có
  `X-Authentication-Denied-Reason` (ví dụ `CAPTCHA_CHALLENGE`), là `JiraAuthError` với thông
  báo tiếng Việt ("sai tài khoản/mật khẩu, hoặc Jira đã bật CAPTCHA sau nhiều lần đăng nhập
  sai: đăng nhập web bằng tài khoản dịch vụ để giải CAPTCHA rồi thử lại"; với PAT: token sai,
  hết hạn hoặc bị thu hồi). 403 không có các header đó (thiếu quyền) vẫn là lỗi thường.
- Endpoint dùng:
  - `GET /rest/api/2/serverInfo`: kiểm tra kết nối, phiên bản (`version`, `versionNumbers`,
    `deploymentType`). Jira trả cả cho người chưa đăng nhập.
  - `GET /rest/api/2/myself`: (kiểm tra kết nối) tài khoản đăng nhập được; Jira 8.0 bỏ qua
    header Bearer lạ và coi như ẩn danh, nên chỉ serverInfo thì không biết đăng nhập có đúng.
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
- **[Quyết định]** (2026-11-05) `JiraAuthError` trong lần sync: dừng cả lần chạy ngay (không gọi
  tiếp dự án nào, không retry), mọi dự án còn lại ghi `last_error`, và ghi
  `app_settings["jira_auth_failure"] = {at, error, fingerprint}`. `fingerprint` là HMAC-SHA256
  của (origin, `auth_type`, `username`, bí mật), khoá dẫn xuất từ `APP_ENCRYPTION_KEY`
  (`SecretBox.fingerprint`): không lộ bí mật, nhưng biết thông tin đăng nhập đã đổi chưa.
  Khi khoá còn khớp với thông tin đăng nhập đang hiệu lực, tick **không** đưa sync tự động vào
  hàng đợi (`paused: login refused`), và một `sync_all` theo lịch đã nằm trong hàng đợi cũng
  kiểm lại và trả `{"skipped": "paused"}` mà không gọi Jira; chỉ **Đồng bộ ngay**
  (`sync_all_job(manual=True)`) chạy bất chấp. Nhờ vậy mật khẩu sai không bị thử mỗi chu kỳ
  rồi khoá tài khoản. Khoá được xoá khi:
  - fingerprint đang hiệu lực khác fingerprint đã ghi (ví dụ sửa `JIRA_TOKEN` rồi khởi động
    lại): tick / sync theo lịch xoá và chạy tiếp;
  - lưu thay đổi URL / `auth_type` / `username` / bí mật, hoặc **Dùng lại biến môi trường**;
  - **Kiểm tra kết nối** thành công với kết nối đang hiệu lực (không kèm giá trị chưa lưu);
  - một lần sync có ít nhất một lời gọi Jira thành công (`/field` trả lời) và không bị từ chối
    đăng nhập. Lần sync mà mọi lời gọi lỗi mạng / 5xx hay mọi dự án đang bận thì không xoá.

  `GET /dpi/jira` trả `auth_failure` (`{at, error}` khi còn khớp), thẻ Kết nối báo đỏ cách gỡ
  (giải CAPTCHA).
- **[Quyết định]** (review 2026-11-05) `last_error` của lỗi không phải `JiraError` chỉ ghi tên
  lớp ngoại lệ (như `_call` của API), vì nội dung có thể trích dữ liệu request (ví dụ
  `UnicodeEncodeError` trích ký tự của header, tức bí mật). `save_token` ghi kèm
  `token_auth_type` (cách đăng nhập bí mật được kiểm theo) vào `jira_connection`. `JIRA_TOKEN`
  chỉ cắt khoảng trắng khi cách đăng nhập là `pat`. `PUT /connection` chỉ kiểm username / PAT
  hợp lệ khi request đổi URL, `auth_type`, `username` hay bí mật: chỉ tắt tích hợp
  (`enabled: false`) luôn được.
- Issue bị xoá trên Jira không phát hiện được qua search, nên vẫn giữ trong DB (ghi nhận).
- **Tương thích Jira Server 8.0** (rà 2026-11-05): `POST /rest/api/2/search` (body `jql`,
  `fields`, `startAt`, `maxResults`, `validateQuery: true`), `/field`, `/project`,
  `/serverInfo`, `/myself` đều có từ trước 8.0. Trường không tồn tại trong `fields` bị Jira bỏ
  qua; `statuscategorychangedate` có thể vắng, ngày đóng đã lùi về `updated`. Sprint 8.0 trả
  dạng chuỗi `…sprint.Sprint@…[id=…,name=…,goal=…,endDate=…]` (đã đọc, kể cả `<null>` và goal
  có dấu phẩy). Ngày dạng `+0700` đọc được. Assignee 8.0 có `key` (thường bằng username),
  `name`, `emailAddress` có thể bị ẩn (khi đó nối người theo `name@<domain>`). 429 chỉ có từ 8.6,
  vô hại. Không chỗ nào khác cần ≥ 8.14 ngoài PAT.
- Issue chuyển sang dự án khác thì đổi key. `id` giữ nguyên nên cập nhật đúng dòng.

## 4. Chọn dự án Jira

- **[Đã chốt]** Admin chọn dự án Jira (theo key) trong Admin › DPI › Jira và gán nó cho
  **dự án CodePulse** (`business_projects`) để lọc báo cáo và phân quyền. **[Quyết định]**
  (user 2026-11-06) một dự án Jira có thể tính cho **nhiều** dự án CodePulse, xem §15.
- Dự án Jira không gán dự án CodePulse nào thì chỉ admin thấy trong báo cáo cấp team.
- Bảng `jira_projects`: `key` (unique), `name`, `enabled`, `synced_until`, `last_synced_at`,
  `last_error`, `issues_count`. Cột `business_project_id` cũ chuyển thành bảng
  `jira_project_scopes` (§15, migration `b2d4f6a8c0e1`).

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

- **[Quyết định]** (user 2026-11-05) Cấu hình Jira cần quyền riêng **`jira.admin`** ("Cấu hình tích hợp Jira", cờ): kết nối, token, dự án Jira, quy đổi trường, nguồn task, đồng bộ ngay. Không bộ quyền mặc định nào có nó (admin có sẵn). Migration `a3c5e7f9b1d4` thêm `jira.admin` cho mọi bộ quyền đang có `dpi.admin`, nên không ai mất quyền. Người có `jira.admin` thấy mọi dự án Jira trên trang Sẵn sàng dữ liệu.
- Trên Jira, tài khoản dịch vụ chỉ cần Browse Projects trên các dự án cần đồng bộ; Hướng dẫn › Tích hợp Jira có cách tạo Personal Access Token.
- Báo cáo cấp team và lỗi production lọc theo dự án CodePulse:
  - issue GitLab theo `projects.business_project_id` như cũ;
  - issue Jira theo dự án nó được tính cho (§15: `issues.business_project_ids`, NULL = mọi dự
    án đã gắn của dự án Jira).
- AI gợi ý điểm (spec 25 §R6) gửi tiêu đề issue Jira như issue GitLab. Không có code.

## 12. Giao diện

- **Admin › Jira** và **DPI › Jira** (cùng một màn hình; tab Admin hiện cho admin và người có
  `jira.admin`). Bố cục thiết kế lại theo §16 (2026-11-06); các nội dung dưới đây vẫn đúng:
  - **[Quyết định]** (user 2026-11-05) **Cách đăng nhập** là lựa chọn phân đoạn "Personal Access
    Token (Jira ≥ 8.14)" / "Tài khoản + mật khẩu (Jira cũ hơn 8.14)". Chọn basic thì hiện
    Username và **Mật khẩu** (ô mật khẩu, chỉ ghi như token, cùng trạng thái đã lưu / biến môi
    trường / chưa có và **Xoá mật khẩu đã lưu**); nhãn đổi giữa token / mật khẩu. `http://` với
    basic là cảnh báo đỏ (mật khẩu đi dạng rõ). Kết quả kiểm tra ghi phiên bản, loại triển khai,
    "đăng nhập là …"; Jira < 8.14 mà đang chọn PAT thì cảnh báo chọn Tài khoản + mật khẩu.
    `auth_failure` hiện thành cảnh báo đỏ ở đầu thẻ.
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

Tiền tố `/dpi/jira`, quyền `jira.admin` (admin có mọi quyền).

| Method | Path | Việc |
|---|---|---|
| GET | `/dpi/jira` | Cấu hình, nguồn task, mapping, dự án đã đăng ký kèm trạng thái sync; khối `connection` |
| PUT | `/dpi/jira/connection` | Lưu kết nối trên web (chỉ các khoá gửi lên); trả `connection` |
| DELETE | `/dpi/jira/connection` | Xoá giá trị đã lưu trên web, biến môi trường áp dụng lại |
| POST | `/dpi/jira/test` | Gọi serverInfo + myself + field: phiên bản, tài khoản, custom field tìm được; body tuỳ chọn để thử giá trị chưa lưu |
| GET | `/dpi/jira/remote-projects` | Dự án trên Jira (key, name) để chọn |
| PUT | `/dpi/jira/projects` | Đăng ký, cập nhật dự án (key, name, enabled, `scopes` §15; `business_project_id` cũ vẫn nhận = một dòng "mọi issue") |
| GET | `/dpi/jira/projects/{key}/options` | Gợi ý cho quy tắc §15: component trên Jira (lỗi thì từ issue đã lưu), label của issue đã lưu |
| POST | `/dpi/jira/projects/{key}/preview` | Issue đã lưu sẽ chia thế nào với `scopes` gửi lên (không lưu) |
| PUT | `/dpi/jira/mapping` | Lưu bảng quy đổi |
| PUT | `/dpi/jira/source` | `gitlab` \| `jira` |
| POST | `/dpi/jira/sync` | Đưa job sync vào hàng đợi (202); body tuỳ chọn `{keys?, full?}` (§16) |

Mỗi thay đổi đều ghi audit.

- **[Quyết định]** (user 2026-11-05, basic auth) `connection` thêm `auth_type` (`pat` |
  `basic`), `auth_type_source`, `username` (hoặc `null`), `username_source`; `env` thêm
  `auth_type` (`JIRA_AUTH_TYPE=basic`) và `username`. `token_set` / `token_*` giữ nghĩa cũ (bí
  mật: PAT hoặc mật khẩu). `PUT /connection` và `POST /test` nhận thêm `auth_type`, `username`;
  `token` là PAT hoặc mật khẩu. Audit `dpi.jira.connection` ghi thêm `auth_type` và `username`
  (không bao giờ ghi bí mật). `GET /dpi/jira` có `auth_failure` (`{at, error}` | `null`).
  `POST /test` trả thêm `deployment_type`, `pat_supported` (phiên bản ≥ 8.14; `null` khi không
  đọc được), `auth_type`, `user` (`name` của `/myself`); `/myself` bị từ chối trên Jira < 8.14
  khi dùng PAT thì 502 với lời nhắc chọn Tài khoản + mật khẩu.
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
- `client`: retry 5xx, 429 + Retry-After, timeout, phân trang, không log token; header Basic
  đúng (mật khẩu UTF-8), không log mật khẩu, 401 / 403 Seraph (CAPTCHA) không retry và có thông
  báo rõ.
- `connection` / API với basic: env và web, username bắt buộc, mật khẩu gắn máy chủ, luật đổi
  `auth_type`; sync dừng ngay khi bị từ chối đăng nhập, ghi `last_error`, tick tạm dừng.
- `sync`: upsert, nối tài khoản theo email, mốc chỉ tiến khi xong, lỗi ghi `last_error`, nối MR
  theo key, defect tạo và rút.
- DPI: facts chỉ đọc nguồn đang chọn; sub-task không điểm bị bỏ; R4 và defect lọc theo dự án
  CodePulse của Jira; key hiển thị.
- GitLab: `jira_keys` trích từ tiêu đề, mô tả và nhánh; collector ngừng lấy issue khi nguồn là
  Jira.

## 15. Một dự án Jira cho nhiều dự án CodePulse

- **[Quyết định]** (user 2026-11-06: "1 project trên Jira có thể liên quan đến 2 project ở
  CodePulse, ví dụ Jira có project chung nhưng CodePulse tách ra FE, BE") Mỗi dự án Jira có
  0..20 **dòng gắn** (`jira_project_scopes`: `jira_project_id`, `business_project_id` — mỗi dự
  án CodePulse một lần, `components`, `labels` (JSONB, tối đa 50 giá trị mỗi loại, 1–200 ký
  tự, không lặp, không phân biệt hoa thường), `position`).
  - Dòng có `components` / `labels`: các issue có **một** component **hoặc** label trong đó
    (không phân biệt hoa thường).
  - Dòng không có cả hai: khi dự án có dòng theo quy tắc thì là **"các issue còn lại"** (issue
    không khớp quy tắc nào); khi không có dòng nào theo quy tắc thì là **mọi issue**.
  - Issue khớp nhiều dòng tính cho tất cả các dự án đó (việc chung FE + BE).
  - Issue không khớp quy tắc nào và không có dòng "còn lại": tính cho **mọi** dự án đã gắn
    (không issue nào bị mất; màn hình báo số issue như vậy để admin thêm dòng "còn lại" hoặc
    sửa component). Lý do chọn: chia theo Component là cách phổ biến nhất trên Jira, label bổ
    sung cho đội không dùng component; chia theo repo của MR thì phụ thuộc thứ tự đồng bộ nên
    không dùng.
- Lưu trên issue Jira: `components` (JSONB, tên component; đồng bộ lấy thêm trường
  `components`) và `business_project_ids` (`int[]`, GIN): kết quả chia, **NULL** = mọi dự án
  đã gắn của dự án Jira. Nhờ vậy dự án Jira không có quy tắc không cần ghi gì cho từng issue, và
  đổi dòng gắn "mọi issue" không phải ghi lại issue. Điều kiện "issue Jira thuộc dự án X" =
  `business_project_ids && {X}` hoặc (`NULL` và dự án Jira có dòng gắn X)
  (`app.jira.scopes.jira_issue_in`), dùng cho R4 Delivery, lỗi production (danh sách, xác nhận
  RCA) và phạm vi người xem; dự án Jira "thuộc" X khi có dòng gắn X (`projects_in`: trang Sẵn
  sàng dữ liệu, R4).
- Đồng bộ chia từng issue theo dòng gắn lúc ghi. Lưu dòng gắn (`PUT /projects`) chia lại ngay
  mọi issue đã lưu của dự án (không gọi Jira). Dòng theo component mà dự án có issue lưu từ
  trước khi có cột `components` (NULL hoặc JSON null) thì `synced_until` về NULL (đồng bộ lại từ
  khoảng lấy lùi) và đưa job đồng bộ dự án đó vào hàng đợi khi Jira đã cấu hình, với `full`
  là tham số của job (một lần đồng bộ đang chạy ghi `synced_until` cũng không huỷ được yêu cầu);
  trả `refetch`, `refetch_queued`. Issue cập nhật lần cuối trước khoảng lấy lùi không lấy lại
  được nên vẫn thiếu component (đi về dòng "còn lại" / mọi dự án); tăng số ngày lấy lùi nếu cần.
- Review 2026-11-06: dòng gắn đổi trong lúc một lần đồng bộ đang chạy thì cuối lần đồng bộ chia
  lại toàn bộ issue của dự án; xoá một dự án CodePulse thì xoá dòng gắn của nó và chia lại issue
  của các dự án Jira liên quan; trang Sẵn sàng dữ liệu chỉ đếm issue của dự án người xem thấy.
  Issue không khớp quy tắc nào (NULL) thuộc mọi dự án đã gắn nên lead của từng dự án đều xác
  nhận RCA lỗi production của nó được — chấp nhận, vì đây là việc chưa phân loại; thêm dòng "các
  issue còn lại" để giao rõ cho một dự án.
- `GET /dpi/jira`: mỗi dự án có `scopes: [{business_project, components, labels, issues}]`
  (issue đã lưu tính cho dòng đó) và `unmatched` (issue không khớp quy tắc nào, đang tính cho
  mọi dự án), thay `business_project`. Trang Sẵn sàng dữ liệu: `business_projects` (danh sách)
  thay `business_project`.
- Migration `b2d4f6a8c0e1`: mỗi `jira_projects.business_project_id` cũ thành một dòng "mọi
  issue"; downgrade giữ một dự án mỗi dự án Jira (dòng "mọi issue" đầu tiên, nếu không thì dòng
  đầu).

## 16. Màn hình Jira (thiết kế lại)

- **[Quyết định]** (user 2026-11-06: cấu hình và đồng bộ "rối, khó dùng, không hợp lý") Bố cục
  mới của Admin › Jira / DPI › Jira, theo phong cách OMICX (trang cài đặt: thẻ chỉ đọc + "Chỉnh
  sửa"; thiết lập lần đầu: các bước đánh số; một nút chính mỗi màn):
  - **Thẻ trạng thái** luôn ở đầu: trạng thái (Đang hoạt động / Hoạt động, N dự án lỗi / Chưa
    bật dự án nào / Tạm dừng: Jira từ chối đăng nhập / Đang tắt / Chưa cấu hình), máy chủ và cách
    đăng nhập, đồng bộ (chu kỳ, lần gần nhất, số dự án lỗi), số dự án đang đồng bộ / đã thêm và
    tổng issue, số tài khoản Jira đã nối người, **nguồn task của DPI** với "Đổi nguồn" (xác nhận
    ngay trong thẻ), nút **Đồng bộ ngay**. Cảnh báo đăng nhập bị từ chối / token không giải mã
    được nằm trong thẻ. `GET /dpi/jira` thêm `sync: {last_at, failed, enabled, never}`.
  - **Chưa cấu hình**: ba bước đánh số — 1. Kết nối máy chủ (form), 2. Thêm dự án và gắn dự án
    CodePulse, 3. Dùng Jira làm nguồn task — bước 2–3 mờ cho tới khi kết nối xong. Lưu kết nối
    lần đầu bật luôn tích hợp. Tích hợp đang tắt: bước 1 có nút "Bật lại".
  - **Đã cấu hình**: tab **Dự án & đồng bộ** (mặc định) · **Kết nối** · **Quy đổi trường**.
    - Dự án & đồng bộ: bảng (dự án Jira; tính cho dự án CodePulse nào và theo quy tắc gì, số
      issue mỗi dòng, cảnh báo "chưa gắn" / "N issue không khớp quy tắc"; trạng thái đồng bộ —
      lỗi, chờ lần đầu, thời điểm; số issue; nút **Đồng bộ** từng dự án và **Sửa**), ô tìm khi
      có hơn 5 dự án, nút chính **Thêm dự án** (ngăn kéo: tìm và chọn dự án trên Jira, rồi sang
      ngăn sửa). Ngăn **Sửa** dự án: bật/tắt đồng bộ; các dòng dự án CodePulse, mỗi dòng chọn
      "Mọi issue / Các issue còn lại" hoặc "Theo component / label" (ô nhập dạng thẻ, gợi ý từ
      `/options`), số issue ước tính mỗi dòng (`/preview`, cập nhật khi sửa), giải thích luật
      chia, cảnh báo phải đồng bộ lại khi issue cũ thiếu component; **Đồng bộ lại từ đầu**.
    - Kết nối: thẻ chỉ đọc (URL, cách đăng nhập, token/mật khẩu — xoá được, CA, chu kỳ / lấy
      lùi / timeout, công tắc bật/tắt tích hợp, hỏi xác nhận khi tắt), **Kiểm tra kết nối**,
      **Chỉnh sửa** mở form: URL, cách đăng nhập, username, token/mật khẩu; CA, chu kỳ, lấy lùi,
      timeout nằm trong "Nâng cao" (tự mở khi có lỗi ở đó). **Dùng lại biến môi trường** khi có
      giá trị lưu trên web.
    - Quy đổi trường: ba thẻ (Trường tuỳ chỉnh, Loại issue → loại DPI, Lỗi production và
      blocked) và thanh lưu dính dưới cùng (Huỷ thay đổi / Lưu quy đổi).
- `POST /dpi/jira/sync` body tuỳ chọn `{keys?: [KEY], full?: bool}`: chỉ các dự án đó (đang
  bật; có `keys` mà không dự án nào khớp thì 409), `full` đặt `synced_until = NULL` và truyền
  `full` cho job (lấy lại khoảng lấy lùi). `sync_all(keys=…, full=…)`,
  `sync_all_job(manual, keys, full)`. Audit `dpi.jira.sync`
  ghi `keys`, `full`.

## 17. Issue Jira của MR trên trang review và MCP

> **[Quyết định]** (user 2026-11-06) Jira không chỉ là nguồn số liệu DPI: người xem review MR và
> agent sửa finding cần biết MR đang làm task nào. Vẫn chỉ đọc (§1).

- `app.jira.links.mr_issues(session, settings, mr, business_project_id)` trả:
  - `issues`: issue Jira nối với MR (`issue_mr_links`) **được tính cho dự án CodePulse của
    repo** (`jira_issue_in`, §15), tối đa 20 liên kết; liên kết `closes` (key ở tiêu đề MR hoặc
    tên nhánh) trước, `mention` (chỉ trong mô tả) sau, rồi theo dự án Jira và số. Mỗi issue:
    `key`, `title`, `state` (opened|closed), `type`, `story_points`, `url` (`…/browse/KEY`),
    `link`.
  - `other_keys`: issue nối với MR nhưng thuộc dự án khác (hoặc repo chưa gắn dự án): **chỉ
    key**. **[Quyết định]** ai cũng gõ được key bất kỳ vào MR; không để việc đó thành cách đọc
    tiêu đề issue của dự án mình không được xem.
  - `missing_keys`: key thuộc dự án Jira **đã đăng ký** mà MR nhắc nhưng issue chưa có trong
    CodePulse (issue mới chưa tới kỳ sync, hoặc tài khoản dịch vụ không xem được). Key của dự án
    chưa đăng ký (`UTF-8`…) bỏ qua như §8.
  - `expected`: nguồn task là Jira (§9), tức MR nên ghi key.
- **API**: `GET /reviews/{id}` thêm `jira` (null với review commit). Ai xem được review thì
  thấy, không cần quyền Jira: tiêu đề issue (đã giới hạn trong dự án của repo) là thông tin
  task, không phải code.
- **Trang review** (MR): dải "Jira" dưới tiêu đề: key (mở Jira), tiêu đề, Đang mở / Đã xong,
  loại · SP, "chỉ nhắc trong mô tả"; key dự án khác chỉ hiện key; key chưa đồng bộ ghi lý do; MR không gắn issue mà nguồn
  task là Jira thì nhắc cách ghi key. Không có gì để nói thì không hiện.
- **MCP** `get_mr_findings`: `review.jira` (như API, tiêu đề đã che giá trị nhạy cảm) và các dòng
  "Jira `KEY`: `tiêu đề` (trạng thái, loại, N SP) url" trong Markdown — tiêu đề là dữ liệu
  không tin cậy, in trong dấu `…` (spec 26 §19).
- Trợ lý (Hỏi Stetho) khi đang xem review: ngữ cảnh thêm dòng issue Jira của MR (tiêu đề che
  giá trị nhạy cảm).
- Key đã đồng bộ nhưng chưa kịp nối (job nối chạy sau) không hiện ở đâu cho tới lần nối kế
  tiếp; tự hết.
- Ngoài phạm vi: trạng thái chi tiết của Jira (In Review…), người được giao, tạo ticket từ
  finding (cần ghi lên Jira, trái §1).

## 18. Task Jira: dùng được dữ liệu Jira (thiết kế lại 2026-11-09)

> **[Quyết định]** (user 2026-11-09: "không thể dùng hay liên kết được gì, chỉ hiện được số lượng
> issue") Jira không chỉ là số đếm cho DPI: có một màn để tra cứu task và đi từ task tới MR, review,
> người làm, và ngược lại. Vẫn chỉ đọc (§1).

- **Màn Task Jira** (`/jira`, menu Theo dõi; hiện khi Jira đã kết nối, `GET /auth/me` có
  `jira_tasks`):
  - Tab **Issue**: KPI trong kỳ (issue làm trong kỳ, đang mở, xong trong kỳ, có MR %, có Story
    Points %, còn lỗi nặng); bảng issue: key (mở drawer) + nút mở Jira, tiêu đề, loại, thuộc
    issue cha, người làm (link trang thành viên), trạng thái, SP, MR đã merge/tổng (0 tô đỏ),
    điểm review trung bình của các MR, critical/high đang mở. Lọc: tìm key/tiêu đề, dự án
    CodePulse, dự án Jira, trạng thái, loại, người làm (có "Tôi"), có/chưa có MR; thời gian dùng
    bộ lọc thời gian chung (issue "làm trong kỳ": tạo trước cuối kỳ, chưa đóng trước đầu kỳ).
    Bộ lọc giữ trên URL (`?jira=ABC&issue=ABC-1…`), chia sẻ link được.
  - **Drawer issue** (`?issue=KEY`): link Jira, trạng thái, loại · SP, người làm, ngày tạo/hạn, dự
    án, component · label, issue cha và việc con (bấm để chuyển), mọi MR ghi key (link GitLab,
    repo, tác giả, "chỉ nhắc trong mô tả", review hiện tại: link trang review, điểm, kết luận,
    critical/high đang mở). Không có MR thì gợi ý cách đặt key vào tên nhánh.
  - Tab **MR chưa gắn issue**: MR mở trong kỳ ở repo mình xem, không nối issue nào; nhánh, key có
    dạng nhưng không khớp, điểm review. Nhắc quy ước khi nguồn task là Jira.
- **Liên kết vào màn này**: số issue ở Admin › Jira (bảng dự án) mở Task Jira lọc theo dự án Jira
  đó; dải Jira trên trang review có "Xem task" mở drawer của issue.
- **Quyền**: issue của dự án Jira đã gắn với dự án CodePulse mình xem được (`jira_issue_in`);
  admin và `jira.admin` thấy mọi issue (kể cả dự án Jira chưa gắn). MR: repo thuộc dự án mình xem
  được. Điểm/finding của review chỉ hiện khi `review.view` cho phép (`work_scope`) — người chỉ xem
  review của mình không thấy điểm của người khác qua issue. Issue không xem được trả 404.
- **API** (đăng nhập là đủ, phạm vi như trên; `app/jira/tasks.py`, `app/api/routers/jira_tasks.py`):
  `GET /jira/tasks/options`, `GET /jira/tasks` (lọc + `page`, `page_size` ≤ 100; trả `items`,
  `total`, `kpis`), `GET /jira/tasks/{KEY}`, `GET /jira/tasks/unlinked-mrs`. Khoảng ngày tối đa
  400 ngày.
- Ngoài phạm vi (ghi `IDEAS.md` nếu cần): nối tay issue ↔ MR, tạo/sửa ticket, sprint/board.
