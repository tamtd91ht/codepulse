# OMICX UI kit (reference components)

Small React + TypeScript components that implement the specs in
[../components.md](../components.md) and the layouts in
[../screen-patterns.md](../screen-patterns.md). They are **reference code to copy and
adapt**, not a package: no dependency beyond React 18+, Tailwind CSS v4 and
[../tokens.css](../tokens.css).

## How to use

1. Check what the project already has. If it uses a component library (HeroUI, MUI, Ant
   Design, shadcn…), build on that library and use these files only as the spec of the
   result: same variants, sizes, states and order. Never install a second library.
2. Otherwise copy the files you need into the project's component folder (for example
   `src/components/ui/`), keep `cx.ts` with them, and import `tokens.css` once.
3. Adapt names, copy and icons to the project; keep the token classes. Every colour, radius
   and shadow below is a token utility; do not replace them with raw values.

| File | Contents |
|---|---|
| `cx.ts` | class-name joiner used by every file |
| `Button.tsx` | `Button` (create, outline, outline-success, tertiary, ghost, danger; sm / md), `IconButton` |
| `AppShell.tsx` | top bar, module rail, grouped sidebar, content panel with breadcrumb |
| `ListPage.tsx` | `SavedViewTabs`, `ListToolbar`, `DataTable`, `Pagination` |
| `Dashboard.tsx` | `PageHeader`, `FilterChip`, `KpiTile`, `SectionCard`, `SegmentedControl`, `LegendRow` |
| `QuickCreateModal.tsx` | native `<dialog>` modal (focus trap, Escape, backdrop) and `Field` |
| `DetailLayout.tsx` | detail header, three-column layout, `CollapsibleSection`, `KeyValue` |
| `States.tsx` | `Skeleton`, `EmptyState`, `ErrorNotice`, `ErrorToast`, `StatusChip`, `CountBadge`, `OverdueBadge` |

Icons are passed in as `ReactNode` so the project keeps its own icon set (20 px outline,
`text-secondary`). Copy (labels) defaults to Vietnamese because the reference product is
Vietnamese; pass props to change it.
