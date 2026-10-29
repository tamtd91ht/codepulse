# OMICX UI - component specs

Measured from the OMICX web app (HeroUI v3 on React Aria, Tailwind v4, brand layer on top).
Sizes in px; colours are token names from [tokens.css](tokens.css). "sm" is the toolbar size,
"md" the form and dialog size.

## Buttons

| Variant | Spec | Example |
|---|---|---|
| Solid create | sm h32 px12 r10 / md h36 px16 r12; bg `success`, text white 14/600 | Thêm mới, submit |
| Solid primary | as solid create but bg `primary`, text `primary-foreground`; only when the main action is not a create | Tải tệp lên (upload) |
| Text action | 14/500 `success` text, no border, inline next to a group title | Thêm phòng ban |
| Outline success | 1 px `success` border, text `success` 14/500, no fill | Tải lên (import) |
| Outline neutral | 1 px `border`, text `primary` 14/500 | Bố cục, Chỉnh sửa, Xem đầy đủ |
| Tertiary | bg `default` (`#e7eaf0` / `#253a55`), text `primary` 14/500 | Đóng |
| Ghost trigger | no border or fill, label + 16 px chevron | Trạng thái ⌄ (filter) |
| Icon | 32x32, 20 px outline icon in `muted`; `⋮` for row and section menus | call, kebab |
| Accent tint | `accent` text and border on a 10% `accent` fill, leading icon | Nâng cấp (upgrade) |

All: gap 8, `white-space: nowrap`, press `scale(.97)` (`.98` for sm), 150 ms colour
transition, disabled at 50% opacity with `not-allowed` cursor, focus = 2 px `accent` ring.

## Tabs and segmented controls

- **Pill tabs (saved views):** h32 px12 py6 r10, 14/400, gap 8. Active: bg `primary`, text
  `primary-foreground`. Idle: bg `surface-quaternary`, text `primary`. The "create view"
  item is an outline button with `muted` text.
- **Underline tabs (inside a page):** 14 px, padding 10 top / 14 bottom, 16 px apart; active
  is `primary` text with a 2 px underline, idle is `muted`.
- **Segmented control:** group r12 h32 on `surface-quaternary`; active segment `surface`
  with a soft shadow.

## Fields

- **Input:** h36, r12, px16, 1 px `border`, bg `field-background`, text 14 `field-foreground`,
  placeholder `muted` ("Nhập giá trị"). Focus: 2 px `accent` ring, border unchanged.
- **Adornments:** country code prefix (`VN`), `@` for email, calendar icon at the end for
  dates (placeholder `dd / MM / yyyy`), search icon at 12 px from the left with `pl-10`.
- **Select:** as input, placeholder "Chọn", chevron at the end.
- **Label:** above the field, 14/400 `primary`, 6-8 px gap; required marker `*` in `danger`.
- **Search field:** pill (`rounded-full`), h32, 1 px `border`, bg `surface`, width animates
  300 ms on focus.
- **Filter chip (dashboard):** bg `surface-quaternary`, r10, px8 py6, 14 px, chevron;
  clearable with a small `×` when it has a value ("Thời gian: 30 ngày trước ×").

## Badges, chips, avatars

- **Count badge:** r6, min-h20, px6, bg `border` colour, 12-14 px ("1", "1 đang mở").
- **Status chip:** 8 px dot + label, `surface-quaternary`, rounded-full ("● Hoạt động").
  Dots: `success` active · grey inactive · `danger` at risk · `warning` lost.
- **Stage chip:** outline, dot, stage name and age in `muted` ("● Đăng ký 14 ngày ⌄").
- **Overdue badge:** bg `danger`, white, r6, 12 px ("-14d").
- **Tag:** HeroUI chip: r16, px8 py2, 12/500, bg `default`.
- **Avatar:** circle; initial on a colour fill with an online dot (`success`, 2 px `surface`
  ring); 20 px mini avatar next to owner names.

## Table

- Container: inside the page panel, top border `border`, horizontal scroll with fading
  scrollbar (thumb visible only while scrolling).
- Header: h36, 12/500 `muted`, inset bottom border, sort icon `⇅` on sortable columns,
  checkbox column 44 px, index column.
- Row: ~64 px, 14/400, bottom border `border`, hover `surface-quaternary`, whole row
  clickable to the detail page. Primary cell = avatar + name (14/600) over a 12 px `muted`
  sub-line (phone). Long text truncates with an ellipsis.
- Sticky last column with row actions (call icon, kebab); its background follows
  `sticky-cell-bg`.
- Footer: page buttons 32x32 r10 (active = `primary` fill), right side "1-15 trên tổng N dữ
  liệu" in `muted` and a page-size select "15 dòng mỗi trang".

## Cards

- **KPI tile:** r20, p16, 1 px `border`, `shadow-soft`, hover `surface-quaternary`, gap 4.
  Label 16/400 · value 22/28 bold · hint 12 `muted` with a comparison or denominator.
- **Section card:** r20, p24, gap 24, 1 px `border`, `shadow-soft`. Title 16/600 with an
  optional 12 px `muted` subtitle; actions (segmented control, menu) right-aligned.
- **Collapsible section (detail rail):** chevron, title 14/600 underlined on hover, count
  badge, `+` and kebab on the right; empty body shows "Chưa có dữ liệu" 12 `muted`.
- **Item card inside a section:** r12, 1 px `border`, p12; first line overdue badge + time,
  then title, then status with a dot.

## Overlays

- **Modal:** width 468 (md), r24, 1 px `border`, overlay shadow, scrim black 50% (dark: the
  `backdrop` token), max-height 85vh with the body scrolling inside. Header: title 16/600 and
  a round 28 px close button. Footer: optional left action (disclosure), right-aligned
  tertiary Close + solid Create.
- **Drawer:** `overlay` background, p24, slides 250 ms in / 200 ms out on
  `cubic-bezier(.32,.72,0,1)`.
- **Popover / dropdown:** `overlay` background, r12, overlay shadow, items h32 px12.
- **Toast:** top-right, r12, px16 py12, overlay shadow; leading status icon, title 14/500,
  description 14 `muted` (for errors: the machine code), action button such as
  "Sao chép mã lỗi" (copy error code).

## States

- **Skeleton:** `surface-tertiary` at 70%, shimmer animation, the shape of the final content.
- **Empty:** one short sentence in place of the content, 12-14 px `muted`; offer the create
  action when the user can fix the emptiness.
- **Error:** inline message with a retry for a block; toast with a copyable code for an
  action; the rest of the page keeps working.
- **Disabled:** 50% opacity, `not-allowed` cursor, no hover change.
