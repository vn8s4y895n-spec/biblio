"""Zotero, dans les deux sens, par son serveur local (127.0.0.1:23119) : aucun compte ni clé, tout reste sur
l'ordinateur, et Zotero doit être ouvert.

- Envoi : par l'interface de son extension de navigateur ; les notices arrivent dans la collection sélectionnée
  dans Zotero, avec leurs tags, leur note et leur PDF.
- Import : par son « API locale » (Zotero 7 et plus), à activer une fois dans Zotero : Réglages → Avancé →
  « Autoriser d'autres applications de cet ordinateur à communiquer avec Zotero ». Notice exacte, PDF rangés dans
  Zotero et tags. Sans elle, les articles glissés depuis Zotero sont ajoutés par leur DOI ou PMID, en ligne.
"""

import html
import json
import re
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from . import core, db, formats, importer, library, notes, organize
from .sources.http import SourceError

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


# --- Import depuis Zotero ----------------------------------------------------------------------------------

LOCAL_API = "/api/users/0"
ENABLE_HINT = ("Dans Zotero : Réglages → Avancé → cocher « Autoriser d'autres applications de cet ordinateur à "
               "communiquer avec Zotero », puis réessayer")  # fmt: skip
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>{}\[\]]+")  # parenthèses permises : 10.1016/S0140-6736(20)30183-5
PMID_RE = re.compile(r"\bPMID:?\s*(\d{5,9})\b", re.I)


def _api(path: str, **params) -> httpx.Response:
    try:
        with _client() as c:
            response = c.get(LOCAL_API + path, params=params)
    except httpx.HTTPError as exc:
        raise ZoteroError("Zotero n'est pas ouvert (ou injoignable) : l'ouvrir, puis réessayer") from exc
    if response.status_code == 403:
        raise ZoteroError(ENABLE_HINT)
    if response.status_code >= 400:
        raise ZoteroError(f"Zotero : HTTP {response.status_code} {response.text[:200]}")
    return response


def local_api() -> bool:
    try:
        _api("/collections", limit=1)
        return True
    except ZoteroError:
        return False


def collections() -> list[dict]:
    """Collections de « Ma bibliothèque » Zotero, dans l'ordre de l'arbre : {key, name, level, count}."""
    rows, start = [], 0
    while True:
        page = _api("/collections", limit=100, start=start).json()
        rows += page
        start += len(page)
        if len(page) < 100:
            break
    by_parent: dict = {}
    for c in rows:
        by_parent.setdefault(c["data"].get("parentCollection") or None, []).append(c)
    out = []

    def walk(parent, level):
        for c in sorted(by_parent.get(parent, []), key=lambda c: c["data"]["name"].casefold()):
            out.append({"key": c["key"], "name": c["data"]["name"], "level": level, "count": c["meta"].get("numItems", 0)})
            walk(c["key"], level + 1)

    walk(None, 0)
    return out


def selected_collection() -> dict:
    """La collection sélectionnée dans Zotero (celle qu'on vient d'y glisser : un glisser commence par un clic)."""
    with _client(timeout=5) as c:
        try:
            target = c.post("/connector/getSelectedCollection", json={}).json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ZoteroError("Zotero n'est pas ouvert : l'ouvrir, puis réessayer") from exc
    if not target.get("name") or target.get("name") == target.get("libraryName"):
        raise ZoteroError("Aucune collection sélectionnée dans Zotero")
    matches = [c for c in collections() if c["name"] == target["name"]]
    if not matches:
        raise ZoteroError(f"Collection Zotero introuvable : {target['name']}")
    return matches[0]


def _item_keys(path: str) -> list[str]:
    keys, start = [], 0
    while True:
        page = _api(path, format="keys", limit=100, start=start).text.split()
        keys += page
        start += len(page)
        if len(page) < 100:
            return keys


def identifiers_in(text: str) -> list[tuple[str, str]]:
    """DOI et PMID d'un texte (références mises en forme par Zotero), sans doublon, dans l'ordre."""
    found = [("doi", library._clean_doi(d)) for d in DOI_RE.findall(text)]
    found += [("pmid", p) for p in PMID_RE.findall(text)]
    return list(dict.fromkeys(found))


def _keys_for(value: str) -> list[str]:
    """Article(s) Zotero portant ce DOI ou ce PMID (recherche dans tous les champs, pièces jointes exclues)."""
    return _api("/items/top", q=value, qmode="everything", format="keys").text.split()[:1]


def import_items(conn, home: Path, keys: list[str], label_id: int | None = None, client: str = "zotero") -> dict:
    """Importe des articles Zotero : notice (CSL-JSON de Zotero), PDF rangés dans Zotero, tags. Un article déjà
    dans Biblio est complété, jamais dupliqué ; `label_id` : collection de Biblio où les ranger."""
    report = {"created": [], "completed": [], "pdfs": 0, "skipped": 0, "errors": []}
    for key in keys:
        item = _api(f"/items/{key}").json()["data"]
        if item["itemType"] in ("attachment", "note", "annotation"):
            report["skipped"] += 1
            continue
        records = formats.parse(_api("/items", itemKey=key, format="csljson").text, "csljson")
        if not records:
            report["errors"].append({"title": item.get("title") or key, "error": "notice sans titre"})
            continue
        work_id, created = core.add_record(conn, records[0], client)
        report["created" if created else "completed"].append(work_id)
        for tag in item.get("tags", []):
            if name := tag.get("tag", "").strip():
                organize.set_label(conn, organize.create_label(conn, "tag", name)["id"], [work_id], True, client)
        if label_id is not None:
            organize.set_label(conn, label_id, [work_id], True, client)
        report["pdfs"] += _import_pdfs(conn, home, key, work_id)
    return report


def _import_pdfs(conn, home: Path, key: str, work_id: int) -> int:
    count = 0
    for child in _api(f"/items/{key}/children").json():
        data = child["data"]
        if data.get("itemType") != "attachment" or data.get("contentType") != "application/pdf" or data.get("linkMode") == "linked_url":
            continue
        try:
            url = _api(f"/items/{child['key']}/file/view/url").text.strip()
            path = Path(unquote(urlparse(url).path))
            pdf = path.read_bytes()
        except (ZoteroError, OSError):
            continue  # fichier absent de l'ordinateur (seulement en ligne dans Zotero)
        with db.transaction(conn):
            sha, _ = library.store_pdf(conn, home, pdf, path.name)
            count += library.attach(conn, home, work_id, sha)
    return count


def import_collection(conn, home: Path, key: str | None, label_id: int | None = None) -> dict:
    """Tous les articles d'une collection Zotero (`key` None : celle sélectionnée dans Zotero), sans ses
    sous-collections."""
    collection = selected_collection() if key is None else next((c for c in collections() if c["key"] == key), None)
    if collection is None:
        raise ZoteroError(f"Collection Zotero inconnue : {key}")
    report = import_items(conn, home, _item_keys(f"/collections/{collection['key']}/items/top"), label_id)
    return {"via": "zotero", "collection": collection["name"], **report, "unmatched": 0}


def import_dropped(conn, home: Path, text: str, label_id: int | None = None) -> dict:
    """Articles glissés depuis Zotero : le texte de sa « copie rapide » (références mises en forme, ou BibTeX, RIS,
    CSL-JSON selon ses réglages). Avec l'API locale, chaque article est retrouvé dans Zotero par son DOI ou son PMID
    et importé avec ses PDF et ses tags ; sinon il est ajouté en ligne par son identifiant (PDF libre s'il existe)."""
    try:
        fmt = formats.detect(text)
    except formats.FormatError:
        fmt = None
    if fmt:  # exporté par Zotero en BibTeX, RIS ou CSL-JSON : notices complètes, sans requête
        out = importer.import_bibliography(conn, text, fmt, "zotero")
        if label_id is not None and out["created"] + out["completed"]:
            organize.set_label(conn, label_id, out["created"] + out["completed"], True, "zotero")
        return {"via": "export", "created": out["created"], "completed": out["completed"], "pdfs": 0, "unmatched": 0,
                "errors": out["errors"]}  # fmt: skip
    found = identifiers_in(text)
    entries = [line for line in text.splitlines() if line.strip()]
    unmatched = sum(1 for line in entries if not identifiers_in(line)) if len(entries) > 1 else int(not found)
    if local_api():
        keys = list(dict.fromkeys(k for _, value in found for k in _keys_for(value)))
        report = import_items(conn, home, keys, label_id)
        return {"via": "zotero", **report, "unmatched": unmatched + len(found) - len(keys) - report["skipped"]}
    report = {"via": "online", "created": [], "completed": [], "pdfs": 0, "errors": []}
    for scheme, value in found:
        try:
            out = importer.add(conn, home, value if scheme == "doi" else f"PMID:{value}", "zotero", True)
        except (ValueError, core.NotFound, SourceError) as exc:
            report["errors"].append({"title": value, "error": str(exc)})
            continue
        report["created" if out["created"] else "completed"].append(out["work"]["id"])
        report["pdfs"] += bool(out["pdf"])
        if label_id is not None:
            organize.set_label(conn, label_id, [out["work"]["id"]], True, "zotero")
    return {**report, "unmatched": unmatched}

