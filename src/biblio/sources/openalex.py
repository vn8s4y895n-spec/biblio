"""OpenAlex : index ouvert de la littérature, nombre de citations, accès libre (https://openalex.org)."""

from . import SEARCH_TTL, Query, http

NAME, LABEL = "openalex", "OpenAlex"
API = "https://api.openalex.org/works"
CSL_TYPES = {"article": "article-journal", "review": "article-journal", "preprint": "article", "book-chapter": "chapter",
             "book": "book", "dataset": "dataset", "dissertation": "thesis"}  # fmt: skip


def search(q: Query) -> list[dict]:
    params = {"search": q.boolean, "per-page": q.limit}
    if years := q.years:
        params["filter"] = f"from_publication_date:{years[0]}-01-01,to_publication_date:{years[1]}-12-31"
    if key := http.api_keys.get("openalex"):
        params["api_key"] = key
    elif http.contact_email:
        params["mailto"] = http.contact_email
    response = http.get(API, params, ttl=SEARCH_TTL)
    return [parse(w) for w in response.json()["results"] if w.get("title")] if response else []


def find_pdf(doi: str) -> dict | None:
    """Meilleure version libre connue d'OpenAlex (qui intègre les données d'Unpaywall), sans e-mail requis."""
    params = {"api_key": key} if (key := http.api_keys.get("openalex")) else {}
    response = http.get(f"{API}/doi:{doi}", params, ttl=7 * 24 * 3600)
    work = response.json() if response else {}
    # La « meilleure » version libre n'a pas toujours de lien PDF : les autres versions connues (dépôts) en ont parfois.
    locations = [work.get("best_oa_location") or {}] + [loc for loc in work.get("locations") or [] if loc.get("is_oa")]
    best = next((loc for loc in locations if loc.get("pdf_url")), None)
    if best is None:
        return None
    return {"pdf_url": best["pdf_url"], "landing_url": best.get("landing_page_url"), "license": best.get("license"),
            "version": best.get("version"), "host_type": None, "source": "openalex"}  # fmt: skip


def _abstract(index: dict | None) -> str | None:
    """OpenAlex fournit le résumé en index inversé {mot: [positions]}."""
    if not index:
        return None
    words = sorted((pos, word) for word, positions in index.items() for pos in positions)
    return " ".join(word for _, word in words)


def _strip(url: str | None, prefix: str) -> str | None:
    return url.rsplit(prefix, 1)[-1].strip("/") if url else None


def parse(w: dict) -> dict:
    location = w.get("primary_location") or {}
    source = location.get("source") or {}
    issns = [source["issn_l"]] if source.get("issn_l") else []
    issns += [i for i in source.get("issn") or [] if i not in issns]
    ids = w.get("ids") or {}
    identifiers = {"openalex": _strip(w["id"], "/")}
    if w.get("doi"):
        identifiers["doi"] = _strip(w["doi"], "doi.org/").lower()
    if ids.get("pmid"):
        identifiers["pmid"] = _strip(ids["pmid"], "/")
    if ids.get("pmcid"):
        pmcid = _strip(ids["pmcid"], "/").upper()
        identifiers["pmcid"] = pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"
    biblio = w.get("biblio") or {}
    pages = "-".join(dict.fromkeys(p for p in (biblio.get("first_page"), biblio.get("last_page")) if p))
    best = w.get("best_oa_location") or {}
    authors = []
    for a in w.get("authorships", []):
        author = a.get("author") or {}
        if author.get("display_name"):
            authors.append({**http.split_name(author["display_name"]), "orcid": _strip(author.get("orcid"), "orcid.org/")})
    subjects = [
        {"scheme": "mesh", "term": m["descriptor_name"], "ui": m.get("descriptor_ui"), "major": bool(m.get("is_major_topic"))}
        for m in w.get("mesh") or []
    ]
    subjects = list({(s["term"]): s for s in subjects}.values())  # OpenAlex répète un descripteur par qualificatif
    return {
        "title": http.clean(w["title"]),
        "abstract": _abstract(w.get("abstract_inverted_index")),
        "csl_type": CSL_TYPES.get(w.get("type"), "article"),
        "study_type": "review" if w.get("type") == "review" else None,
        "container_title": source.get("display_name"),
        "container_abbrev": None,
        "issn_print": issns[0] if issns else None,
        "issn_e": issns[1] if len(issns) > 1 else None,
        "nlmid": None,
        "year": w.get("publication_year"),
        "issued": w.get("publication_date"),
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "pages": pages or None,
        "language": w.get("language"),
        "license": best.get("license"),
        "authors": authors,
        "identifiers": identifiers,
        "subjects": subjects,
        "is_oa": bool((w.get("open_access") or {}).get("is_oa")),
        "oa_pdf_url": best.get("pdf_url"),
        "cited_by_count": w.get("cited_by_count"),
    }
