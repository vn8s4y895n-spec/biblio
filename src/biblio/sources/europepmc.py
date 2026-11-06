"""Europe PMC : PubMed + PMC + preprints, avec liens vers le texte intégral libre (https://europepmc.org)."""

from . import SEARCH_TTL, Query, http, pubmed

NAME, LABEL = "europepmc", "Europe PMC"
API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def search(q: Query) -> list[dict]:
    query = f"({q.boolean})"
    if years := q.years:
        query += f" AND PUB_YEAR:[{years[0]} TO {years[1]}]"
    for heading in q.mesh:
        query += f' AND MESH:"{heading}"'
    response = http.get(API, {"query": query, "format": "json", "resultType": "core", "pageSize": q.limit}, ttl=SEARCH_TTL)
    return [parse(r) for r in response.json()["resultList"]["result"] if r.get("title")] if response else []


def _oa_pdf(r: dict) -> str | None:
    for link in (r.get("fullTextUrlList") or {}).get("fullTextUrl", []):
        if link.get("documentStyle") == "pdf" and link.get("availabilityCode") in ("OA", "F"):
            return link["url"]
    return None


def parse(r: dict) -> dict:
    info = r.get("journalInfo") or {}
    journal = info.get("journal") or {}
    authors = []
    for a in (r.get("authorList") or {}).get("author", []):
        family = a.get("lastName") or a.get("collectiveName")
        if family:
            orcid = a.get("authorId", {}).get("value") if a.get("authorId", {}).get("type") == "ORCID" else None
            authors.append({"family": family, "given": a.get("firstName"), "orcid": orcid})
    subjects = [
        {"scheme": "mesh", "term": m["descriptorName"], "ui": None, "major": m.get("majorTopic_YN") == "Y"}
        for m in (r.get("meshHeadingList") or {}).get("meshHeading", [])
    ]
    subjects += [{"scheme": "keyword", "term": k, "ui": None, "major": False} for k in (r.get("keywordList") or {}).get("keyword", [])]
    identifiers = {}
    if r.get("doi"):
        identifiers["doi"] = r["doi"].lower()
    if r.get("pmid"):
        identifiers["pmid"] = r["pmid"]
    if r.get("pmcid"):
        identifiers["pmcid"] = r["pmcid"].upper()
    pub_types = (r.get("pubTypeList") or {}).get("pubType", [])
    language = r.get("language")
    return {
        "title": http.clean(r["title"]).rstrip("."),
        "abstract": http.clean(r.get("abstractText")),
        "csl_type": "article" if r.get("source") == "PPR" else "article-journal",
        "study_type": pubmed._study_type(pub_types, [s["term"] for s in subjects if s["scheme"] == "mesh"]),
        "container_title": journal.get("title"),
        "container_abbrev": journal.get("isoabbreviation") or journal.get("medlineAbbreviation"),
        "issn_print": journal.get("issn"),
        "issn_e": journal.get("essn"),
        "nlmid": journal.get("nlmid"),
        "year": int(r["pubYear"]) if str(r.get("pubYear", "")).isdigit() else None,
        "issued": info.get("printPublicationDate") or r.get("firstPublicationDate"),
        "volume": info.get("volume"),
        "issue": info.get("issue"),
        "pages": r.get("pageInfo"),
        "language": pubmed.LANGUAGES.get(language, language),
        "license": r.get("license"),
        "authors": authors,
        "identifiers": identifiers,
        "subjects": subjects,
        "is_oa": r.get("isOpenAccess") == "Y",
        "oa_pdf_url": _oa_pdf(r),
        "cited_by_count": r.get("citedByCount"),
    }
