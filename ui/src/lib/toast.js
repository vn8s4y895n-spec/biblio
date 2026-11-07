import { writable } from "svelte/store";

export const toasts = writable([]);
let next = 0;

export function toast(text, kind = "info", ms = 4500) {
  const id = next++;
  toasts.update((list) => [...list, { id, text, kind }]);
  setTimeout(() => toasts.update((list) => list.filter((x) => x.id !== id)), ms);
}
