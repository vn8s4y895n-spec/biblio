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


NOTICES_BATCH = 40  # DOI par requête (filtre « doi:… » répété)
NOTICES_TTL = 24 * 3600


def _date(parts: dict | None) -> str | None:
    values = ((parts or {}).get("date-parts") or [[None]])[0]
    return "-".join(f"{int(v):02d}" for v in values if v) or None


def notices(dois: list[str]) -> dict[str, dict]:
    """Pour chaque DOI : les avis publiés à son sujet (`updates` : rétractation, correction, expression de
    préoccupation… ; Crossref intègre les données de Retraction Watch) et, pour un preprint, le DOI de sa version
    publiée (`published`). Les DOI sont interrogés par lots."""
    out: dict[str, dict] = {}
    for i in range(0, len(dois), NOTICES_BATCH):
        chunk = dois[i : i + NOTICES_BATCH]
        response = http.get(API.rstrip("/"), {"filter": ",".join(f"doi:{d}" for d in chunk), "rows": len(chunk)}, ttl=NOTICES_TTL)
        for item in response.json()["message"]["items"] if response else []:
            updates = {}
            for u in item.get("updated-by") or []:
                key = (u.get("type") or "update", (u.get("DOI") or "").lower())
                updates.setdefault(key, {"kind": key[0], "doi": key[1], "date": _date(u.get("updated")), "source": u.get("source")})
            published = [r["id"].lower() for r in (item.get("relation") or {}).get("is-preprint-of", []) if r.get("id-type") == "doi"]
            out[item["DOI"].lower()] = {"updates": list(updates.values()), "published": published}
    return out


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
