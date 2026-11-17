/*
 * Window events between the visible triggers (top bar buttons), the keyboard shortcuts and the
 * command palette / shortcut help (docs/specs/31 §20.2). Events, not a context, so any component can
 * open them without being under a provider.
 */

export const PALETTE_EVENT = "cp:palette-open";
export const HELP_EVENT = "cp:shortcut-help";

/** "search": the palette lists tasks only (the `/` shortcut); "all": tasks and commands. */
export type PaletteMode = "all" | "search";

export function openPalette(mode: PaletteMode = "all") {
  window.dispatchEvent(new CustomEvent<{ mode: PaletteMode }>(PALETTE_EVENT, { detail: { mode } }));
}

export function toggleShortcutHelp() {
  window.dispatchEvent(new Event(HELP_EVENT));
}

/** True while focus is in a field one types into: shortcuts stay out of the way. */
export function typingIn(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return !!target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']");
}

/**
 * Something floats over the page and owns the keys: a modal, the palette, a popover (filter bar,
 * multi-select, date range, the frame's menus), a menu or listbox, a trigger whose popup is open,
 * the bottom sheet. The task drawer (`aria-modal="false"`) is not one: shortcuts work in it.
 * A listbox always on the page (not a popup) carries `data-inline`. docs/specs/31 §22.10.
 */
const OVERLAY = [
  ".modal-backdrop",
  ".cmdk-backdrop",
  "[aria-modal='true']",
  "[role='dialog']:not([aria-modal='false'])",
  "[role='alertdialog']",
  "[role='menu']",
  "[role='listbox']:not([aria-hidden='true']):not([data-inline])",
  "[aria-haspopup]:not([aria-haspopup='false'])[aria-expanded='true']",
  ".msel-pop",
  ".fb-pop",
  ".fb-sheet",
  ".tf-pop",
  ".tl-add-field[open]",
].join(", ");

export function overlayOpen(): boolean {
  return typeof document !== "undefined" && !!document.querySelector(OVERLAY);
}

/** A modal, the palette or any popover / menu is open (see `overlayOpen`). */
export function dialogOpen(): boolean {
  return overlayOpen();
}

/**
 * The one guard of every single-key shortcut (C, /, F, V, 1–7, J/K, N, E, G, ? …): true when the
 * key must be left alone — Ctrl / Alt / Meta held (Shift is the caller's call), an IME composing,
 * already handled, focus in a field one types into, or an overlay open (`overlayOpen`).
 * `allowOverlay` lets "?" close its own help dialog.
 */
export function shortcutBlocked(e: KeyboardEvent, opts: { allowOverlay?: boolean } = {}): boolean {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return true;
  if (typingIn(e.target) || (typeof document !== "undefined" && typingIn(document.activeElement))) return true;
  return !opts.allowOverlay && overlayOpen();
}
