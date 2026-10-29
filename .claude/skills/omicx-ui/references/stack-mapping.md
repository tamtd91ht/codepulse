# OMICX UI - expressing the tokens in your stack

The source of truth is [tokens.css](tokens.css). Pick the section for the library the
project already uses; never add a second UI library to get this look. Keep semantic names
(`primary`, `success`, `muted`, `surface-quaternary`…) so components never hold a hex value.

## Tailwind CSS v4

Import `tokens.css` once (for example from `app/globals.css` after `@import "tailwindcss";`).
Its `@theme inline` block gives `bg-primary`, `text-secondary` (muted), `border-tertiary`,
`bg-quaternary`, `rounded-medium`, `rounded-large`… Toggle themes with the `dark` class on
`<html>` (`@custom-variant dark (&:where(.dark, .dark *));`).

## Tailwind CSS v3

Keep the CSS variables from `tokens.css` (drop the `@theme` block) and map them:

```js
// tailwind.config.js
module.exports = {
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        primary: "var(--primary)",
        accent: "var(--accent)",
        success: "var(--success)",
        danger: "var(--danger)",
        warning: "var(--warning)",
        secondary: "var(--muted)",
        tertiary: "var(--border)",
        quaternary: "var(--surface-quaternary)",
        background: "var(--background)",
        surface: "var(--surface)",
      },
      borderRadius: { tiny: "6px", small: "8px", medium: "10px", large: "12px", extra: "16px" },
      boxShadow: { soft: "var(--shadow-soft)", common: "var(--shadow-common)" },
      fontFamily: { sans: ["Inter", "Geist", "ui-sans-serif", "system-ui", "sans-serif"] },
    },
  },
};
```

## HeroUI / NextUI, shadcn/ui

Both read CSS variables. Keep `tokens.css` as the theme file and point the library's
variables at it (shadcn: `--primary`, `--background`, `--border`, `--ring: var(--accent)`,
`--radius: 12px`). HeroUI v3 already uses the same names; only the brand layer is needed.

## MUI

```ts
const light = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#1e3150", contrastText: "#fff" },
    info: { main: "#00b1ff" },
    success: { main: "#56cc6e", contrastText: "#fff" },
    error: { main: "#ff5955" },
    warning: { main: "#fea220" },
    background: { default: "#f8f8f9", paper: "#ffffff" },
    text: { primary: "#1e3150", secondary: "#8e9aad" },
    divider: "#e7eaf0",
  },
  shape: { borderRadius: 12 },
  typography: { fontFamily: "Inter, Geist, system-ui, sans-serif", fontSize: 14 },
});
// dark: primary.main #fff (contrastText #1e3150), background #0c1c35 / #16243c, divider #253a55
```

Give the create button `color="success" variant="contained"`, everything else `outlined` or
`text`.

## Ant Design

```ts
<ConfigProvider theme={{
  token: {
    colorPrimary: "#1e3150", colorInfo: "#00b1ff", colorSuccess: "#56cc6e",
    colorError: "#ff5955", colorWarning: "#fea220", colorText: "#1e3150",
    colorTextSecondary: "#8e9aad", colorBorder: "#e7eaf0", colorBgLayout: "#f8f8f9",
    borderRadius: 10, borderRadiusLG: 20, controlHeight: 36, controlHeightSM: 32,
    fontFamily: "Inter, Geist, system-ui, sans-serif", fontSize: 14,
  },
}}>
```

Use `theme.darkAlgorithm` plus the dark values from `tokens.css` for dark mode.

## Vuetify

Define `light` and `dark` themes with the same keys (`primary`, `info` = accent, `success`,
`error`, `warning`, `background`, `surface`) and set `defaults` for `VBtn`
(`rounded: "lg"`, `height: 36`) and `VTextField` (`variant: "outlined"`, `density:
"compact"`).

## React Native

```ts
export const colors = {
  light: { primary: "#1e3150", onPrimary: "#fff", accent: "#00b1ff", success: "#56cc6e",
    danger: "#ff5955", warning: "#fea220", background: "#f8f8f9", surface: "#ffffff",
    surfaceQuaternary: "#f5f6fa", muted: "#8e9aad", border: "#e7eaf0" },
  dark: { primary: "#ffffff", onPrimary: "#1e3150", accent: "#00b1ff", success: "#56cc6e",
    danger: "#ff5955", warning: "#fea220", background: "#0c1c35", surface: "#16243c",
    surfaceQuaternary: "#152942", muted: "#8e9aad", border: "#253a55" },
};
export const radius = { tiny: 6, small: 8, medium: 10, large: 12, extra: 16, card: 20, dialog: 24 };
```

Read the scheme with `useColorScheme()`; components take colours from this file only. Touch
targets stay at least 44 pt even where the web control is 32 px.

## Flutter

Build a `ColorScheme` per brightness (`primary`, `onPrimary`, `secondary` = accent,
`surface`, `error`, `outline` = border) plus a `ThemeExtension` for `success`, `warning`,
`muted` and `surfaceQuaternary`; set `FilledButton` to the success colour for create
actions, `InputDecorationTheme` with radius 12 and the accent focused border. Minimum touch
target 48 dp.
