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

### 19.3 P2 – Hằng ngày (migration `c9e1a3b5d7f0`)
- **Reviewer của MR:** collector đọc `reviewers` của MR GitLab → `merge_requests.reviewer_account_ids`
  (tài khoản `gitlab_user`; có từ lần đồng bộ tiếp theo). `first_review_at` có sẵn cho thời gian chờ.
- **Việc của tôi** (`GET /tasks/my-work`, trang `/tasks/me`, nút ở đầu trang Task): *Cần xử lý ngay* —
  task của tôi quá hạn, code của task còn critical, MR đang mở tôi là reviewer (không phải tác giả,
  repo tôi xem được, kèm tuổi MR), @nhắc chưa đọc, testcase phủ task đang Failed (§19.4); *Đang làm*;
  *Chờ người khác* (bị chặn bởi task "blocks" còn mở); *Cần làm* (sprint đang chạy trước, tối đa 30);
  *Xong tuần này* (lý do `done`); mỗi sprint đang chạy tôi có task: còn mấy ngày, SP / task tôi xong.
  Mỗi dòng một hành động chính. Tự làm mới mỗi phút. Inbox (spec 24) giữ nguyên.
- **Board:** *swimlane* chọn được (không chia / theo epic / theo task cha / theo người làm / theo ưu tiên,
  nhớ trong trình duyệt); **WIP** theo cột (`task_statuses.wip_limit`, đếm cả các lane, vượt thì cột đỏ,
  không chặn); **tuổi trong cột** (`age_warning_hours`, từ `status_changed_at`); card chỉ hiện **một tín
  hiệu** quan trọng nhất: critical > quá hạn > quá tuổi; **tạo nhanh** ở cuối cột (khi không chia lane):
  `Sửa lỗi OTP @an #bug !cao 3sp` (`@` email hoặc một chữ trong tên, `#` loại, `!` tên / mã / mức ưu tiên
  không dấu, `Nsp`), vào trạng thái của cột và sprint đang chạy. Cài đặt › Trạng thái thêm cột WIP và
  "Cảnh báo sau (giờ)".
- **Sức chứa sprint:** trung bình SP hoàn thành của 3 sprint đóng gần nhất (`capacity` trong sprint đang
  mở; null khi chưa đủ 3); Backlog ghi "sức chứa ~X SP", đỏ khi kéo vào nhiều hơn.
- **Sức khoẻ task:** một dòng dưới tiêu đề: số MR (đã merge), điểm review TB, critical / high mở,
  pipeline, đã deploy chưa; ẩn khi task chưa có code.
- **Ctrl+K / Cmd+K:** mở task theo mã, tìm task theo tiêu đề, lệnh nhanh (Việc của tôi, Task, Tạo task →
  `/tasks?create=1` mở form tạo, Tổng quan, Review, Testcase), ẩn lệnh khi thiếu quyền.
- **Mẫu mô tả theo loại:** Story có "Là … tôi muốn … để …" + tiêu chí chấp nhận; Bug có các bước tái hiện,
  mong đợi / thực tế, môi trường. Đổi loại thì đổi mẫu khi mô tả còn nguyên mẫu.
- Chưa làm ở P2: cập nhật lạc quan + "Hoàn tác 5 giây" cho mọi thao tác (Board đã cập nhật lạc quan khi
  kéo thả), phím tắt E/A/S/C (để P5).

### 19.4 P3 – Khép vòng chất lượng (không có migration; dùng `task_test_links` của P1)
- **Testcase phủ task** (`GET/POST /tasks/{key}/tests`, `DELETE /tasks/{key}/tests/{case_id}`; gắn / gỡ cần
  `tasks.link`, xem cần `tests.view`; testcase phải thuộc dự án của task; nhập `C123` hoặc id): liên kết
  `covers`. Chi tiết task có mục **Testcase**: mỗi case với kết quả lần chạy gần nhất, tổng Passed / Failed.
  Testcase phủ task đang Failed hiện ở *Việc của tôi* của người được giao.
- **Definition of Done** (`rules.dod = {checks, min_score, enforce}`, Task › Cài đặt): chọn từ *Có MR*, *MR
  đã merge* (MR fixes, không có thì mọi MR), *Điểm review đạt* (≥ ngưỡng, mặc định 7), *Không còn
  critical*, *Pipeline không lỗi*, *Testcase đều Passed*. Chi tiết task hiện danh sách tự tích. Bật
  *Bắt buộc*: người chuyển sang Xong (lý do `done`) khi thiếu → 409 kèm mục thiếu; git tự chuyển thì ghi
  lịch sử "chưa chuyển Done: thiếu DoD" và giữ nguyên (như cổng critical). Đóng với lý do khác `done`
  không cần DoD.
- **Sẵn sàng phát hành** (`GET /tasks/versions/{id}/readiness`): mọi task đã xong, không còn bug mức
  Cao / Cao nhất mở, testcase phủ task đều Passed, MR của task đã merge (MR đóng không merge không chặn);
  mỗi mục liệt kê tối đa 20 thứ đang chặn. `POST …/release` khi chưa sẵn sàng → 409; `{force: true,
  reason}` để vẫn phát hành (lý do bắt buộc, ghi audit `task.version.release` kèm mục thiếu). Trang
  Phiên bản mở hộp kiểm tra trước khi phát hành.
- **Retest:** Bug có liên kết `bug` tới kết quả test (tạo từ testcase hoặc gắn tay) chuyển sang Xong với
  lý do `done` → thông báo `task.retest` cho người ghi kết quả và người được giao test; ô Bug của test
  tự hiện "Cần retest" (§19.2).

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

### 19.6 P5 – Mẫu dự án, chế độ Kanban (migration `e2b4d6f8a0c3`)
- **Mẫu khi bật Task** (`PUT /tasks/spaces/{bp}` với `template`: `scrum` mặc định | `kanban` | `qa`;
  `app/tasks/templates.py`). Chỉ áp dụng khi dự án **chưa** bật Task; gửi `template` cho dự án đã bật →
  422 (không âm thầm bỏ qua, vì mẫu không đổi được workflow đang có task). Không gửi mẫu = Scrum, giống
  hệt cách bật cũ.
  - **Scrum:** Cần làm / Đang làm / Đang review / Xong, mọi loại, luật git như trước, bảng Scrum.
  - **Kanban:** Cần làm / Đang làm / Xong; loại Task, Bug, Sub-task; bảng Kanban. Không có trạng thái
    review nên `review_on_mr` tắt (mở MR giữ task ở Đang làm).
  - **QA:** Mới (Cần làm) / Đang xác minh / Đã sửa (Đang làm) / Đã đóng (Xong); loại Bug, Task,
    Sub-task; bảng Scrum. Luật git: bắt đầu → Đang xác minh, `review_on_mr` bật (MR sửa → Đã sửa),
    `done_on_merge` **tắt** (tester đóng sau khi kiểm lại, đúng luồng retest). Ưu tiên mặc định.
  - `types` gửi kèm thắng loại của mẫu; `board_mode` gửi kèm thắng chế độ của mẫu. Mọi thứ sửa được sau
    trong Cài đặt như dự án thường.
- **Chế độ bảng** `task_spaces.board_mode` (`scrum` | `kanban`, mặc định `scrum`, CHECK `board_mode`):
  có trong `GET /tasks/spaces`, đổi qua `PUT /tasks/spaces/{bp}` (`board_mode`, cần `tasks.manage`),
  Task › Cài đặt › "Mã, loại task và chế độ bảng". Kanban: Board không theo sprint (luôn hiện mọi task
  chưa xong + task xong 14 ngày qua), ẩn tab Backlog (link `?tab=backlog` về Board). API sprint vẫn chạy
  bình thường; đổi về Scrum là thấy lại sprint cũ. Đổi chế độ không đổi workflow.
- **Hộp bật Task** thành 3 bước: (1) mã dự án + loại task, (2) chọn mẫu (thẻ có mô tả và chuỗi trạng
  thái), (3) xác nhận. Loại task theo mẫu cho tới khi người quản lý tự chọn ở bước 1.

### 19.8 P5 – Lọc bằng câu, phím tắt, mật độ, điện thoại
- **Lọc nhanh bằng câu** (`POST /tasks/nl-filter {space, text}` → `{chips: [{field, value, label}],
  query, used_ai}`; cần `tasks.view` (403) và xem được dự án (404)). Bộ phân tích **cố định, không
  AI** (`app/tasks/nlfilter.py`): bỏ dấu, khớp cụm dài nhất trước. Hiểu: loại (`bug`, `lỗi`, `story`,
  `tính năng`, `epic`, `sub-task`, `loại task`; chỉ loại dự án dùng), ưu tiên theo **tên / mã của dự án**
  (`cao`, `cao nhất`, `ưu tiên cao`) và từ mức (`khẩn`, `urgent`, `critical` → mức highest đầu tiên),
  trạng thái theo **tên của dự án** (`đang làm` là trạng thái "Đang làm" nếu có, không thì nhóm Đang
  làm qua `category`), nhóm (`chưa làm`, `in progress`, `đã xong`, `hoàn thành`…), người làm (`của tôi`,
  `chưa giao`, `@email-name` hoặc `@một-chữ-trong-tên` khi chỉ khớp đúng một người), sprint (`sprint
  này` = đang chạy, `backlog`, tên sprint như `sprint 12`), phiên bản (`phiên bản v2.1` hoặc tên), nhãn
  (`nhãn X`, `label X`, `#X`), hạn (`quá hạn`; `đến hạn` / `hạn tuần này` = 7 ngày tới), cập nhật
  (`tuần này` = từ thứ Hai, `hôm nay`, `7 ngày qua` → `updated_from`). Từ đệm (`các`, `task`, `việc`,
  `của`…) bỏ qua; từ lạ thành `q`. Mỗi trường một giá trị (Danh sách giữ một giá trị mỗi bộ lọc), từ
  sau thắng. `query` là đúng tham số của `GET /tasks`.
- **Không dùng AI** (`used_ai` luôn `false`): gọi LLM của ứng dụng chạy trong worker (timeout phút),
  không hợp với ô lọc cần trả lời ngay; từ vựng lọc ít nên bộ phân tích cố định đủ. Có thể thêm AI dự
  phòng sau, chỉ gửi câu lọc và tên trạng thái / ưu tiên / loại, trong ngân sách tháng.
- **Giao diện Danh sách:** ô "Lọc nhanh" trên lưới bộ lọc; Enter hoặc "Lọc" → thay toàn bộ bộ lọc bằng
  `query`, hiện chip; bỏ chip là bỏ bộ lọc đó; chip tự ẩn khi bộ lọc đó bị đổi tay. Danh sách nhận thêm
  `category`, `updated_from` (không có ô riêng; "Xoá lọc", "Lưu bộ lọc" vẫn tính).
- **Phím tắt ở chi tiết task:** `E` sửa mô tả, `A` chọn người làm, `S` chọn trạng thái, `C` viết bình
  luận, `.` mở bảng lệnh (như Ctrl+K), `?` hiện danh sách phím tắt. Không chạy khi đang gõ (input,
  textarea, select, contenteditable), khi giữ Ctrl / Alt / Meta, hoặc khi đang mở hộp thoại. Phần tử
  tìm theo `data-shortcut` (`edit-description`, `assignee`, `status`, `comment`).
- **Mật độ "Thoáng / Gọn"** ở Danh sách và Board, nhớ riêng từng màn trong trình duyệt
  (`tk-density-list`, `tk-density-board`); Gọn: ô bảng thấp hơn, ẩn nhãn / badge dưới tiêu đề, card và
  cột Board hẹp hơn.
- **Điện thoại (≤ 640px):** Danh sách ẩn cột SP và Cập nhật (bảng vẫn cuộn trong khung của nó); chi
  tiết task một cột, khung thông tin nằm **dưới** nội dung chính; khối code, bảng, ảnh trong mô tả không
  làm tràn trang; Việc của tôi xuống dòng hành động. Chỉ CSS, không đổi component.
- Chưa làm: AI dự phòng cho câu lọc; một trường nhiều giá trị ("bug hoặc story"); phím tắt trên Board /
  Danh sách (J/K di chuyển dòng).

### 19.7 P4 – Quy tắc tự động, thông báo (không migration)
- **Thư viện quy tắc, không có bộ dựng quy tắc tự do.** Mỗi quy tắc bật / tắt theo dự án, vài quy tắc có
  một tham số; lưu ở `task_spaces.rules["automation"]` dạng `{quy_tắc: {"on": bool, ...}}`
  (`app/tasks/automation.py`). `GET /tasks/spaces/{bp}/automation` (`tasks.view`), `PUT` (`tasks.manage`,
  chỉ gửi quy tắc cần đổi; tên / tham số lạ → 422; audit `task.automation`). Task › Cài đặt › **Quy tắc
  tự động**: công tắc, mô tả ngắn, ô số cho tham số. Form "Luật đồng bộ git" gửi lại cả `rules` nên
  `PUT /tasks/spaces/{bp}` bỏ qua khoá `automation`.
- **Các quy tắc** (mặc định tắt hết trừ quy tắc giao diện, để dự án cũ không bỗng bị nhắc):

  | Khoá | Khi nào | Làm gì |
  |---|---|---|
  | `parent_done_ask` (bật sẵn) | Chuyển task cha sang nhóm Xong khi còn sub-task mở | Hỏi đóng luôn các sub-task. `GET /tasks/{key}/open-subtasks`, `POST /tasks/{key}/close-subtasks {resolution}` (409 khi quy tắc tắt hoặc task cha chưa ở nhóm Xong): đóng sub-task người bấm được chuyển trạng thái, sang trạng thái "xong" của luật git (không xét luồng chuyển; cổng critical vẫn chặn, trả về trong `skipped`). Hộp hỏi `ParentDoneAsk.tsx` có sẵn, **chưa gắn vào TaskDetail** (ngoài phạm vi file lần này). |
  | `subtasks_done_review` | Sub-task cuối cùng của task cha vào nhóm Xong | Task cha đang ở Cần làm / Đang làm sang trạng thái "review" của luật git (theo luồng chuyển; luồng cấm thì bỏ qua). |
  | `assign_on_start` | Task chưa giao vào nhóm Đang làm, người chuyển là người (web / MCP) | Giao cho người chuyển. |
  | `bug_highest_notify` | Tạo Bug có ưu tiên mức "highest" | Báo người có quyền dự án `manage` (Lead / Manager) trong hộp thư (`task.alert`); bot Telegram của dự án có `allowed_chat_ids` thì đăng vào các chat đó (job RQ sau khi commit). Bot trả lời mọi chat (không ghi chat) thì không đăng: không biết nhóm nào. |
  | `critical_reassign` | Task vào trạng thái review / nhóm Xong, và mỗi ngày với task mở có code | Code của task còn finding critical mở → báo người làm. **Không tự đổi người làm**: người viết code là người sửa nhanh nhất, đổi người tự động làm mất dấu trách nhiệm và giẫm lên phân công của lead. |
  | `unblock_notify` | Task "blocks" task khác vào nhóm Xong và task kia không còn gì chặn | Báo người làm của task kia. |
  | `review_stale_remind` (giờ, mặc định 48) | Hằng ngày | Task nằm ở trạng thái review lâu hơn N giờ (`status_changed_at`) → nhắc người làm và người tạo. |
  | `overdue_remind` | Hằng ngày | Task mở quá hạn → nhắc người làm. |
  | `due_soon_remind` (ngày, mặc định 1) | Hằng ngày | Task mở tới hạn trong N ngày → nhắc người làm. |

- **Tự động là hệ thống:** lịch sử không có người (`user_id` null, `via` web), thay đổi đi qua đúng hàm
  của service (luồng chuyển, cổng critical). Một quy tắc không bao giờ kích hoạt chính nó hay quy tắc khác
  trong cùng lời gọi (`ContextVar`, an toàn giữa các luồng của API). Mỗi quy tắc chạy trong SAVEPOINT: lỗi
  chỉ huỷ phần của nó, không huỷ thao tác của người dùng. Móc vào `service.set_status` (sau khi đổi, trước
  flush) và `service.create_task` (cuối hàm).
- **Nhắc tối đa một lần mỗi task, người, ngày:** `ref` của thông báo là `auto:<rule>:<task id>:<ngày địa
  phương>`, tìm trong lịch sử và trong thông báo đang giữ trước khi ghi. Nhắc từ 08:00 (REPORT_TZ). Job
  scheduler `task-automation` mỗi 15 phút (`app/workers/task_automation_jobs.py`).
- **Thông báo đúng lúc (cài đặt của từng người):** `app_settings` khoá `task_notify:<user id>` (không cần
  migration): `mode` `immediate | digest`, `quiet_hours` `[bắt đầu, kết thúc)` giờ địa phương (qua nửa đêm
  được), `kinds` loại tắt trong `task.assigned, task.status, task.commented, task.mentioned, task.remind,
  task.alert`. `GET/PUT /me/task-notify`; giao diện ở trang **Thông báo** (thẻ "Thông báo Task", chỉ khi có
  `tasks.view`).
  - Áp dụng ở `app.notifications.inbox.notify` cho mọi loại `task.*` (nên `service.tell` không đổi): loại
    tắt → bỏ; chế độ gộp hoặc giờ yên lặng → **giữ** (`app_settings` `task_notify_held:<user id>`, thêm
    nguyên tử bằng `INSERT … ON CONFLICT … value || excluded.value`). Thông báo review / khiếu nại không bị ảnh hưởng.
  - Job mỗi 15 phút: chế độ gộp gửi mỗi ngày một lần từ 08:00 (ngoài giờ yên lặng); chế độ ngay chỉ có thông
    báo bị giữ vì giờ yên lặng, gửi khi hết giờ. Thông báo đang giữ vào lịch sử dạng **đã đọc** (giữ thời
    điểm gốc), cộng **một** thông báo chưa đọc `task.digest` "N cập nhật trên CRM-2, CRM-5 …" (mở thẳng task
    khi chỉ có một task). Giữ tối đa 300 mục mỗi người (bỏ mục cũ nhất khi thêm). Ngày gửi bản gộp gần nhất ở khoá riêng `task_notify_digest:<user id>`; job khoá hàng đang giữ bằng `SKIP LOCKED` (không chờ, không deadlock với request đang thêm); mỗi dự án chạy trong SAVEPOINT riêng.
- Chưa làm: gắn `ParentDoneAsk` vào chi tiết task; nhãn / biểu tượng cho các loại `task.*` trong
  `KIND_LABEL` của chuông (đã thiếu từ trước, áp dụng cho mọi loại task); múi giờ riêng từng người (dùng
  REPORT_TZ).

## 20. Lộ trình đợt 2 (người dùng 2026-11-16: "làm toàn bộ theo đề xuất đi")

Nguồn: "CodePulse Task: Định hướng nâng cấp, đợt 2" và bản phản biện đã chốt. Nhiều mục "còn tồn" của tài
liệu đã có từ đợt 1 nhưng khó thấy (Ctrl+K không có nút, mẫu chỉ hiện khi bật Task lần đầu, sức chứa cần ≥ 3
sprint đóng, tuỳ chọn thông báo ở trang Thông báo): đợt 2 sửa cách thấy, không làm lại. Quyết định đã chốt:

- R4: **không** gom sub-task vào card cha làm mặc định (người làm sub-task sẽ mất việc của mình trên Board);
  có công tắc "Gom sub-task vào card cha", nhớ theo người.
- Phím tắt: `N` tạo task (không dùng `C`, đang là bình luận trong chi tiết), `/` tìm, `G B`, `G M`, `?`.
- Tạo nhanh lưu ngay; chỉ hiện chip để sửa khi có chỗ mơ hồ.
- Không thêm loại task "Test" (dùng testcase + liên kết covers); mẫu QA dùng Bug / Task.
- Hoàn tác cho sửa hàng loạt / đổi trạng thái; xoá giữ hộp xác nhận (không xoá mềm).
- Đo sử dụng giao diện chỉ đếm gộp theo ngày và dự án, không theo người.
- `obsolete` (Lỗi thời) được thêm như lý do đóng không có đầu ra; velocity chỉ tính lý do `done`.

| Giai đoạn | Nội dung | Mục |
|---|---|---|
| 2.0 Tin cậy & thấy được | R3, velocity, `obsolete`, phạm vi Team (R1), đồng bộ team từ nhóm GitLab (R2), nút Ctrl+K / `?`, áp mẫu cho dự án đã bật, sẵn sàng phát hành trên danh sách phiên bản, đổi tên trạng thái sang tiếng Việt | 20.0, 20.7 |
| 2.1 Một màn hình | Sức khoẻ gộp, dòng thời gian gộp, ô bình luận cố định `@` / `#`, ẩn trường trống, huy hiệu bị chặn / MR đóng | 20.1 |
| 2.2 Nhanh tay | Lệnh trên task trong Ctrl+K, phím tắt, `^OCX-1`, công tắc gom sub-task | 20.2 |
| 2.3 QA | Bug từ từng bước test, giao mặc định, retest vào Việc của tôi, tiêu chí chấp nhận có mã nối testcase | 20.3 |
| 2.4 Nhân rộng | Mẫu lưu được, mẫu Mobile, Tình trạng bật Task, hướng dẫn bật đủ bước | 20.4 |
| 2.5 Thông báo & quy tắc | Kênh theo sự kiện, Bug Cao nhất bỏ qua giờ yên lặng, gom 10 phút, tóm tắt 17:30 Telegram, tạm ẩn, nhật ký + chạy thử, 5 quy tắc mới | 20.5 |
| 2.6 Đánh giá sprint | Tóm tắt khi đóng sprint + trang retro, thay đổi phạm vi, dự báo có khoảng, xu hướng 4 sprint, trạng thái rỗng | 20.6 |

### 20.0 Tin cậy & thấy được (migration `a1c3e5f7b9d1`)
- **R3:** `POST /tasks/{key}/move` nhận `resolution` và kiểm tra như `transition` (giá trị lạ → 422; trước
  đây bị bỏ qua và lưu `done`). Board: kéo vào nhóm Xong đóng với "Đã xong" ngay, kèm thanh "Lý do khác:
  Không làm · Trùng · …" đổi bằng một bấm (không hỏi trước).
- **`obsolete` (Lỗi thời)** thêm vào lý do đóng; như các lý do khác `done`, không phải đầu ra (DPI bỏ qua).
- **Velocity, sức chứa, SP xong của sprint chỉ tính lý do `done`** (task đóng không làm không còn đẩy velocity).
  Danh sách lọc `resolution=`; Báo cáo có thẻ "Đóng theo lý do".
- **R1 – phạm vi Team:** `tasks.edit/assign/transition/delete/worklog/link` (own · team · project · all),
  `tasks.moderate` (team · project · all) và `tests.run` (team · project · all). Team = task do người mình
  lead hoặc manage tạo / được giao (delete: tạo), trong dự án mình xem được; với `tests.run` là ghi kết quả
  cho test giao cho người trong team. Bộ quyền Lead có sẵn nhận các quyền này ở mức team (migration chỉ
  thêm, không ghi đè chỉnh sửa của admin). Quyết định: **không** thêm team cho `tests.edit/approve/manage/
  delete` (testcase là tài sản chung của dự án, quyền kiểm theo dự án ở mọi nơi) và `tasks.sprint` (sprint
  thuộc dự án).
- **R2:** vai trò tester = bộ quyền "Tester" gán cho team (21 §11, đợt 1) + đồng bộ team từ nhóm GitLab (§20.7).
- **Thấy được:** nút "Đổi tên sang tiếng Việt" ở Cài đặt › Trạng thái (To Do → Cần làm, In Progress → Đang làm,
  In Review → Đang review, Done → Xong; smart commit vẫn nhận `#done`, `#in-progress`); danh sách Phiên bản
  hiện "Sẵn sàng / Chưa sẵn sàng (n mục)". Nút Ctrl+K và `?` trên thanh trên cùng: §20.2; áp mẫu cho dự án đã
  bật: §20.4.

### 20.2 Nhanh tay
- **Thấy được:** thanh trên cùng có nút "Tìm / lệnh" (kèm `Ctrl K`; điện thoại chỉ còn biểu tượng) mở
  bảng lệnh, và nút `?` mở danh sách phím tắt. Nút, phím tắt và bảng lệnh gặp nhau qua sự kiện cửa sổ
  (`cp:palette-open {mode}`, `cp:shortcut-help`, `components/tasks/shortcutEvents.ts`), không cần context.
- **Lệnh trên task đang mở** (URL `/tasks/KEY` hoặc `/tasks?task=KEY`, đọc lúc mở bảng lệnh): *Giao cho
  tôi* (`PATCH /tasks/{key} {assignee_id}`; cần `can.assign`, ẩn khi đã là của tôi); *Đổi trạng thái…*
  (danh sách con từ `transitions` của chi tiết; `POST /tasks/{key}/transition`, trạng thái nhóm "done"
  gửi `resolution: "done"`; còn finding critical thì phải Enter lần hai; cần `can.transition`); *Thêm vào
  sprint…* (sprint chưa đóng của dự án, trừ sprint hiện tại, thêm "Backlog (bỏ khỏi sprint)"; `POST
  /tasks/{key}/move {sprint_id}`; cần `can.edit`, ẩn với dự án Kanban và epic); *Sao chép link*
  (`/tasks/KEY`). Lỗi API hiện ở chân bảng lệnh, bảng lệnh giữ mở; thành công thì đóng và làm mới task.
  Hỏi đóng sub-task còn mở / lý do đóng khác chỉ có trong chi tiết task.
- **Mọi trang:** *Chuyển dự án…* (`GET /tasks/spaces` → `/tasks?space=`; dự án chưa bật ghi "Chưa bật
  Task") và *Bộ lọc đã lưu…* (`GET /tasks/filters` → `/tasks?space=&tab=list&filter=ID&<query>`). Danh
  sách con: gõ để lọc, Backspace (ô trống) / Esc quay lại. Mỗi danh sách chỉ tải khi mở.
- **Phím tắt mọi trang:** `N` tạo task (`/tasks?create=1`, giữ `space` khi đang ở trang Task; cần
  `tasks.create`), `/` mở bảng lệnh ở chế độ chỉ tìm task, `G` rồi `B` (trong 1,5 giây) → Board
  (`/tasks?tab=board`), `G` rồi `M` → `/tasks/me` (cần `tasks.view`), `?` bật / tắt danh sách phím tắt.
  Phím tắt chi tiết (`E`, `A`, `S`, `C`, `.`) giữ nguyên; `.` mở bảng lệnh (có lệnh trên task). Cùng điều
  kiện §19.8: không khi đang gõ, giữ Ctrl / Alt / Meta, hay có hộp thoại (`?` vẫn đóng được chính danh sách
  phím tắt). Danh sách phím tắt chia "Mọi trang" và "Khi đang mở một task".
- **Tạo nhanh `^`:** `^OCX-12` (hoặc `^12` = mã dự án) đặt `parent_key`. Cha là story / task / bug và dự án
  có loại sub-task, không ghi `#loại` → task tạo ra là sub-task; cha là epic → loại giữ như gõ / mặc định;
  cha là sub-task hoặc không tìm thấy → báo lỗi, không tạo. Cha tìm trên Board trước, không có thì `GET
  /tasks/{key}`.
- **Chip khi mơ hồ:** `@an` khớp nhiều người (hoặc `!cao` khớp theo mức nhiều ưu tiên) → không báo lỗi mà
  hiện chip dưới ô: mỗi chỗ mơ hồ chọn một giá trị hoặc "Bỏ", rồi "Tạo task" hoặc Enter. Sửa dòng chữ thì
  bỏ các lựa chọn cũ. Dòng không mơ hồ vẫn lưu ngay khi Enter.
- **Gom sub-task vào card cha** (công tắc trên Board, mặc định tắt, nhớ trong trình duyệt
  `tk-board-group-subs`; chỉ hiện khi dự án có loại sub-task; tắt được khi swimlane "Theo task cha" vì đã gom
  theo lane): bật thì card sub-task có cha trên Board (sau bộ lọc) bị ẩn; card cha có thanh `xong/tổng`
  (số của server) bấm để mở danh sách sub-task (mã, tiêu đề, trạng thái, người làm; bấm mở task), ghi "+N
  sub-task không ở board này". Sub-task có cha không ở Board vẫn là card. WIP của cột vẫn đếm cả sub-task bị
  gom.
- Chưa làm: Danh sách chưa đọc tham số `filter` / bộ lọc trên URL (bộ lọc đã lưu mở đúng dự án và tab Danh
  sách nhưng cần một sửa nhỏ trong `TaskList` để tự áp); hoàn tác sau lệnh của bảng lệnh (để mục Hoàn tác
  chung).

### 20.1 Một màn hình

Chi tiết task đủ ngữ cảnh trên một màn hình (roadmap đợt 2 §3.1, §3.3 phần huy hiệu). Không migration.

- **Dòng thời gian** `GET /api/v1/tasks/{key}/timeline?types=comment,history,worklog,git,review,test,deploy&before=<iso>&limit=50`
  (quyền `tasks.view` trên dự án của task; `app/tasks/timeline.py`, router `task_timeline.py` đứng trước
  router `tasks`). Một danh sách, mới nhất trước, mỗi mục `{id, at, type, actor, text, link, data}`:
  - `comment` (TaskComment, `data.body`, `data.can_edit`), `history` (TaskHistory), `worklog` (TaskWorklog,
    xếp theo lúc ghi `created_at`; `data.day` là ngày được ghi).
  - `git`: mỗi liên kết nhánh / commit / MR lúc được nối (`TaskGitLink.created_at`, người nối nếu nối tay);
    MR thêm một mục lúc merge (`merged_at`) hoặc lúc đóng không merge (`closed_at`).
  - `review`: mọi review `done` của commit / MR đã nối (lúc `finished_at`), điểm, số finding critical / high.
  - `test`: kết quả mới nhất (có trạng thái) của từng test trong từng run, của các testcase nối với task
    (`covers` hoặc `bug`); chỉ khi người xem có `tests.view`.
  - `deploy`: lần lên production đầu tiên, đúng cái `git_detail` tính (`summary.deployed`).
  - `types` rỗng = tất cả, loại lạ → 422. `before` lấy cả mốc (`at <= before`), `limit` 1..200; trang sau
    dùng `at` của mục cuối, các mục cùng mốc trả lại và giao diện bỏ những `id` đã có (nhiều hơn `limit`
    mục cùng một mốc thì dừng). Mỗi nguồn lấy tối đa `limit` mục mới nhất rồi gộp, cắt còn `limit`; mục
    cùng lúc xếp theo loại rồi số id.
  - Test chỉ lấy run của dự án người xem thấy được (testcase có thể nối với task của dự án khác). Review
    của commit khớp theo SHA (review không mang repo), như `latest_reviews`.
- **Giao diện chi tiết** (`TaskDetail.tsx`, `TaskActivity.tsx`):
  - Ba tab Bình luận / Lịch sử / Giờ làm thay bằng **Dòng thời gian** với chip `Tất cả | Bình luận | Code |
    Test | Thay đổi | Giờ làm` (Code = git + review + deploy). Chip Giờ làm hiện bảng giờ làm và form ghi giờ
    như cũ. Bình luận vẫn sửa / xoá ngay trên dòng thời gian. "Xem cũ hơn" tải trang tiếp theo.
  - **Ô bình luận cố định** cuối cột chính (sticky): `@` gợi ý người của dự án (`/tasks/people`), chèn
    `@<phần trước @ của email>` (đúng cách `mentioned()` đọc); `#` gợi ý task (`/tasks?q=&space=&limit=8`),
    chèn mã. Mũi tên chọn, Enter / Tab chèn, Esc đóng gợi ý, Ctrl+Enter gửi. Dán / kéo thả file như cũ.
  - **Khối Sức khoẻ** luôn ở đầu: ⛔ bị chặn bởi KEY (trạng thái), số MR + đã merge, điểm review, critical /
    high, pipeline (khi có MR), deploy, MR bị đóng không merge, Testcase x/y Passed, DoD x/y. Bấm một mục
    cuộn tới phần của nó (Liên kết, Code, Testcase, DoD). Mục không áp dụng thì ẩn (chưa có code, dự án
    chưa đặt DoD, chưa có testcase hoặc không có `tests.view`).
  - **Cột phải** chỉ hiện trường có giá trị, luôn có Trạng thái, Người làm, Ưu tiên, Sprint (và Loại, Theo
    dõi); trường tuỳ chỉnh bắt buộc luôn hiện. "+ Thêm trường" mở các trường còn trống (chỉ trong lượt xem
    này, không lưu).
- **API bổ sung** (chỉ thêm trường): TaskRow có `blocked` (một task chưa Xong `blocks` nó, ở bất kỳ dự án nào: `rows()` không biết người xem, nên card có thể hiện ⛔ khi chi tiết không liệt kê được task chặn) và `mr_closed` (có
  MR nối và mọi MR đều `closed`, không MR nào mở hay đã merge). Chi tiết task có
  `blocked_by: [{key, title, status}]` (task chưa Xong chặn nó, người xem thấy được; `status` là tên trạng thái).
- **Card Board** hiện một huy hiệu theo thứ tự: ⛔ bị chặn > critical > quá hạn > quá tuổi cột > 🔁 MR bị đóng
  (task đã Xong không hiện huy hiệu nào).

### 20.3 QA (migration `b2d4f6a8c0e2`)
- **Bug từ một bước Failed** (`POST /testcases/tests/{id}/bug` nhận `step`, đếm từ 0): bước đó phải
  Failed trong kết quả được chọn (không thì 422). Tiêu đề "[C123] <nội dung bước> – Failed"; mô tả chỉ
  gồm các bước tới bước lỗi (mong đợi / thực tế), phần còn lại (tiền điều kiện, môi trường, log, đính
  kèm, link) như cũ. Vẫn một bug cho mỗi kết quả Failed (409 lần hai, kể cả tạo từ bước khác). Áp
  cho cả đích Jira ở API; giao diện chỉ thêm nút "Tạo bug" cạnh từng bước Failed trong lịch sử kết quả
  của ngăn test khi đích là task CodePulse, kết quả chưa có bug và người xem ghi được kết quả.
- **Giao mặc định:** testcase có liên kết `covers` tới task → người xử lý mặc định của bug là người làm
  task đó (task còn mở trước, rồi liên kết mới nhất; người đã rời dự án thì bỏ). API dùng khi không
  gửi `assignee_id` (gửi `null` = "Chưa giao") và người tạo được giao (chính mình, hoặc có
  `tasks.assign`). `GET /testcases/tests/{id}/bug/defaults` → `{assignee_id, task_key}` để hộp tạo bug
  chọn sẵn.
- **Retest trong Việc của tôi:** mục *Cần xử lý ngay* loại `retest` cho test của run đang mở cần chạy
  lại (logic `bugs.retest_ids`: Failed, mọi bug đã xong) mà test giao cho tôi hoặc kết quả Failed gần
  nhất do tôi ghi; chỉ dự án tôi có `tests.view`; tối đa 20. Chữ "Cần chạy lại test Txx (bug KEY đã
  xong)", nút Mở tới `/testcases?project=..&tab=runs&run=..&test=..` (trường `link`; không gắn
  `task_key` để nút mở đúng run).
- **Tiêu chí chấp nhận có mã** (bảng `task_criteria`: task, vị trí, nội dung ≤ 500, đạt, người tạo;
  `task_test_links.criterion_id` SET NULL): `GET/POST /tasks/{key}/criteria`,
  `PATCH/DELETE /tasks/{key}/criteria/{id}` (`{text?, done?, position?}`; sửa / thêm / xoá / đổi chỗ cần
  `tasks.edit`, tối đa 50 tiêu chí, ghi lịch sử trường `criterion`). Gắn testcase:
  `POST /tasks/{key}/tests {case, criterion_id?}`; gửi lại cùng testcase với `criterion_id` khác (hoặc
  `null`) thì chuyển tiêu chí — một testcase chỉ nối một tiêu chí của task (ràng buộc duy nhất cũ).
  `quality.coverage` (và `GET /tasks/{key}/tests`) thêm `criteria: [{id, position, text, done, cases,
  status}]`, `status`: `none` (chưa có testcase) | `failed` (có case Failed) | `passed` (mọi case
  Passed) | `untested`; mỗi case có `criterion_id`. Người không có `tests.view` thấy tiêu chí, không
  thấy testcase (`status: null`). Xoá tiêu chí: testcase vẫn phủ task.
- Giao diện chi tiết task: mục "Tiêu chí chấp nhận" (AC1, AC2… theo thứ tự; tích đạt, sửa, lên / xuống,
  xoá, thêm), mỗi tiêu chí kèm testcase và trạng thái, không có thì "chưa có testcase"; mục Testcase có
  ô chọn tiêu chí khi gắn và trên từng dòng. Story tạo từ mẫu mô tả không tự sinh tiêu chí.
- Chưa làm: biểu tượng riêng cho loại `retest` ở trang Việc của tôi (đang dùng biểu tượng mặc định,
  `MyWork.tsx` ngoài phạm vi đợt này); tạo bug từ bước cho đích Jira trên giao diện.

### 20.7 Đồng bộ team từ nhóm GitLab (R2)

Mục đích: gán bộ quyền "Tester" cho team QA (spec 21 §11) thành tự động: ai vào nhóm GitLab QA thì vào team
QA và có bộ quyền, ai rời nhóm thì rời team và mất quyền. Migration `e5a7c9d1f3b6`.

- **Dữ liệu**: `teams.gitlab_group_path` (đường dẫn đầy đủ, ví dụ `omicx/qa`; NULL: team quản lý tay),
  `teams.gitlab_sync_at`, `teams.gitlab_sync_error` (NULL: lần cuối thành công). `team_members.source`:
  `manual` (mặc định, mọi dòng cũ, Admin / CSV) hoặc `gitlab` (do đồng bộ thêm).
- **Chỉ thành viên trực tiếp** của nhóm (`GET /groups/:path/members`, không `/members/all`): thành viên kế thừa
  từ nhóm cha (`omicx`) sẽ kéo cả công ty vào team QA; thành viên nhóm con không tính (nhóm con có team riêng).
  Bỏ qua: tài khoản bị khoá, lời mời chưa nhận, quyền "minimal access" (< guest), bot (mẫu tên như
  autolink), người CodePulse đã tắt.
- **Ánh xạ**: thành viên GitLab → `accounts` (`kind = gitlab_user`, `external_key` = id GitLab) → `user_id`.
  Thành viên chưa gắn người được liệt kê trong kết quả (`unmapped`), **không** tự tạo người (gắn ở Người &
  Team › Tài khoản hoặc "Từ GitLab").
- **Áp dụng** (khoá dòng team khi ghi, nút và job không thêm trùng):
  - người được ánh xạ chưa ở team hôm nay → thêm dòng `role = member`, `joined_at` = hôm nay (UTC, như
    `load_principal`), `source = gitlab`; nếu có dòng vào hôm nay đã kết thúc hôm nay thì mở lại dòng đó;
  - dòng `source = gitlab`, vai trò member, của người không còn trong nhóm → `left_at` = hôm nay (giữ lịch
    sử);
  - **không bao giờ** đụng dòng thêm tay (`manual`) hay dòng lead / manager (kể cả dòng do đồng bộ thêm rồi
    admin nâng vai trò).
  - Lỗi GitLab (không kết nối nào thấy nhóm, mạng, 5xx sau khi thử lại) không đổi thành viên nào; lỗi ghi vào
    `gitlab_sync_error`.
- **Kết nối**: đọc qua kết nối GitLab (`connections.kind = gitlab`) đầu tiên thấy được nhóm (404 / 403 thì thử
  kết nối kế), cùng token với repo. Gọi qua `GitLabClient.group_members` (thử lại, timeout, giới hạn tốc độ như
  collector); nút bấm dùng ít lần thử hơn, timeout ngắn hơn vì có người chờ.
- **Lịch**: job `team-gitlab-sync` mỗi giờ (`app/workers/team_gitlab_jobs.py`, chạy trên worker hàng
  `default`), mỗi team một giao dịch. Audit `team.gitlab_sync` (người bấm hoặc NULL cho job) khi có thay đổi.
- **Quyền**: chỉ **admin** gắn / đổi / bỏ nhóm (`PUT /teams/{id}/gitlab {group_path}`, nhận cả link nhóm):
  ai trong nhóm GitLab sẽ có các bộ quyền gán cho team, vượt qua các chặn của `admin.people` uỷ quyền (spec 21
  §3); audit `team.gitlab_group`. "Đồng bộ ngay" (`POST /teams/{id}/gitlab/sync`): `admin.people`, người
  không phải admin không đồng bộ team của chính mình. Bỏ gắn nhóm giữ nguyên thành viên đang có (rời tay).
- **Hệ quả**: bộ quyền gán cho team (`team_permission_sets`) theo thành viên, nên đi theo nhóm GitLab, không cần
  thêm gì. Người ra khỏi nhóm GitLab mất quyền ngay ở lần đồng bộ kế (left_at = hôm nay, ngày rời không tính).
- **Giao diện**: Admin › Người & Team › Team, khung thành viên: dòng "Theo nhóm GitLab …, lần cuối …", nút
  "Đồng bộ ngay", "Gắn nhóm GitLab" / "Đổi nhóm" (lưu rồi đồng bộ luôn), "Bỏ gắn"; kết quả ghi số thêm / rời
  và tên GitLab chưa gắn người. Dòng thành viên do đồng bộ thêm có nhãn "GitLab".
- Admin rút tay ("Rời team") một người do đồng bộ thêm mà người đó vẫn ở nhóm GitLab: lần đồng bộ sau thêm lại.
  Muốn loại hẳn thì bỏ họ khỏi nhóm GitLab hoặc bỏ gắn nhóm.

### 20.4 Nhân rộng (migration `c3e5a7b9d1f4`)
- **Mẫu là một payload JSON** (`app/tasks/templates.py`): `types`, `board_mode`, `statuses` (tên, nhóm,
  `wip_limit`, `age_warning_hours`, `transitions` theo **tên**), `priorities` (tên, mức, màu, mặc định),
  `rules` (cờ luật git, `start` / `review` / `done` theo tên trạng thái, `dod`, `automation`), `fields`
  (định nghĩa `TaskField`: tên, loại, lựa chọn, bắt buộc). Không theo id nên chuyển được giữa dự án.
- **Mẫu có sẵn** vẫn nằm trong code: Scrum, Kanban, QA (giữ nguyên §19.6) và **Mobile** mới = Scrum + trường
  chọn một "Nền tảng" (iOS / Android / Cả hai). Bảng gợi ý của tài liệu đợt 2 (Scrum có DoD đủ 6 mục,
  Kanban WIP 3 / 5, QA dạng Kanban có loại Test) **không** áp: đổi mẫu có sẵn làm dự án mới khác hẳn dự án
  bật trước đó, và đợt 2 đã chốt không thêm loại "Test". Ai muốn cấu hình đó thì lưu thành mẫu riêng.
- **Mẫu đã lưu** (bảng `task_space_templates`: `key` duy nhất, `name`, `description`, `payload` JSONB,
  `builtin` (luôn false khi lưu từ dự án; để dành cho mẫu do admin nạp), `created_by`):
  - `POST /tasks/spaces/{bp}/save-template {name, description}` (`tasks.manage` của dự án) chụp cấu hình
    hiện tại (bỏ trường đã lưu trữ). Tên trùng (không phân biệt hoa thường, kể cả tên mẫu có sẵn) → 409.
    `key` = tên bỏ dấu dạng `cau-hinh-crm`, thêm `-2`… khi trùng. Audit `task.template.save`.
  - `GET /tasks/templates` (`tasks.view`): mẫu có sẵn trước rồi mẫu đã lưu, mỗi mẫu có chuỗi trạng thái,
    loại, chế độ bảng, tên ưu tiên, tên trường, `has_dod`, `has_automation`, `can_delete`.
  - `DELETE /tasks/templates/{id}`: chỉ mẫu đã lưu; người tạo hoặc admin (403 với người khác). Dự án đã áp
    mẫu giữ nguyên cấu hình (chỉ còn `template_key` cũ). Audit `task.template.delete`.
- **Áp mẫu cho dự án đã bật** (`POST /tasks/spaces/{bp}/apply-template {key, types?, board_mode?}`,
  `tasks.manage`; khoá hàng `task_spaces` trong lúc áp). Trả `{mode, applied, skipped, space}`:
  - **Chưa có task** (`full`): thay toàn bộ: loại, chế độ bảng, trạng thái (trạng thái cùng tên giữ id),
    luồng chuyển, WIP / tuổi, ưu tiên (cùng tên giữ key), luật git, DoD, quy tắc tự động (mẫu không có thì
    về mặc định), trường tuỳ chỉnh (trường không có trong mẫu được lưu trữ).
  - **Đã có task** (`partial`): chỉ phần không mất dữ liệu: thêm loại task còn thiếu, chế độ bảng, thêm mức
    ưu tiên còn thiếu theo tên (tối đa 12, phần thừa vào `skipped`), cờ luật git, DoD, quy tắc tự động,
    thêm trường còn thiếu theo tên (trường lưu trữ cùng tên cùng loại thì hiện lại; khác loại thì bỏ qua).
    Trạng thái và luồng chuyển **giữ nguyên**; `start` / `review` / `done` chỉ đổi khi dự án có trạng thái
    cùng tên, không thì giữ như cũ và ghi vào `skipped`.
  - Ghi `task_spaces.template_key` (có trong `GET /tasks/spaces`). Bật Task bằng `PUT` cũng ghi mẫu đã dùng.
    Audit `task.template.apply`.
- **Task › Cài đặt › Mã, loại task và chế độ bảng**: dòng "Mẫu đang dùng", nút **Áp mẫu** (hộp chọn mẫu, xoá
  mẫu đã lưu của mình, kết quả "Đã áp / Bỏ qua"; đóng hộp thì tải lại trang để mọi thẻ cài đặt lấy cấu hình
  mới) và **Lưu cấu hình thành mẫu**.
- **Hướng dẫn bật Task** (EnableSpace): (1) mã + loại, (2) mẫu (có sẵn + đã lưu), (3) **thành viên**,
  (4) xác nhận, rồi (5) **Nhập từ Jira** chỉ khi dự án có issue Jira đã đồng bộ (`GET /tasks/spaces/{bp}/import/jira`
  trả `sources` không rỗng; dùng lại thẻ Nhập từ Jira). Scrum / Kanban / QA bật bằng `PUT` như cũ; Mobile và
  mẫu đã lưu: `PUT` (Scrum) rồi `apply-template` ngay (dự án chưa có task nên áp toàn bộ).
  - Bước thành viên: `GET /tasks/spaces/{bp}/members` (`tasks.manage` của dự án): người có quyền vào dự án
    (`project_grants`, đang hoạt động) và **gợi ý** = người dùng có tài khoản liên kết là tác giả commit trong
    các repo của dự án 90 ngày qua mà chưa có quyền (tối đa 50, nhiều commit trước), `can_grant` =
    `can_staff_project`. "Cấp quyền xem" dùng lại `GET` + `PUT /business-projects/{id}/grants` (đọc danh
    sách hiện tại, thêm `view`; mọi luật chặn leo quyền của spec 21 §10 giữ nguyên). Không có quyền cấp thì
    chỉ xem và được nhắc nhờ Manager dự án / admin.
- **Tình trạng bật Task** (`GET /tasks/adoption`, trang `/tasks/adoption`, nút ở đầu trang Task cho người có
  `tasks.manage` hoặc `admin.projects`): mỗi dự án: đã bật chưa, mã, mẫu (`template_key` + tên), số task, số
  task chưa ở nhóm Xong, hoạt động gần nhất (max `tasks.updated_at` / bình luận), số người xem được Task
  (người đang hoạt động có grant dự án hoặc `manage_all` mà bộ quyền có `tasks.view`; admin theo
  ADMIN_EMAILS không có grant thì không đếm). Admin / `admin.projects` thấy mọi dự án kể cả chưa bật (link
  "Bật Task" → `/tasks?space=<id>`, mở hướng dẫn bật); người chỉ có `tasks.manage` thấy dự án mình xem được
  đã bật. Thiếu cả hai quyền → 403.
- Chưa làm: mẫu do admin nạp sẵn (`builtin = true`) chưa có màn quản lý; số người xem được tính bằng nạp bộ
  quyền từng người (chậm dần khi có hàng trăm người, đủ cho trang quản trị).

### 20.5 Thông báo & quy tắc (migration `d4f6b8c0e2a5`)

- **Kênh theo loại** (`task_notify:<user id>` thêm `channels`): mỗi loại trong cài đặt "Thông báo Task" chọn
  `inbox | telegram | off`. `off` chính là danh sách `kinds` bị tắt của §19.7 (API cũ vẫn nhận `kinds`).
  `telegram` chỉ chọn được khi người đó đã liên kết Telegram (`/link MÃ`, quyền `telegram.self`, bảng
  `accounts` kind `telegram`; id người dùng Telegram là id chat riêng) — chưa liên kết thì 422.
  **Quyết định:** `telegram` = dòng trong hộp thư **vẫn được ghi** (lịch sử, và là thứ nút Đã xem / Ẩn tác
  động) **cộng** một tin nhắn riêng trên Telegram, gửi bằng job RQ sau khi giao dịch commit
  (`telegram_note_job`). Bot gửi: các bot đang bật, bot của dự án của task trước; bot chỉ nhắn được người
  đã từng bắt đầu chat với nó, nên job thử lần lượt tới khi một bot gửi được. Thông báo bị giữ (giờ yên lặng
  / chế độ gộp) không đi Telegram.
- **`task.alert` bỏ qua giờ yên lặng và chế độ gộp** (Bug Cao nhất, code còn critical, hết bị chặn, testcase
  Failed khi review…), nhưng vẫn tôn trọng `off`.
- **Gom 10 phút** (`app/notifications/inbox.py`, chỉ `task.assigned | task.status | task.commented` có
  `ref` `task:<id>`): cập nhật mới trên cùng task cho cùng người, khi còn một dòng **chưa đọc, không tạm ẩn**
  được tạo trong 10 phút, **sửa dòng đó** thay vì thêm dòng: tiêu đề "N cập nhật trên OCX-2", nội dung là cập
  nhật mới nhất, `updated_at` mới; đẩy lại qua SSE cùng id (chuông hiện lại, không tăng số chưa đọc). Cửa sổ
  tính từ dòng đầu. Nhắc `@`, nhắc việc, cảnh báo, retest không gom (mỗi cái đòi người đó làm gì). Telegram
  chỉ gửi cho dòng mới, không gửi lại khi gom.
- **Tóm tắt cuối ngày** (`summary: {on, time, channel}`, mặc định **tắt**, 17:30, hộp thư; giờ theo
  REPORT_TZ): job `task-automation` (15 phút) gửi ở lần chạy đầu tiên từ giờ đó, mỗi ngày một lần
  (`task_notify_summary:<user id>`), loại `task.summary` (không bị giữ), liên kết `/tasks/me`. Nội dung lấy
  từ `app/tasks/mywork.py`: việc xong hôm nay (lý do `done`), việc còn quá hạn, MR chờ tôi review. Không
  có gì thì không gửi. Kênh Telegram như trên (kèm dòng hộp thư).
- **Tạm ẩn** (`user_notifications.snoozed_until`): `POST /inbox/snooze {ids}` (dòng chưa đọc của chính
  mình) đặt +1 ngày; dòng đang tạm ẩn không tính vào số chưa đọc, không có trong `?unread=true` và không
  hiện trong chuông; trang Thông báo vẫn có. Chuông có nút "Tạm ẩn 1 ngày" trên dòng chưa đọc.
  `GET /inbox?kind=` nhận cả loại `task.*`.
- **Nút trên Telegram**: Mở (URL), Đã xem, Ẩn 1 ngày. Bot nhận được callback (poller đọc `getUpdates` của
  mọi bot đang bật), nên Đã xem / Ẩn 1 ngày là nút callback `nt|<id>|r|s`: chỉ tác động lên thông báo của
  đúng người đã liên kết tài khoản Telegram bấm nút, không xét danh sách chat của bot. Telegram từ chối
  nút URL tới địa chỉ không công khai (localhost): khi đó liên kết nằm trong nội dung tin.
- **Nhật ký quy tắc** (bảng `task_rule_runs`: `business_project_id, rule, task_id, action, detail JSONB,
  dry_run, at`): mỗi hành động của quy tắc ghi một dòng (`notify | assign | move | set_version`, bị luồng
  chặn → `skipped`, lỗi → `failed`). `GET /tasks/spaces/{bp}/automation/log?limit=` (`tasks.view`, tối đa
  200). Task › Cài đặt › Quy tắc tự động có chế độ xem **Nhật ký** (lọc theo quy tắc).
- **Chạy thử** `POST /tasks/spaces/{bp}/automation/{rule}/dry-run` (quản lý Task): **quyết định** — chạy
  quy tắc ngay (bật hay tắt, mọi giờ) trong một SAVEPOINT luôn bị huỷ, nên không đổi gì, không gửi gì
  (kể cả Telegram), rồi ghi **một** dòng nhật ký `dry_run` (`action dry_run`, `detail {count, items}`) và
  trả về danh sách task + việc sẽ làm. Chỉ quy tắc chạy theo nhịp scheduler chạy thử được (`can_dry_run`);
  quy tắc chạy theo thao tác (`assign_on_start`, …) trả 409. Nút "Chạy thử" trên từng quy tắc đó.
- **5 quy tắc mới** (mặc định tắt; các quy tắc "khi có sự kiện" được kiểm ở mỗi nhịp 15 phút của scheduler,
  không móc vào luồng git / kết quả test — trễ tối đa 15 phút):

  | Khoá | Khi nào | Làm gì |
  |---|---|---|
  | `release_version` | MR của task (`task_git_links` kind mr) đã merge trong 14 ngày vào nhánh `release/<tên>` hoặc `release-<tên>`, `<tên>` trùng (không phân biệt hoa thường) tên một phiên bản của dự án; task chưa có phiên bản | Gắn phiên bản đó (sub-task theo cha), lịch sử không người. Mỗi task một lần: gỡ tay thì không gắn lại. |
  | `column_age_warn` | Task nằm ở một cột có `age_warning_hours` (không phải nhóm Xong) lâu hơn mức đó | Báo quản lý dự án (quyền `manage`) một lần mỗi lần vào cột; nhiều task gộp một thông báo. |
  | `test_failed_back` | Task ở trạng thái review của luật git, một testcase phủ (`covers`) có kết quả mới nhất Failed ghi **sau** khi task vào review | Chuyển về trạng thái "start" của luật git (theo luồng chuyển; luồng cấm → `skipped`), báo người làm (`task.alert`). Tối đa một lần mỗi task mỗi ngày. |
  | `sprint_risk` (ngày 2, % 40) | Sprint đang chạy còn ≤ N ngày và phần điểm chưa xong > ngưỡng | Báo quản lý một lần mỗi sprint. |
  | `unassigned_in_sprint` (ngày 3) | Task mở trong sprint đang chạy chưa có người làm, tạo quá N ngày trước (không lưu lúc task vào sprint nên tính từ lúc tạo) | Báo quản lý một lần mỗi task mỗi sprint; nhiều task gộp một thông báo. |

  Không có quản lý nào thì không báo và không ghi nhật ký (sẽ báo khi dự án có quản lý).
- Chưa làm: múi giờ riêng từng người (vẫn REPORT_TZ); sửa tin Telegram sau khi bấm (chỉ trả lời toast);
  gom thông báo cho người nhận qua Telegram thành một tin.

### 20.6 Đánh giá sprint

Chỉ đọc, không migration. Mã: `app/tasks/sprint_review.py` (mới), `app/tasks/health.py`,
`app/api/routers/task_health.py`; giao diện ở tab Sức khoẻ sprint, hộp "Hoàn tất sprint" của Backlog và
trang `/tasks/overview`.

- **Lúc sprint bắt đầu** là thời điểm của dòng audit `task.sprint.start` (ngày bắt đầu chỉ là ngày và có thể
  lùi về trước); không có dòng audit thì lấy 0 giờ của ngày bắt đầu theo múi giờ báo cáo. Cửa sổ của sprint là
  lúc bắt đầu → lúc đóng (`closed_at`), sprint đang chạy thì tới hiện tại.
- **Thay đổi phạm vi** (`scope_change` trong `GET /tasks/spaces/{bp}/health`, và
  `GET /tasks/sprints/{id}/scope-change` nhẹ cho Backlog; cần `tasks.view`): `added_after_start` /
  `removed_after_start`, mỗi cái có `count`, `points`, `keys`, `tasks` (tối đa 50). *Thêm*: lịch sử trường
  `sprint` có giá trị mới là **tên** sprint (lịch sử lưu tên, `service._named`) sau lúc bắt đầu, hoặc task tạo
  sau lúc bắt đầu thẳng vào sprint (tạo nhanh trên Board) — sprint đầu tiên của nó là sprint này. *Bỏ ra*:
  lịch sử có giá trị cũ là tên sprint, sau lúc bắt đầu và trước lúc đóng. Chỉ Story / Task / Bug (epic và
  sub-task không tính). Khớp theo tên trong cùng dự án: hai sprint trùng tên sẽ lẫn nhau. Giao diện: khối
  "Thay đổi phạm vi" ở tab Sức khoẻ, và dòng tiêu đề sprint đang chạy ở Backlog ghi "· +3 task sau khi bắt
  đầu" (rê chuột thấy mã và SP).
- **Đánh giá sprint** `GET /tasks/sprints/{id}/review` (`tasks.view`, 404 khi không thấy): `done` (lý do
  `done`, kèm SP; `done_pct` so với point cam kết), `closed_without_done` (Lỗi thời / Không làm),
  `carried_over` (task chưa xong, không tính sub-task, như lúc đóng), `added_after_start` /
  `removed_after_start`, `bugs_created` (bug của dự án tạo trong cửa sổ, kèm số ở mức ưu tiên cao nhất),
  `findings` (critical / high **mở ra** trong cửa sổ trên mọi review đã xong của commit / MR nối với task của
  sprint; mỗi `fingerprint` tính một lần, vào cửa sổ chứa lần thấy **đầu tiên** của nó, nên review lại lặp
  finding cũ không tính là mới), `cycle_time_h` (trung vị bắt đầu làm → xong của task đã xong),
  `can_create_retro` (`docs.view` + `docs.edit` trên dự án), `markdown` (trang retro).
  - **Giới hạn đã chấp nhận:** hoàn tất sprint chuyển task chưa xong đi mà không ghi lịch sử (không sửa
    `tasks.py` ở đợt này), nên với sprint **đã đóng** chỉ biết *số* task chuyển đi (từ `moved` của dòng audit
    `task.sprint.complete`), không biết mã và SP; task tạo thẳng vào sprint sau lúc bắt đầu rồi bị chuyển đi khi
    đóng cũng không còn nhận ra là "thêm sau". Vì vậy hộp "Hoàn tất sprint" **giữ đánh giá đọc trước khi xác
    nhận** để hiện sau khi đóng và để tạo trang retro.
- **Hộp "Hoàn tất sprint"**: hiện đánh giá trước nút xác nhận; sau khi đóng hộp đổi tiêu đề "Đã hoàn tất",
  vẫn hiện đánh giá đó, có nút **"Tạo trang retro"** (ẩn khi thiếu quyền tài liệu) và "Đóng".
- **Trang retro**: giao diện gửi `markdown` qua `POST /docs` có sẵn của Tài liệu dự án (spec 29; loại "Khác",
  tiêu đề "Retro {tên sprint}", ghi chú phiên bản "Tạo từ đánh giá sprint"), rồi hiện link "Mở trang retro"
  (`/docs?project={bp}&doc={id}`). Nội dung: bảng tóm tắt, danh sách đã xong / chuyển sang sau / thêm sau khi
  bắt đầu, và ba mục trống **Điều tốt / Cần cải thiện / Hành động**.
- **Dự báo có khoảng** (`forecast.range`, `forecast.few_data` trong API sức khoẻ): ngoài dự phóng tuyến tính,
  (1) theo tốc độ từng ngày: điểm xong mỗi ngày đã qua (task xong trước lúc bắt đầu tính vào ngày đầu, xong hôm
  nay tính vào ngày cuối), trung bình ± 1 độ lệch chuẩn nhân số ngày còn lại cộng điểm đã xong, khi đã qua
  **≥ 4 ngày**; (2) theo velocity: điểm xong (lý do `done`) của tối đa 6 sprint đã đóng gần nhất, trung bình
  ± 1 độ lệch chuẩn, khi có **≥ 3 sprint đóng**. Có cả hai thì lấy trung bình hai cận. Kẹp trong [điểm đã
  xong, phạm vi]. Số ngày còn lại là `tổng − đã qua`, đúng như dự phóng tuyến tính (tâm của khoảng theo ngày
  trùng với dự phóng tuyến tính). Không đủ dữ liệu → `range: null`, giao diện ghi một giá trị kèm "ít dữ liệu"; có khoảng thì
  "Dự kiến xong 85–95% SP".
- **Xu hướng 4 sprint** (`trends` trên mỗi dòng của `GET /tasks/overview`, null khi chưa có sprint đóng): 4
  sprint đóng gần nhất (cũ → mới; cửa sổ từ 0 giờ ngày bắt đầu tới `closed_at`): lead time trung vị (tạo → xong)
  của task lý do `done` thuộc sprint, số bug mức ưu tiên cao nhất tạo trong cửa sổ, số finding critical mở ra
  trong cửa sổ trên code nối với task của dự án. Ba truy vấn gộp (`percentile_cont`, `VALUES` cửa sổ, union
  review theo commit / MR) cho mọi dự án cùng lúc, không vòng theo task. Hướng: giá trị mới nhất so với trung
  bình các giá trị trước, lệch ≤ 10 % (hoặc ≤ 0,5) là đứng yên; cần ≥ 2 giá trị. Cả ba chỉ số càng thấp càng
  tốt: mũi tên lên màu đỏ, xuống màu xanh; tooltip liệt kê 4 giá trị.
- **Trạng thái rỗng** `GET /tasks/spaces/{bp}/review/latest` (`tasks.view`): `{mode, review, flow}`. Chế độ
  Scrum: đánh giá của sprint đóng gần nhất (hoặc null); Kanban: dòng chảy 14 ngày (`throughput`, SP,
  `per_week`, `created`, `cycle_time_h`). Tab Sức khoẻ không có sprint đang chạy hiện khối này kèm link "Bắt
  đầu sprint" tới Backlog (Scrum) và nút "Tạo trang retro" cho sprint gần nhất. `GET .../health` vẫn trả
  `{"sprint": null}` như §19.5.

## 21. Lộ trình đợt 3 (người dùng 2026-11-17: "codepulse-task-roadmap-v3 tiếp tục với file này")

Nguồn: "CodePulse Task: Định hướng nâng cấp, đợt 3". Nhiều mục "còn tồn" đã có nhưng khó thấy (tuỳ chọn thông báo ở
cuối trang Thông báo; khối Sức khoẻ ẩn hết khi task chưa có code; lệnh Ctrl+K chỉ khi đang mở task; sức chứa cần 3
sprint đóng): đợt 3 làm chúng hiện rõ thay vì làm lại. Quyết định đã chốt:

- **R2 vai trò tester:** cờ `users.is_tester` (hồ sơ, Người & Team) và cờ `teams.is_qa`; ai có cờ hoặc ở team QA
  thì có `auto_role = tester`; bộ "Tester" chuyển thành bộ tự gán.
- **R1b phạm vi Team cho quyền cấp dự án** (`tests.edit/approve/manage/delete`, `tasks.sprint/manage`): dự án mà
  ít nhất một người trong team mình lead / manage đang được cấp quyền (project grant), trong dự án mình xem được.
- **Thoát Jira:** CodePulse không khoá được Jira; làm phía CodePulse: cảnh báo commit / MR còn dùng mã Jira cũ,
  công tắc "Ngừng dùng Jira" theo dự án, checklist bật Task cho Lead, nút tạo / xoá dữ liệu demo.
- **Đo lường** ẩn danh: chỉ (vai trò, sự kiện, nơi, thời điểm), không lưu người và nội dung task.

| Giai đoạn | Nội dung | Mục |
|---|---|---|
| 3.0 Đánh bóng | F1–F6, R2 | 21.0 |
| 3.1 Một cái nhìn | Khối Sức khoẻ có màu tổng, Ctrl+K lệnh đầy đủ theo ngữ cảnh, tìm mờ không dấu, gần đây, `>` `#` `@` | 21.1 |
| 3.2 Không bỏ sót | Trang Cài đặt thông báo, mặc định của tổ chức, cuối tuần yên lặng, nút "Bắt đầu" trên Telegram; đo lường sử dụng + bảng cho Admin | 21.2 |
| 3.3 Kế hoạch | Tab Timeline theo Epic (+ nhiều dự án); lập sprint: sức chứa tay, tải theo người, kiểm tra trước khi bắt đầu | 21.3 |
| 3.4 Tìm gì cũng thấy | Bộ lọc dạng chip có toán tử / nhóm HOẶC, trường mở rộng, chia sẻ / ghim / đăng ký, CSV, tìm toàn văn | 21.4 |
| 3.4b Phân quyền còn lại | R1b, "Quyền của tôi" tự xem được, giải thích vì sao | 21.5 |
| 3.5 Chuyển đổi | Cảnh báo mã Jira cũ, Ngừng dùng Jira, checklist, dữ liệu demo | 21.6 |
| 4 Tin cậy | Đồng bộ thời gian thực Board / chi tiết, Hoàn tác 10 giây, di chuyển card bằng bàn phím | 21.7 |

### 21.1 Một cái nhìn

Roadmap đợt 3 §3.1 (R5) và §3.2 (R7b). Không migration; một API đọc mới.

- **Khối Sức khoẻ** (`components/tasks/TaskHealthBlock.tsx`) luôn ngay dưới tiêu đề task, có **một màu tổng** (viền
  trái + chấm + chữ "Ổn / Cần chú ý / Có vấn đề"):
  - Đỏ: bị chặn (task chưa Xong chặn nó; cả khi người xem không thấy task chặn: `blocked` của TaskRow) hoặc còn finding
    critical mở.
  - Vàng: thiếu mục DoD (bắt buộc hay không), testcase Failed, pipeline lỗi, mọi MR đóng không merge, quá hạn.
  - Xanh: còn lại (đủ DoD, hoặc không có DoD và không có gì sai).
  - Ba dòng, mục không áp dụng thì ẩn: (1) ⛔ Bị chặn bởi KEY (trạng thái), bấm mở task chặn; (2) code: tối đa 3 MR
    `MR !42 ✓ đã merge` mở GitLab ở tab mới (+n MR cuộn tới Code; không có MR thì "n commit"), `Review 7.8`, `n critical`,
    `n high` mở review đáng xem nhất (`/reviews/{id}`: nhiều critical nhất, rồi nhiều high nhất), Pipeline ✓ / n
    pipeline lỗi mở pipeline trên GitLab (pipeline lỗi trước), Đã lên / Chưa deploy, MR bị đóng không merge;
    (3) Quá hạn, `AC x/y có testcase` (tiêu chí có ít nhất một testcase gắn vào, từ `criteria` của
    `GET /tasks/{key}/tests`; cuộn tới Tiêu chí), `Testcase p/t Passed` mở test run của testcase Failed đầu tiên (không
    có thì testcase có run đầu tiên): `/testcases?project=<dự án của task>&tab=runs&run=&test=`, `DoD x/y · Xem thiếu gì`
    mở danh sách mục thiếu ngay trong khối (kèm chi tiết, và dòng "bắt buộc trước khi Xong" khi dự án bắt buộc).
  - Không mục nào áp dụng: một dòng mờ "Chưa có code, DoD hay testcase cho task này".
  - Phần **Code** (`TaskCode`) chỉ còn danh sách nhánh / MR / commit (dải tổng hợp chuyển lên khối Sức khoẻ).
  - Quyết định: test run mở theo dự án của task (coverage không trả dự án của run; testcase của dự án khác hiếm).
- **Card Board** hiện **một** huy hiệu theo cùng logic (`components/tasks/taskSignal.ts`, dùng chung thứ tự):
  ⛔ bị chặn > critical > quá hạn > quá tuổi cột > 🔁 MR bị đóng > `DoD thiếu n` (chỉ khi dự án bắt buộc DoD). Task đã
  Xong không hiện gì.
  - `GET /api/v1/tasks/spaces/{bp}/dod-gaps` (`app/api/routers/task_dod_gaps.py`, `tasks.view` + xem được dự án) →
    `{enforced, gaps: {KEY: [nhãn mục thiếu]}}`: chỉ khi dự án bật bắt buộc DoD, chỉ task ở trạng thái nhóm "doing"
    (task chưa bắt đầu thiếu DoD là đương nhiên), tối đa 300 task cập nhật gần nhất; task đủ DoD không có trong `gaps`.
    Board chỉ gọi khi `rules.dod.enforce` và có mục DoD (một request cho cả Board, SWR dùng chung).
- **Chọn card:** bấm (hoặc focus) một card thì card được chọn (viền accent), `Esc` bỏ chọn (không khi đang gõ hay có
  hộp thoại). Kho nhỏ cấp module `components/tasks/cardSelection.ts` (+ sự kiện `cp:card-select`); bảng lệnh đọc nó khi
  mở, chỉ khi card còn trên màn hình.
- **Ctrl+K thành bảng lệnh** (`components/CommandPalette.tsx`):
  - Ngữ cảnh = task đang mở (URL), không có thì card đang chọn. Lệnh của ngữ cảnh lên đầu (nhóm "Lệnh trên KEY"), theo
    quyền của task: *Giao cho tôi* (`can.assign`, ẩn khi đã là của tôi), *Giao cho…* (người của dự án, + "Bỏ giao"),
    *Chuyển sang…* (chỉ `transitions` hợp lệ; trạng thái nhóm "done" mở danh sách lý do đóng, "Đã xong" đứng đầu và
    được chọn sẵn; còn critical thì phải Enter lần hai), *Thêm vào sprint…* (như §20.2), *Gắn phiên bản…* (phiên bản
    chưa phát hành, + "Bỏ phiên bản"; `PATCH version_id`), *Đổi mức ưu tiên…* (mức của dự án), *Thêm nhãn…* (nhãn đã
    dùng trong 200 task cập nhật gần nhất của dự án, hoặc gõ nhãn mới; khoảng trắng → `-`), *Sao chép link*, *Sao chép
    tên nhánh* (tính ở trình duyệt đúng như `app/tasks/keys.py branch_name`: `feature/KEY-tieu-de` / `bugfix/`, ≤ 60
    ký tự; tên hiện ngay ở cột gợi ý), *Tạo sub-task* (mở form tạo với task cha; cần `can_create`, cha không là
    sub-task, dự án có loại sub-task hoặc cha là epic). Khi đã gõ chữ, mỗi trạng thái hợp lệ còn là một lệnh riêng
    "Chuyển sang <trạng thái>".
  - **Tìm mờ không dấu** (`components/paletteMatch.ts`): mọi từ gõ phải khớp, bỏ dấu tiếng Việt, đ → d; khớp đầu từ
    > giữa từ > chữ cái đầu ("gct" → "Giao cho tôi") / chữ rải rác từ đầu từ; nhãn nặng hơn từ khoá ẩn; nhãn bắt đầu
    bằng cả chuỗi gõ được cộng thêm. "giao" ↔ "Giao cho tôi", "chuyen review" ↔ "Chuyển sang Đang review".
  - **Tìm task theo tiêu đề** (`GET /tasks?q=&limit=8&sort=updated`): mỗi dòng có mã, tiêu đề, trạng thái, người làm.
    Chờ 120 ms sau phím cuối, request cũ bị huỷ (`AbortController`), kết quả nhớ theo chuỗi trong lượt mở; kết quả cũ
    giữ trên màn hình trong lúc chờ (không nháy).
  - **Gần đây** khi ô trống: 5 task vừa mở (ghi mỗi khi chi tiết task hiện, ở bất cứ đâu) và 3 trang vừa mở (theo
    đường dẫn, nhãn theo menu), trong `localStorage` (`cp:palette:recent-tasks`, `cp:palette:recent-pages`; đọc / ghi
    lỗi thì bỏ qua).
  - **Tiền tố:** `>` chỉ lệnh, `#` chỉ task (từ 1 ký tự), `@` người của dự án (dự án của ngữ cảnh, không có thì `space`
    trên URL / dự án Task mở lần cuối / dự án bật Task đầu tiên). Chọn một người → danh sách việc đang mở của họ (nhóm
    todo + doing, 20 việc cập nhật gần nhất) và lệnh "Mở Danh sách lọc theo <người>" (`/tasks?space=&tab=list&assignee=ID`).
    Có lệnh "Tìm người…" chèn `@`.
  - Danh sách được chia nhóm có tiêu đề (Lệnh trên KEY, Task gần đây, Trang gần đây, Lệnh, Người, Task).
- Chưa làm: tab Danh sách chưa đọc tham số `assignee` trên URL (lệnh "Mở Danh sách lọc theo …" mở đúng dự án và tab,
  cần một sửa nhỏ trong `TaskList` để tự áp; danh sách việc của người đó đã có ngay trong bảng lệnh); hoàn tác sau
  lệnh của bảng lệnh (để mục Hoàn tác chung §21.7).
### 21.5 Phân quyền còn lại

(Giai đoạn 3.4b, không migration: bộ quyền có sẵn không đổi.)

- **R1b – phạm vi Team cho quyền cấp dự án:** `tests.edit`, `tests.approve`, `tests.manage`, `tests.delete`,
  `tasks.sprint`, `tasks.manage` có thêm mức `team` (thứ tự vẫn own < team < project < all; danh mục
  `PROJECT_TEAM_KEYS`). Team = dự án mà ít nhất một người **khác** trong team mình lead, hoặc manage (cả team con;
  cả sáu quyền đều `managed_team`), đang được cấp quyền dự án (`project_grants`, người còn hoạt động), trong các
  dự án mình xem được. Quyền dự án của chính mình không tính (không phải "của team"); `manage_all` của người
  trong team không tính (không phải grant). Kiểm ở một chỗ: `Principal.covers_project(key, bp)` /
  `Principal.team_projects(key)`; `testcases._scoped`, `tasks.can_manage_space`, `tasks.can_sprint` gọi nó.
  `tests.run` mức team vẫn tính theo từng test (§20.0), không mở cả dự án.
- **Gộp bộ quyền (bài học §20.0):** gộp giữ mức rộng nhất, nên khi **một bộ** cho quyền ở mức `team` và bộ khác cho
  `project`, mức `project` vẫn với tới dự án của team (`Principal.team_granted`: các quyền cấp dự án mà ít nhất một
  bộ cho ở mức team). Quyết định: `project` **một mình** thì **không** với tới dự án của team. Lý do: bộ "Quản lý
  dự án" có sẵn cho `tasks.manage`/`tasks.sprint`/`tests.*` ở mức project với mọi người manage một dự án; nếu
  project tự mở dự án của team thì mọi lead đang manage một dự án sẽ thành người quản trị Task / testcase của
  mọi dự án team mình tham gia. `all` vẫn là mọi dự án xem được.
- **Bộ Lead có sẵn: không nhận quyền nào trong sáu quyền trên** (quyết định). Lead là người kèm cặp, không phải
  chủ dự án khác: một người trong team chỉ cần được cấp xem một dự án là cả dự án ấy (sprint, trạng thái, luật
  git, testcase) mở cho lead – quá rộng để mặc định. Admin muốn thì tạo bộ (ví dụ "QA Lead": `tests.edit`,
  `tests.approve`, `tests.manage` = team; "Scrum master": `tasks.sprint` = team) rồi gán cho người / team. Vì bộ có
  sẵn không đổi nên không cần migration; `tests/permissions/test_catalog.py` vẫn so với migration `a1c3e5f7b9d1`.
- **Quyền của tôi:** `GET /api/v1/me/permissions` (chỉ cần đăng nhập, không cần `admin.permissions`) trả đúng như
  `GET /admin/users/{id}/permissions` cho chính mình: vai trò, bộ quyền và nguồn (`via`: role / given / personal /
  team), mỗi quyền với mức và các bộ cho nó; thêm `projects` (dự án mình xem được, để chọn).
  - `?explain=1`: mỗi quyền có `label` và `meaning` ("chỉ task bạn tạo hoặc được giao", "dự án có người trong team
    bạn lead hoặc manage được cấp quyền"…).
  - `?check=<key>&task=<KEY>` hoặc `&project=<id>`: `check {key, label, scope, allowed, target, reason, lines}`
    bằng tiếng Việt, ví dụ "Bạn có tasks.edit ở mức own (của mình) (từ bộ Thành viên): chỉ task bạn tạo hoặc được
    giao. Task CRM-3 do Binh tạo, giao cho chưa ai. Mức own chỉ gồm task bạn tạo hoặc được giao. Kết luận: không
    được." `allowed` lấy từ đúng hàm kiểm quyền của endpoint (`tasks.may`, `can_sprint`, `may_edit`…), lời giải
    thích chỉ đi lại các bước; quyền task hỏi cho cả dự án mà còn tuỳ task → `allowed: null`. Task / dự án không
    xem được → 404 (không lộ có hay không); quyền lạ → 422. Mã nguồn: `app/permissions/explain.py`.
  - Trang `/me/permissions` ("Quyền của tôi", mục trong menu tài khoản góc phải): ô "Tôi có làm được không?"
    (quyền, task hoặc dự án → được / không được / tuỳ task kèm lý do), vai trò và bộ quyền, bảng quyền đang có
    (mức, nghĩa là, từ bộ nào).
### 21.3 Kế hoạch

Migration `b3c4d5e6f7a8` (`sprints.capacity_points`, `sprints.capacity_source` `auto` | `manual`). Mã:
`app/tasks/roadmap.py`, `app/tasks/planning.py`, `app/api/routers/task_planning.py` (trước router tasks:
`/tasks/timeline` không phải mã task); giao diện `components/tasks/TaskTimeline.tsx`, tab **Timeline** cạnh Backlog,
dòng lập sprint và hộp "Bắt đầu sprint" của Backlog, chế độ Timeline ở `/tasks/overview`. Không sửa
`routers/tasks.py`: kéo thả gọi `PATCH /tasks/{key}` (ngày hạn, cần `tasks.edit`) và `POST /tasks/{key}/move`
(sprint) có sẵn.

- **`GET /tasks/spaces/{bp}/timeline?from=&to=`** (`tasks.view`, 404 khi không xem được dự án). Không gửi khoảng thì
  từ thứ Hai của 2 tuần trước, 12 tuần; chỉ gửi một đầu thì 12 tuần tính từ đầu đó; ngược hoặc dài hơn 366 ngày
  → 422. Trả:
  - `epics`: mọi epic chưa xong và epic đã xong có thanh (của nó hoặc của con) chạm khoảng; tối đa 100, theo thứ
    tự backlog. Mỗi epic có `children` (Story / Task / Bug con trực tiếp, sub-task không vẽ), `children_count`,
    `done_count`.
  - **Thanh**: `start` = ngày của `started_at` (múi giờ báo cáo), không có thì ngày bắt đầu của sprint, không có thì
    ngày tạo; `end` = `due_date` (`end_source: "due"`), không có thì ngày kết thúc của sprint (`"sprint"`), không có
    thì null (giao diện vẽ tới hôm nay, viền đứt). Epic không có ngày riêng thì trải theo con: bắt đầu sớm nhất,
    kết thúc muộn nhất (`"children"`).
  - `progress` (0..1): task con là 1 khi ở trạng thái nhóm "done"; epic là SP xong (lý do `done`) / tổng SP của
    con, con chưa ai ước lượng thì đếm số con xong / số con. `overdue` khi chưa xong và `end` < hôm nay;
    `overdue_from` là `end` (giao diện tô đỏ từ sau `end` tới hôm nay).
  - `loose`: Story / Task / Bug không có task cha, đã có sprint hoặc ngày hạn, chạm khoảng (tối đa 200), nhóm
    "Không thuộc epic".
  - `sprints`: sprint có đủ hai ngày chạm khoảng (dải); `versions`: phiên bản có `release_date` trong khoảng (◆).
  - `links`: liên kết "blocks" mà cả hai đầu đều có trong timeline, `late` khi task chặn chưa xong, task bị chặn
    chưa xong và `end` của task chặn **sau** `end` (ngày hạn, hoặc cuối sprint) của task bị chặn. Khi đó task bị
    chặn có `blocked_late`, và `warnings` có một dòng "OCX-2 bị chặn quá hạn: OCX-5 xong 20/11, sau hạn 18/11 của
    OCX-2". `blockers` của thanh là các task chặn còn mở.
  - `can_edit` theo `tasks.edit` trên từng task; `can_sprint` của dự án.
- **Tab Timeline** (không phải Gantt): cột trái 280 px dính khi cuộn ngang (mã, tiêu đề, cảnh báo, nút "Đổi lịch"),
  phải là các ngày (18 px / ngày) với hàng **tuần** (số tuần ISO + ngày thứ Hai), hàng **phiên bản** (◆, xanh khi
  đã phát hành), hàng **sprint** (dải; sprint đang chạy viền xanh), vạch **hôm nay**. Hàng epic mở ra con; lần đầu
  tự mở epic có con bị chặn quá hạn. Thanh: phần đậm là đã xong, nhạt là còn lại, đoạn **đỏ** là phần quá hạn;
  mũi tên mảnh cho "chặn" (đỏ khi `late`). Lùi / tới 4 tuần, "Hôm nay".
  - **Kéo mép phải** của thanh (người có `tasks.edit`, cả epic) → `PATCH /tasks/{key} {due_date}`; không cho hạn
    trước ngày bắt đầu của thanh. **Kéo thanh con lên một dải sprint** → `POST /tasks/{key}/move {sprint_id}`.
  - Bàn phím: mỗi thanh sửa được có nút "Đổi lịch {mã}" mở hộp nhỏ: ngày hạn, và (trừ epic) sprint (sprint chưa
    đóng hoặc Backlog). Thanh là nút: Enter mở task.
- **Nhiều dự án** `GET /tasks/timeline?space=..&space=..&from=&to=` (`tasks.view`): chỉ dự án bật Task mà người
  xem được (mã khác bị bỏ qua, không lỗi); không gửi `space` là mọi dự án xem được (tối đa 30). Mỗi dự án: tên,
  mã, epic (không kèm con, `can_edit` luôn false), phiên bản. `/tasks/overview` có nút chọn **Bảng / Timeline**;
  Timeline có ô chọn nhiều dự án (mặc định mọi dự án), chỉ đọc, bấm tên dự án / epic mở tab Timeline của dự án đó.
- **Sức chứa sprint**: `capacity_source = "auto"` (mặc định) dùng trung bình SP hoàn thành của 3 sprint đóng gần nhất
  (§19.3, null khi chưa đủ); `"manual"` dùng `capacity_points`. `PATCH /tasks/sprints/{id}/capacity
  {capacity_points}` (`tasks.sprint`, 409 với sprint đã đóng, ghi audit `task.sprint.capacity`): số ≥ 0 → tay;
  null → về tự động. Đặt tay được cả khi đã có lịch sử (ghi đè), có nút "Dùng TB 3 sprint".
- **`GET /tasks/sprints/{id}/plan`** (`tasks.view`): `points` (Story / Task / Bug của sprint, mọi trạng thái),
  `capacity`, `source`, `auto`, `over_capacity`, `load` theo người (`points`, `tasks`, `pct` trên tổng SP của sprint,
  `over` khi một người **> 40 %**; dòng "Chưa giao" không cảnh báo), `overloaded`, `can_sprint`. Đầu khối sprint ở
  Backlog: "10 / 18 SP · sức chứa = TB 3 sprint gần nhất" (hoặc "· sức chứa đặt tay"; đỏ khi vượt); chưa có sức chứa
  thì người có `tasks.sprint` thấy ô nhập; dưới là thanh tải theo người (đỏ khi vượt 40 %). Thay cho dòng
  "sức chứa ~X SP" của §19.3 (`Sprint.capacity` trong API sprints vẫn là giá trị tự động).
- **`GET /tasks/sprints/{id}/preflight`** (`tasks.view`): `{ok, warnings}`; mỗi cảnh báo `{kind, text, count,
  tasks}` (tối đa 50 task): `unestimated` (Story / Task / Bug chưa xong chưa có SP), `unassigned` (chưa có người
  làm), `blocked_outside` (bị chặn bởi task còn mở không thuộc sprint này, kèm `blocker`), `over_capacity`,
  `overloaded`. Hộp "Bắt đầu sprint" hiện các cảnh báo trước nút xác nhận; có cảnh báo thì nút đổi thành
  "Vẫn bắt đầu" (không chặn). Không có cảnh báo thì một dòng xanh.
- Chưa làm: kéo cả thanh để dời ngày bắt đầu (không có trường ngày bắt đầu riêng, `started_at` là lúc bắt đầu làm
  thật); mũi tên tới task bị ẩn trong epic đang thu gọn; chọn nơi nhận task dở khi đóng sprint đã có từ trước.
### 21.2 Không bỏ sót (migration `a2b3c4d5e6f7`)

- **Trang Cài đặt thông báo** `/settings/notifications`, mở từ menu tài khoản (avatar → "Cài đặt thông báo"), không
  còn chỉ nằm cuối trang lịch sử (thẻ cũ ở trang Thông báo vẫn còn, cùng component). Bảng sự kiện × kênh
  (Hộp thư / Telegram / Tắt, radio), mặc định Hộp thư cho tất cả:

  | Sự kiện | Loại | Telegram |
  |---|---|---|
  | Được giao task | `task.assigned` | có |
  | Được nhắc tên | `task.mentioned` | có |
  | Task mình theo dõi đổi trạng thái | `task.status` | – |
  | Bình luận trên task của mình | `task.commented` | – |
  | Hết bị chặn | `task.unblocked` (**mới**; quy tắc `unblock_notify` trước gửi `task.alert`) | có |
  | Nhắc quá hạn / sắp tới hạn | `task.remind` | có |
  | MR chờ mình review | `task.review` (**mới**) | có |
  | Testcase Failed trên task của mình | `task.test_failed` (**mới**) | có |
  | Cảnh báo khẩn (Bug Cao nhất, code critical) | `task.alert` | có |

  **Quyết định:** "–" = chọn Telegram cho loại đó trả 422; cài đặt cũ đã lưu Telegram cho `task.status` /
  `task.commented` được đọc như Hộp thư. `GET /me/task-notify` thêm `quiet_days`, `telegram_kinds`,
  `customised`, `org_defaults`; `DELETE /me/task-notify` = quay về mặc định của tổ chức.
- **Phát hiện ở nhịp `task-automation` (15 phút, `automation.send_personal`)**, chỉ sự kiện trong 2 ngày gần
  nhất (bật tính năng không dội lại việc cũ):
  - `task.review`: MR `opened` có `reviewer_account_ids` (collector), cập nhật trong 2 ngày; người review là
    tài khoản `gitlab_user` đã gắn người, không phải tác giả, có `tasks.view` và xem được dự án của repo. Một
    lần mỗi MR mỗi người (`ref review:<mr id>`). Liên kết: task gắn MR, không có thì `/tasks/me`.
  - `task.test_failed`: testcase `covers` một task mở có người làm, kết quả mới nhất Failed trong 2 ngày.
    Một lần mỗi kết quả (`ref testfail:<task>:<test>:<giờ test>`); bỏ qua khi quy tắc `test_failed_back` đã
    báo task đó hôm nay.
- **Giờ yên lặng + cuối tuần**: `quiet_days` ⊂ {5 thứ Bảy, 6 Chủ nhật} (yên lặng cả ngày, giờ REPORT_TZ);
  thông báo bị giữ như giờ yên lặng, bản gộp và tóm tắt cuối ngày không gửi ngày đó. `task.alert` và
  `task.incident` (dành cho sự cố production) vẫn gửi ngay (vẫn tôn trọng "Tắt").
- **Tóm tắt cuối ngày** thêm "việc ngày mai": task mở của tôi có hạn = ngày mai; tiêu đề "… N việc ngày mai".
- **Telegram "Bắt đầu"** (`nt|<id>|b`): chỉ hiện khi task của thông báo đang ở nhóm Cần làm và dự án có trạng
  thái "start" (luật git); bấm → chuyển sang "start" nếu người đã liên kết Telegram có quyền chuyển trạng
  thái (`tasks.transition`, theo luồng chuyển; bị chặn thì trả lời lý do), đánh dấu thông báo đã xem, trả lời
  "OCX-2 → Đang làm." Không có loại `task.review`, tóm tắt, bản gộp.
- **Mặc định của tổ chức** (`app_settings` khoá `task_notify_defaults`, cùng dạng cài đặt cá nhân):
  `GET /task-notify/defaults` (`tasks.view`), `PUT` (Admin, audit `task.notify_defaults`). Người chưa có
  `task_notify:<id>` dùng mặc định này (kênh, giờ yên lặng, cuối tuần, chế độ, tóm tắt); lưu lần đầu = sao
  chép mặc định rồi sửa. Mặc định chọn Telegram được (người chưa liên kết chỉ có dòng hộp thư). Tóm tắt bật ở
  mặc định → mọi người dùng đang hoạt động chưa tự chỉnh nhận (ai không có `tasks.view` thì bỏ qua). Trang
  Cài đặt thông báo có thẻ "Mặc định của tổ chức" cho Admin.
- **Đo lường ẩn danh** (bảng `usage_events (id, role, event, surface, at)`, `app/usage`): không lưu người, mã
  hay nội dung task. `role` = vai trò tự động cao nhất (member < lead < manager < project_manager < manage_all
  < admin), `system` cho git và quy tắc.

  | Sự kiện | Nơi (`surface`) | Ghi ở đâu |
  |---|---|---|
  | `page_open` | my_work, board, backlog, timeline, list, reports, task_detail, health, versions, overview | Trình duyệt (`UsageTracker` trong app shell, theo URL) |
  | `status_change` | drag (`/move`), detail (`/transition`, PATCH), palette, bulk, git, rule, mcp, ai, telegram | Máy chủ, `service.set_status` |
  | `task_create` | button (`POST /tasks`: nút Tạo và tạo nhanh, chưa tách), ai_breakdown (`/tasks/batch`), failed_test, jira_import, mcp | Máy chủ, `service.create_task` |
  | `palette` | open, search_task, navigate, command | Trình duyệt |
  | `shortcut` | key (e . ? / n g) | Trình duyệt |
  | `rule_undone` | khoá quy tắc | Nhịp `task-automation` |

  - Nơi của sự kiện máy chủ đọc từ đường dẫn request (middleware ASGI `UsageContext`). Lệnh chạy từ Ctrl+K
    đặt cookie `cp_surface=palette` sống 5 giây → `/transition` khi đó tính là `palette`.
  - Beacon `POST /api/v1/usage {events:[{event, surface}]}` (đăng nhập, tối đa 50, chỉ `page_open | palette |
    shortcut`, sai thì bỏ qua, 204), gom 5 giây, `keepalive` khi ẩn trang, lỗi bỏ qua.
  - `rule_undone`: dòng `task_rule_runs` (move / assign / set_version) đủ 24 giờ; dòng lịch sử không người
    của quy tắc (±1 phút) bị **một người** đặt lại đúng giá trị cũ trong 24 giờ → một sự kiện (vai trò người
    đó). Con trỏ `app_settings usage_undo_cursor`; lần chạy đầu chỉ đặt con trỏ.
  - Giữ 180 ngày (xoá ở nhịp `task-automation`).
  - `GET /usage/summary?weeks=` (Admin, 1–26, mặc định 8): số theo tuần (thứ Hai, UTC) theo sự kiện × nơi và
    theo vai trò; tỉ lệ đổi trạng thái bởi git + quy tắc (mục tiêu ≥ 60%); tỉ lệ bàn phím = (lệnh Ctrl+K trừ
    "open" + phím tắt) / (đó + số lần mở trang). Bảng "Số liệu sử dụng" ở cuối trang Tình trạng bật Task, chỉ
    Admin.
- Chưa làm: nhãn / biểu tượng chuông cho ba loại mới (`KIND_LABEL` trong `Inbox.tsx`, ngoài phạm vi file; hiện
  không nhãn); tách "nút Tạo" và "tạo nhanh"; nhận diện kéo thả vs. Ctrl+K chỉ theo đường dẫn / cookie (đổi
  trạng thái qua Ctrl+K dùng `/transition` nên phụ thuộc cookie); `task.incident` chưa có nguồn phát (dành sẵn).
### 21.4 Tìm gì cũng thấy (migration `c4d5e6f7a8b9`)

- **Cây điều kiện** (`app/tasks/search.py`): `{all: [nút]}` / `{any: [nút]}`, nút là điều kiện `{field, op, value}`
  hoặc nhóm (lồng tối đa 4 cấp, 60 điều kiện, 100 giá trị mỗi điều kiện; sai → 422). Toán tử `is`, `is_not`,
  `empty`, `not_empty`, `before`, `after`, `within_days`, thêm `gte` / `lte` cho số (tuổi trong cột, SP, trường số).
  Trường: mọi thứ `GET /tasks` lọc (`text`, `space`, `status`, `category`, `type`, `priority`, `resolution`,
  `assignee`, `reporter`, `label`, `component`, `sprint` (`active` / `backlog` / id), `version`, `parent`, `due`
  (`overdue` / `week` / ngày), `updated`) và thêm `watcher`, `created`, `resolved`, `points`,
  `age_in_column_hours` (`status_changed_at`), `has_mr`, `blocked` (bị một task chưa xong chặn), `has_critical`
  (finding critical còn mở trên review mới nhất của commit / MR), `test_failed` (testcase phủ task Failed ở lần
  chạy mới nhất), `dod_met` (`met` / `unmet` / `blocking` = chưa đạt ở dự án bật bắt buộc DoD; chỉ tính task chưa
  xong, tối đa 1000 task gần nhất), trường tuỳ biến `custom:<id>` theo kiểu (text: chứa; number: `is` / `gte` /
  `lte`; date: như ngày; select, user: như danh sách). `is_not` không bỏ sót ô trống ("không là X" gồm cả task
  chưa có giá trị). `before` / `after` là trước / sau **cả ngày** đó; `within_days N` của hạn và trường ngày là N
  ngày **tới**, của tạo / cập nhật / đóng là N ngày **qua**.
- **API** (`app/api/routers/task_search.py`, đăng ký trước `tasks`): `POST /tasks/search {space[], tree,
  filter_id?, sort, order, limit ≤ 500, offset}` → `{items: TaskRow[], total, tree}`, cùng quyền với Danh sách
  (`tasks.view`, dự án xem được, Task đã bật; dự án không xem được trong `space` → 404); `filter_id` AND thêm cây
  của bộ lọc đã lưu (không thấy → 404). `GET /tasks/search?q=&space=&limit=` cho bảng lệnh: theo mã (khớp đúng /
  đầu mã), tiêu đề, rồi văn bản (mô tả, bình luận), mỗi kết quả có `match` (`key` / `title` / `text`) và `snippet`.
  `GET /tasks/search/fields?space=` (trường, kiểu, toán tử, trường tuỳ biến). `GET` (`tree` là JSON) / `POST
  /tasks/search/export.csv`: UTF-8 có BOM, cột key, title, type, status, priority, assignee, sprint, due, points,
  labels, resolution, tối đa 5000 dòng; ô bắt đầu bằng `= + - @` được thêm `'` (chống công thức).
- **Tìm toàn văn không dấu:** không dùng extension `unaccent` (cơ sở dữ liệu được quản lý có thể không cho tạo
  extension); cột `tasks.search_text` = mã + tiêu đề + mô tả + bình luận, đã gấp (chữ thường, bỏ dấu, `đ` → `d`),
  giữ bằng hook `after_flush` (đăng ký khi import `app.tasks`): task mới, sửa mã / tiêu đề / mô tả, bình luận thêm /
  sửa / xoá → dựng lại từ DB, **không đổi `updated_at`**. Migration điền cho task đang có. So khớp: mỗi từ đã gấp
  ILIKE `%từ%` (AND), hoặc đúng mã. Không có index trigram (cần `pg_trgm`); đủ nhanh với vài chục nghìn task.
- **Bộ lọc đã lưu** (`task_filters` thêm `tree`, `shared_scope`, `pinned`, `subscription`):
  - `shared_scope`: `me` / `team` (người cùng team với chủ hoặc lead / manager team đó tại thời điểm xem) /
    `project` (mọi người xem được dự án; không gắn dự án → mọi người có `tasks.view`). `shared` cũ = `project`
    (migration chuyển), và vẫn được ghi cho danh sách cũ `GET /tasks/filters`.
  - `GET /tasks/search/filters?space=&pinned=`, `POST` (tạo), `PATCH /{id}` (chỉ chủ: tên, cây, phạm vi, ghim,
    `subscription {cadence}`, `unsubscribe`); xoá vẫn là `DELETE /tasks/filters/{id}`. Bộ lọc cũ không có cây được
    đổi từ `query` khi đọc.
  - **Ghim** cần dự án; Board hiện các bộ lọc đã ghim của dự án (ai thấy bộ lọc thì thấy ghim) ở ô "Lọc nhanh":
    bấm → `POST /tasks/search {filter_id, limit 500}`, Board chỉ giữ card có mã trong kết quả; bấm lại để bỏ.
  - **Đăng ký** (`daily` / `weekly`): chỉ cho **chủ** bộ lọc. Job `filter_digest_job` (scheduler, 15 phút): từ 8:00
    (giờ `REPORT_TZ`), hằng ngày hoặc thứ Hai, một lần (`subscription.last_sent`); không có task khớp thì không gửi.
    Gửi vào hộp thông báo (kind `filter_digest`, không qua tắt / gom của thông báo task vì người dùng tự đăng ký),
    và qua Telegram riêng khi chủ đã liên kết; link mở Danh sách với bộ lọc (`?filter=ID`). Quyền tính lại lúc gửi.
- **Lọc bằng câu** (§19.8): mỗi chip có thêm `op` và `cond` (điều kiện của cây; `q` → `text`, `updated_from` →
  `updated after` ngày trước đó). Danh sách đưa các chip vào thanh chip thay cho bộ lọc hiện tại.
- **Giao diện Danh sách** (`FilterChips.tsx`): thanh chip "Trường · toán tử · giá trị", "+ Điều kiện" mở form nhỏ
  (trường, toán tử, giá trị theo kiểu), bấm chip để sửa, × để bỏ; các chip "là" cùng trường nối bằng "hoặc", còn
  lại AND. Lưới bộ lọc cũ vẫn còn và AND với thanh chip; Danh sách đọc `POST /tasks/search`. Mở bộ lọc đã lưu →
  toàn bộ cây thành chip. "Lưu bộ lọc": tên, ai thấy, ghim lên Board, nhận tóm tắt; nút bánh răng đổi các cài đặt
  đó của bộ lọc của mình; nút "CSV" tải theo bộ lọc đang xem.
- Chưa làm: chọn nhiều giá trị trong một chip (dùng nhiều chip "là"); nhóm HOẶC giữa các trường khác nhau trên giao
  diện (API có); bảng lệnh Ctrl+K dùng `GET /tasks/search?q=` (việc của 3.1); đăng ký cho người không phải chủ.
### 21.6 Chuyển đổi (migration `d5e6f7a8b9c0`)

CodePulse không khoá được Jira (việc của admin Jira); phía CodePulse làm bốn việc, đều cần `tasks.manage` của dự án
(`app/tasks/switchover.py`, `app/api/routers/task_switchover.py`, thẻ ở cuối Task › Cài đặt):

- **Mã Jira cũ vẫn được dùng.** Khi commit / MR / nhánh nối vào task qua mã của issue Jira đã nhập (`task_imports`,
  nhánh TaskImport của `git._tasks`), ghi một dòng `task_key_aliases_seen` (dự án, mã cũ, task, loại `commit | mr |
  branch`, repo, sha / MR iid / nhánh, người viết (tài khoản liên kết, kèm email git hoặc username GitLab), lúc).
  Mỗi mã cũ một dòng cho mỗi commit / MR / nhánh (`ref` duy nhất), webhook gửi lại hay sync lại không thêm dòng.
  `at` = lúc viết commit / MR cập nhật (không có thì lúc thấy). Quyết định: git.py chỉ thêm **một dòng** gọi
  `switchover.alias_used`; hàm này đọc commit / MR / nhánh từ khung gọi `_tasks` (`_commit`, `on_sync`, `on_push`),
  chạy trong SAVEPOINT và không bao giờ làm hỏng sync. Mã của chính task không tính.
  - Người viết (có tài khoản liên kết) nhận **một** thông báo mỗi ngày (`task.remind`, ref `jira-alias:<user>:<ngày>`):
    "Dùng OCX-12 thay cho OMX-5". Lần dùng cũ hơn 1 ngày (sync lần đầu cả lịch sử) không báo ai.
  - Thẻ "Chuyển đổi khỏi Jira" có bảng **Mã Jira cũ vẫn được dùng (7 ngày qua)**: mã cũ → mã mới, ở đâu (repo ·
    commit / MR / nhánh), ai, lúc (tối đa 100 dòng mới nhất, kèm tổng số lần và số người).
- **Ngừng dùng Jira** (`task_spaces.jira_retired_at`, `jira_retired_by`): `POST /tasks/spaces/{bp}/switchover/retire-jira
  {reason?}`, `DELETE` để dùng lại. Kiểm tra sẵn sàng: mọi issue Jira đã đồng bộ của dự án đã nhập (`importer.sources`)
  và không dùng mã cũ trong **14 ngày** (khoảng một sprint chạy song song). Chưa đạt → 409 kèm lý do; vẫn ngừng được
  khi ghi lý do ≥ 10 ký tự (quyết định: cả hai điều kiện đều là cảnh báo cho vượt, vì Jira vẫn còn dữ liệu để đọc).
  Audit `task.jira.retire` (`override`, `reason`, `not_imported`, `alias_uses`), `task.jira.restore`.
  - Sau khi ngừng: Testcase › Cài đặt dự án trả `jira_retired`, `bug_target` / `bug_target_effective` = `codepulse`
    (giá trị đã lưu giữ nguyên, dùng lại Jira thì hiện lại); `PUT bug_target: jira` → 422; "Tạo bug" luôn thành task
    CodePulse; radio "Issue Jira" bị khoá kèm giải thích. Thẻ Nhập từ Jira ẩn nút xem trước / nhập, vẫn hiện số đã
    nhập (API nhập không chặn: nhập thêm từ dữ liệu đã đồng bộ không hại gì).
  - Banner nhắc: CodePulse không khoá được Jira, nhờ admin Jira đặt project (các mã Jira đã đồng bộ) ở chế độ chỉ đọc.
- **Checklist bật Task cho Lead** (`GET /tasks/spaces/{bp}/switchover` → `checklist`), mỗi mục tích từ dữ liệu thật,
  có nút sửa (cuộn tới thẻ cài đặt, mở Backlog, hộp cấp quyền thành viên):
  WIP + cảnh báo tuổi trên ít nhất một trạng thái nhóm Đang làm; DoD có ít nhất một mục; ≥ 5 quy tắc tự động bật
  hoặc đủ 5 quy tắc gợi ý (`parent_done_ask`, `assign_on_start`, `bug_highest_notify`, `unblock_notify`,
  `overdue_remind`; nút "Bật 5 quy tắc gợi ý" = `POST .../switchover/recommended-rules`); sprint đầu tiên (không tính
  sprint demo; không áp dụng cho Kanban); nhập Jira xong (chỉ khi dự án có issue Jira đồng bộ); thành viên: ≥ 2 người
  có quyền và không còn ai commit vào repo của dự án 90 ngày qua mà chưa có quyền. Trên Board, người quản lý thấy một
  dòng "Checklist bật Task: x/y bước" khi chưa xong, ẩn được (nhớ theo trình duyệt).
- **Dữ liệu demo** (`POST /tasks/spaces/{bp}/demo`, `DELETE` cùng đường dẫn; audit `task.demo.create` / `.delete`):
  tạo bằng `service.create_task` một epic, hai story (một đang làm), một task đã xong, một bug, một task ở backlog, một
  sub-task, sprint "[Mẫu] Sprint demo" (chưa bắt đầu), phiên bản "[Mẫu] v1.0", ba bình luận; nhãn `mau-demo`, tiêu đề
  "[Mẫu] …", giao cho người tạo (không ai khác nhận thông báo). Loại dự án không dùng thì thành Task. Đã có task demo
  → 409. Xoá: chỉ task có nhãn `mau-demo` **và** tiêu đề "[Mẫu] " của dự án (gắn nhãn tay cho việc thật không bị xoá), xoá qua ORM để bản chiếu DPI theo kịp, kèm file đính kèm; sub-task thật nằm dưới task demo được tách ra
  (không bị cascade); sprint / phiên bản demo chỉ xoá khi không còn task nào (tạo lại thì dùng lại sprint demo còn sót). Có hộp xác nhận.
- Chưa làm: tuỳ chọn tắt thông báo "Dùng mã mới" theo dự án; số ngày sẵn sàng (14) và cửa sổ danh sách (7) chưa chỉnh
  được trên màn hình.
### 21.7 Tin cậy

Không migration. Mã: `app/tasks/live.py` (mới), `app/tasks/__init__.py`, `app/api/routers/task_live.py` (mới),
`dashboards/web/lib/useTaskLive.ts` (mới), `dashboards/web/components/UndoToast.tsx` (mới), `TaskBoard.tsx`,
`TaskDetail.tsx`, `TaskList.tsx`, cuối `globals.css` (tiền tố `lv-`). Test: `tests/api/test_task_live.py`.

- **Đồng bộ thời gian thực** `GET /tasks/spaces/{bp}/live` (Server-Sent Events; 404 khi không có `tasks.view`
  trên dự án hoặc dự án chưa bật Task, 401 khi chưa đăng nhập). Sự kiện `ready` lúc mở, rồi mỗi thay đổi một
  sự kiện `task`: `{type: "task", key, changed: [...]}`; `changed` là tên cột của task đã đổi (`status_id`,
  `rank`, `sprint_id`, `title`…, bỏ `updated_at`), hoặc `created` / `deleted`, hoặc `comment`, `git`,
  `worklog`, `attachment`, `links`, `watchers` cho dòng con thêm / sửa / xoá. Không cần gọi ở từng chỗ sửa:
  `live.py` nghe `after_flush` của mọi Session (đăng ký khi import `app.tasks`, như `app/tasks/dpi.py`), gom
  theo (dự án, mã task) trong `session.info`, và **sau khi transaction ngoài cùng commit** đẩy lên Redis kênh
  `codepulse:tasks` (pipeline, client riêng timeout 2 giây); rollback thì bỏ. SAVEPOINT được release không
  đẩy (chờ commit ngoài cùng); SAVEPOINT rollback trả về những gì đã ghi nhận lúc nó bắt đầu (như
  `app/audit_trail.py`), nên task bị bỏ qua trong thao tác hàng loạt không được báo. Hơn 50 task của một dự án
  trong một commit (sửa hàng loạt, import, rải lại rank khi kéo) gộp thành một sự kiện
  `{type: "space", key: "*", changed}`: trình duyệt tải lại task của dự án một lần. **Giới hạn đã chấp nhận:**
  câu `UPDATE` hàng loạt kiểu Core (đổi / xoá trạng thái, ánh xạ lại ưu tiên, xoá sprint) không qua flush nên
  không phát sự kiện; đó là thay đổi cấu hình, Board cập nhật ở lần tải sau. Nhờ vậy quy tắc git, tự động
  hoá, import, thao tác hàng loạt đều phát sự kiện như người sửa trên web. Redis lỗi: ghi cảnh báo và **nghỉ
  30 giây** không thử lại (không để mỗi request chờ timeout); chỉ mất đẩy trực tiếp, dữ liệu không ảnh hưởng.
  Mỗi tiến trình API một subscription, chia theo dự án (`live.Hub`, như hộp thư spec 24). Luồng tự đóng sau
  10 phút (ping mỗi 15 giây), trình duyệt nối lại và **kiểm quyền lại**, nên người mất quyền thôi nhận trong
  vài phút; không replay: nối lại sau khi rớt thì tải lại mọi thứ của dự án một lần.
  - Giao diện: `useTaskLive(bp)` dùng chung **một** EventSource cho mỗi dự án trong trang (Board, Danh sách,
    chi tiết task đang mở). Sự kiện gom 400 ms rồi `mutate` SWR các khoá: danh sách `/tasks?…space=BP…`,
    `/tasks/my-work`, task `/tasks/KEY` và các khoá con `/tasks/KEY/…`, `/tasks/spaces/BP/sprints|epics`.
    Rớt kết nối: thử lại sau 1 s, 2 s, 4 s … tối đa 60 s; nhận `ready` thì về lại 1 s.
- **Hoàn tác 10 giây** (`useUndo()` trong `UndoToast.tsx`): toast góc dưới trái "Đã chuyển OCX-2 sang Đang
  làm · Hoàn tác", thanh thời gian 10 giây, Ctrl+Z (ngoài ô nhập) cũng hoàn tác; một thay đổi mới thay toast cũ
  (chỉ hoàn tác được thay đổi gần nhất). Hoàn tác bằng lệnh ngược, không có API riêng:
  - kéo thả / di chuyển bằng phím trên Board: `POST /tasks/{key}/move` với trạng thái cũ (kèm **lý do đóng**
    cũ khi trạng thái cũ là "xong") và hai card kề cũ (`before_key` / `after_key`); đổi sprint không có trên
    Board (Backlog không thuộc đợt này);
  - đổi trường ở chi tiết task: `PATCH` các trường đó về giá trị cũ (trường tuỳ chỉnh mới thêm về `null`);
    đổi trạng thái ở chi tiết: `POST /transition` về trạng thái cũ kèm lý do cũ. Không đổi gì thì không hiện
    toast;
  - thao tác hàng loạt ở Danh sách: theo từng task, giá trị cũ gom theo giá trị để mỗi nhóm một lần
    `POST /tasks/bulk`; trạng thái cũ là "xong" với lý do khác "Đã xong" thì `PATCH resolution` thêm; nhãn thêm
    thì bỏ ở task trước đó chưa có, nhãn bỏ thì thêm lại ở task trước đó có; task bị bỏ qua (luồng chuyển,
    critical, DoD) không hoàn tác. Chỉ task trên trang đang xem mới biết giá trị cũ.
  - Hoàn tác vẫn qua luồng chuyển trạng thái và cổng critical / DoD; bị chặn thì toast ghi "Không hoàn tác
    được: …". **Xoá giữ hộp xác nhận**, không hoàn tác (không xoá mềm, §20).
- **Bàn phím trên Board**: Tab tới card (nút tiêu đề), mũi tên lên / xuống đi giữa card trong cột, trái / phải
  sang cột bên cạnh có card. `Space` nhấc card (viền và vòng focus), mũi tên trái / phải chuyển tới cột kế tiếp
  **mà luồng chuyển cho phép** (cột không cho phép bị bỏ qua và mờ đi như khi kéo), lên / xuống đổi vị trí
  (vạch thả hiện như khi kéo), `Space` thả (cùng hàm và API với kéo thả, có Hoàn tác), `Esc` hoặc rời focus
  huỷ. Vùng `aria-live` đọc tiếng Việt: "Đã nhấc OCX-2 ở cột Cần làm, vị trí 1 trên 4…", "OCX-2: cột Đang làm,
  vị trí 2 trên 3.", "Đã thả OCX-2 vào cột Đang làm.", "Đã huỷ di chuyển OCX-2."; đoạn hướng dẫn ẩn cho trình
  đọc màn hình. Có swimlane thì di chuyển trong lane của card; cuối lane là cuối cột.
- **Tương phản** (chỉ đổi token, áp cho mọi màn hình): tối `--faint` #6f7d94 → #94a1b5 (3,7 → 5,9 : 1 trên
  card), `--muted` #8e9aad → #a3aec0 (4,1 → 5,2 : 1 trên chip `--primary-soft`); sáng `--faint` #8e9aad →
  #647288 (2,9 → 4,9 : 1 trên nền trắng). Chữ xám nhỏ của màn hình Task dùng hai token này.
- **Đo tải Board**: bản dev ghi `performance.mark` lúc Board mount và khi lần tải đầu có dữ liệu, log
  `[tk-board] first load: N task, X ms` (bản production không chạy). Đo 2026-11-17 phía máy chủ với 500 task
  (PostgreSQL 16 trong Docker, máy dev Windows): ba truy vấn cột của Board (`category=todo|doing|done`,
  `limit=500`) chạy **nối tiếp** hết 325–407 ms, trình duyệt gọi song song nên ít hơn; cộng một lượt
  `/sprints` trước đó. Còn thời gian vẽ 500 card ở trình duyệt: đọc từ log dev khi chạy trên dữ liệu thật;
  mục tiêu < 1,5 giây.

### 21.0 Đánh bóng (F1–F6, R2; migration `f1a2b3c4d5e6`)

- **F1** dòng thời gian: mỗi dòng lịch sử là một câu tiếng Việt (`app/tasks/history_text.py`), không còn tên trường thô.
- **F2/F3** CSS bị cắt do merge được khôi phục; thanh Epic ở Backlog có style riêng (`tk-epicbar`).
- **F4** dự án cấu hình tay hiện "Tuỳ chỉnh" (Cài đặt, Mức áp dụng) kèm gợi ý "Lưu cấu hình thành mẫu" / "Áp mẫu".
- **F5** chip lọc dòng thời gian mặc định "Tất cả"; chip đang chọn nền đặc (không nhầm với hover).
- **F6** tạo nhanh: khi gõ, các giá trị nhận ra (@người, #loại, !ưu tiên, SP, ^cha) hiện thành chip dưới ô;
  ký hiệu không khớp gạch chân đỏ (vẫn nằm trong tiêu đề).
- **R2 vai trò tester:** `users.is_tester` (hồ sơ người) và `teams.is_qa` (Người & Team, `PATCH /teams/{id}`);
  có cờ hoặc đang ở team QA → `auto_role = tester`. Migration biến bộ "Tester" tự tạo thành bộ dựng sẵn
  tự gán: team đang được gán bộ đó → `is_qa`, người được gán tay → `is_tester`, các dòng gán cũ bị xoá
  (downgrade trả lại). Không ai tự bật cờ cho mình hoặc cho team mình đang ở/lead/manage (trừ admin).

### 22.5 Backlog

Bước 5 của bản chuẩn hoá UI/UX (§4.3, §5 "Đầu sprint 40 px", tiêu chí A5: ≥ 12 dòng task ở 1510 × 855).
Code: `components/tasks/TaskBacklog.tsx`, CSS cuối `app/globals.css` khối `/* UI/UX Backlog */` (tiền tố `bl-`).

- **Bố cục:** `bl-screen` cao `calc(100dvh - var(--tk-frame-h, 112px))` (biến do khung trang đặt), gồm hàng
  công cụ 40 px (`bl-toolbar`) và vùng Backlog chiếm phần còn lại. Chỉ danh sách sprint (`bl-scroll`) và danh
  sách epic cuộn bên trong; trang không cuộn. Dưới 900 px trở lại cuộn cả trang, cột Epic nằm trên.
- **Hàng công cụ:** trái là chip `Epic: <tên> ✕` khi đang lọc theo epic (vẫn thấy khi cột Epic thu gọn) và số
  `N sprint · M task trong backlog`; phải là `+ Tạo sprint` (nút viền, vì nút đặc duy nhất của màn là "Tạo task").
  FilterBar dùng chung (bước 2) sẽ thay phần trái sau; nút giữ chỗ bên phải.
- **Cột Epic** bên trái rộng 220 px, nút `⟨⟨` ở chân cột thu thành dải 40 px (chữ "Epic · mã epic đang chọn"
  xoay dọc, nút `⟩⟩` để mở lại). Trạng thái thu gọn nhớ theo trình duyệt (`localStorage`
  `codepulse.tasks.backlog.epicCollapsed`, đọc/ghi trong try/catch, đọc sau khi mount để không lệch SSR).
  Thẻ epic gọn hơn (tên một dòng, mã epic ở góc phải, thanh tiến độ, `% · còn SP`); không dấu chấm đầu dòng.
- **Đầu sprint một dòng 40 px**, ghim trong vùng cuộn: nút thu/mở, tên (mục tiêu ở tooltip), nhãn "Đang chạy",
  ngày, số task, chip **SP so với sức chứa** (`10/18 SP`; đỏ kèm ⚠ khi vượt; `· đặt sức chứa` khi chưa có và
  người xem được quản lý sprint), ghi chú phạm vi đổi sau khi bắt đầu, nút `Bắt đầu` / `Hoàn tất`, menu ⋯
  (`Sửa sprint`, `Xoá sprint` khi còn kế hoạch). Bấm chip SP mở **dòng phụ**: mục tiêu sprint và dòng kế hoạch
  §21.3 (sức chứa, đặt tay / dùng TB 3 sprint, tải theo người). Hộp thoại Bắt đầu kèm Preflight giữ nguyên.
- **Sprint rỗng tự thu gọn** còn một dòng (mặc định: mở khi có task, gọn khi rỗng; người dùng bấm thì theo lựa
  chọn đó, không lưu). Ô "Kéo task vào đây" chỉ hiện khi đang kéo: sprint đang gọn hiện một ô thả (thả vào cuối
  sprint), sprint đang mở và rỗng hiện ô thả trong danh sách. Backlog cũng thu/mở được như sprint.
- **Dòng task gọn** cao khoảng 34 px (trước ~44 px). Kéo thả, chèn trước/sau dòng, thả vào cuối danh sách giữ
  nguyên cách cũ (`/tasks/{key}/move` với `before_key`/`after_key`).
- Ước tính ở 1510 × 855 (khung 112 px): vùng cuộn ~690 px, trừ 3 đầu sprint (120 px) còn ~16 dòng task.
- **Chưa làm:** nút `+ Epic` trong cột Epic (tạo epic vẫn qua "Tạo task" loại Epic); popover Hiển thị của
  Backlog (§3.5: hiện sprint rỗng, hiện thanh Epic, mật độ) chờ FilterBar dùng chung.
### 22.1 Khung trang

Bước 1 của chuẩn hoá UI/UX (codepulse-task-uiux §3.1, §3.2, §5, §6, §7, §4.10): mọi màn Task
(`/tasks`, `/tasks/me`, `/tasks/overview`, `/tasks/adoption`) dùng chung một component
`components/tasks/TaskFrame.tsx`.

- **Một hàng 48 px, ghim** ngay dưới thanh trên cùng: breadcrumb `Task / <Dự án> ▾` (nút `?` mang
  phụ đề cũ dạng tooltip), tab gạch chân 14 px, ⚙ ở cuối hàng tab, bên phải `Của tôi` (→ `/tasks/me`)
  và nút xanh `+ Tạo`. Bỏ `PageHeader`, nhãn "Dự án" và ô `<select>`.
- **Đổi dự án** bằng popover dưới tên dự án: ô tìm (không dấu, theo tên hoặc mã), ↑↓ / Enter / Esc,
  dự án chưa bật ghi "chưa bật" xám; breadcrumb ghi "· chưa bật" thay cho hàng thông báo.
- **Tab** theo thứ tự UI/UX: Board, Backlog, Danh sách, Báo cáo, Sức khoẻ, Phiên bản; Timeline (không có
  trong danh sách đó) đứng cuối. Cài đặt không còn là tab: ⚙ là menu gồm "Cài đặt Task của dự án"
  (người quản lý dự án) và "Tình trạng bật Task" (`tasks.manage` hoặc `admin.projects`; §4.10 — nút
  riêng ở đầu trang bỏ). `?tab=settings` vẫn mở được; ⚙ gạch chân khi đang ở đó. Ở `/tasks/me`,
  `/tasks/overview` ⚙ chỉ có "Tình trạng bật Task"; `/tasks/adoption` không có ⚙.
- **Màn hẹp:** dưới 1280 px tab thứ 4 trở đi vào menu "Thêm ▾" (tab đang mở vẫn ở trên hàng);
  dưới 768 px chỉ còn tab đang mở, chữ "Của tôi" / "Tạo" ẩn còn icon. Không đo bằng JS: chỉ CSS, nên
  không giật bố cục.
- **Phím** (thêm vào hộp "?" nhóm "Trang Task"): `1`…`6` chuyển Board, Backlog, Danh sách, Báo cáo,
  Sức khoẻ, Phiên bản; `7` Timeline; `C` tạo task (ở `/tasks/me`, `/tasks/overview` mở
  `/tasks?create=1`). `C` nhường cho "Viết bình luận" khi đang mở task (có `[data-shortcut=comment]`);
  không phím nào chạy khi đang gõ, giữ Ctrl/Alt/Meta/Shift hoặc sau hộp thoại. `N` (toàn cục) giữ nguyên.
- **Bề rộng:** trang Task bỏ giới hạn 1480 px, lề 16 px (< 1440 px) / 24 px (≥ 1440 px), không có
  dòng chú thích chân trang (để view chính lấp đầy màn hình).
- **Biến CSS cho các màn** (globals.css, mục "UI/UX frame"): `--tk-head-h` 48px, `--tk-bar-h` 40px
  (thanh lọc), `--tk-gap` 8px, `--tk-pad-x`, `--tk-pad-b` 12px, `--tk-bar-top` (sticky `top` của thanh
  lọc), `--tk-frame-h` (toàn bộ khung quanh view chính có thanh lọc), `--tk-view-h` =
  `100dvh − --tk-frame-h` (chiều cao Board / bảng Danh sách / Backlog), `--tk-view-h-full` (màn không
  có thanh lọc). Màn đặt thanh lọc là phần tử đầu tiên, `position: sticky; top: var(--tk-bar-top)`,
  cao `var(--tk-bar-h)`, rồi view chính `height: var(--tk-view-h)` và tự cuộn bên trong.
- Khối `?task=KEY` (ngăn chi tiết) nằm ngoài khung, không đổi.
### 22.6 Ngăn chi tiết task

UI/UX §4.8, §6, §7 (bước 6). Bấm task ở Board, Danh sách, Backlog hoặc Việc của tôi mở **ngăn bên phải**
(`components/tasks/TaskDrawer.tsx`) thay cho drawer phủ cả màn hình; `/tasks/KEY` vẫn là trang riêng.

- **Không chặn view phía sau:** ngăn cố định bên phải dưới thanh trên cùng, không có lớp nền mờ, không khoá
  cuộn, không bẫy focus (`role="dialog"`, `aria-modal="false"`). Màn ≥ 1280 px: `main` chừa lề phải bằng độ
  rộng ngăn (biến CSS `--td-w`, lớp `td-open` trên `body`) nên không task nào bị che; màn hẹp hơn ngăn nằm
  đè; dưới 768 px ngăn chiếm toàn màn hình (§6).
- **Độ rộng:** mặc định 560 px, kéo mép trái từ 420 px tới 60% chiều rộng cửa sổ; tay nắm là
  `role="separator"` có focus, mũi tên trái / phải đổi 16 px (giữ Shift: 64 px), Home / End về min / max,
  bấm đúp về 560 px. Nhớ theo trình duyệt (`localStorage` `cp:tasks:drawer-w`, lỗi bộ nhớ thì bỏ qua).
- **Đầu ngăn (48 px):** loại, mã (`h2`, nhận focus khi mở), vị trí trong danh sách `3/24`, nút ▲ / ▼ (task
  trước / kế tiếp), ⤢ mở toàn trang, ↗ sao chép link `/tasks/KEY` (đổi thành ✓ "Đã sao chép link", báo qua
  `aria-live`), ✕ đóng. Mọi nút chỉ có icon đều có nhãn và tooltip. Đóng ngăn trả focus về chỗ đã mở.
- **Phím:** `J` / `K` task kế tiếp / trước của danh sách đang hiện; `Esc` đóng ngăn, trừ khi đang gõ trong ô
  nhập hoặc đang mở popover, menu, listbox, hộp thoại (`[aria-modal=true]`, `[role=menu]`, `[role=listbox]`,
  nút `[aria-haspopup][aria-expanded=true]`, bảng lệnh, "+ Thêm trường") hoặc phím đã được thành phần khác
  xử lý (`defaultPrevented`). Không chạy khi giữ Ctrl / Alt / Meta / Shift.
- **Danh sách cho J/K** (`components/tasks/drawerNav.ts`): store module, mỗi màn gọi
  `useDrawerList(keys)` với mã task theo đúng thứ tự đang hiện (Board: card sau lọc; Danh sách: các dòng
  trang hiện tại; Backlog: các sprint mở rồi Backlog; Việc của tôi: các task của trang). Màn đăng ký sau cùng
  thắng, màn unmount chỉ xoá danh sách của chính nó. Task không có trong danh sách thì J/K không làm gì và
  đầu ngăn không hiện vị trí.
- **Trong ngăn** (`TaskDetail mode="drawer"`): một cột; dưới tiêu đề là **dải thuộc tính 2 cột** Trạng thái,
  Người làm, Ưu tiên, Sprint, SP, Hạn (SP và Hạn luôn hiện kể cả khi trống); mọi thứ khác (Loại, Lý do đóng,
  Nhãn, Thành phần, Phiên bản, Ước lượng, Đã làm, trường tuỳ biến, chọn thêm trường trống, Theo dõi, nút AI,
  người tạo / thời gian, Xoá task) gập trong "+ Thêm trường", nhãn kèm số trường đã có giá trị. Khối Sức
  khoẻ nằm dưới dải thuộc tính. Đổi task bằng J/K cuộn ngăn về đầu.
- **Toàn trang** (`/tasks/KEY`, `mode="page"`): bỏ tiêu đề lớn `TASK / KEY`; đầu trang là breadcrumb
  `Task / KEY`, tiêu đề task là `h1`. Tiêu đề cùng khối Sức khoẻ ghim dưới thanh trên cùng khi cuộn; cột
  thuộc tính ghim (cuộn riêng nếu cao hơn màn hình). Dưới 1100 px giữ bố cục một cột như trước.
- **Chuyển động:** ngăn trượt vào 180 ms `ease-out`; tắt khi `prefers-reduced-motion: reduce`.
- **Việc của tôi** (`/tasks/me`) đọc `?task=KEY` và mở cùng ngăn (`UrlTaskDrawer`); các link task trong
  `MyWork.tsx` trỏ về `/tasks/me?task=KEY` để mở ngăn thay vì rời trang.
