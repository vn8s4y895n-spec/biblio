import { writable } from "svelte/store";

export const toasts = writable([]);
let next = 0;

// Renvoie une fonction qui retire le message avant son délai (message « en cours… »).
export function toast(text, kind = "info", ms = 4500) {
  const id = next++;
  const dismiss = () => toasts.update((list) => list.filter((x) => x.id !== id));
  toasts.update((list) => [...list, { id, text, kind }]);
  setTimeout(dismiss, ms);
  return dismiss;
}
