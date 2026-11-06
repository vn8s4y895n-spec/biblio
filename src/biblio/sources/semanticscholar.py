"""Semantic Scholar : index de l'Allen Institute for AI (https://www.semanticscholar.org).
Sans clé, le service partage une limite commune et refuse souvent (HTTP 429)."""

from . import SEARCH_TTL, Query, http

NAME, LABEL = "semanticscholar", "Semantic Scholar"
API = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,year,venue,externalIds,authors,publicationTypes,journal,openAccessPdf,citationCount,isOpenAccess,publicationVenue,publicationDate"
STUDY_TYPES = [("MetaAnalysis", "meta-analysis"), ("ClinicalTrial", "clinical-trial"), ("CaseReport", "case-report"), ("Review", "review")]


def search(q: Query) -> list[dict]:
    params = {"query": q.plain, "limit": q.limit, "fields": FIELDS}
    if years := q.years:
        params["year"] = f"{years[0]}-{years[1]}"
    key = http.api_keys.get("semanticscholar")
    response = http.get(API, params, ttl=SEARCH_TTL, headers={"x-api-key": key} if key else None)
    return [parse(p) for p in (response.json().get("data") or []) if p.get("title")] if response else []


def parse(p: dict) -> dict:
    ext = p.get("externalIds") or {}
    venue = p.get("publicationVenue") or {}
    journal = p.get("journal") or {}
    issns = [i for i in [venue.get("issn"), *(venue.get("alternate_issns") or [])] if i]
    identifiers = {}
    if ext.get("DOI"):
        identifiers["doi"] = ext["DOI"].lower()
    if ext.get("PubMed"):
        identifiers["pmid"] = str(ext["PubMed"])
    if ext.get("PubMedCentral"):
        identifiers["pmcid"] = f"PMC{ext['PubMedCentral']}".replace("PMCPMC", "PMC")
    if ext.get("CorpusId"):
        identifiers["s2"] = str(ext["CorpusId"])
    types = p.get("publicationTypes") or []
    pdf = p.get("openAccessPdf") or {}
    return {
        "title": http.clean(p["title"]),
        "abstract": http.clean(p.get("abstract")),
        "csl_type": "article-journal" if "JournalArticle" in types or issns else "article",
        "study_type": next((code for label, code in STUDY_TYPES if label in types), None),
        "container_title": journal.get("name") or p.get("venue") or None,
        "container_abbrev": None,
        "issn_print": issns[0] if issns else None,
        "issn_e": issns[1] if len(issns) > 1 else None,
        "nlmid": None,
        "year": p.get("year"),
        "issued": p.get("publicationDate"),
        "volume": (journal.get("volume") or "").strip() or None,
        "issue": None,
        "pages": (journal.get("pages") or "").strip() or None,
        "language": None,
        "license": None,
        "authors": [{**http.split_name(a["name"]), "orcid": None} for a in p.get("authors") or [] if a.get("name")],
        "identifiers": identifiers,
        "subjects": [],
        "is_oa": bool(p.get("isOpenAccess")),
        "oa_pdf_url": pdf.get("url") or None,
        "cited_by_count": p.get("citationCount"),
    }
