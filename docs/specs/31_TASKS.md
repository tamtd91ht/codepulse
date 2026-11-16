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

### 4.1 Chia như permission scheme của Jira (người dùng 2026-11-16: "phân quyền cho menu này, giống phân quyền jira", migration `e1a3c5e7b9d2`)

| Quyền | Jira | Phạm vi | Nghĩa | Thành viên | Quản lý dự án / tổng |
|---|---|---|---|---|---|
| `tasks.view` | Browse projects | flag | thấy menu Task; xem task, bảng, backlog, báo cáo; theo dõi | ✓ | ✓ |
| `tasks.create` | Create issues | flag | tạo task (cả sub-task, AI tách task) ở dự án xem được | ✓ | ✓ |
| `tasks.edit` | Edit issues | own/project/all | sửa tiêu đề, mô tả, trường, sprint, phiên bản; AI soạn | own | project / all |
| `tasks.assign` | Assign issues | own/project/all | đổi người làm; tạo task giao cho người khác (không có: chỉ nhận về mình) | own | project / all |
| `tasks.transition` | Transition issues | own/project/all | chuyển trạng thái, kéo thả cột trên bảng | own | project / all |
| `tasks.delete` | Delete issues | own/project/all | xoá task (`own`: task mình tạo) | — | project / all |
| `tasks.comment` | Add comments | flag | bình luận; sửa / xoá bình luận của mình | ✓ | ✓ |
| `tasks.attach` | Create attachments | flag | đính kèm file, ảnh, dán ảnh chụp màn hình; xoá file mình tải | ✓ | ✓ |
| `tasks.worklog` | Work on issues | own/project/all | ghi giờ; xoá giờ mình ghi | own | project / all |
| `tasks.link` | Link issues | own/project/all | liên kết task–task, nối / gỡ commit, MR tay, tạo branch | own | project / all |
| `tasks.moderate` | Edit/Delete all comments, attachments, worklogs | project/all | sửa / xoá bình luận, file, giờ làm của người khác | — | project / all |
| `tasks.sprint` | Manage sprints | project/all | lập, sửa, bắt đầu, đóng, xoá sprint | — | project / all |
| `tasks.manage` | Administer projects | project/all | bật Task, mã, loại, trạng thái + chuyển tiếp, luật git, phiên bản, trường tuỳ biến; **giữ mọi quyền Task ở dự án đó** | — | project / all |

- `own` = task mình tạo (reporter) hoặc được giao (assignee); riêng `tasks.delete` own = task mình
  tạo. `project` = mọi task của dự án mình có quyền `manage` (ngoài ra vẫn gồm `own`); `all` = mọi
  task của dự án mình xem được. Quyền flag áp cho mọi task mình xem được.
- Mọi quyền đều cần xem được task (`tasks.view` + được xem dự án): không quyền nào mở task dự án khác.
- Bộ quyền cũ giữ nguyên hành vi: migration chép vào **mọi** bộ quyền (có sẵn và admin tự tạo)
  `tasks.view` → `comment`, `attach`; `tasks.edit`@X → `create`, `assign`/`transition`/`worklog`/`link`@X;
  `tasks.manage`@X → `delete`/`moderate`/`sprint`@X. Chỉ thêm mục mới, không sửa mục admin đã chỉnh.
- Smart commit (§5): `#time` cần `tasks.worklog`, `#comment` cần `tasks.comment`, `#<trạng thái>` cần
  `tasks.transition` của người commit; lệnh thiếu quyền bị bỏ qua, commit vẫn được nối.
- MCP: `move_task` cần `tasks.transition`, `comment_task` cần `tasks.comment`, `log_work` cần `tasks.worklog`.
- API trả quyền cho giao diện: `GET /tasks/{key}` → `can:{edit,assign,transition,delete,comment,attach,
  worklog,link,moderate,manage}`, mỗi file `can_delete`; `GET /tasks/spaces` → thêm `can_sprint`, `can_assign`.

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
  có quyền tương ứng trên task, §4.1): `#time 1h30m` (ghi giờ), `#comment nội dung` (bình luận), `#<trạng thái>`
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
| `GET /tasks/{key}` | chi tiết: TaskRow + `description, components, estimate_min, spent_min, custom, watchers, watching, links:[{id,kind,direction(out/in),task:TaskRow}], subtasks:TaskRow[], comments:[{id,author,body,created_at,edited_at,can_edit}], history:[{user,field,old,new,via,at}], attachments:[{id,name,size,content_type,uploaded_by,created_at,url,can_delete}], worklogs:[{id,user,minutes,day,note,via}], git (§7), transitions:[status], can:{edit,assign,transition,delete,comment,attach,worklog,link,moderate,manage}` (§4.1) |
| `PATCH /tasks/{key}` | sửa trường bất kỳ của POST, `status_id` (theo `transitions`), `parent_key` |
| `DELETE /tasks/{key}` | `tasks.delete`; sub-task xoá theo |
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

## 15. DPI đọc task CodePulse (người dùng 2026-11-16)

> "làm luôn phần DPI đọc task CodePulse đi" — chuẩn bị bỏ Jira.

- Nguồn task của DPI (spec 27 §9, `app_settings["dpi_task_source"]`) có thêm **`codepulse`**.
  - Đổi ở DPI hàng tháng › Cấu hình › **Nguồn task** (§16).
- **Cách đọc** (`app/tasks/dpi.py`): mỗi task được ghi **một chiều** thành một dòng `issues`, với
  `source = codepulse`, `id = 2·10^15 + task.id` và `key`, cùng liên kết và lỗi production của
  nó. Logic chấm DPI giữ nguyên; module Task vẫn không import gì của Jira.

  | Trường DPI | Từ task |
  |---|---|
  | `type` | story → feature, task → chore, bug → bug; sub-task theo cha; **Epic không là task DPI** |
  | `state`, `closed_at_src` | nhóm Done → closed, lúc `resolved_at`; trạng thái huỷ (Won't Do, Cancelled, Huỷ…) → không có dòng |
  | `complexity` | story point như spec 27 §6 (≤1 XS, ≤2 S, ≤3 M, ≤5 L, còn lại XL) |
  | `parent_key` | chỉ với sub-task (story dưới epic là task riêng) |
  | `assignee_account_ids` | tài khoản `codepulse_user` của người được giao (tạo khi cần) |
  | `due_date`, `milestone` | hạn của task, không có thì ngày kết thúc sprint / ngày phát hành phiên bản; tên sprint / phiên bản |
  | `prod_severity` → `prod_defects` | Bug có nhãn `production` / `prod` / `prod-bug`: highest → P1, high → P2, còn lại P3 |
  | `issue_mr_links` | MR `fixes` → closes, `refs` → mention, MR của nhánh đã nối → branch |
  | `issue_commit_links` | commit đã nối (`fixes` / `refs`) có trong `commits` |
  | `business_project_ids` | dự án của task (lọc R4, lỗi production theo dự án) |

- **Giữ đồng bộ:**
  - Một hook của session (`after_flush` + `before_commit`) chiếu lại mọi task vừa đổi, dù đổi
    từ web, git, smart commit hay MCP; chỉ chạy khi nguồn là `codepulse`.
  - Thay đổi hàng loạt (sửa workflow, đổi ngày sprint) chiếu lại các task liên quan.
  - Chuyển sang `codepulse` chiếu toàn bộ task, đồng thời xoá dòng của task không còn.
  - Xoá task xoá dòng của nó và của sub-task.
- **Mã trùng:** `uq_issues_key` thành duy nhất theo (`source`, `key`) (migration `d9f1b3c5e7a2`),
  để task `CRM-1` nằm cạnh issue Jira `CRM-1` cũ khi dùng lại mã.
- **R4 Delivery** với nguồn `codepulse`: mỗi dự án là một dòng "Task · <dự án>".
- **Gợi ý AI (DPI)** đọc tiêu đề task như với Jira.
- **Sẵn sàng dữ liệu:** các cột issue theo repo ẩn đi (chỉ có nghĩa với GitLab Issues).
- Chuyển từ `codepulse` hoặc Jira về `gitlab`: đồng bộ đầy đủ mọi repo, như §9 của spec 27.

## 16. Nút đổi nguồn ở trang DPI (người dùng 2026-11-16)

> "chuyển nút đổi nguồn sang trang DPI luôn".

- Thiết lập nguồn task là của DPI, giữ lại cả khi bỏ Jira: chuyển từ `app/jira/settings.py` sang
  `app/dpi/task_source.py`, gồm `task_source`, `set_task_source`, `switch`, `view`. Mọi nơi gọi
  import từ đó.
- **API:**
  - `GET /dpi/task-source` → `{task_source, label, can_change, options:[{value, label, note,
    available}]}`, cho người có `dpi.view`, `dpi.admin` hoặc `jira.admin`;
  - `PUT /dpi/task-source {source}` cho `dpi.admin` (hoặc `jira.admin`, người giữ nút trước đây).
    Chọn Jira khi chưa kết nối trả 409.
  - Audit `dpi.task_source` (thay `dpi.jira.source`).
  - `PUT /dpi/jira/source` vẫn chạy (gọi cùng hàm), để giao diện cũ không gãy.
- **Giao diện:**
  - DPI hàng tháng › **Cấu hình** có thẻ **Nguồn task** đứng đầu: nguồn hiện tại, ô chọn nguồn
    mới kèm ghi chú hệ quả, nút "Chuyển nguồn". Jira chưa kết nối thì hiện "(chưa kết nối)" và
    không chọn được.
  - Tab Jira chỉ còn hiện nguồn đang dùng, kèm ghi chú "đổi ở DPI › Cấu hình".

## 17. Đính kèm file, ảnh (người dùng 2026-11-16)

> "task chưa hỗ trợ liên kết files, images hãy bổ sung, cho phép load files, images từ disk hoặc paste
> 1 hình ảnh đã copy trước đó (ảnh chụp screen shot)"

- **Ba cách thêm** (cần `tasks.attach`): nút *Tải file lên* (chọn nhiều file từ máy); kéo thả file
  vào khung *Đính kèm*; **dán ảnh** (Ctrl+V) — đang xem task (con trỏ không ở ô nhập) thì ảnh thành
  file đính kèm. Ảnh dán không tên (`image.png`) được đặt `anh-chup-YYYYMMDD-HHmmss.png`.
- **Trong mô tả và bình luận:** dán ảnh, kéo thả hoặc bấm *Đính file* ở ô soạn → file được tải lên
  task và chèn vào chỗ con trỏ: ảnh thành `![tên](/api/v1/tasks/attachments/<id>?inline=1)` (hiện
  ngay trong nội dung), file khác thành link `[tên](/api/v1/tasks/attachments/<id>)`. Trong lúc tải
  ô soạn hiện `[Đang tải …]`; lỗi thì dòng đó bị gỡ và báo lỗi dưới ô.
- **Hiển thị:** ảnh (PNG, JPEG, GIF, WebP, BMP) là lưới ảnh thu nhỏ, bấm để xem cỡ lớn, mở tab mới
  hoặc tải về; file khác là danh sách tải về. Markdown chỉ hiện ảnh đính kèm của task — ảnh từ
  site khác không tải (lộ người đọc cho site đó).
- **An toàn:** `GET /tasks/attachments/{id}?inline=1` chỉ trả `inline` cho 5 loại ảnh trên; mọi
  loại khác (kể cả SVG, HTML) luôn là `application/octet-stream` + `attachment`. Mọi file kèm
  `X-Content-Type-Options: nosniff` và `Content-Security-Policy: default-src 'none'; sandbox`.
  Loại file: header trình duyệt gửi, nếu trống / `octet-stream` thì đoán theo tên.
- **Upload trả** `{id, name, size, content_type, url}`. Giới hạn giữ nguyên: ≤ 20 MB/file,
  ≤ 2 GB/dự án.
- **Xoá:** người tải lên (còn `tasks.attach`) hoặc người có `tasks.moderate`; giao diện chỉ hiện
  nút xoá theo `can_delete` của từng file. Xoá file không sửa mô tả / bình luận đã chèn nó (ảnh
  hiện thành ảnh hỏng, như Jira).

### 17.1 Ngay trong form tạo task (người dùng 2026-11-16: "chưa thấy nút liên kết ảnh, file ở màn hình tạo task, bug")
- Form *Tạo task* (mọi loại: Task, Bug, Story…) có mục **Đính kèm**: *Chọn file, ảnh*, kéo thả vào form,
  dán ảnh (Ctrl+V) vào ô mô tả. Ảnh dán / thả vào mô tả chèn tạm `![tên](pending:…)` và hiện thu nhỏ
  trong danh sách; bỏ một file thì gỡ luôn chỗ chèn của nó.
- Bấm *Tạo task*: tạo task, tải từng file lên (`POST /tasks/{key}/attachments`), rồi `PATCH` mô tả để
  thay chỗ chèn tạm bằng ảnh thật. Lỗi một phần (file quá lớn, thiếu quyền sửa mô tả): task vẫn được
  tạo, form báo phần lỗi và nút chính thành *Mở {KEY}*.
- Mục chỉ hiện khi có `tasks.attach` (`GET /tasks/spaces` trả `can_attach`).
- Sửa kèm: CSS chung `[hidden] { display: none !important }` — nút submit ẩn của form (Enter để tạo)
  bị quy tắc `button` hiện thành một nút trống không tên ngay trên Huỷ / Tạo task.

## 18. Mức ưu tiên riêng của dự án (người dùng 2026-11-16: "làm cả 3 luôn đi", migration `f3b5d7e9a1c4`)

### 18.1 Như priority scheme của Jira
- Bảng `task_priorities` (`business_project_id`, `key`, `name`, `level`, `color`, `position`, `is_default`):
  mỗi dự án tự đặt **tên**, **màu** (đỏ, cam, vàng, xanh lá, xanh dương, tím, xám — token của design
  system), **thứ tự** (trên cùng = gấp nhất) và **mức mặc định** khi tạo task; tối đa 12 mức.
- `level` là một trong 5 mức cũ (`highest`…`lowest`): quyết định **mũi tên** hiển thị và **nghĩa**
  (DPI: Bug production có mức `highest` → P1, `high` → P2, còn lại P3).
- `tasks.priority` giữ `key` của mức (bỏ CHECK cố định). Key sinh từ tên lúc tạo (ASCII:
  "Khẩn cấp" → `khan-cap`), không đổi khi đổi tên — lọc đã lưu, MCP, smart commit vẫn đúng.
- Dự án bật Task mới và mọi dự án có sẵn nhận 5 mức cũ với đúng key cũ (task không đổi gì).
- `PUT /tasks/spaces/{bp}/priorities` (`tasks.manage`): `{priorities:[{id?, name, level, color,
  is_default}], move:{id_cũ: id_mới}}`. Xoá mức còn task mà không có `move` → 409; kiểm tra xong mới
  ghi. Tên trống / trùng (không phân biệt hoa thường) hoặc màu lạ → 422. `GET` cùng đường dẫn đọc.
- `GET /tasks/spaces` trả `priorities`; mỗi TaskRow thêm `priority_name`, `priority_level`,
  `priority_color`. Sắp xếp theo ưu tiên dùng thứ tự của dự án. Lịch sử ghi tên mức, không ghi key.
- Giao diện: *Cài đặt › Mức ưu tiên* (sửa tên, mũi tên, màu, mặc định, lên / xuống, thêm, xoá kèm chọn
  mức chuyển task); mọi ô chọn ưu tiên (tạo task, chi tiết, lọc danh sách, sửa hàng loạt) dùng danh
  sách của dự án.

### 18.2 Board lọc theo ưu tiên
- Bộ lọc Board thêm **Ưu tiên** (chọn nhiều, kèm biểu tượng), cạnh Người làm / Loại / Nhãn.

### 18.3 Báo cáo theo ưu tiên
- `GET /tasks/spaces/{bp}/reports` thêm `by_priority:[{priority, open, overdue, open_points, created,
  resolved}]` theo thứ tự mức của dự án: đang mở, quá hạn (đang mở, hạn < hôm nay), story point đang
  mở; tạo mới / xong trong khoảng ngày đã chọn. Tab Báo cáo có bảng **Theo ưu tiên** với thanh tỉ lệ
  theo màu của mức.

## 19. Lộ trình nâng cấp Task (người dùng 2026-11-16: "làm toàn bộ theo đề xuất đi")

Nguồn: tài liệu "CodePulse Task — Định hướng nâng cấp sản phẩm" (16/11/2026) cùng bản phản biện đã
chốt. Đã bỏ B4 (`/watch` là `PUT`, nút Theo dõi vẫn chạy đúng). Đã sắp lại thứ tự theo phụ thuộc:
P0 Nền tảng → P1 Thoát Jira → P2 Hằng ngày → P3 Khép vòng chất lượng → P4 Tổng quan & tự động →
P5 Mở rộng. Mỗi giai đoạn là một mục con bên dưới, ghi cả quyết định đã chốt (người dùng giao toàn quyền quyết).

### 19.1 P0 – Nền tảng (migration `a7c9e1b3d5f8`)
- **Lý do đóng (resolution)** `done | wont_do | duplicate | cannot_reproduce` trên `tasks`: gán khi vào
  một trạng thái nhóm Xong (mặc định `done`), xoá khi rời nhóm. Chuyển trạng thái nhận `resolution`
  (`POST /tasks/{key}/transition`, `PATCH` khi đã đóng; mở thì 409). Giao diện: chọn trạng thái nhóm
  Xong ở chi tiết task sẽ mở hộp "Lý do đóng" (kèm ghi chú thành bình luận). Kéo thả trên Board dùng
  `done`. Ô trạng thái hiện "Xong · Không làm". Lịch sử ghi tên lý do.
- **Bỏ trạng thái "Won't Do":** dự án mới không có trạng thái này. Dự án cũ: task ở trạng thái huỷ
  ("Won't Do", "Huỷ", "Không làm"… nhóm Xong) sang trạng thái Xong của luật git với `wont_do`;
  trạng thái huỷ bị xoá, chuyển tiếp trỏ tới nó được gỡ. Dự án không còn trạng thái Xong nào khác thì
  giữ nguyên.
- **DPI:** task có lý do ≠ `done` không phải đầu ra (bỏ khỏi `issues`); tên trạng thái huỷ vẫn được
  nhận cho dữ liệu cũ.
- **Cycle time:** từ `started_at` (lần đầu vào nhóm Đang làm) tới lúc xong, chỉ task lý do `done`.
  Task nhảy thẳng vào Xong không tính, đếm riêng (`cycle_time.skipped`). `started_at` và
  `status_changed_at` (tuổi trong cột, dùng ở P2) được dựng lại từ lịch sử.
- **Phân cấp:** Epic không lên Board, không là dòng Backlog. Backlog có **thanh Epic** bên trái
  (`GET /tasks/spaces/{bp}/epics`: số con xong / tổng, SP còn lại; bấm để lọc theo epic). Sub-task **theo
  sprint và phiên bản của cha**: kế thừa khi tạo, đổi theo khi cha đổi; tự đổi riêng → 422; sửa hàng
  loạt bỏ qua sub-task cho hai trường này. Migration đồng bộ sub-task hiện có. Sub-task không là dòng
  Backlog (vẫn là card trên Board; swimlane theo task cha ở P2).
- **Story point của sprint** (cam kết, burndown, velocity, Backlog) chỉ tính Story / Task / Bug.
- **Thành phần (components):** hiện và sửa ở chi tiết task, lọc `component=` ở danh sách.
- **Một ngôn ngữ:** trạng thái mặc định dự án mới là "Cần làm, Đang làm, Đang review, Xong". Dự án cũ
  giữ tên. Từ smart commit / MCP so với tên đã bỏ dấu (`#dang-lam`), và nhận bí danh của trạng thái theo
  luật git bất kể tên: `#in-progress #start #doing` → bắt đầu, `#review #in-review` → review,
  `#done #xong` → xong, `#todo` → nhóm Cần làm.
- **Phân quyền theo team** (spec 21 §11): bảng `team_permission_sets`; bộ quyền tự tạo gán được cho
  team (`PUT /admin/permission-sets/{id}/teams`, màn Phân quyền › "Gán cho team"); ai đang ở team có bộ
  quyền, vào sau tự có, rời thì mất; "Quyền hiệu lực" ghi nguồn `team`. Migration tạo bộ **"Tester"**
  (viết / chạy testcase mọi dự án xem được, tạo và xử lý task của mình) để gán cho team QA. Thay cho B6
  (phạm vi Team) và B7 (vai trò tester): Lead quản lý task cả dự án đã có qua Lead/Manager dự án (21 §10).

### 19.2 P1 – Thoát Jira (migration `b8d0f2a4c6e9`)
- **Nơi nhận bug của testcase** (`test_project_settings.bug_target`: `codepulse` mặc định | `jira`).
  Đích thực tế: `jira` khi chọn Jira; ngược lại dự án đã bật Task → task CodePulse; dự án chưa bật Task
  mà đã chọn project Jira → vẫn Jira (dự án đang dùng Jira không gãy). Migration đặt `jira` cho dự án đã
  có project Jira. Testcase › Thiết lập › Cài đặt dự án có lựa chọn "Nơi nhận bug".
- **Tạo bug thành task** (`POST /testcases/tests/{id}/bug` khi đích là CodePulse): cần `tasks.create`
  (giao cho người khác cần `tasks.assign`). Loại Bug (Task nếu dự án không dùng Bug), tiêu đề như cũ,
  mô tả Markdown gồm tiền điều kiện, dữ liệu, các bước (mong đợi / thực tế), kết quả thực tế, log, môi
  trường (run, cấu hình, build, nhánh, commit, MR), đính kèm của kết quả và link về test. Ưu tiên, người
  xử lý, nhãn chọn trong hộp tạo bug. Mã task vào ô Bug của kết quả; liên kết `task_test_links`
  (`bug`, testcase, kết quả). Vẫn một bug cho mỗi kết quả Failed (409 lần hai).
- **Gắn bug có sẵn** nhận cả mã task CodePulse (chỉ task mình xem được) và tạo liên kết `bug`.
- **Trạng thái bug** (`GET /testcases/bugs/status`, ô Bug, "Cần retest"): task CodePulse trả lời trước
  (đóng khi ở nhóm Xong, `source: codepulse`, link `/tasks/{KEY}`), rồi mới tới issue Jira đã đồng bộ.
  Nhờ vậy "Cần retest" tự hiện khi mọi bug-task của test đã xong.
- **Nhập từ Jira** (`GET/POST /tasks/spaces/{bp}/import/jira`, `tasks.manage`; Task › Cài đặt › Nhập từ
  Jira): đọc issue Jira **đã đồng bộ** của dự án (không gọi Jira) theo project; xem trước rồi nhập.
  Loại: bug / incident → Bug, feature → Story, sub-task Jira → Sub-task của cha đã nhập, còn lại Task.
  Đã đóng → trạng thái Xong + `done` (giữ thời điểm đóng); mở → Cần làm. Giữ tiêu đề, nhãn, thành
  phần, story point, hạn, ngày tạo; người làm khi họ thuộc dự án (không gửi thông báo "giao cho bạn");
  mô tả có link issue Jira; MR / commit đã nối với issue thành liên kết git của task. Mỗi issue chỉ
  nhập một lần (`task_imports`); tuỳ chọn đổi mã Jira trong ô Bug của kết quả test sang mã task.
- **Mã Jira cũ vẫn dùng được:** commit / MR / nhánh ghi `ABC-123` của issue đã nhập nối vào task mới;
  trạng thái bug theo mã cũ cũng trả lời từ task.
- **Cha – con khi nhập:** `parent` của Jira chỉ epic cho story nhưng chỉ story cho sub-task, dòng đồng
  bộ không phân biệt. Suy theo cấu trúc: issue trên cùng có cháu, hoặc có con mang story point trong khi
  nó không có, là **Epic**; con của Epic giữ loại của mình; con của issue trên cùng khác, hoặc cháu của
  Epic, là **Sub-task**; sâu hơn thì bỏ quan hệ cha. Không bao giờ có sub-task làm cha (nhập không gãy).
- **Mã trùng:** không nhập project Jira có mã trùng mã Task của một dự án (409); không đặt mã Task mới
  trùng tiền tố issue Jira đã nhập. Nhờ vậy mã cũ luôn chỉ đúng một thứ.
- **Một lượt nhập một lúc:** lượt nhập khoá không gian task của dự án trước khi đọc danh sách đã nhập.
- **Quyền:** người có `tests.view` mà không xem được Task chỉ thấy trạng thái bug-task, không thấy tiêu
  đề; gắn bug bằng mã task ở dự án mình không xem được thì coi như mã Jira (không lộ task). Mô tả bug từ
  testcase không quét @nhắc (log hay có `@...`).
- Chưa làm (để sau khi gỡ Jira hẳn): nhập bình luận / lịch sử Jira (CodePulse không đồng bộ chúng).

### 19.5 P4 – Tổng quan (Sprint Health, đa dự án)
Chỉ đọc, không migration. Mã: `app/tasks/health.py`, `app/api/routers/task_health.py` (gắn trước router
`tasks` để `/tasks/overview` không bị hiểu là mã task).
- **`GET /tasks/spaces/{bp}/health`** (`tasks.view` trên dự án; 404 như các API không gian khác). Dự án
  không có sprint `active` → `{"sprint": null}` (không 404). Có sprint thì trả:
  - **Dự báo:** các ngày burndown (dùng lại `reports.burndown`), point cam kết, phạm vi (point hiện có
    trong sprint, chỉ Story / Task / Bug), point xong (task ở nhóm Xong), lý tưởng còn lại hôm nay. **Dự
    phóng tuyến tính:** tốc độ = point xong / số ngày đã qua; `projected_done_points` = tốc độ × số ngày
    của sprint (tối đa bằng phạm vi), `projected_pct` = phần trăm của phạm vi. Chỉ tính khi đã qua ≥ 2
    ngày, trước đó `null`. Giao diện vẽ thêm đường "Dự phóng" từ hôm nay tới ngày cuối.
  - **Kẹt:** (1) task mở ở một trạng thái lâu hơn ngưỡng của trạng thái (`age_warning_hours` khi có,
    mặc định 72 giờ cho nhóm Đang làm; nhóm Cần làm không có mặc định), tính từ `status_changed_at`;
    (2) MR đang mở nối với task của sprint, mở quá 24 giờ (`created_at_src` của MR) mà chưa có
    `first_review_at`; (3) task mở bị chặn bởi một task mở khác (liên kết `blocks`).
  - **Tải:** mỗi người (và "Chưa giao") số task mở và point mở trong sprint, **xếp theo tên**, thanh
    ngang, không có lời lẽ xếp hạng. Epic không tính.
  - **Chất lượng:** finding critical còn mở trên review mới nhất của commit / MR nối với task trong
    sprint (`service.git_summaries`), bug mức ưu tiên cao nhất còn mở trong sprint, bug của dự án tạo
    trong khoảng ngày sprint, số task trong sprint bị mở lại (lịch sử `status` từ tên trạng thái nhóm
    Xong sang tên trạng thái mở, trong khoảng ngày sprint; mỗi task tính một lần).
- **`GET /tasks/overview`** (Manager, nhiều dự án; cần `tasks.view`): một dòng cho mỗi dự án bật Task mà
  người xem thấy, theo tên: sprint đang chạy, % point xong, `projected_pct` (cùng cách tính), số bug
  **đang mở có ưu tiên mức `highest`** (khoá ưu tiên → `TaskPriority.level`), số finding critical còn mở
  trên các task đang mở, **lead time trung vị** (tạo → xong, lý do `done`, xong trong 30 ngày qua, giờ),
  **phiên bản kế tiếp** (chưa phát hành, `release_date` sớm nhất, không có ngày xếp sau) với % task xong.
- **Màu sức khoẻ** (`health`, cả hai API): **đỏ** khi dự phóng < 70% hoặc có bug mức cao nhất đang mở
  hoặc có finding critical đang mở; **vàng** khi dự phóng < 90%; còn lại **xanh** (không có sprint hay
  chưa đủ 2 ngày thì chỉ bug / critical quyết định). Giao diện luôn kèm chữ ("Rủi ro", "Cần chú ý",
  "Ổn"), không chỉ màu.
- **Giao diện:** tab "Sức khoẻ sprint" của trang Task (`/tasks?space={bp}&tab=health`) với 4 khối Dự báo,
  Kẹt, Tải, Chất lượng (mỗi khối có skeleton / trống / lỗi + Thử lại); trang `/tasks/overview` là bảng các
  dự án, mỗi dòng mở tab Sức khoẻ sprint của dự án đó.
- Chưa làm: lịch sử sức khoẻ theo thời gian, thông báo tự động khi chuyển đỏ (phần "tự động" của P4).
