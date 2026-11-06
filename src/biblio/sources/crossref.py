"""Crossref : métadonnées éditeur par DOI (https://api.crossref.org)."""

from urllib.parse import quote

from . import http

API = "https://api.crossref.org/works/"

CSL_TYPES = {
    "journal-article": "article-journal",
    "posted-content": "article",
    "book": "book",
    "book-chapter": "chapter",
    "proceedings-article": "paper-conference",
    "report": "report",
    "dataset": "dataset",
}


def fetch(doi: str) -> dict | None:
    response = http.get(API + quote(doi, safe="/"))
    return parse(response.json()["message"]) if response else None


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
        "csl_type": CSL_TYPES.get(m.get("type"), "article"),
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
    }
