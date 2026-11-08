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

// Supprime des notices après confirmation ; leur note et leurs PDF vont à la Corbeille. Renvoie true si supprimées.
export async function deleteWorks(works, title = null) {
  const n = works.length;
  if (!confirm(get(t)(n === 1 && title ? "delete.confirm_one" : "delete.confirm", { n, title }))) return false;
  try {
    const out = await api("/works/delete", { method: "POST", body: { works: works.map(String) } });
    toast(get(t)("delete.done", { n: out.deleted.length }), "ok");
    return true;
  } catch (e) {
    toast(e.message, "error");
    return false;
  }
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

// Export Zotero RDF (Zotero : Exporter… → Zotero RDF, avec les fichiers et les notes). Dans la fenêtre de Biblio, le
// sélecteur du Mac donne le chemin du .rdf : Biblio lit aussi les PDF du dossier de l'export. Dans un navigateur, le
// fichier seul est envoyé : notices, notes, tags et collections, sans les PDF.
// `label` : collection de Biblio sélectionnée, où ranger les articles.
export async function importZoteroRdf(label = null) {
  const tr = get(t);
  const native = window.pywebview?.api?.choose_file;
  let out;
  try {
    if (native) {
      const path = await native(tr("zotero.rdf_label"), "rdf");
      if (!path) return null;
      const busy = toast(tr("zotero.rdf_importing"), "info", 600_000);
      try {
        out = await api("/import/zotero-rdf", { method: "POST", body: { path, label } });
      } finally {
        busy();
      }
    } else {
      const file = await pickFile(".rdf");
      if (!file) return null;
      out = await api("/import", { method: "POST", body: file });
      toast(tr("zotero.rdf_browser"), "info", 10_000);
    }
  } catch (e) {
    toast(e.message, "error", 12_000);
    return null;
  }
  toast(tr("zotero.rdf_done", { created: out.created.length, completed: out.completed.length, pdfs: out.pdfs,
                               notes: out.notes, collections: out.collections }), "ok", 10_000);
  if (out.target) toast(tr("zotero.rdf_target", { name: out.target }), "ok", 10_000);
  if (native && out.missing_pdfs) toast(tr("zotero.rdf_missing", { n: out.missing_pdfs }), "warn", 12_000);
  for (const err of out.errors) toast(`${err.title} : ${err.error}`, "error", 12_000);
  return out;
}

function pickFile(accept) {
  return new Promise((resolve) => {
    const input = Object.assign(document.createElement("input"), { type: "file", accept });
    input.onchange = () => resolve(input.files[0] ?? null);
    input.click();
  });
}
