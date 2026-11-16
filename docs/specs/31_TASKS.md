# 31 – Quản lý task (Task) gắn với git và code review

> Người dùng 2026-11-16: "xây dựng 1 hệ thống quản lý task trên codepulse, mô hình giống Jira nhưng
> cho phép liên kết với các commit của git để đồng bộ"; duyệt phương án: "làm theo đề xuất đi, hoàn
> thành toàn bộ … nó không liên quan gì jira, vì xong đây sẽ bỏ jira luôn nên trước mắt cứ làm độc
> lập, nó liên quan git, code review".
>
> **[Đã chốt]** Độc lập hoàn toàn với tích hợp Jira (spec 27): bảng riêng (`tasks`, …), mã và
> luật đọc mã riêng (`app/tasks/`), không ghi vào `issues`, không import `app/jira/`.
> Các quyết định §12 do người dùng giao chọn theo đề xuất.

## 1. Ý tưởng

Mỗi task biết code của nó: nhánh, commit, MR, điểm review, finding critical còn mở, pipeline, đã
lên production chưa. Trạng thái task chạy theo git (§6), không ai phải kéo thẻ bằng tay.

## 2. Không gian task (task space)

- Mỗi **dự án** (`business_projects`) bật Task với một **mã** `^[A-Z][A-Z0-9]{1,9}$` (ví dụ `CRM`),
  duy nhất toàn hệ thống. Task đánh số `CRM-1`, `CRM-2`… (`task_spaces.next_number`).
- Đổi mã chỉ khi dự án chưa có task (409).
- `types`: các loại bật cho dự án, trong `epic`, `story`, `task`, `bug`, `subtask` (mặc định cả 5).
- `rules` (JSONB) – luật tự động §6 và cổng chất lượng:

  | Khoá | Mặc định | Nghĩa |
  |---|---|---|
  | `start_on_commit` | true | nhánh / commit đầu tiên có mã → trạng thái `start` |
  | `review_on_mr` | true | MR có mã mở → trạng thái `review` |
  | `done_on_merge` | true | MR **fixes** merge vào nhánh mặc định, hoặc commit `Fixes:` vào nhánh mặc định → `done` |
  | `reopen_on_mr_close` | true | MR đóng không merge, task đang ở `review` → `start` |
  | `block_done_on_critical` | false | còn finding critical mở trên code của task thì không cho Done (người và git) |
  | `mr_comment` | false | bình luận link task vào MR khi MR được liên kết (ghi GitLab) |
  | `start` / `review` / `done` | id trạng thái | trạng thái đích của từng luật (seed theo tên mặc định) |

## 3. Mô hình dữ liệu (migration `c5e7a9b1d3f6`)

| Bảng | Cột chính |
|---|---|
| `task_spaces` | `business_project_id` PK, `key` unique, `next_number`, `types` JSONB, `rules` JSONB, `created_by` |
| `task_statuses` | `id`, `business_project_id`, `name`, `category` (`todo`/`doing`/`done`), `position`, `transitions` JSONB (id được chuyển tới; NULL = mọi trạng thái). Unique (dự án, tên) |
| `tasks` | `id`, `business_project_id`, `number`, `key` unique, `type`, `title`, `description` (markdown), `status_id`, `priority` (`highest`/`high`/`medium`/`low`/`lowest`), `assignee_id`, `reporter_id`, `parent_id`, `labels` JSONB, `components` JSONB, `story_points`, `estimate_min`, `spent_min`, `due_date`, `sprint_id`, `version_id`, `rank` (float, thứ tự board/backlog), `resolved_at`, `custom` JSONB, `created_at`, `updated_at` |
| `task_comments` | `id`, `task_id`, `author_id`, `body`, `edited_at` |
| `task_history` | `id`, `task_id`, `user_id` (NULL = tự động), `field`, `old`, `new`, `via` (`web`/`git`/`smart_commit`/`mcp`/`ai`), `at` |
| `task_links` | `id`, `from_task_id`, `to_task_id`, `kind` (`blocks`/`relates`/`duplicates`/`clones`) |
| `task_watchers` | (`task_id`, `user_id`) |
| `task_attachments` | `id`, `task_id`, `name`, `content_type`, `size`, `path`, `uploaded_by` — file trong `TASK_FILES_DIR` (mặc định `/data/task-files`), ≤ 20 MB/file, ≤ 2 GB/dự án |
| `task_worklogs` | `id`, `task_id`, `user_id`, `minutes`, `day`, `note`, `via` |
| `task_git_links` | `id`, `task_id`, `kind` (`branch`/`commit`/`mr`), `project_id` (repo), `sha`, `mr_iid`, `branch`, `title`, `relation` (`fixes`/`refs`), `state` (MR: `opened`/`merged`/`closed`), `manual`, `linked_by`, `created_at` |
| `sprints` | `id`, `business_project_id`, `name`, `goal`, `state` (`planned`/`active`/`closed`), `start_date`, `end_date`, `closed_at`, `committed_points` |
| `task_versions` | `id`, `business_project_id`, `name` (unique theo dự án), `description`, `release_date`, `released_at` |
| `task_filters` | `id`, `user_id`, `business_project_id` (NULL = mọi dự án), `name`, `query` JSONB, `shared` |
| `task_fields` | `id`, `business_project_id`, `name`, `kind` (`text`/`number`/`date`/`select`/`user`), `options` JSONB, `required`, `position`, `archived` |
| `task_ai_runs` | `id`, `task_id`, `kind` (`breakdown`/`summary`), `status`, `input`, `output` JSONB, `error`, `model`, `cost_usd`, `requested_by`, `requested_at`, `finished_at` |

Trạng thái mặc định khi bật: **To Do** (todo), **In Progress** (doing), **In Review** (doing),
**Done** (done), **Won't Do** (done). `start`=In Progress, `review`=In Review, `done`=Done.
Vào nhóm `done` thì đặt `resolved_at`; ra khỏi thì xoá.

## 4. Quyền (bộ quyền spec 21)

| Quyền | Phạm vi | Nghĩa | Mặc định |
|---|---|---|---|
| `tasks.view` | flag | xem task của dự án mình được xem; bình luận, theo dõi | Thành viên |
| `tasks.edit` | own/project/all | tạo task; sửa, chuyển trạng thái, ghi giờ, liên kết git. `own` = task mình tạo hoặc được giao | Thành viên own; Quản lý dự án project; Quản lý tổng all |
| `tasks.manage` | project/all | bật Task, mã, trạng thái, luật, sprint, phiên bản, trường tuỳ biến, xoá task | Quản lý dự án project; Quản lý tổng all |

- `project` = dự án mình có quyền `manage`; `all` = mọi dự án mình xem được.
- Tạo task cần `tasks.edit` (bất kỳ phạm vi) trên dự án xem được.
- Bình luận: sửa/xoá bình luận của mình; `tasks.manage` xoá được mọi bình luận.

## 5. Đọc mã từ git (`app/tasks/keys.py`)

- Mã `KEY-123` (chữ hoa; tên nhánh không phân biệt hoa thường), chỉ khi `KEY` là mã của **dự án chứa
  repo** đó và task tồn tại (tránh nối nhầm repo khác, `UTF-8`, `SHA-256`).
- **Commit:**
  - `fixes`: dòng `Fixes:`/`Closes:`/`Resolves:` (nhiều mã), câu "fixes/closes/resolves KEY",
    hoặc tiêu đề `fix(...)`/`hotfix(...)`/`bugfix(...)` có mã;
  - `refs`: mã ở chỗ khác (ví dụ `feat(x): … [CRM-12]`). Merge commit không nối;
    commit `Revert "…"` chỉ `refs`.
- **MR:** mã trong tiêu đề hoặc nhánh nguồn → `fixes` (quy ước: nhánh `feature/CRM-12-…` là việc
  của task); "Closes/Fixes KEY" trong mô tả → `fixes`; mã chỉ nhắc trong mô tả → `refs`.
- **Nhánh:** `<loại>/<KEY>-<mô-tả>` hoặc tên chứa mã → link `branch`.
- **Smart commit** (chỉ lần đầu một commit được nối với task; tác giả commit phải gắn với một người
  có `tasks.edit` trên task): `#time 1h30m` (ghi giờ), `#comment nội dung` (bình luận), `#<trạng thái>`
  (`#done`, `#in-progress`, `#in-review`, … = tên trạng thái viết thường, khoảng trắng → `-`).

## 6. Đồng bộ với git (`app/tasks/git.py`)

- **Push Hook** (webhook, mọi nhánh): nối nhánh có mã; nối các commit trong payload; áp smart commit;
  luật `start_on_commit`; commit `fixes` vào nhánh mặc định → `done_on_merge`.
- **Sync repo** (collector convert): nối MR (tiêu đề, nhánh, mô tả) và commit của MR / nhánh mặc
  định; theo dõi `state` của MR trên link:
  - mở → `review_on_mr` (trừ khi task đã ở nhóm done);
  - merge vào nhánh mặc định + `fixes` → `done_on_merge`;
  - đóng không merge → `reopen_on_mr_close`.
  - Luật chỉ chạy khi `state` của link **đổi** (idempotent).
- Luật không bao giờ lùi task đã Done (trừ `reopen_on_mr_close` từ `review`), không kéo task từ
  `done` về `doing`.
- `block_done_on_critical`: còn finding critical mở trên review của commit/MR đã nối thì không vào
  Done; lịch sử ghi "chưa chuyển Done: còn N critical", người được giao nhận thông báo.
- Mọi thay đổi tự động ghi `task_history.via = git`, thông báo người được giao.
- **Ghi ngược GitLab** (tự chọn): `POST /tasks/{key}/branch` tạo nhánh
  `feature|bugfix/<KEY>-<slug>` từ nhánh mặc định; `rules.mr_comment` bình luận
  `Task [KEY](link): tiêu đề` vào MR khi nối lần đầu.

## 7. Chất lượng code trên task

`GET /tasks/{key}` → `git`:
- `branches`, `commits` (sha, tiêu đề, repo, quan hệ, review mới nhất: id, điểm, kết luận,
  critical/high còn mở), `mrs` (repo, iid, tiêu đề, state, web_url, review, pipeline của head),
  `summary`: `commits`, `mrs`, `open_critical`, `open_high`, `avg_score`, `pipelines_failed`,
  `deployed` (`{environment, at}` khi một deployment production thành công có sha của commit
  merge/squash của MR hoặc commit đã nối).
- Trang review (`/reviews/{id}`) hiện dải các task nối với commit/MR của review
  (`GET /tasks/linked`).

## 8. API (`/api/v1`, `app/api/routers/tasks.py`)

Kiểu chung:
- `TaskRow` = `{id, key, space, type, title, status:{id,name,category}, priority, assignee:Person|null,
  reporter:Person|null, labels, story_points, due_date, sprint_id, version_id, parent_key,
  subtasks:{done,total}, rank, git:{commits, mrs, open_critical, open_high}, created_at,
  updated_at, resolved_at}`;
- `Person` = `{id, full_name, primary_email, avatar_url}`.

| Endpoint | Việc |
|---|---|
| `GET /tasks/spaces` | dự án xem được: `[{business_project_id, name, enabled, key, types, rules, statuses:[{id,name,category,position,transitions}], can_manage, can_create}]` (dự án chưa bật chỉ hiện với người có `tasks.manage` trên nó) |
| `PUT /tasks/spaces/{bp}` | bật / sửa `{key, types?, rules?}` (`tasks.manage`) |
| `PUT /tasks/spaces/{bp}/statuses` | thay danh sách `{statuses:[{id?, name, category, transitions?}], move?:{old_id:new_id}}`; bỏ trạng thái đang có task mà thiếu `move` → 409 |
| `GET /tasks/people?space=` | người giao được việc (được cấp dự án, đang hoạt động) |
| `GET /tasks` | lọc: `space` (lặp), `q` (mã / tiêu đề / mô tả), `status`, `category`, `type`, `assignee` (`me`/`none`/id), `reporter`, `priority`, `label`, `sprint` (id/`backlog`/`active`), `version`, `parent`, `due` (`overdue`/`week`), `updated_from`, `sort` (`rank`/`updated`/`created`/`priority`/`due`/`key`), `order`, `limit` ≤ 500, `offset` → `{items:TaskRow[], total}` |
| `POST /tasks` | tạo `{space, type, title, description?, priority?, assignee_id?, parent_key?, labels?, components?, story_points?, estimate_min?, due_date?, sprint_id?, version_id?, custom?}` → chi tiết |
| `POST /tasks/batch` | tạo nhiều `{space, parent_key?, items:[{type,title,description?,story_points?}]}` (dùng cho AI tách task) |
| `GET /tasks/{key}` | chi tiết: TaskRow + `description, components, estimate_min, spent_min, custom, watchers, watching, links:[{id,kind,direction(out/in),task:TaskRow}], subtasks:TaskRow[], comments:[{id,author,body,created_at,edited_at,can_edit}], history:[{user,field,old,new,via,at}], attachments:[{id,name,size,content_type,uploaded_by,created_at}], worklogs:[{id,user,minutes,day,note,via}], git (§7), transitions:[status], can:{edit,delete,comment,manage}` |
| `PATCH /tasks/{key}` | sửa trường bất kỳ của POST, `status_id` (theo `transitions`), `parent_key` |
| `DELETE /tasks/{key}` | `tasks.manage`; sub-task xoá theo |
| `POST /tasks/{key}/transition` | `{status_id, comment?}` |
| `POST /tasks/{key}/move` | board / backlog: `{status_id?, sprint_id? (null = backlog), before_key?, after_key?}` |
| `POST /tasks/bulk` | `{keys, set:{status_id?, assignee_id?, priority?, sprint_id?, version_id?, labels_add?, labels_remove?}, delete?}` |
| `POST/PATCH/DELETE /tasks/{key}/comments[/{id}]` | `{body}`; `@tên` → thông báo |
| `POST /tasks/{key}/links`, `DELETE /tasks/{key}/links/{id}` | `{key, kind}` |
| `PUT /tasks/{key}/watch` | `{watching}` |
| `POST /tasks/{key}/worklogs`, `DELETE …/{id}` | `{minutes, day?, note?}` |
| `POST /tasks/{key}/attachments?filename=` | body là file; `GET /tasks/attachments/{id}` tải; `DELETE` |
| `GET /tasks/{key}/commit-candidates?q=` | commit của repo trong dự án (SHA tiền tố ≥ 4 hoặc từ trong message), mới nhất trước |
| `POST /tasks/{key}/git`, `DELETE /tasks/{key}/git/{id}` | nối tay `{kind:"commit", sha, relation}` hoặc `{kind:"mr", project_id, mr_iid, relation}`; gỡ link tay (hoặc `tasks.manage`) |
| `POST /tasks/{key}/branch` | `{project_id, from_ref?}` tạo nhánh GitLab và nối |
| `GET /tasks/linked?project_id=&sha=` / `&mr_iid=` | task nối với commit / MR (trang review) |
| `GET/POST /tasks/spaces/{bp}/sprints`, `PATCH/DELETE /tasks/sprints/{id}` | sprint `{name, goal, start_date, end_date}`; danh sách kèm `tasks`, `points`, `done_points` |
| `POST /tasks/sprints/{id}/start` | `{start_date, end_date}`; một sprint active mỗi dự án |
| `POST /tasks/sprints/{id}/complete` | `{move_to: sprint_id \| null}` task chưa xong sang sprint khác / backlog |
| `GET /tasks/sprints/{id}/burndown` | `{days:[{day, remaining_points, remaining_tasks, ideal_points}], committed_points}` |
| `GET /tasks/spaces/{bp}/reports?from=&to=` | `{velocity:[{sprint_id,name,committed,completed}], flow:[{day,created,resolved}], cycle_time:{median_h,p85_h,count}, by_status:[{status,count}], by_assignee:[{user,open,done,points}], worklog:[{user,minutes}]}` |
| `GET/POST /tasks/spaces/{bp}/versions`, `PATCH/DELETE /tasks/versions/{id}`, `POST /tasks/versions/{id}/release` | phiên bản |
| `GET /tasks/versions/{id}/notes` | `{markdown}` release notes: task Done theo loại, kèm MR |
| `GET/POST /tasks/filters`, `DELETE /tasks/filters/{id}` | bộ lọc lưu `{name, space?, query, shared}` |
| `GET/PUT /tasks/spaces/{bp}/fields` | trường tuỳ biến (thay danh sách); giá trị ở `custom[field_id]` |
| `POST /tasks/{key}/ai` | `{kind:"breakdown"\|"summary", hint?}` → `{run_id}` (job hàng đợi `reviews_asked`) |
| `GET /tasks/ai/{run_id}` | `{status, output, error}`; breakdown: `{items:[{type,title,description,story_points}]}`, summary: `{markdown}` |

## 9. Thông báo

Trong app (spec 24), `kind`:
- `task.assigned`: người được giao;
- `task.mentioned`: người được `@nhắc` trong bình luận / mô tả;
- `task.commented`: người được giao, người tạo, người theo dõi;
- `task.status`: người tạo, người theo dõi; tự động bởi git thì cả người được giao.

Người làm không tự nhận thông báo.

## 10. MCP (Claude Code)

- Scope đọc `findings:read`: `list_my_tasks(status?, space?)`, `get_task(key)` (kèm git §7).
- Scope mới `tasks:write` (ô "Cập nhật task" khi tạo token): `move_task(key, status)`,
  `comment_task(key, body)`, `log_work(key, minutes, note?)`.

## 11. AI

- **Tách task** (`breakdown`): từ tiêu đề, mô tả của Epic/Story (và gợi ý) → 3–12 việc con có loại,
  mô tả, story point; người dùng chọn rồi tạo bằng `POST /tasks/batch`.
- **Tóm tắt tiến độ** (`summary`): từ mô tả, bình luận, commit (tiêu đề), MR, điểm review, tiêu đề
  finding còn mở → markdown.
- Cần `tasks.edit` trên task.
- Chung ngân sách LLM: audit `task.ai.cost`, cộng vào chi phí tháng.
- Không gửi gì của repo `allow_external_llm = false` khi provider không phải local.

## 12. Quyết định (đề xuất đã duyệt 2026-11-16)

1. Độc lập với Jira (người dùng chốt).
2. `done_on_merge` bật mặc định, chỉ với quan hệ `fixes`.
3. Còn critical mở: **cảnh báo** (badge, xác nhận), không chặn; `block_done_on_critical` để chặn.
4. Ghi ngược GitLab (tạo nhánh, bình luận MR) làm luôn nhưng tự chọn: nút tạo nhánh; `mr_comment` mặc định tắt.
5. Thành viên: tạo task, sửa task của mình (tạo / được giao), bình luận mọi task xem được.
6. Làm đủ ba giai đoạn trong một đợt (người dùng: "hoàn thành toàn bộ").
7. Ngoài phạm vi (IDEAS.md): DPI đọc task CodePulse làm nguồn task (sau khi bỏ Jira), nhập dữ liệu
   từ Jira, Telegram cho task.

## 13. Giao diện (`/tasks`)

- Menu **Task** (người có `tasks.view`).
- Đầu trang: chọn dự án (space) và nút **Tạo task** (modal tạo nhanh).
- Tab:
  - **Board**: cột theo trạng thái, kéo thả đổi trạng thái / thứ tự, lọc người / loại / nhãn,
    sprint đang chạy nếu có;
  - **Backlog**: các sprint + backlog, kéo task vào sprint, bắt đầu / kết thúc sprint;
  - **Danh sách**: bảng lọc đủ trường, bộ lọc lưu, sửa hàng loạt, "Task của tôi";
  - **Báo cáo**: burndown, velocity, tạo / xong, cycle time, theo người, giờ làm;
  - **Phiên bản**: release notes;
  - **Cài đặt** (chỉ `tasks.manage`): mã, loại, trạng thái + chuyển tiếp, luật git, trường tuỳ biến.
- **Chi tiết task:** drawer (`?task=KEY`) và trang `/tasks/{KEY}`.
  - Cột trái: tiêu đề, mô tả markdown, sub-task, liên kết, **Code** (§7), bình luận / lịch sử /
    giờ làm, đính kèm.
  - Cột phải: trạng thái, người làm, ưu tiên, loại, nhãn, sprint, phiên bản, story point, ước
    lượng, hạn, trường tuỳ biến, theo dõi, nút tạo nhánh, AI.
- **Trang review:** dải "Task" (mã, tiêu đề, trạng thái).
- **Token MCP:** thêm ô "Cập nhật task".

## 14. Chốt sau lượt soát (2026-11-16)

- **Commit của collector** đến từ mọi nhánh (`commits?all=true`), nên chỉ được nối, không bao giờ
  chuyển Done. Done chỉ đến từ:
  - MR `fixes` merge vào nhánh mặc định;
  - Push Hook của nhánh mặc định có commit `fixes`. Commit đã nối từ nhánh phụ trước đó vẫn
    chuyển Done khi tới nhánh mặc định.
- **Lịch sử cũ:** commit / MR tạo trước ngày bật Task của dự án hơn 1 ngày không được nối. Mã
  dùng lại từ công cụ cũ không kéo lịch sử về, không tự đóng task.
- **Smart commit** chỉ chạy từ Push Hook khi **người push** là tác giả commit, vì email tác giả
  tự đặt được. Không chạy lại khi sync.
- `reopen_on_mr_close` chỉ chạy với MR `fixes` khi không còn MR `fixes` nào khác đang mở.
- **Giao việc:** chỉ cho người được cấp dự án, hoặc chính mình.
- **Liên kết task khác dự án:** người không xem được dự án kia thì không thấy liên kết đó.
- **Đổi workflow:** `resolved_at` đi theo nhóm của trạng thái mới.
- **Xoá task:** xoá luôn file đính kèm, kể cả của sub-task.
- **Ghi đồng thời:** link git ghi trong savepoint, trùng (webhook và sync cùng lúc) thì dùng link
  đã có; bước task của sync chạy trong savepoint riêng, lỗi không làm hỏng sync repo.
- **Mã commit gõ tay** khớp nhiều commit → 409.
- **MCP:** `comment_task` thông báo cả người được `@nhắc`.
- Hai thao tác cùng lúc (bật Task, bắt đầu sprint) trả 409 thay vì 500.
