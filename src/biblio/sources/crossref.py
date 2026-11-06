"""Crossref : métadonnées éditeur par DOI (https://api.crossref.org)."""

from urllib.parse import quote

from . import SEARCH_TTL, Query, http

NAME, LABEL = "crossref", "Crossref"
API = "https://api.crossref.org/works/"

CSL_TYPES = {
    "journal-article": "article-journal",
    "posted-content": "manuscript",  # posters, présentations… ; seul le sous-type « preprint » est un preprint
    "dissertation": "thesis",
    "book": "book",
    "book-chapter": "chapter",
    "proceedings-article": "paper-conference",
    "report": "report",
    "dataset": "dataset",
}


def fetch(doi: str) -> dict | None:
    response = http.get(API + quote(doi, safe="/"))
    return parse(response.json()["message"]) if response else None


def search(q: Query) -> list[dict]:
    """Crossref ne comprend que le texte libre : la requête est simplifiée."""
    params = {"query.bibliographic": q.plain, "rows": q.limit}
    if years := q.years:
        params["filter"] = f"from-pub-date:{years[0]},until-pub-date:{years[1]}"
    response = http.get(API.rstrip("/"), params, ttl=SEARCH_TTL)
    return [parse(m) for m in response.json()["message"]["items"] if m.get("title")] if response else []


def _first(values: list | None) -> str | None:
    return values[0] if values else None


def parse(m: dict) -> dict:
    parts = (m.get("published") or m.get("issued") or {}).get("date-parts") or [[None]]
    parts = [p for p in parts[0] if p]
    issn = {i["type"]: i["value"] for i in m.get("issn-type", [])}
    licenses = m.get("license") or []
    return {
        "title": http.clean(_first(m.get("title"))),
        "abstract": http.clean(m.get("abstract")),
        "csl_type": "article" if m.get("subtype") == "preprint" else CSL_TYPES.get(m.get("type"), "document"),
        "container_title": _first(m.get("container-title")),
        "container_abbrev": _first(m.get("short-container-title")),
        "issn_print": issn.get("print"),
        "issn_e": issn.get("electronic"),
        "year": parts[0] if parts else None,
        "issued": "-".join(f"{p:02d}" for p in parts) or None,
        "volume": m.get("volume"),
        "issue": m.get("issue"),
        "pages": m.get("page"),
        "language": m.get("language"),
        "license": licenses[0].get("URL") if licenses else None,
        "authors": [
            {
                "family": a.get("family") or a.get("name"),
                "given": a.get("given"),
                "orcid": a["ORCID"].rstrip("/").rsplit("/", 1)[-1] if a.get("ORCID") else None,
            }
            for a in m.get("author", [])
            if a.get("family") or a.get("name")
        ],
        "identifiers": {"doi": m["DOI"].lower()},
        "subjects": [],
        "cited_by_count": m.get("is-referenced-by-count"),
    }
