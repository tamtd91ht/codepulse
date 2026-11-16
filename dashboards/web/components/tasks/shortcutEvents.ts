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

/** A modal dialog or the palette is open (the task drawer is not one: shortcuts work in it). */
export function dialogOpen(): boolean {
  return !!document.querySelector(".modal-backdrop, .cmdk-backdrop");
}
