"""Lecture des fichiers bibliographiques : BibTeX, RIS, MEDLINE (.nbib, export PubMed), CSL-JSON.
Les exports Zotero, EndNote, Mendeley ou JabRef utilisent ces formats. Chaque notice est convertie au format interne."""

import json
import re
import unicodedata

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode

from . import core
from .journals import normalize_issn
from .sources import http, pubmed

FORMATS = ("bibtex", "ris", "nbib", "csljson")


class FormatError(ValueError):
    pass


def detect(text: str) -> str:
    head = text.lstrip("﻿ \t\r\n")[:2000]
    if head.startswith(("[", "{")):
        return "csljson"
    if re.search(r"^@\w+\s*[{(]", head, re.M):
        return "bibtex"
    if re.search(r"^TY  - ", head, re.M):
        return "ris"
    if re.search(r"^PMID- ", head, re.M):
        return "nbib"
    raise FormatError("Format non reconnu (attendu : BibTeX, RIS, MEDLINE/nbib ou CSL-JSON)")


def parse(text: str, fmt: str | None = None) -> list[dict]:
    fmt = fmt or detect(text)
    if fmt not in FORMATS:
        raise FormatError(f"Format inconnu : {fmt}")
    records = {"bibtex": _bibtex, "ris": _ris, "nbib": _nbib, "csljson": _csljson}[fmt](text)
    return [r for r in records if r.get("title")]


def _record(**fields) -> dict:
    rec = dict.fromkeys(core.FIELDS)
    rec.update(authors=[], identifiers={}, subjects=[], csl_type="article-journal")
    rec.update({k: v for k, v in fields.items() if v not in (None, "", [], {})})
    return rec


def _identifiers(*candidates: str | None) -> dict:
    """Identifiants reconnus parmi des valeurs brutes (DOI, URL, PMID, PMCID, arXiv)."""
    found = {}
    for value in candidates:
        if not value:
            continue
        try:
            scheme, normalized = core.parse_identifier(str(value))
        except core.InvalidIdentifier:
            continue
        if scheme != "url":
            found.setdefault(scheme, normalized)
    return found


def _name(text: str) -> dict:
    text = re.sub(r"[{}]", "", text).strip()
    if "," in text:
        family, _, given = text.partition(",")
        return {"family": family.strip(), "given": given.strip() or None, "orcid": None}
    return {**http.split_name(text), "orcid": None}


def _issns(text: str | None) -> list[str]:
    return [i for i in (normalize_issn(x) for x in re.split(r"[,;\s]+", text or "")) if i]


def _year(text: str | None) -> int | None:
    m = re.search(r"\b(1[5-9]\d\d|2\d{3})\b", text or "")
    return int(m.group()) if m else None


def _keywords(values: list[str]) -> list[dict]:
    terms = {t.strip() for v in values for t in re.split(r"[;,]", v) if t.strip()}
    return [{"scheme": "keyword", "term": t, "ui": None, "major": False} for t in sorted(terms)]


# --- BibTeX --------------------------------------------------------------------------------------

BIBTEX_TYPES = {"article": "article-journal", "book": "book", "inbook": "chapter", "incollection": "chapter",
                "inproceedings": "paper-conference", "conference": "paper-conference", "phdthesis": "thesis",
                "mastersthesis": "thesis", "techreport": "report", "unpublished": "manuscript", "online": "webpage"}  # fmt: skip


def _unlatex(text: str) -> str:
    """Accolades retirées ; « {\\'\\i} » donne un i sans point suivi d'un accent combiné : on recompose « í »."""
    text = re.sub("ı(?=[̀-ͯ])", "i", re.sub(r"[{}]", "", text))
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", text)).strip()


def _bibtex(text: str) -> list[dict]:
    parser = BibTexParser(common_strings=True, ignore_nonstandard_types=False)
    parser.customization = convert_to_unicode
    records = []
    for e in bibtexparser.loads(text, parser).entries:
        def f(key):
            return _unlatex(e.get(key, "")) or None

        issns = _issns(f("issn"))
        arxiv = f("eprint") if (f("archiveprefix") or f("eprinttype") or "").lower() == "arxiv" else None
        is_preprint = bool(arxiv) or (f("journal") or "").lower() in ("biorxiv", "medrxiv", "arxiv")
        records.append(_record(
            title=f("title"), abstract=f("abstract"),
            csl_type="article" if is_preprint else BIBTEX_TYPES.get(e["ENTRYTYPE"].lower(), "document"),
            container_title=f("journal") or f("journaltitle") or f("booktitle"), container_abbrev=f("shortjournal"),
            issn_print=issns[0] if issns else None, issn_e=issns[1] if len(issns) > 1 else None,
            year=_year(f("year") or f("date")), volume=f("volume"), issue=f("number") or f("issue"),
            pages=(f("pages") or "").replace("--", "-") or None, language=f("language"),
            authors=[_name(_unlatex(a)) for a in re.split(r"\s+and\s+", e.get("author", "")) if a.strip()],
            identifiers=_identifiers(f("doi"), f("pmid") and f"pmid:{f('pmid')}", f("pmcid"), arxiv and f"arXiv:{arxiv}"),
            subjects=_keywords([f("keywords") or ""]),
        ))  # fmt: skip
    return records


# --- RIS et MEDLINE : lignes « ÉTIQUETTE - valeur » --------------------------------------------------


def _tagged(text: str, start: str, line_re: str) -> list[dict[str, list[str]]]:
    """Découpe un fichier à étiquettes en notices {étiquette: [valeurs]}. Une ligne sans étiquette prolonge la
    valeur précédente (MEDLINE coupe les longues valeurs sur plusieurs lignes)."""
    entries, current, last = [], None, None
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(line_re, line)
        if m:
            tag, value = m.group(1).strip(), m.group(2).strip()
            if tag == start:
                current = {}
                entries.append(current)
            if current is not None:
                current.setdefault(tag, []).append(value)
                last = tag
        elif current is not None and last and line.startswith(" ") and line.strip():
            current[last][-1] += " " + line.strip()
    return entries


RIS_TYPES = {"JOUR": "article-journal", "EJOUR": "article-journal", "BOOK": "book", "CHAP": "chapter",
             "CONF": "paper-conference", "CPAPER": "paper-conference", "THES": "thesis", "RPRT": "report",
             "UNPB": "manuscript", "ELEC": "webpage", "GEN": "document"}  # fmt: skip


def _ris(text: str) -> list[dict]:
    records = []
    for e in _tagged(text, "TY", r"^([A-Z][A-Z0-9])  - ?(.*)$"):
        def f(*tags):
            return next((e[t][0] for t in tags if e.get(t) and e[t][0]), None)

        issns = _issns(" ".join(e.get("SN", [])))
        start, end = f("SP"), f("EP")
        records.append(_record(
            title=f("TI", "T1"), abstract=f("AB", "N2"), csl_type=RIS_TYPES.get(f("TY") or "", "document"),
            container_title=f("JF", "JO", "T2"), container_abbrev=f("JA", "J2"),
            issn_print=issns[0] if issns else None, issn_e=issns[1] if len(issns) > 1 else None,
            year=_year(f("PY", "Y1", "DA")), volume=f("VL"), issue=f("IS"),
            pages="-".join(p for p in (start, end) if p) or None, language=f("LA"),
            authors=[_name(a) for a in e.get("AU", []) + e.get("A1", [])],
            identifiers=_identifiers(f("DO"), *e.get("UR", []), *e.get("L2", []),
                                     *(f"pmid:{v}" for v in e.get("AN", []) if v.isdigit())),
            subjects=_keywords(e.get("KW", [])),
        ))  # fmt: skip
    return records


def _nbib(text: str) -> list[dict]:
    records = []
    for e in _tagged(text, "PMID", r"^([A-Z]{1,4})\s*- (.*)$"):
        def f(tag):
            return e.get(tag, [None])[0]

        issns = {}
        for value in e.get("IS", []):
            m = re.match(r"(\S+)\s*\((\w+)\)", value)
            if m:
                issns.setdefault(m.group(2).lower(), normalize_issn(m.group(1)))
        ids = [f"pmid:{f('PMID')}", f("PMC")]
        ids += [v.split(" [doi]")[0] for v in e.get("AID", []) + e.get("LID", []) if v.endswith("[doi]")]
        mesh = []
        for heading in e.get("MH", []):
            term = heading.split("/")[0].lstrip("*").strip()
            mesh.append({"scheme": "mesh", "term": term, "ui": None, "major": "*" in heading})
        orcids = [re.sub(r"^ORCID:\s*", "", v) for v in e.get("AUID", [])]
        authors = [_name(a) for a in e.get("FAU", [])] or [_name(a) for a in e.get("AU", [])]
        if len(orcids) == len(authors):  # sinon on ne sait pas à quel auteur rattacher chaque ORCID
            for author, orcid in zip(authors, orcids):
                author["orcid"] = orcid
        title = f("TI") or ""
        records.append(_record(
            title=title[:-1] if title.endswith(".") else title, abstract=f("AB"),
            study_type=pubmed._study_type(e.get("PT", []), [m["term"] for m in mesh]),
            container_title=f("JT"), container_abbrev=f("TA"), nlmid=f("JID"),
            issn_print=issns.get("print") or issns.get("linking"), issn_e=issns.get("electronic"),
            year=_year(f("DP")), volume=f("VI"), issue=f("IP"), pages=f("PG"),
            language=pubmed.LANGUAGES.get(f("LA"), f("LA")), authors=authors,
            identifiers=_identifiers(*ids), subjects=mesh + _keywords(e.get("OT", [])),
        ))  # fmt: skip
    return records


# --- CSL-JSON (Zotero, Better BibTeX, Pandoc) ------------------------------------------------------------


def _csljson(text: str) -> list[dict]:
    try:
        items = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FormatError(f"CSL-JSON invalide : {exc}") from exc
    items = items if isinstance(items, list) else items.get("items", [items])
    records = []
    for it in items:
        if not isinstance(it, dict):
            continue
        parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        issn = it.get("ISSN")
        issns = _issns(" ".join(issn) if isinstance(issn, list) else issn)
        authors = []
        for a in it.get("author") or []:
            if a.get("family") or a.get("literal"):
                authors.append({"family": a.get("family") or a["literal"], "given": a.get("given"), "orcid": None})
        records.append(_record(
            title=it.get("title"), abstract=it.get("abstract"), csl_type=it.get("type") or "article-journal",
            container_title=it.get("container-title"), container_abbrev=it.get("container-title-short") or it.get("journalAbbreviation"),
            issn_print=issns[0] if issns else None, issn_e=issns[1] if len(issns) > 1 else None,
            year=parts[0] if parts and isinstance(parts[0], int) else _year(str(parts[0] or "")),
            issued="-".join(f"{int(p):02d}" for p in parts if str(p).isdigit()) or None,
            volume=it.get("volume"), issue=it.get("issue"), pages=it.get("page"), language=it.get("language"),
            authors=authors,
            identifiers=_identifiers(it.get("DOI"), it.get("PMID") and f"pmid:{it['PMID']}", it.get("PMCID"), it.get("URL")),
            subjects=_keywords([it.get("keyword") or ""]),
        ))  # fmt: skip
    return records
