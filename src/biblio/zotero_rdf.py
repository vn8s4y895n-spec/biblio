"""Import d'un export « Zotero RDF » : dans Zotero, clic droit sur une collection (ou Fichier → Exporter la
bibliothèque…) → format Zotero RDF, avec « Exporter les fichiers » et « Exporter les notes ». Zotero écrit un dossier
qui contient le fichier .rdf et un sous-dossier files/ avec les PDF.

Biblio en reprend les notices, les PDF, les notes, les tags (ceux posés à la main ; les tags automatiques, souvent
des mots-clés importés en masse, sont laissés de côté) et les collections, sous-collections comprises. Un article
déjà dans Biblio est complété, jamais dupliqué. Format : celui du traducteur « Zotero RDF.js » de Zotero.
"""

import html
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

from . import core, db, formats, library, notes, organize

NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "z": "http://www.zotero.org/namespaces/export#",
    "dc": "http://purl.org/dc/elements/1.1/", "dcterms": "http://purl.org/dc/terms/",
    "bib": "http://purl.org/net/biblio#", "foaf": "http://xmlns.com/foaf/0.1/",
    "prism": "http://prismstandard.org/namespaces/1.2/basic/", "link": "http://purl.org/rss/1.0/modules/link/",
}  # fmt: skip
RDF = "{%s}" % NS["rdf"]
TYPES = {"journalArticle": "article-journal", "magazineArticle": "article-magazine", "newspaperArticle": "article-newspaper",
         "book": "book", "bookSection": "chapter", "thesis": "thesis", "report": "report", "conferencePaper": "paper-conference",
         "preprint": "article", "webpage": "webpage", "manuscript": "manuscript", "dataset": "dataset"}  # fmt: skip
SKIPPED = ("attachment", "note", "annotation")


class Graph:
    """RDF/XML tel que Zotero l'écrit : des nœuds de premier niveau (rdf:about, rdf:nodeID), reliés par rdf:resource
    ou imbriqués. `get` suit indifféremment les deux formes."""

    def __init__(self, root: ET.Element):
        self.nodes = list(root)
        self.index = {}
        for node in self.nodes:
            for attr in ("about", "nodeID"):
                if key := node.get(RDF + attr):
                    self.index[key] = node

    def values(self, node: ET.Element, prop: str) -> list:
        """Valeurs d'une propriété : texte, ou nœud (référencé ou imbriqué)."""
        out = []
        for el in node.findall(prop, NS):
            ref = el.get(RDF + "resource") or el.get(RDF + "nodeID")
            if ref is not None:
                out.append(self.index.get(ref, ref))
            elif len(el):
                out.append(el[0])
            else:
                out.append((el.text or "").strip())
        return out

    def text(self, node: ET.Element, prop: str) -> str | None:
        return next((v for v in self.values(node, prop) if isinstance(v, str) and v), None)


def _item_type(node: ET.Element) -> str | None:
    el = node.find("z:itemType", NS)
    return el.text.strip() if el is not None and el.text else None


def _record(g: Graph, node: ET.Element) -> dict:
    containers = [c for c in g.values(node, "dcterms:isPartOf") if isinstance(c, ET.Element)]
    container = containers[0] if containers else None
    ids = [v for n in (node, container) if n is not None for v in g.values(n, "dc:identifier") if isinstance(v, str)]
    urls = [g.text(v, "rdf:value") for v in g.values(node, "dc:identifier") if isinstance(v, ET.Element)]
    about = node.get(RDF + "about", "")
    extra = g.text(node, "dc:description") or ""
    doi = next((i[4:].strip() for i in ids if i.upper().startswith("DOI ")), None)
    pmid = re.search(r"PMID:\s*(\d+)", extra)
    pmcid = re.search(r"PMCID:\s*(PMC\d+)", extra)
    issns = formats._issns(" ".join(i[5:] for i in ids if i.upper().startswith("ISSN ")))
    authors = []
    for seq in g.values(node, "bib:authors"):
        for li in g.values(seq, "rdf:li") if isinstance(seq, ET.Element) else []:
            if isinstance(li, ET.Element) and (family := g.text(li, "foaf:surname")):
                authors.append({"family": family, "given": g.text(li, "foaf:givenName"), "orcid": None})
    date = g.text(node, "dc:date") or ""
    iso = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", date)
    return formats._record(
        title=g.text(node, "dc:title"), abstract=g.text(node, "dcterms:abstract"),
        csl_type=TYPES.get(_item_type(node) or "", "article-journal"),
        container_title=container is not None and g.text(container, "dc:title") or None,
        container_abbrev=container is not None and g.text(container, "dcterms:alternative") or None,
        volume=container is not None and g.text(container, "prism:volume") or g.text(node, "prism:volume"),
        issue=container is not None and g.text(container, "prism:number") or g.text(node, "prism:number"),
        pages=g.text(node, "bib:pages"), language=g.text(node, "z:language"),
        issn_print=issns[0] if issns else None, issn_e=issns[1] if len(issns) > 1 else None,
        year=formats._year(date), issued="-".join(p for p in iso.groups() if p) if iso else None,
        authors=authors,
        identifiers=formats._identifiers(doi, pmid and f"pmid:{pmid[1]}", pmcid and pmcid[1],
                                         *(u for u in urls if u), about if about.startswith("http") else None),
    )  # fmt: skip


def _note_text(note_html: str) -> str:
    """Note Zotero (HTML) → texte Markdown simple : paragraphes, retours à la ligne, sans balises."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", note_html)
    text = re.sub(r"(?i)</(p|div|h\d|li)>", "\n\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def import_rdf(conn: sqlite3.Connection, home: Path, path: Path, label_id: int | None = None, client: str = "zotero") -> dict:
    """Importe un export Zotero RDF. Les PDF sont cherchés à côté du fichier .rdf (dossier files/ de l'export).

    Rangement : dans la collection de Biblio `label_id` si elle est donnée ; les collections décrites dans l'export
    sont recréées. L'export d'une seule collection ne la décrit pas : sans `label_id`, les articles vont alors dans
    une collection au nom du fichier (Zotero lui donne le nom de la collection exportée). `target` : son nom."""
    try:
        g = Graph(ET.parse(path).getroot())
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"Export Zotero RDF illisible : {exc}") from exc
    report = {"read": 0, "created": [], "completed": [], "pdfs": 0, "missing_pdfs": 0, "notes": 0, "collections": 0,
              "skipped": 0, "errors": []}  # fmt: skip
    works: dict[str, int] = {}  # rdf:about de l'article Zotero → notice Biblio
    children = {el.get(RDF + "resource") for n in g.nodes for el in n.findall("link:link", NS) + n.findall("dcterms:isReferencedBy", NS)}
    for node in g.nodes:
        kind = _item_type(node)
        if kind is None or node.tag == "{%s}Collection" % NS["z"]:
            continue
        if kind in SKIPPED:  # pièces jointes et notes : reprises avec leur article ; isolées, ignorées
            report["skipped"] += node.get(RDF + "about") not in children
            continue
        report["read"] += 1
        rec = _record(g, node)
        if not rec.get("title"):
            report["errors"].append({"title": node.get(RDF + "about"), "error": "notice sans titre"})
            continue
        try:
            work_id, created = core.add_record(conn, rec, client)
        except (ValueError, sqlite3.Error) as exc:
            report["errors"].append({"title": rec["title"], "error": str(exc)})
            continue
        works[node.get(RDF + "about") or ""] = work_id
        key = conn.execute("SELECT citekey FROM works WHERE id = ?", (work_id,)).fetchone()[0]
        report["created" if created else "completed"].append(key)
        for tag in g.values(node, "dc:subject"):
            if isinstance(tag, str) and tag:
                organize.set_label(conn, organize.create_label(conn, "tag", tag)["id"], [work_id], True, client)
        _attach_pdfs(conn, home, g, node, path.parent, work_id, report)
        _add_notes(conn, home, g, node, work_id, report)
    _collections(conn, g, works, report, client)
    if works and label_id is None and not any(n.tag == "{%s}Collection" % NS["z"] for n in g.nodes):
        label_id = organize.create_label(conn, "collection", path.stem)["id"]
    if works and label_id is not None:
        organize.set_label(conn, label_id, list(works.values()), True, client)
        report["target"] = conn.execute("SELECT name FROM labels WHERE id = ?", (label_id,)).fetchone()[0]
    return report


def _attach_pdfs(conn, home: Path, g: Graph, node: ET.Element, folder: Path, work_id: int, report: dict) -> None:
    for att in g.values(node, "link:link"):
        if not isinstance(att, ET.Element):
            continue
        rel = next((el.get(RDF + "resource") for el in att if el.tag in (RDF + "resource", "{%s}path" % NS["z"])), None)
        is_pdf = (g.text(att, "link:type") or "") == "application/pdf" or (rel or "").lower().endswith(".pdf")
        if not rel or not is_pdf or "://" in rel:  # lien web, ou fichier non exporté
            continue
        file = (folder / unquote(rel)).resolve()
        if not file.is_file() or folder.resolve() not in file.parents:  # absent, ou hors du dossier de l'export
            report["missing_pdfs"] += 1
            continue
        with db.transaction(conn):
            sha, _ = library.store_pdf(conn, home, file.read_bytes(), file.name)
            report["pdfs"] += library.attach(conn, home, work_id, sha)


def _add_notes(conn, home: Path, g: Graph, node: ET.Element, work_id: int, report: dict) -> None:
    texts = [_note_text(g.text(memo, "rdf:value") or "") for memo in g.values(node, "dcterms:isReferencedBy")
             if isinstance(memo, ET.Element)]  # fmt: skip
    texts = [t for t in texts if t]
    if not texts:
        return
    current = notes.read(conn, home, work_id).get("text") or ""
    new = [t for t in texts if t not in current]  # réimport : pas de note en double
    if new:
        notes.write(conn, home, work_id, "\n\n".join([current.strip(), *new]).strip() + "\n")
        report["notes"] += len(new)


def _collections(conn, g: Graph, works: dict, report: dict, client: str) -> None:
    """Collections Zotero → collections Biblio ; une sous-collection garde le chemin : « Parent / Enfant »."""
    collections = {n.get(RDF + "about"): n for n in g.nodes if n.tag == "{%s}Collection" % NS["z"]}
    parent = {}
    for about, node in collections.items():
        for part in node.findall("dcterms:hasPart", NS):
            if part.get(RDF + "resource") in collections:
                parent[part.get(RDF + "resource")] = about

    def name(about: str) -> str:
        own = g.text(collections[about], "dc:title") or "Zotero"
        return f"{name(parent[about])} / {own}" if about in parent else own

    for about, node in collections.items():
        refs = [works[p.get(RDF + "resource")] for p in node.findall("dcterms:hasPart", NS) if p.get(RDF + "resource") in works]
        if refs:
            label = organize.create_label(conn, "collection", name(about))
            organize.set_label(conn, label["id"], refs, True, client)
            report["collections"] += 1
