---
name: omicx-ui
description: The house UI/UX style for web and mobile screens, taken from the OMICX web app - design tokens (navy ink, cyan focus, green create CTA, surfaces, radii, shadows, Inter type scale, light and dark), component specs and screen patterns (app shell, list page, dashboard, quick-create modal, detail page, feedback). Use when building, restyling or reviewing any user-facing screen, component, theme, Tailwind config or CSS, or when choosing colours, spacing, typography or layout.
---

# OMICX UI (house style for web and mobile screens)

Every screen this project ships should feel like the OMICX web app: calm navy-on-white
chrome, one muted grey for everything secondary, colour reserved for status and actions,
dense 14 px content softened by large card radii, and a first-class dark mode. This skill
is the source of truth for that look; `references/` holds the full detail.

## 0. Decide how it applies to this project

1. Look for an existing design system first: a tokens / theme file, `tailwind.config.*` or a
   Tailwind v4 `@theme`, a component library theme (HeroUI, MUI, Ant Design, shadcn, Vuetify,
   React Native / Flutter theme constants).
2. **No design system yet** (new project, new app, prototype): adopt
   [references/tokens.css](references/tokens.css) wholesale and copy the components you
   need from [references/kit/](references/kit/README.md) (React + Tailwind v4); in other
   stacks build them to [references/components.md](references/components.md).
3. **An established brand already exists:** keep its brand colours and fonts, and still
   apply the structure here - semantic roles, the radius and type scales, the component
   anatomy, the screen patterns and the UX rules. Map roles, do not paste hex values.
4. Never install a second component library to get this look; express it in the one the
   project already uses ([references/stack-mapping.md](references/stack-mapping.md)).

## 1. Principles

1. **Calm chrome, colour carries meaning.** Text is the ink colour, secondary text is muted
   grey, lines are hairlines. Colour appears only for status, actions and alerts.
2. **One ink colour that inverts.** `primary` is navy in light mode and white in dark mode.
   Active navigation, the active tab and the current page fill with it.
3. **Green means create.** The single solid CTA of a view (`Thêm mới`, submit) is `success`
   green; when the main action is not a create (upload, connect) it is a solid `primary`
   (ink) button instead. Cyan `accent` is for focus rings, links and upgrade prompts.
4. **Dense but breathable.** 14 px body, 32 px toolbar controls, 36 px form fields, ~64 px
   table rows; 20 px card and 24 px dialog radii soften the density.
5. **Progressive disclosure.** Quick actions show the minimum (required fields, three
   filters, collapsed sections) and reveal the rest on demand.
6. **Shadows in light, borders in dark.** Light cards float on `shadow-soft`; dark mode drops
   shadows and separates surfaces with borders and three surface steps.
7. **Short, physical motion.** 150 ms colour transitions, buttons press to `scale(.97)`,
   drawers slide 250 ms in / 200 ms out on `cubic-bezier(.32,.72,0,1)`; honour
   `prefers-reduced-motion`; hover styles only under `(hover: hover)`.

## 2. Core tokens (full set: references/tokens.css)

| Role | Light | Dark | Use |
|---|---|---|---|
| `primary` (ink) | `#1e3150` | `#ffffff` | text, headings, active nav / tab / page |
| `primary-foreground` | `#ffffff` | `#1e3150` | text on an active fill |
| `accent` | `#00b1ff` | `#00b1ff` | 2 px focus ring, links, info, upgrade |
| `success` | `#56cc6e` | `#56cc6e` | create / submit CTA, active status |
| `danger` | `#ff5955` | `#ff5955` | required `*`, overdue, destructive, error |
| `warning` | `#fea220` | `#fea220` | at risk, warnings |
| `indigo`, `yellow` | `#6c63ff`, `#fbbf24` | same | chart series, tags |
| `background` | `#f8f8f9` | `#0c1c35` | app canvas |
| `surface` / `overlay` | `#ffffff` | `#16243c` | panels, cards, tables, dialogs |
| `surface-quaternary` | `#f5f6fa` | `#152942` | idle tab, filter chip, hover |
| `muted` | `#8e9aad` | `#8e9aad` | secondary text, table header, icons |
| `border` | `#e7eaf0` | `#253a55` | hairlines, outline buttons, inputs |

Type (Inter; Geist as fallback; both cover Vietnamese): page title 22/28 bold · section and
dialog title 16/24 semibold · body 14/20 regular · strong 14/20 semibold · caption and table
header 12/16 (header weight 500) in `muted`.

Radius: 6 badge · 8 small · **10** small buttons, pill tabs, chips · **12** inputs, medium
buttons, toasts · 16 · **20** cards · **24** dialogs · full for search.
Spacing: 4 px grid; 8 / 12 / 16 / 24 are the working steps. Heights: top bar 56, toolbar
control 32, field 36, table header 36.

## 3. Screen patterns (detail: references/screen-patterns.md)

- **App shell:** 56 px top bar (logo, workspace switcher, balance, upgrade, theme toggle,
  notifications, settings, language, avatar) · 40 px module icon rail · 180 px contextual
  sidebar with grouped nav · content in a rounded `surface` panel with a breadcrumb.
- **List page:** saved-view pill tabs → toolbar `Title [count] ↻ · search · ≤3 filters ·
  more` … `view switch · layout · Import (outline) · Create (solid green)` → table → footer
  with pagination, range text and page size.
- **Dashboard:** 22 px title + one-line muted description → filter chips → equal KPI tiles
  (label, big value, context hint) → section cards (donut + legend with count and %, bar
  lists with a segmented dimension switch).
- **Quick-create modal:** 468 px, required fields only, autofocus the first field,
  "show full form" disclosure, footer `Close (tertiary)` + `Create (green)`.
- **Settings:** one centred 588 px column, sections of read-only cards each with its own
  "Chỉnh sửa"; **setup wizard:** numbered steps, later steps muted until answered, choice
  tiles that show live state.
- **Detail page:** header (avatar, name, status chip, stage with age, quick actions, owner,
  Edit) · three columns: key/value info · tabs with timeline and composer · related-object
  rail of collapsible sections with counts, `+` and a menu.

## 4. Rules that are checked in review

1. No raw colour literals in components: use a token (CSS variable, Tailwind theme colour,
   theme constant). Hex values live only in the token / theme file. Radii, spacing and font
   sizes use the scale utilities (`rounded-card`, `p-4`, `text-title`), not arbitrary
   `rounded-[20px]` / `p-[14px]` / `text-[22px]` values.
2. One solid CTA per view (`success` for create, `primary` otherwise); every other action is
   outline, tertiary or ghost.
3. Focus is always visible: 2 px `accent` ring; never `outline: none` without a replacement.
4. Every data-bound block has loading (skeleton), empty ("Chưa có dữ liệu" style, in place)
   and error states; errors show a copyable code in a toast, not only prose.
5. Time-sensitive items show relative age; overdue is `danger` (`-14d`, "Quá hạn").
6. KPI values always carry context: a comparison or a denominator ("1 / 1 khách hàng").
7. Dark mode works for every new component: check both themes before calling it done.
8. Copy is short, sentence case, verb-first; Vietnamese first when the product is Vietnamese
   (`Thêm mới`, `Tải lên`, `Đóng`, `Chỉnh sửa`); numbers `5.141`, dates `dd/MM/yyyy HH:mm`.
9. Icons: one outline set, 20 px, `muted` by default, `primary` when active; icon-only
   buttons have an accessible label.
10. Contrast: body text on `surface` and on-primary text meet WCAG AA; muted grey is for
    secondary text only, never for the only copy of an instruction.

## 5. Done checklist for a screen

- [ ] Uses tokens only; no new hex, rgb() or named colour in component code
- [ ] Matches the pattern for its screen type (§3) and the component specs
- [ ] One green CTA; other actions demoted
- [ ] Loading, empty and error states present
- [ ] Keyboard path and focus ring verified; dialogs trap and restore focus
- [ ] Light and dark both checked; reduced motion respected
- [ ] Copy follows rule 8; numbers and dates formatted

## References

- [references/tokens.css](references/tokens.css) - drop-in CSS variables for both themes
  and the Tailwind v4 `@theme` mapping. Read when creating or changing a theme.
- [references/components.md](references/components.md) - measured specs for buttons, tabs,
  inputs, chips, badges, tables, cards, modal, drawer, toast, empty and skeleton. Read when
  building or reviewing a component.
- [references/screen-patterns.md](references/screen-patterns.md) - app shell, list,
  dashboard, quick-create, detail and feedback layouts. Read when building a new screen.
- [references/kit/](references/kit/README.md) - reference React + TypeScript + Tailwind v4
  components (app shell, list toolbar and table, pagination, KPI tile, section card,
  segmented control, quick-create modal and fields, detail layout, states). Read before
  writing a component for a new screen; copy and adapt rather than rewrite.
- [references/stack-mapping.md](references/stack-mapping.md) - how to express the tokens in
  Tailwind v3, MUI, Ant Design, shadcn, Vuetify, React Native and Flutter. Read when the
  project does not use Tailwind v4.
