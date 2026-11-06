"""arXiv : notices des preprints par identifiant (https://info.arxiv.org/help/api)."""

import xml.etree.ElementTree as ET

from . import http

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}


def fetch(arxiv_id: str) -> dict | None:
    response = http.get(API, {"id_list": arxiv_id})
    entry = ET.fromstring(response.content).find("a:entry", NS) if response else None
    if entry is None or entry.find("a:title", NS) is None:  # identifiant inconnu : flux sans entrée ou entrée d'erreur
        return None
    published = entry.findtext("a:published", "", NS)
    identifiers = {"arxiv": arxiv_id, "doi": f"10.48550/arxiv.{arxiv_id}"}
    return {
        "title": http.clean(entry.findtext("a:title", "", NS)),
        "abstract": http.clean(entry.findtext("a:summary", "", NS)),
        "csl_type": "article",  # preprint
        "study_type": None,
        "container_title": "arXiv",
        "container_abbrev": None,
        "issn_print": None,
        "issn_e": None,
        "nlmid": None,
        "year": int(published[:4]) if published[:4].isdigit() else None,
        "issued": published[:10] or None,
        "volume": None,
        "issue": None,
        "pages": None,
        "language": "en",
        "license": None,
        "authors": [{**http.split_name(a.findtext("a:name", "", NS)), "orcid": None} for a in entry.findall("a:author", NS)],
        "identifiers": identifiers,
        "subjects": [],
    }
