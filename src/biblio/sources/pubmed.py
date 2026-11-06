"""PubMed (NCBI E-utilities) : notice MEDLINE complète, MeSH, type d'étude."""

import re
import xml.etree.ElementTree as ET

from . import SEARCH_TTL, Query, http

NAME, LABEL = "pubmed", "PubMed"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
LANGUAGES = {"eng": "en", "fre": "fr", "ger": "de", "spa": "es", "ita": "it", "por": "pt", "chi": "zh", "jpn": "ja"}

# Ordre de priorité : le premier type trouvé l'emporte.
PUBLICATION_TYPES = [
    ("Meta-Analysis", "meta-analysis"),
    ("Systematic Review", "systematic-review"),
    ("Randomized Controlled Trial", "rct"),
    ("Clinical Trial", "clinical-trial"),
    ("Practice Guideline", "guideline"),
    ("Guideline", "guideline"),
    ("Observational Study", "observational"),
    ("Case Reports", "case-report"),
]
MESH_DESIGNS = [
    ("Cohort Studies", "cohort"),
    ("Prospective Studies", "cohort"),
    ("Retrospective Studies", "cohort"),
    ("Case-Control Studies", "case-control"),
    ("Cross-Sectional Studies", "cross-sectional"),
]


def _params(**params) -> dict:
    params["tool"] = "biblio"
    if http.contact_email:
        params["email"] = http.contact_email
    if key := http.api_keys.get("ncbi"):
        params["api_key"] = key
    return params


def find_pmid(term: str) -> str | None:
    """PMID unique correspondant à une requête exacte (« 10.1001/…[doi] », « PMC123[pmcid] »), sinon None."""
    response = http.get(EUTILS + "esearch.fcgi", _params(db="pubmed", retmode="json", term=term))
    ids = response.json()["esearchresult"]["idlist"] if response else []
    return ids[0] if len(ids) == 1 else None


def fetch(pmid: str) -> dict | None:
    response = http.get(EUTILS + "efetch.fcgi", _params(db="pubmed", retmode="xml", id=pmid))
    if not response:
        return None
    article = ET.fromstring(response.content).find("PubmedArticle")
    return parse(article) if article is not None else None


def search(q: Query) -> list[dict]:
    """La requête est transmise telle quelle : PubMed comprend sa syntaxe avancée."""
    term = q.text
    if years := q.years:
        term = f"({term}) AND {years[0]}:{years[1]}[dp]"
    for heading in q.mesh:
        term += f' AND "{heading}"[MeSH Terms]'
    found = http.get(
        EUTILS + "esearch.fcgi",
        _params(db="pubmed", retmode="json", sort="relevance", retmax=q.limit, term=term),
        ttl=SEARCH_TTL,
    )
    ids = found.json()["esearchresult"]["idlist"] if found else []
    if not ids:
        return []
    fetched = http.get(EUTILS + "efetch.fcgi", _params(db="pubmed", retmode="xml", id=",".join(ids)), ttl=SEARCH_TTL)
    records = {}
    for article in ET.fromstring(fetched.content).findall("PubmedArticle") if fetched else []:
        rec = parse(article)
        rec["is_oa"] = "pmcid" in rec["identifiers"]  # tout article dans PMC est lisible gratuitement
        records[rec["identifiers"]["pmid"]] = rec
    return [records[i] for i in ids if i in records]  # ordre de pertinence de PubMed


def _text(el: ET.Element | None) -> str | None:
    return http.clean("".join(el.itertext())) if el is not None else None


def _study_type(pub_types: list[str], mesh: list[str]) -> str | None:
    for label, code in PUBLICATION_TYPES:
        if any(p.startswith(label) for p in pub_types):
            return code
    for label, code in MESH_DESIGNS:
        if label in mesh:
            return code
    return "review" if "Review" in pub_types else None


def parse(art: ET.Element) -> dict:
    mc = art.find("MedlineCitation")
    a = mc.find("Article")
    journal = a.find("Journal")
    date = journal.find("JournalIssue/PubDate")
    year = date.findtext("Year") or (re.match(r"\d{4}", date.findtext("MedlineDate") or "") or [None])[0]
    month = date.findtext("Month")
    month = MONTHS.get(month) or (int(month) if month and month.isdigit() else None)
    day = date.findtext("Day")
    issued = "-".join(f"{int(p):02d}" for p in (year, month, day if month else None) if p) or None

    abstract = "\n".join(
        f"{t.get('Label')}: {_text(t)}" if t.get("Label") else _text(t) for t in a.findall("Abstract/AbstractText") if _text(t)
    )
    title = _text(a.find("ArticleTitle")) or ""
    pages = a.findtext("Pagination/MedlinePgn") or "-".join(
        p for p in (a.findtext("Pagination/StartPage"), a.findtext("Pagination/EndPage")) if p
    )

    authors = []
    for au in a.findall("AuthorList/Author"):
        orcid = au.findtext("Identifier[@Source='ORCID']")
        family = au.findtext("LastName") or au.findtext("CollectiveName")
        if family:
            authors.append({
                "family": family,
                "given": au.findtext("ForeName"),
                "orcid": orcid.rstrip("/").rsplit("/", 1)[-1] if orcid else None,
            })

    subjects = []
    for heading in mc.findall("MeshHeadingList/MeshHeading"):
        name = heading.find("DescriptorName")
        major = name.get("MajorTopicYN") == "Y" or any(q.get("MajorTopicYN") == "Y" for q in heading.findall("QualifierName"))
        subjects.append({"scheme": "mesh", "term": name.text, "ui": name.get("UI"), "major": major})
    subjects += [{"scheme": "keyword", "term": _text(k), "ui": None, "major": False} for k in mc.findall("KeywordList/Keyword") if _text(k)]

    ids = {i.get("IdType"): i.text.strip() for i in art.findall("PubmedData/ArticleIdList/ArticleId") if i.text}
    identifiers = {"pmid": mc.findtext("PMID")}
    if ids.get("doi"):
        identifiers["doi"] = ids["doi"].lower()
    if ids.get("pmc"):
        identifiers["pmcid"] = ids["pmc"].upper()

    language = a.findtext("Language")
    return {
        "title": title[:-1] if title.endswith(".") else title,  # MEDLINE ajoute un point final
        "abstract": abstract or None,
        "csl_type": "article-journal",
        "study_type": _study_type(
            [p.text for p in a.findall("PublicationTypeList/PublicationType")],
            [s["term"] for s in subjects if s["scheme"] == "mesh"],
        ),
        "container_title": journal.findtext("Title"),
        "container_abbrev": journal.findtext("ISOAbbreviation") or mc.findtext("MedlineJournalInfo/MedlineTA"),
        "issn_print": journal.findtext("ISSN[@IssnType='Print']"),
        "issn_e": journal.findtext("ISSN[@IssnType='Electronic']"),
        "nlmid": mc.findtext("MedlineJournalInfo/NlmUniqueID"),
        "year": int(year) if year else None,
        "issued": issued,
        "volume": journal.findtext("JournalIssue/Volume"),
        "issue": journal.findtext("JournalIssue/Issue"),
        "pages": pages or None,
        "language": LANGUAGES.get(language, language),
        "license": None,
        "authors": authors,
        "identifiers": identifiers,
        "subjects": subjects,
    }
