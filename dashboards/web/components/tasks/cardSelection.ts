"use client";

import { useSyncExternalStore } from "react";

import { dialogOpen, typingIn } from "./shortcutEvents";

/*
 * The selected Board card (docs/specs/31 §21.1): a click (or keyboard focus) on a card selects it,
 * `Esc` clears it. The command palette reads it as its context when no task is open. A module-level
 * store, not a context: the palette is mounted in the app shell, far from the Board.
 */

export const CARD_SELECT_EVENT = "cp:card-select";

let selected: string | null = null;
const listeners = new Set<() => void>();
let escInstalled = false;

function installEsc() {
  if (escInstalled || typeof window === "undefined") return;
  escInstalled = true;
  window.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !selected || e.defaultPrevented) return;
    if (typingIn(e.target) || dialogOpen()) return;
    selectCard(null);
  });
}

export function selectCard(key: string | null) {
  if (key === selected) return;
  selected = key;
  installEsc();
  for (const f of listeners) f();
  window.dispatchEvent(new CustomEvent<{ key: string | null }>(CARD_SELECT_EVENT, { detail: { key } }));
}

/** The selected card's task key, only while that card is still on screen. */
export function selectedCard(): string | null {
  if (!selected || typeof document === "undefined") return null;
  const on = Array.from(document.querySelectorAll<HTMLElement>("[data-card-key]")).some((el) => el.dataset.cardKey === selected);
  return on ? selected : null;
}

function subscribe(f: () => void) {
  listeners.add(f);
  return () => {
    listeners.delete(f);
  };
}

/** Whether this card is the selected one (re-renders only the cards whose answer changes). */
export function useCardSelected(key: string): boolean {
  return useSyncExternalStore(
    subscribe,
    () => selected === key,
    () => false,
  );
}
