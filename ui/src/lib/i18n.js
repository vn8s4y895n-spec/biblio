// Traductions : locales/fr.json et en.json (mêmes clés, vérifié par tests/test_organize.py).
// Usage : $t("ask.title"), $t("library.count", { n: 3 }) ; « {n} » est remplacé.
import { derived, writable } from "svelte/store";
import en from "../locales/en.json";
import fr from "../locales/fr.json";

const packs = { fr, en };
export const systemLang = navigator.language?.toLowerCase().startsWith("fr") ? "fr" : "en";
export const lang = writable(systemLang);

export const t = derived(lang, ($lang) => (key, vars = {}, fallback = key) => {
  const text = key.split(".").reduce((o, k) => o?.[k], packs[$lang]) ?? fallback;
  return text.replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? "");
});

// Dates et nombres dans la langue choisie.
export const fmt = derived(lang, ($lang) => ({
  date: (iso) => (iso ? new Date(iso).toLocaleDateString($lang, { day: "numeric", month: "long", year: "numeric" }) : ""),
  number: (n) => new Intl.NumberFormat($lang).format(n),
}));
