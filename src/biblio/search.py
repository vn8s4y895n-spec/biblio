"""Recherche en ligne fédérée : interroge les sources en parallèle, fusionne les doublons, filtre et classe."""

import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import core, journals
from .sources import Query, crossref, europepmc, http, openalex, pubmed, semanticscholar, unpaywall

log = logging.getLogger("biblio")

# Ordre de priorité à la fusion : la première source qui fournit un champ l'emporte (PubMed : MeSH, résumé structuré).
SOURCES = {m.NAME: m for m in (pubmed, europepmc, openalex, crossref, semanticscholar)}
RRF_K = 60  # constante de la fusion par rangs réciproques (valeur usuelle)


@dataclass
class Filters:
    year_from: int | None = None
    year_to: int | None = None
    study_types: list[str] = field(default_factory=list)
    open_access: bool = False
    mesh: list[str] = field(default_factory=list)
    journal: str | None = None
    # Par défaut, seules les revues de la liste des revues recommandées sont montrées.
    journal_status: list[str] = field(default_factory=lambda: ["recommended"])


def _run(name: str, query: Query) -> tuple[str, list[dict], dict]:
    start = time.perf_counter()
    try:
        records = SOURCES[name].search(query)
        status = {"status": "ok", "count": len(records)}
    except Exception as exc:  # une source défaillante (réseau, format inattendu) ne bloque pas les autres
        if not isinstance(exc, http.SourceError):
            log.exception("Source %s : réponse inattendue", name)
        records, status = [], {"status": "error", "count": 0, "error": str(exc)}
    status["ms"] = round((time.perf_counter() - start) * 1000)
    return name, records, status


def _keys(rec: dict) -> list[tuple]:
    """Clés de dédoublonnage : identifiants (DOI > PMID > PMCID), puis titre normalisé + année.
    Le premier auteur n'entre pas dans la clé : certaines sources ne séparent pas nom et prénom."""
    keys = [(scheme, value) for scheme, value in rec["identifiers"].items() if scheme in ("doi", "pmid", "pmcid")]
    title = core.normalize(rec.get("title"))
    if len(title.split()) >= 5 and rec.get("year"):  # un titre trop court (« Editorial ») ne suffit pas
        keys.append(("title", title, rec["year"]))
    return keys


def merge_results(by_source: dict[str, list[dict]]) -> list[dict]:
    """Regroupe les notices qui désignent le même article (union par clés communes) et les fusionne."""
    entries = [(name, rank, rec) for name in SOURCES for rank, rec in enumerate(by_source.get(name, []), 1)]
    parent = list(range(len(entries)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[tuple, int] = {}
    for i, (_, _, rec) in enumerate(entries):
        for key in _keys(rec):
            if key in owner:
                parent[root(i)] = root(owner[key])
            else:
                owner[key] = i
    groups: dict[int, list[int]] = {}
    for i in range(len(entries)):
        groups.setdefault(root(i), []).append(i)

    merged = []
    for members in groups.values():
        rec, ranks = None, {}
        for i in members:  # `entries` suit l'ordre de priorité des sources
            name, rank, other = entries[i]
            rec = other if rec is None else core.merge(rec, other)
            ranks[name] = min(rank, ranks.get(name, rank))
        merged.append({**rec, "sources": ranks, "score": round(sum(1 / (RRF_K + r) for r in ranks.values()), 6)})
    merged.sort(key=lambda r: (-r["score"], -len(r["sources"]), -(r.get("year") or 0)))
    return merged


def _matches(rec: dict, f: Filters) -> bool:
    year = rec.get("year")
    if (f.year_from and (not year or year < f.year_from)) or (f.year_to and (not year or year > f.year_to)):
        return False
    if f.study_types and rec.get("study_type") not in f.study_types:
        return False
    if f.open_access and not (rec.get("is_oa") or rec.get("oa_pdf_url")):
        return False
    if f.mesh:
        terms = {s["term"].casefold() for s in rec["subjects"] if s["scheme"] == "mesh"}
        if not all(m.casefold() in terms for m in f.mesh):
            return False
    if f.journal:
        wanted = f.journal.casefold()
        listed = rec["journal_status"]["journal"] or {}  # la liste connaît les deux ISSN, la source parfois un seul
        names = [rec.get("container_title"), rec.get("container_abbrev"), rec.get("issn_print"), rec.get("issn_e"),
                 listed.get("name"), listed.get("abbrev"), listed.get("issn_print"), listed.get("issn_e")]  # fmt: skip
        if not any(n and wanted in n.casefold() for n in names):
            return False
    return True


def search(conn: sqlite3.Connection, text: str, sources: list[str] | None = None, per_source: int = 25,
           limit: int = 50, filters: Filters | None = None) -> dict:  # fmt: skip
    """Recherche fédérée. Les filtres de date et de MeSH sont transmis aux sources qui les comprennent,
    puis tous les filtres sont réappliqués sur les notices fusionnées."""
    filters = filters or Filters()
    names = [n for n in SOURCES if n in (sources or SOURCES)]
    if unknown := set(sources or ()) - set(SOURCES):
        raise ValueError(f"Source inconnue : {', '.join(sorted(unknown))}")
    query = Query(text, per_source, filters.year_from, filters.year_to, tuple(filters.mesh))

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        runs = list(pool.map(lambda n: _run(n, query), names))
    by_source = {name: records for name, records, _ in runs}
    statuses = {name: {"label": SOURCES[name].LABEL, **status} for name, _, status in runs}

    results, hidden = [], {}
    for rec in merge_results(by_source):
        rec["journal_status"] = journals.lookup(
            conn, issn=rec.get("issn_print"), issn_e=rec.get("issn_e"), nlmid=rec.get("nlmid"),
            abbrev=rec.get("container_abbrev"), csl_type=rec.get("csl_type") or "article-journal",
        )  # fmt: skip
        if not _matches(rec, filters):
            continue
        status = rec["journal_status"]["status"]
        if filters.journal_status and status not in filters.journal_status:
            hidden[status] = hidden.get(status, 0) + 1
            continue
        work_id = core.find_by_identifiers(conn, rec["identifiers"])
        rec["in_library"] = conn.execute("SELECT citekey FROM works WHERE id = ?", (work_id,)).fetchone()[0] if work_id else None
        results.append(rec)

    return {
        "query": {"pubmed": text, "boolean": query.boolean, "plain": query.plain},
        "sources": statuses,
        "total": len(results),
        "hidden": hidden,
        "results": results[:limit],
    }


def find_open_access(doi: str | None = None, pmcid: str | None = None) -> dict | None:
    """Version libre et légale d'un article : PMC (via Europe PMC), sinon Unpaywall."""
    if pmcid:
        pmcid = pmcid.upper()
        return {"pdf_url": f"https://europepmc.org/articles/{pmcid}?pdf=render",
                "landing_url": f"https://europepmc.org/article/PMC/{pmcid}", "license": None,
                "version": "publishedVersion", "host_type": "repository", "source": "pmc"}  # fmt: skip
    if doi:
        return unpaywall.find(doi)
    raise ValueError("Indiquer un DOI ou un PMCID")
