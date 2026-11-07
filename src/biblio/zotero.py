"""Envoi direct dans Zotero, par le serveur local de Zotero (celui qu'utilise son extension de navigateur).

Zotero doit être ouvert. Les notices arrivent dans la collection sélectionnée dans Zotero, avec leurs tags,
leur note et leur PDF. Aucun compte ni clé : tout reste sur l'ordinateur.
"""

import html
import json
import uuid
from pathlib import Path

import httpx

from . import core, library, notes

URL = "http://127.0.0.1:23119"
HEADERS = {"X-Zotero-Connector-API-Version": "3"}
ITEM_TYPES = {"article-journal": "journalArticle", "article": "preprint", "book": "book", "chapter": "bookSection",
              "paper-conference": "conferencePaper", "thesis": "thesis", "report": "report", "manuscript": "manuscript",
              "webpage": "webpage"}  # fmt: skip
transport: httpx.BaseTransport | None = None  # remplacé dans les tests


class ZoteroError(RuntimeError):
    pass


def _client(timeout: float = 30) -> httpx.Client:
    return httpx.Client(base_url=URL, headers=HEADERS, timeout=timeout, transport=transport)


def status() -> dict:
    """Zotero est-il ouvert, et dans quelle collection les notices arriveront-elles ?"""
    try:
        with _client(timeout=2) as c:
            target = c.post("/connector/getSelectedCollection", json={}).json()
    except (httpx.HTTPError, ValueError):
        return {"running": False}
    return {"running": True, "library": target.get("libraryName"), "collection": target.get("name"),
            "editable": target.get("editable", True)}  # fmt: skip


def _note_html(text: str) -> str:
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in text.strip().split("\n\n") if p.strip())


def to_item(work: dict, note: str | None) -> dict:
    ids = work["identifiers"]
    extra = [f"PMID: {ids['pmid']}"] if ids.get("pmid") else []
    extra += [f"PMCID: {ids['pmcid']}"] if ids.get("pmcid") else []
    extra += [f"Citation Key: {work['citekey']}"]
    item = {
        "id": f"biblio-{work['id']}", "itemType": ITEM_TYPES.get(work.get("csl_type"), "document"),
        "title": work["title"], "abstractNote": work.get("abstract"),
        "creators": [{"lastName": a["family"], "firstName": a.get("given") or "", "creatorType": "author"} for a in work["authors"]],
        "publicationTitle": work.get("container_title"), "journalAbbreviation": work.get("container_abbrev"),
        "volume": work.get("volume"), "issue": work.get("issue"), "pages": work.get("pages"),
        "date": work.get("issued") or (str(work["year"]) if work.get("year") else None), "DOI": ids.get("doi"),
        "ISSN": ", ".join(i for i in (work.get("issn_print"), work.get("issn_e")) if i) or None,
        "language": work.get("language"), "url": f"https://doi.org/{ids['doi']}" if ids.get("doi") else ids.get("url"),
        "extra": "\n".join(extra), "tags": [{"tag": label["name"]} for label in work.get("labels", []) if label["kind"] == "tag"],
        "notes": [{"note": _note_html(note)}] if note and note.strip() else [], "attachments": [],
    }  # fmt: skip
    return {k: v for k, v in item.items() if v is not None}


def send(conn, home: Path, refs: list, pdf: bool = True) -> dict:
    """Envoie les notices (et leur PDF) dans Zotero. Renvoie la collection de destination et le nombre de PDF."""
    target = status()
    if not target["running"]:
        raise ZoteroError("Zotero n'est pas ouvert : lance Zotero puis recommence")
    if not target["editable"]:
        raise ZoteroError("La collection sélectionnée dans Zotero n'est pas modifiable")
    works = [core.get_work(conn, ref) for ref in refs]
    session = uuid.uuid4().hex
    items = [to_item(w, notes.read(conn, home, w["id"])["text"]) for w in works]
    sent_pdfs = 0
    with _client() as c:
        r = c.post("/connector/saveItems", json={"sessionID": session, "uri": "biblio://", "items": items})
        if r.status_code != 201:
            raise ZoteroError(f"Zotero a refusé les notices (HTTP {r.status_code})")
        for w in works if pdf else []:
            files = library.files_of(conn, w["id"])
            if not files:
                continue
            meta = {"sessionID": session, "parentItemID": f"biblio-{w['id']}", "title": "PDF",
                    "url": f"https://doi.org/{w['identifiers']['doi']}" if w["identifiers"].get("doi") else ""}  # fmt: skip
            data = library.pdf_path(home, files[0]["sha256"]).read_bytes()
            r = c.post("/connector/saveAttachment", content=data,
                       headers={"X-Metadata": json.dumps(meta), "Content-Type": "application/pdf"})  # fmt: skip
            sent_pdfs += r.status_code == 201
    return {"sent": len(items), "pdfs": sent_pdfs, "library": target["library"], "collection": target["collection"]}
