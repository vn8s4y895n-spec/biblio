// Actions communes aux écrans : citation, export, Zotero, paquets .biblio.
import { get } from "svelte/store";
import { api } from "./api.js";
import { t } from "./i18n.js";
import { toast } from "./toast.js";

export const EXPORTS = [
  ["bibtex", "BibTeX (.bib)", "bib"],
  ["ris", "RIS (.ris)", "ris"],
  ["csljson", "CSL-JSON (.json)", "json"],
  ["markdown", "Markdown (.md)", "md"],
];

function save(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// `selection` : { works: [...] } ou { label: id }.
export async function exportWorks(selection, format, name = "biblio") {
  try {
    const ext = EXPORTS.find(([f]) => f === format)[2];
    save(await api("/export", { method: "POST", body: { ...selection, format } }), `${name}.${ext}`);
  } catch (e) {
    toast(e.message, "error");
  }
}

export async function sendToZotero(selection) {
  try {
    const out = await api("/zotero", { method: "POST", body: selection });
    toast(get(t)("zotero.sent", { n: out.sent, pdfs: out.pdfs, where: out.collection ?? out.library }), "ok", 6000);
  } catch (e) {
    toast(e.message, "error", 8000);
  }
}

export async function makePackage(body, name = "articles") {
  try {
    save(await api("/packages", { method: "POST", body }), `${name.replace(/[^\p{L}\p{N} _-]+/gu, "").trim() || "articles"}.biblio`);
    toast(get(t)("package.made"), "ok");
  } catch (e) {
    toast(e.message, "error");
  }
}

// Référence formatée (style des réglages) : d'une notice de la bibliothèque, ou d'un résultat en ligne.
export async function copyCitation(target) {
  try {
    const out = typeof target === "string" || typeof target === "number"
      ? await api(`/works/${encodeURIComponent(target)}/citation`)
      : await api("/citation", { method: "POST", body: { record: target } });
    await navigator.clipboard.writeText(out.text);
    toast(get(t)("cite.copied"), "ok", 2500);
  } catch (e) {
    toast(e.message, "error");
  }
}
