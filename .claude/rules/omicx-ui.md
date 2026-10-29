---
description: OMICX house UI style - applies to every UI file (components, styles, screens).
paths:
  - "**/*.tsx"
  - "**/*.jsx"
  - "**/*.vue"
  - "**/*.svelte"
  - "**/*.astro"
  - "**/*.html"
  - "**/*.css"
  - "**/*.scss"
  - "**/*.sass"
  - "**/*.less"
  - "**/*.dart"
---

# OMICX house UI style

Screens in this project follow the OMICX house style. Before creating or restyling UI, read
the `omicx-ui` skill (tokens, component specs, screen patterns) and start from its
`references/kit/` components instead of writing new ones from scratch.

**Precedence for look and layout** (palette, type, radius, density, list and dashboard
layouts): a design system already established in this repository wins; otherwise this rule
and `omicx-ui` win over generic aesthetic guidance (for example "anti-template" or "pick a
bold style direction" rules). Accessibility, security and performance rules still apply.

- Colours, radii, shadows and fonts come from tokens or scale utilities only; no raw
  `#hex` / `rgb()` / `hsl()`, no arbitrary `rounded-[..px]` / `p-[..px]` / `text-[..px]`.
- One solid action per view (green for create / submit, ink `primary` otherwise); every
  other action is outline, tertiary or ghost.
- Use the screen pattern for the type: list, dashboard, quick-create modal, detail.
- Every data block has a skeleton, an in-place empty text and an error with retry or a
  copyable code.
- Visible 2 px accent focus ring; icon-only buttons have a label.
- Check light and dark mode before calling the UI done.
