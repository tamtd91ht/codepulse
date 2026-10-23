---
name: ui-ux-guidelines
description: UI/UX quality guidelines for web and mobile interfaces — accessibility, layout, typography, color, forms, navigation, feedback, motion, responsive behaviour and a pre-delivery checklist. Use when designing, building or reviewing user-facing UI, when a user says the interface "doesn't look professional", or before shipping a page or screen. Guidance only; it does not generate design systems or verify implementation quality.
---

# UI/UX Guidelines

Markdown-only guidance adapted from UI UX Pro Max (MIT). The upstream project also ships a
Python search engine and design-system generator over CSV data; those are **not** included
here. If you need palette/typography/style matching, install the full toolkit with
`npx ui-ux-pro-max-cli init --ai claude` (reviewed separately as a native capability).

## How to use

1. **Before building** a page or screen: skim the priority table below, then read
   `references/quick-reference.md` for the full rule set of the categories that apply.
2. **For native/mobile UI** (iOS, Android, React Native, Flutter): also read
   `references/pro-rules.md` and run its pre-delivery checklist before handing off.
3. **When reviewing** existing UI: audit against the CRITICAL categories first, then HIGH.
4. State clearly when a recommendation is a general fallback rather than a verified match
   for the product domain; do not present guidance as proof of implementation quality.

## Priority table

| Priority | Category | Non-negotiables |
|---|---|---|
| CRITICAL | Accessibility | 4.5:1 contrast, visible focus states, alt text, keyboard navigation, labelled form controls |
| CRITICAL | Touch & targets | 44x44pt minimum targets, safe-area awareness on mobile |
| HIGH | Layout & spacing | consistent spacing scale, clear hierarchy, alignment to a grid |
| HIGH | Typography | 1–2 families, modular scale, 45–75 characters per line, sufficient line height |
| HIGH | Forms | inline validation, clear error text, preserved input on error |
| HIGH | Feedback & state | loading, empty, error and success states for every async surface |
| MEDIUM | Color | one accent, semantic colors for status, dark-mode parity |
| MEDIUM | Navigation | obvious current location, back behaviour, max 5 primary destinations on mobile |
| MEDIUM | Motion | purposeful, 150–300 ms, respects reduced-motion preferences |
| MEDIUM | Responsive | content-first breakpoints, no horizontal scroll, fluid media |

## References

- `references/quick-reference.md` — all rules in 10 categories (stack-agnostic, web + desktop)
- `references/pro-rules.md` — native/mobile professional-polish rules and delivery checklist

Attribution: UI UX Pro Max, https://github.com/nextlevelbuilder/ui-ux-pro-max-skill (MIT,
Copyright (c) 2024 Next Level Builder). See LICENSE-UI-UX-PRO-MAX.txt.
