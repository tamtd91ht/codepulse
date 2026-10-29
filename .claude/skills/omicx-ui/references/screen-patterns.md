# OMICX UI - screen patterns

Layouts observed in the OMICX web app (CRM module). Use the pattern that matches the screen
you are building; keep the order of elements, it is what users of the product expect.

## App shell

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ logo  [workspace ⌄]  [● 5.141 Nạp]        [↑ Nâng cấp] ☾ 🔔4 ⚙ 🇻🇳 (avatar) │ 56 px
├──┬────────────┬──────────────────────────────────────────────────────────┤
│▣ │ Module     │ ⊟ Module › Page                                          │
│◇ │ GROUP      │ ┌──────────────────────────────────────────────────────┐ │
│⚙ │ ▣ Item     │ │ page content on `surface`                            │ │
│  │ ▣ Item     │ │                                                      │ │
│40│ GROUP      │ └──────────────────────────────────────────────────────┘ │
│px│ 180 px     │ canvas = `background`                                    │
└──┴────────────┴──────────────────────────────────────────────────────────┘
```

- Top bar: brand and context on the left (workspace switcher, credit balance with a top-up
  link); global actions on the right (upgrade, theme toggle, notifications with a red count,
  settings, language, avatar).
- Icon rail switches modules; the sidebar shows the current module's navigation in groups
  (group label 12 px `muted`, item 14 px with a 20 px outline icon, active item filled with
  `primary`, r10). Separators between groups.
- Content panel: rounded, `surface`, 1 px `border`; breadcrumb row on top with a sidebar
  collapse icon; current crumb 14/600.
- Persistent floating actions (softphone bottom-left, assistant bottom-right) never cover
  primary content on common viewports.

## List page

```text
[Tất cả] [Tiềm năng] [Chính thức] [Tạo bộ lọc mới]
Khách hàng [1] ↻  (🔍 Tìm kiếm)  Trạng thái ⌄  Hành trình ⌄  Phụ trách ⌄  ⫶ Thêm     ☰ ▥  [▦ Bố cục] [Tải lên] [Thêm mới]
┌ ☐ # Khách hàng        Hành trình  Số điện thoại  Email   Phụ trách   Nguồn   Ngày tạo ⇅  … ┐
│ ☐ 1 (N) Nguyễn Văn A   ● Đăng ký   +849...        a@b.c   (Đ) Tâm     Web     11:27 14/10  📞 ⋮ │
└──────────────────────────────────────────────────────────────────────────────────────────┘
[1] [2] …                                           1-15 trên tổng 120 dữ liệu | 15 dòng mỗi trang ⌄
```

Order is fixed: saved views → title with count and refresh → search → at most three inline
filters → "more filters" → (right) list / board switch → column layout → import → create.

## Dashboard

```text
Tổng quan                                   (22/28 bold)
One-line description of what the page answers (14 muted)

[Thời gian: 30 ngày trước ⌄ ×] [Nhân viên phụ trách ⌄] [Phòng ban ⌄]

┌ Tổng KH ┐ ┌ Tăng trưởng ┐ ┌ Có SĐT ┐ ┌ Có Email ┐ ┌ Trùng ┐     equal KPI tiles, r20
│ 1       │ │ —           │ │ 100%   │ │ 100%     │ │ 0%    │     value 22 bold
│ hint    │ │ vs prev.    │ │ 1 / 1  │ │ 1 / 1    │ │ 0 / 1 │     hint 12 muted
└─────────┘ └─────────────┘ └────────┘ └──────────┘ └───────┘
┌ Khách hàng theo trạng thái ──────────────────────────────────────────┐
│ subtitle: what clicking does                                         │
│  (donut 100% Hoạt động)   ● Hoạt động        1   100%                │
│                           ● Không hoạt động  0     0%                │
└──────────────────────────────────────────────────────────────────────┘
┌ Khách hàng theo hành trình          [Hành trình|Nhóm|Tag|Nguồn] ┐
│ ● Đăng ký  ███████████████████████████████████████████  1        │
└──────────────────────────────────────────────────────────────────┘
```

- Filters apply to the whole page and are shown as chips; a set filter is clearable in
  place.
- A KPI without context is not shown: give a comparison period or a denominator; show `—`
  when the comparison cannot be computed.
- Legend rows carry the count and the percentage; the hovered or selected row highlights
  with a `border` outline.
- A segmented control switches the dimension of one chart instead of adding more cards.

## Quick-create modal

- Opened from the list's create button; 468 px, required and most-used fields only
  (name and gender on one row, phone with country prefix, email, birth date).
- Focus goes to the first field; Enter submits; Escape closes without saving.
- "Xem đầy đủ ⌄" (show the full form) expands in place or opens the full page.
- Footer: Close (tertiary) and Create (solid green), right-aligned.
- Validation messages appear under the field in `danger`; the submit button shows a spinner
  and is disabled while the request runs.

## Detail page

```text
(avatar) Nguyễn Văn Demo  ● Hoạt động                     Phụ trách (Đ) Tôi ⌄  Liên quan — ⌄
         +849... · demo@example.com
[● Đăng ký 14 ngày ⌄] | 📞 📝 ☑ 📅 $ |                              [✎ Chỉnh sửa] [⋯] [▣]
Tạo lúc 11:27 14/10/2026 · (Đ) Tôi   Cập nhật 11:27 14/10/2026 · (Đ) Tôi   Nguồn: Web Client
┌ Thông tin cơ bản ┬ Tương tác | Hành trình | Chăm sóc | Lịch sử ┬ AI insights          ┐
│ Họ tên   value   │ (search)            ‹ › 2026 ›             │ ⌄ Quy trình chăm sóc + ⋯│
│ SĐT      value ⧉ │ activity heat-calendar (3 months)          │ ⌄ Cơ hội            + ⋯ │
│ Email    value ⧉ │ (Đ) Ghi chú cho khách hàng này…        (↑) │ ⌄ Công việc [1 mở]  + ⋯ │
│ Giới tính -      │ Thứ 4, 14 tháng 10 ⌄                       │   [-14d] 13:00 …        │
│ …                │ ☑ = Gọi tư vấn (Đ) 13:00 Đang mở ›         │ ⌄ Lịch hẹn [1 mở]   + ⋯ │
└──────────────────┴────────────────────────────────────────────┴─────────────────────────┘
```

- Left: key/value list (label `muted`, value `primary`, copy icon on contact values, `-` for
  empty), collapsible, resizable divider.
- Centre: underline tabs; the interaction tab combines search, a period navigator, an
  activity heat-calendar, a note composer and a day-grouped timeline with status and links.
- Right: related objects as collapsible sections with count badges, `+` to create in
  context and a menu; overdue items show a `danger` badge with the relative age.
- Header quick actions create related records without leaving the page.

## Page header with tabs (module pages)

Most module pages that are not the module's main list open with a page header: title 22/28
bold, one-line muted description, then underline tabs for the page's sub-views ("Webhooks |
Lịch sử gửi", "Danh sách | Vai trò | Ca làm việc | Lịch sử thay đổi"), then the list card.
Sibling categories of one object type use pill tabs under the description instead
("Chăm sóc khách hàng | Công việc | Lịch hẹn | Cơ hội").

## Master-detail list

A narrow left column (≈200 px) holds a tree or group list (organisation, folders) with an
inline text action in `success` ("Thêm phòng ban"); the right side is the normal list card
filtered by the selection. Role badges use a tinted fill of their colour (owner: `danger`
text on a 10 % `danger` fill), not a solid one.

## Usage / metrics card

Above a usage-billed list: a card with the metric name and period in 12 px muted
("Credit · 10/2026"), the big value (22 bold), a line or bar chart across the card, a legend
below, and on the right a period navigator (`‹ Tháng 10/2026 ›`, next disabled for the
current period) plus a segmented switch for the metric ("Credit | Sản lượng").

## Settings page

Settings render as one centred column (`max-w-147` ≈ 588 px, 88 px top and bottom padding,
32 px between sections), never full width:

```text
                 Tổng quan                                  (22/28 bold)
                 Manage general information … (14 muted)

                 Thông tin doanh nghiệp                     (16/600)
                 ┌───────────────────────────────────────────────┐
                 │ (T)  TamTD                       [Chỉnh sửa] │  r16, p16, border
                 │      tamtd.omicx.one ⧉                        │
                 │ 👥 1 người dùng   📅 Tạo từ 28/07/2026        │
                 │ ───────────────────────────────────────────── │
                 │ Chủ sở hữu hiện tại: Bạn                      │
                 └───────────────────────────────────────────────┘
                 Khu vực
                 ┌───────────────────────────────────────────────┐
                 │ [icon] (UTC +07:00) Asia/Ho_Chi_Minh [Chỉnh sửa]│
                 └───────────────────────────────────────────────┘
```

Each section is read-only with its own outline "Chỉnh sửa" button (edit one section at a
time, in a modal or in place); a 36 px icon box leads each row.

## Setup wizard

First-time setup of a feature (for example a switchboard) is one centred column with
numbered steps ("1. Chọn khu vực tổng đài", "2. Thông tin"). Later steps stay visible but
muted until the earlier step is answered. Choices are tiles in a two-column grid, grouped
under small muted group labels (countries); each tile shows an icon, the name and live
state on the right (latency `118ms` with a `warning` dot, "Mất kết nối" with a `danger`
dot, `•••` while measuring), so the user picks on facts, not names.

## Empty list

Inside a list card, the empty state is centred in the table body: an optional 24 px muted
icon above "Chưa có dữ liệu" / "Không có dữ liệu" in 14 px muted; the toolbar and the create
action stay visible.

## Feedback

- Toast for the result of an action; errors include the machine code and a copy action.
- In-place empty text for empty sections; skeletons for loading blocks; the page shell and
  navigation render immediately.
- Destructive actions confirm in a dialog that names the object and the consequence; the
  confirm button is `danger`.
