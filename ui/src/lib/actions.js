// Actions communes aux écrans : citation, export, Zotero, paquets .biblio.
import { get } from "svelte/store";
import { api, identifierOf } from "./api.js";
import { t } from "./i18n.js";
import { toast } from "./toast.js";

export const EXPORTS = [
  ["bibtex", "BibTeX (.bib)", "bib"],
  ["ris", "RIS (.ris)", "ris"],
  ["csljson", "CSL-JSON (.json)", "json"],
  ["markdown", "Markdown (.md)", "md"],
];

export function save(blob, filename) {
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

// Ajoute une notice trouvée en ligne (par identifiant, ou saisie manuelle sans identifiant). Renvoie sa clé.
export async function addRecord(rec, question = null, scope = "online") {
  const id = identifierOf(rec);
  const out = id
    ? await api("/works", { method: "POST", body: { identifier: id, question, scope } })
    : await api("/works/manual", {
        method: "POST",
        body: {
          title: rec.title, year: rec.year, container_title: rec.container_title, abstract: rec.abstract, question,
          authors: (rec.authors ?? []).map((a) => [a.family, a.given].filter(Boolean).join(", ")),
        },
      });
  const warn = out.warnings?.length ? ` (${out.warnings.join(" ; ")})` : "";
  toast(`${get(t)("ask.added")} ${out.work.citekey}${warn}`, warn ? "warn" : "ok");
  return out.work.citekey;
}

export function copy(text) {
  navigator.clipboard.writeText(text).then(() => toast(get(t)("detail.copied"), "ok", 2000));
}

// Fichier renvoyé par l'API (GET ou POST sans corps) enregistré dans Téléchargements.
export async function download(path, filename, method = "GET") {
  try {
    save(await api(path, { method }), filename);
  } catch (e) {
    toast(e.message, "error", 8000);
  }
}

// Glisser depuis Zotero : des articles (texte de sa copie rapide), ou une collection (Zotero ne transmet alors que
// son numéro interne : Biblio importe la collection sélectionnée dans Zotero). `label` : collection de Biblio.
export function isFromOutside(dataTransfer) {
  return !dataTransfer?.types.includes("text/x-biblio-works");
}

export async function importFromZotero(dataTransfer, label = null) {
  const text = dataTransfer.getData("text/plain"); // à lire avant tout await : le contenu disparaît ensuite
  const tr = get(t);
  const body = text.trim() ? { text, label } : { collection: "selected", label };
  const busy = toast(tr("zotero.importing"), "info", 600_000);
  try {
    const out = await api("/zotero/import", { method: "POST", body });
    toast(tr("zotero.imported", { created: out.created.length, completed: out.completed.length, pdfs: out.pdfs }), "ok", 8000);
    if (out.unmatched) toast(tr("zotero.unmatched", { n: out.unmatched }), "warn", 10_000);
    for (const err of out.errors ?? []) toast(`${err.title} : ${err.error}`, "error", 12_000);
    if (out.via === "online") toast(tr("zotero.enable_api"), "info", 12_000);
    return out;
  } catch (e) {
    toast(e.message, "error", 12_000);
    return null;
  } finally {
    busy();
  }
}
