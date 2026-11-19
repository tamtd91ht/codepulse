# 32 – Phản ánh & Đề xuất (Feedback)

> Yêu cầu của người dùng (2026-11-18): một menu cho phép mọi người phản ánh và đề xuất tính năng
> mới cho CodePulse; một LLM phân tích, tổng hợp thành nội dung `.md`, lưu theo cấu trúc cây; người
> quản lý xem từng phản ánh (của ai, lĩnh vực, chức năng, đề xuất gì…), tích chọn và bấm
> **Export** để ra **một file `.md`** giao cho agent code nâng cấp. Phản ánh luôn ghi rõ tác giả.
> Thiết kế UI/UX giao cho agent đề xuất; các quyết định ghi ở §9.

## 1. Mục tiêu

- Ai cũng gửi được phản ánh trong < 1 phút, không phải biết hệ thống chia module ra sao.
- Người quản lý nhìn được *toàn cảnh*: phản ánh nằm ở lĩnh vực nào, chức năng nào, bao nhiêu
  người cùng kêu, đề xuất gì — không phải đọc từng tin nhắn thô.
- Đầu ra cuối cùng là một file Markdown mà một coding agent (Claude Code) đọc là làm được:
  bối cảnh, vấn đề, đề xuất, tiêu chí chấp nhận, tác giả, mã tham chiếu.

## 2. Luồng

```
Người dùng ──gửi──▶ Phản ánh (thô, có tác giả)
                       │ job LLM (hàng đợi "asked")
                       ▼
                 Bản phân tích .md + trường cấu trúc
                 (loại, lĩnh vực, chức năng, ưu tiên, tóm tắt, đề xuất, tiêu chí, trùng với #…)
                       │
Người quản lý ──xem cây Lĩnh vực ▸ Chức năng ▸ Phản ánh──▶ duyệt / sửa phân loại / từ chối
                       │ tích chọn  ▶ Export
                       ▼
                 Gói export .md (lưu lại, tải lại được) ── giao cho agent code
```

## 3. Dữ liệu

`feedback_items`

| cột | ý nghĩa |
|---|---|
| id, author_id (users) | tác giả — luôn hiện ở mọi màn và trong file export |
| kind | `bug` · `feature` · `improvement` · `question` (người gửi chọn; AI có thể sửa) |
| area | khoá lĩnh vực (§4), người gửi chọn hoặc để AI đoán |
| title, body | nội dung thô người gửi viết (body ≤ 8 000 ký tự) |
| page_url | trang người gửi đang ở khi bấm (tự điền, sửa được) |
| status | `new` · `accepted` · `planned` · `done` · `rejected` (vòng đời xử lý) |
| analysis_status | `queued` · `analyzing` · `ready` · `failed` |
| ai_kind, ai_area, feature, priority | kết quả phân loại (`priority`: `p1`·`p2`·`p3`) |
| ai_title, summary, analysis_md | tiêu đề chuẩn hoá, tóm tắt 1 câu, nội dung `.md` |
| ai_result (JSONB) | `problem`, `proposal`, `acceptance[]`, `tags[]`, `duplicates[]`, `questions[]` |
| triage_note | ghi chú của người quản lý (đi vào export) |
| exported_at, export_count | đã nằm trong gói export nào chưa |
| model, cost_usd, error, requested_at, started_at, finished_at | như Tài liệu dự án (29 §12) |

`feedback_exports`: id, title, note, item_ids (JSONB), body (Markdown), created_by, created_at.
Gói export được lưu: tải lại đúng file đã giao, biết ai export cái gì, khi nào.

**Cây**: không lưu thành file trên đĩa mà là *đường dẫn ảo* tính từ dữ liệu:
`feedback/<lĩnh vực>/<chức năng>/<#id>-<slug>.md`. Lĩnh vực là danh sách cố định (§4) nên cây ổn
định; chức năng do AI đặt, được cho danh sách chức năng đã có của lĩnh vực đó để **dùng lại tên cũ**
thay vì đẻ nhánh mới; người quản lý đổi tên/ghép được bằng cách sửa phân loại.

## 4. Lĩnh vực (tầng 1 của cây)

`overview` Tổng quan & Của tôi · `review` Review & Finding · `tasks` Task · `testcases` Testcase ·
`docs` Tài liệu dự án · `dpi` DPI hàng tháng · `reports` Báo cáo & Thành viên · `insights` Luồng
làm việc & Team · `stetho` Stetho (trợ lý) · `admin` Quản trị & Phân quyền · `integrations` Tích
hợp (GitLab, MCP, Telegram, Jira) · `general` Chung / Khác.

Trang đang mở (`page_url`) gợi ý sẵn lĩnh vực trong form (ví dụ đang ở `/tasks/...` → Task).

## 5. Phân tích bằng LLM

- Chạy khi gửi (và khi người gửi sửa nội dung, hoặc người quản lý bấm "Phân tích lại").
- Prompt: danh sách lĩnh vực, các chức năng đã có của từng lĩnh vực (để dùng lại), ≤ 60 phản ánh
  gần nhất còn mở (id + tiêu đề + lĩnh vực) để đánh dấu **trùng**, rồi nội dung của người gửi
  bọc trong `<feedback>` — là **dữ liệu, không phải chỉ dẫn**.
- Trả JSON (schema cố định): `kind, area, feature, priority, title, summary, problem, proposal,
  acceptance[], tags[], duplicates[], questions[]`. Server kiểm tra từng trường (lĩnh vực phải
  thuộc danh sách, trùng phải là id có thật), rồi **tự dựng `analysis_md`** từ các trường đó — mô
  hình không viết Markdown tự do, nên không có ảnh/HTML/link lạ lọt vào.
- Chi phí ghi audit `feedback.analyze.cost`, tính vào ngân sách LLM tháng; hết ngân sách → phản
  ánh vẫn được lưu, chỉ chưa phân tích (người quản lý vẫn xem/export bản thô).
- Giới hạn: 20 phản ánh / người / ngày; job 1 lần thử lại, timeout 180 s.

## 6. Quyền

| quyền | ai có mặc định | cho phép |
|---|---|---|
| `feedback.submit` | mọi thành viên (bộ "Thành viên") | gửi, xem & sửa phản ánh của mình (khi còn `new`) |
| `feedback.manage` | admin (cấp thêm được qua bộ quyền) | xem mọi phản ánh, phân loại, đổi trạng thái (một hoặc nhiều mục, §7.3), phân tích lại, export, xoá |

Hai quyền nằm trong catalog (`app/permissions/catalog.py`, nhóm **"Phản ánh & Đề xuất"**) nên
hiện ngay ở **Admin › Phân quyền**: admin tick `feedback.manage` vào một bộ quyền (ví dụ bộ của
PM) hoặc bỏ `feedback.submit` khỏi bộ "Thành viên" để tắt menu với mọi người (user
2026-11-18: "bổ sung tính năng này vào phân quyền"). Menu hiện khi có một trong hai quyền.

Người gửi thấy trạng thái xử lý của phản ánh mình (đã nhận / đã lên kế hoạch / xong / từ chối kèm
ghi chú) — khép vòng phản hồi để mọi người còn muốn góp ý tiếp.

## 7. UI/UX

Menu **"Phản ánh & Đề xuất"** (icon tin nhắn) trong sidebar, với ai có `feedback.submit` hoặc
`feedback.manage`. Một trang `/feedback`, hai tab (tab thứ hai chỉ người quản lý thấy):

### 7.1 Tab "Của tôi" (mọi người)

```
┌───────────────────────────────────────────────┬─────────────────────────────┐
│ Gửi phản ánh                                  │ Phản ánh của tôi            │
│ Loại: (Lỗi)(Đề xuất tính năng)(Cải tiến)(Hỏi) │ ● Đề xuất · Task · Đã nhận  │
│ Lĩnh vực: [Task ▾]   (gợi ý từ trang đang mở) │   Lọc board theo sprint     │
│ Tiêu đề: [______________________________]     │   AI: "Task ▸ Board" · P2    │
│ Mô tả:   [ Vấn đề gặp phải / mong muốn …  ]   │ ● Lỗi · Review · Mới        │
│          [ gợi ý khung: Hiện tại / Mong muốn ]│   …                         │
│ Trang liên quan: /tasks/board (tự điền)       │                             │
│                               [Gửi phản ánh]  │                             │
└───────────────────────────────────────────────┴─────────────────────────────┘
```

- Loại chọn bằng 4 nút segmented to, rõ; lĩnh vực là select; mô tả có placeholder hướng dẫn
  ("Hiện tại… / Mong muốn… / Vì sao quan trọng…").
- Gửi xong: toast "Đã gửi — AI đang phân tích", mục mới hiện đầu danh sách bên phải, trạng thái
  phân tích tự cập nhật (poll 3 s khi còn đang phân tích).
- Bấm một mục: xem bản phân tích AI (`.md`) + ghi chú của người quản lý; sửa / xoá khi còn `new`.

### 7.2 Tab "Tổng hợp" (người quản lý) — ba cột

```
┌ Cây ───────────────┬ Danh sách (đã lọc theo nút cây) ──────────┬ Chi tiết ─────────────────┐
│ 🔍 Tìm             │ [☐] Chọn tất cả 12   Trạng thái[▾] Loại[▾] │ #42 Lọc board theo sprint │
│ ▾ Task        (12) │ ☑ #42 Lọc board theo sprint      P2  Đã nhận│ 👤 Nguyễn An · Team BE    │
│   ▸ Board      (5) │ ☐ #40 Kéo thả chậm khi >200 task P1  Mới    │ Đề xuất · Task ▸ Board    │
│   ▸ Sprint     (4) │ ☑ #37 Thêm WIP limit             P3  Mới    │ 18/11/2026 · trang /tasks │
│ ▸ Review       (7) │   👤 tác giả · 2 người cùng ý (trùng #31)   │ ─── phân tích AI (.md) ── │
│ ▸ DPI          (3) │                                             │ ## Vấn đề  ## Đề xuất     │
│ ▸ Chung        (1) │                                             │ ## Tiêu chí chấp nhận     │
│                    │                                             │ ─── nội dung gốc ───       │
│ 📄 feedback/tasks/ │                                             │ Phân loại: [lĩnh vực▾]    │
│    board/42-….md   │                                             │ [chức năng] [P2▾] [trạng thái▾]│
└────────────────────┴─────────────────────────────────────────────┴───────────────────────────┘
                         thanh nổi khi có mục được chọn:
               ┌───────────────────────────────────────────────────────┐
               │ Đã chọn 3 phản ánh · 2 lĩnh vực   [Bỏ chọn] [⬇ Export .md] │
               └───────────────────────────────────────────────────────┘
```

- **Cây** Lĩnh vực ▸ Chức năng, mỗi nút có số đếm; bấm nút lọc danh sách; mỗi nút có checkbox
  "chọn cả nhánh". Dưới cây hiện đường dẫn `.md` ảo của mục đang xem.
- **Danh sách**: mỗi dòng có checkbox, mã `#id`, tiêu đề (AI chuẩn hoá), ưu tiên, trạng thái,
  dòng phụ: tác giả · loại · ngày · "trùng #n". Bộ lọc trạng thái/loại là MultiSelect, mặc định
  ẩn `done`/`rejected`. Tick chọn giữ nguyên khi đổi nhánh cây (chọn gom từ nhiều nhánh).
- **Chi tiết**: khối tác giả (avatar, tên, team, thời điểm, trang), bản `.md` render, nội dung
  gốc thu gọn, form phân loại (lĩnh vực, chức năng, ưu tiên, trạng thái, ghi chú) — lưu là áp ngay.
  Hành động hàng: Phân tích lại · Tải `.md` của mục này · Xoá (icon-only, tooltip).
- **Thanh export nổi** (sticky đáy) khi đã chọn ≥ 1: nói đã chọn bao nhiêu, bao nhiêu lĩnh vực;
  "Export .md" mở hộp thoại: tên gói, ghi chú cho agent, tuỳ chọn "đánh dấu các mục là Đã lên kế
  hoạch" (bật sẵn). Xác nhận → tải ngay file, gói được lưu ở mục "Lịch sử export" (dưới cây) để tải lại.
- Màn vừa (< 1280 px): cây + danh sách hai cột, chi tiết xuống dưới (bấm một dòng thì cuộn tới);
  màn hẹp (< 1024 px): xếp chồng cây (cao tối đa 320 px), danh sách, chi tiết.

### 7.3 Chuyển trạng thái một / nhiều phản ánh (user 2026-11-19)

> "cho phép chuyển riêng từng nội dung hoặc chọn nhiều nội dung để chuyển trạng thái, chỉ admin mới
> được thao tác. Ví dụ … chọn 4 xuất .md, tiến hành nâng cấp xong thì … chọn cả 4 or 5 mục để
> chuyển trạng thái đã xong."

- **Từng mục**: trong khung chi tiết, trạng thái cạnh `#id` là **stage chip** kiểu OMICX (viền,
  chấm màu, tên, `⌄`) với người có `feedback.manage`; bấm mở menu 5 trạng thái (chấm màu, dấu ✓ ở
  trạng thái hiện tại), chọn là lưu ngay. Người chỉ có `feedback.submit` thấy pill tĩnh.
- **Nhiều mục**: khi có mục được chọn, hàng đầu danh sách (dính khi cuộn, nền `primary-soft`)
  thành **thanh chọn**: `☑ Đã chọn N · M lĩnh vực [· K ngoài bộ lọc]` … `Bỏ chọn` (ghost) ·
  `Chuyển trạng thái ⌄` (outline, menu như trên, ghi "N đã ở đây" từng trạng thái) · `Export .md`
  (nút đặc duy nhất, màu ink). Thanh nằm **trong cột danh sách**, không còn thanh nổi giữa màn
  hình đè lên khung chi tiết (user 2026-11-19: "thanh menu mới tràn sang cả bên view chi tiết").
  Chọn trạng thái → hộp xác nhận 468 px: danh sách mục (chấm trạng thái cũ → mới, mục đã ở trạng
  thái đó mờ đi), ô ghi chú cho tác giả (tuỳ chọn, **nối thêm** dưới ghi chú đang có, không ghi
  đè), chân hộp `Đóng` + `Chuyển N phản ánh`. Xong → bỏ chọn, thông báo "Đã chuyển N phản ánh sang …".
- **Chuông cho tác giả** (user 2026-11-19): mỗi lần trạng thái phản ánh đổi (chuyển một / nhiều
  mục, form phân loại, export đánh dấu "Đã lên kế hoạch"), tác giả nhận một thông báo
  `feedback.status`: "Phản ánh #12 chuyển sang “Đã xong”" (nhiều mục của cùng người trong một lần →
  một thông báo "3 phản ánh của bạn …" liệt kê ≤ 5 mục), kèm ghi chú nếu có; màu xanh khi Xong, đỏ
  khi Từ chối. Link `/feedback?item=<id>` mở thẳng phản ánh ở tab "Của tôi". Người chuyển không tự
  nhận; mục không đổi trạng thái không báo.
- **Chọn lại đúng gói đã export**: mỗi gói trong **Lịch sử export** có nút icon "Chọn các phản
  ánh của gói này" — chọn đúng các mục của gói (bỏ lọc trạng thái để thấy cả mục đã xong), rồi
  chuyển trạng thái ở thanh chọn đầu danh sách. Đây là luồng "export → agent nâng cấp → đánh dấu Đã xong".
- API: `POST /feedback/status` `{item_ids[1..500], status, note?}` (quyền `feedback.manage`) →
  `{changed[], unchanged[]}`; tất cả hoặc không (một id không còn → 404, không áp nửa chừng); ghi
  `triaged_by/at`, audit `feedback.status` (không ghi khi không có gì thay đổi). Một mục cũng dùng
  endpoint này.
- Trạng thái chuyển tự do giữa 5 giá trị (mở lại một mục `done` về `accepted` được) — vòng đời do
  người duyệt quyết, không ép thứ tự.
  Chuyển về `new` thì tác giả lại sửa / xoá được (đúng nghĩa "trả lại cho tác giả").
- Đồng thời: khoá dòng (`SELECT … FOR UPDATE`, theo id) ở cả chuyển trạng thái và lúc tác giả sửa;
  ghi chú nối thêm mà vượt 4 000 ký tự → 422 cả lệnh, không cắt ghi chú cũ; audit mỗi mục một dòng
  `feedback:<id>` để lịch sử từng phản ánh thấy được.

**Sửa lỗi (user 2026-11-19)**: chi tiết báo "Đã export 3 lần" trong khi "Lịch sử export (1)". Backend
đếm đúng (mỗi gói +1); danh sách Lịch sử ở trình duyệt bị cũ vì lệnh làm mới chạy sau khi tải file.
Giờ gói mới được chèn ngay vào lịch sử, danh sách được làm mới **trước** khi tải file, và pill
"Đã export N lần" trong chi tiết đếm từ **cùng** danh sách lịch sử (cùng khoá SWR, tooltip ghi tên
các gói) nên hai chỗ không thể lệch nhau.

## 8. File export (đầu vào của agent code)

```markdown
# Gói nâng cấp CodePulse: <tên gói>
> Xuất ngày … bởi … · N phản ánh · lĩnh vực: …

## Hướng dẫn cho agent
- Đọc docs/specs/00_README.md và spec của từng lĩnh vực trước khi sửa.
- Làm từng mục theo thứ tự ưu tiên (P1 → P3); mỗi mục: viết test, implement, chạy lint/test.
- Không làm ngoài phạm vi tiêu chí chấp nhận; điểm chưa rõ → ghi IDEAS.md / hỏi.
- Commit `feat(<lĩnh vực>): … (feedback #id)`.
<ghi chú của người export>

## Tóm tắt
| # | Ưu tiên | Lĩnh vực ▸ Chức năng | Loại | Tiêu đề | Tác giả |

## Task ▸ Board
### #42 · Lọc board theo sprint  (P2 · Đề xuất tính năng)
- Tác giả: Nguyễn An (Team BE) · gửi 18/11/2026 · trang /tasks/board
- Trùng / liên quan: #31
**Vấn đề** … **Đề xuất** … **Tiêu chí chấp nhận** - [ ] …
**Ghi chú người duyệt** …
<details><summary>Nội dung gốc</summary> … </details>
```

Mục chưa phân tích được vẫn export bằng nội dung gốc (ghi "chưa phân tích").

## 9. Quyết định (agent tự quyết, user 2026-11-03 "tự quyết, ghi vào spec")

- [x] **Cây ảo trong DB, không ghi file ra đĩa** — một nguồn sự thật, phân quyền được, sửa phân loại
  là cây đổi theo; đường dẫn `.md` vẫn hiện và tải từng mục được.
- [x] **Export ghép tất định, không gọi LLM lần hai** — tức thì, không tốn tiền, không "bịa thêm";
  phần tổng hợp thông minh đã nằm ở bước phân tích từng mục.
- [x] **Markdown do server dựng từ trường JSON**, không để mô hình viết tự do (chặn prompt injection
  chèn ảnh/link vào file sẽ giao cho agent code).
- [x] **Lĩnh vực cố định 12 mục, chức năng do AI đặt và tái dùng** — tầng 1 ổn định, tầng 2 linh hoạt.
- [x] **`feedback.manage` chỉ admin mặc định** — nội dung phản ánh có thể nhạy cảm (phàn nàn về quy
  trình/người); admin cấp thêm cho PM qua bộ quyền.
- [x] **Không đính kèm ảnh ở v1** — ghi IDEAS.md (dán ảnh chụp màn hình như Task).
- [x] **Không vote/+1 công khai ở v1** — AI đánh dấu trùng thay thế; vote ghi IDEAS.md.
- [x] **"Chỉ admin chuyển trạng thái" = quyền `feedback.manage`** (2026-11-19) — quyền này mặc định
  chỉ admin có; admin tự quyết cấp thêm cho ai qua bộ quyền, nên không tách thêm quyền riêng.
- [x] **Ghi chú khi chuyển hàng loạt được nối thêm, không ghi đè** — không làm mất ghi chú riêng
  từng mục đã viết trước.
- [x] **Không ép thứ tự trạng thái** — mở lại mục đã xong / từ chối nhầm phải làm được ngay.

## 10. Kiểm thử

- API: gửi (quyền, giới hạn/ngày, xếp job), của tôi chỉ thấy của mình, quản lý thấy hết, sửa/xoá
  khi `new`, phân loại, export (đúng thứ tự, có tác giả, đánh dấu planned, lưu gói, tải lại),
  chuyển trạng thái nhiều mục (chỉ quản lý, tất cả hoặc không, mục đã ở trạng thái giữ nguyên,
  ghi chú nối thêm, audit).
- Phân tích: kết quả hợp lệ dựng đúng `.md`; lĩnh vực lạ → `general`; trùng id lạ bị bỏ; nội dung
  người gửi nằm trong `<feedback>`; JSON hỏng → `failed`; hết ngân sách → `failed` có lý do.
- Migration: bảng mới, quyền `feedback.submit` vào bộ "Thành viên".
