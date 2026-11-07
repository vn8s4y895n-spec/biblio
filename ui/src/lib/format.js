// Mise en forme courte des notices (liste, cartes de sources).

export function authorsShort(rec, max = 3) {
  const names = (rec.authors ?? []).map((a) => {
    const initials = (a.given ?? "").split(/[\s-]+/).filter(Boolean).map((p) => p[0]).join("");
    return `${a.family}${initials ? " " + initials : ""}`;
  });
  if (!names.length) return rec.first_author ?? "";
  return names.slice(0, max).join(", ") + (names.length > max ? ", et al." : "");
}

export function sourceLine(rec) {
  const journal = rec.container_abbrev || rec.container_title || "";
  return [journal, rec.year].filter(Boolean).join(" · ");
}

export function doiUrl(rec) {
  const doi = rec.identifiers?.doi ?? rec.doi;
  return doi ? `https://doi.org/${doi}` : rec.identifiers?.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${rec.identifiers.pmid}/` : null;
}

// Découpe `text` autour de `part` pour la surligner sans HTML : [avant, partie, après] ou null.
export function splitAround(text, part) {
  const i = part ? (text ?? "").indexOf(part) : -1;
  return i < 0 ? null : [text.slice(0, i), part, text.slice(i + part.length)];
}
