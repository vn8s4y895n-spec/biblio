"""Imports : par identifiant avec PDF libre, PDF déposé (DOI repéré puis vérifié), fichiers bibliographiques,
boîte d'entrée surveillée."""

import logging
import shutil
import sqlite3
import time
from pathlib import Path

from . import core, db, formats, library, search
from .sources import Query, crossref, http

log = logging.getLogger("biblio")


def _attach(conn: sqlite3.Connection, home: Path, work_id: int, sha: str, client: str, source_url: str | None = None) -> None:
    with db.transaction(conn):
        if library.attach(conn, home, work_id, sha, source_url):
            core._log(conn, work_id, client, "update", "files", None, {"sha256": sha, "source_url": source_url})


def fetch_open_pdf(conn: sqlite3.Connection, home: Path, work_id: int, client: str = "api") -> dict | None:
    """Télécharge et rattache la version libre et légale d'un article, s'il n'a pas déjà de PDF."""
    if library.files_of(conn, work_id):
        return None
    ids = core.get_work(conn, work_id)["identifiers"]
    if not (ids.get("pmcid") or ids.get("doi")):
        return None
    candidates = search.open_access_candidates(doi=ids.get("doi"), pmcid=ids.get("pmcid"))
    failures = []
    for found in candidates:  # un éditeur ou un dépôt peut refuser le téléchargement : on essaie le suivant
        try:
            data = http.download(found["pdf_url"], library.MAX_PDF)
            with db.transaction(conn):
                sha, _ = library.store_pdf(conn, home, data, found["pdf_url"].rsplit("/", 1)[-1][:200])
        except (http.SourceError, library.FileError) as exc:
            failures.append(f"{found['source']} : {exc}")
            continue
        _attach(conn, home, work_id, sha, client, found["pdf_url"])
        return {"sha256": sha, **found}
    if failures:
        raise http.SourceError(" ; ".join(failures))
    return None


def add(conn: sqlite3.Connection, home: Path, text: str, client: str = "api", pdf: bool = True) -> dict:
    """Ajout par identifiant, avec le PDF libre s'il existe. Un échec du PDF n'annule pas l'ajout."""
    work, created, warnings = core.add_by_identifier(conn, text, client)
    pdf_found = None
    if pdf:
        try:
            pdf_found = fetch_open_pdf(conn, home, work["id"], client)
        except (http.SourceError, library.FileError) as exc:
            warnings.append(f"PDF libre non récupéré : {exc}")
    return {"created": created, "warnings": warnings, "pdf": pdf_found, "work": core.get_work(conn, work["id"])}


def _candidates(title: str | None) -> list[dict]:
    """Saisie assistée : notices Crossref proches du titre deviné."""
    if not title:
        return []
    try:
        records = crossref.search(Query(title, limit=3))
    except http.SourceError:
        return []
    return [{"doi": r["identifiers"]["doi"], "title": r["title"], "year": r.get("year"),
             "container_title": r.get("container_title"),
             "first_author": r["authors"][0]["family"] if r["authors"] else None} for r in records]  # fmt: skip


def import_pdf(conn: sqlite3.Connection, home: Path, data: bytes, filename: str | None = None, client: str = "api") -> dict:
    """Range un PDF puis cherche sa notice : DOI des métadonnées ou des premières pages, **vérifié** en comparant
    le titre de la notice trouvée au texte du PDF (un DOI cité en première page n'est pas celui de l'article).

    Statuts : `attached` (notice trouvée ou créée), `duplicate` (PDF déjà rattaché), `needs_metadata`
    (PDF rangé sans notice : `candidates` propose des notices Crossref, à confirmer avec `attach_file`)."""
    with db.transaction(conn):
        sha, _ = library.store_pdf(conn, home, data, filename)
    owners = [r[0] for r in conn.execute("SELECT work_id FROM work_files WHERE sha256 = ?", (sha,))]
    if owners:
        return {"status": "duplicate", "sha256": sha, "work": core.get_work(conn, owners[0])}
    path = library.pdf_path(home, sha)
    pages = library.pdf_text(path, max_pages=2)
    metadata = library.pdf_metadata(path)
    dois = library.find_dois(data, metadata, pages)
    head = " ".join(pages)
    warnings = []
    for doi in dois[:5]:
        if work_id := core.find_by_identifiers(conn, {"doi": doi}):
            work = core.get_work(conn, work_id)
            if not library.title_matches(work["title"], head):
                continue
        else:
            try:
                rec, fetch_warnings = core.fetch("doi", doi)
            except (core.NotFound, http.SourceError) as exc:
                warnings.append(str(exc))
                continue
            if not library.title_matches(rec["title"], head):
                continue
            rec["identifiers"].setdefault("doi", doi)
            work_id, _ = core.add_record(conn, rec, client)
            warnings += fetch_warnings
        _attach(conn, home, work_id, sha, client)
        return {"status": "attached", "sha256": sha, "doi": doi, "warnings": warnings, "work": core.get_work(conn, work_id)}
    title = library.guess_title(metadata, pages[0] if pages else "")
    return {"status": "needs_metadata", "sha256": sha, "dois_found": dois, "title_guess": title,
            "candidates": _candidates(title), "warnings": warnings}  # fmt: skip


def attach_file(conn: sqlite3.Connection, home: Path, sha: str, identifier: str | None = None,
                work_ref: str | None = None, client: str = "api") -> dict:  # fmt: skip
    """Saisie assistée : rattache un PDF rangé à une notice existante (`work_ref`) ou à un identifiant (ajouté au besoin)."""
    if not conn.execute("SELECT 1 FROM files WHERE sha256 = ?", (sha,)).fetchone():
        raise core.NotFound(f"Aucun fichier {sha}")
    if work_ref:
        work_id = core.resolve(conn, work_ref)
    elif identifier:
        work_id = core.add_by_identifier(conn, identifier, client)[0]["id"]
    else:
        raise ValueError("Indiquer une notice ou un identifiant")
    _attach(conn, home, work_id, sha, client)
    return core.get_work(conn, work_id)


def import_bibliography(conn: sqlite3.Connection, text: str, fmt: str | None = None, client: str = "api") -> dict:
    """Importe un fichier BibTeX, RIS, MEDLINE ou CSL-JSON, tel quel (sans requête en ligne). Les notices déjà
    présentes (même DOI, PMID…) sont complétées ; les doublons probables sans identifiant commun sont signalés."""
    fmt = fmt or formats.detect(text)
    records = formats.parse(text, fmt)
    report = {"format": fmt, "read": len(records), "created": [], "completed": [], "errors": []}
    before = conn.execute("SELECT count(*) FROM dedup_candidates WHERE status = 'open'").fetchone()[0]
    for rec in records:
        try:
            work_id, created = core.add_record(conn, rec, client)
        except (ValueError, sqlite3.Error) as exc:
            report["errors"].append({"title": rec.get("title"), "error": str(exc)})
            continue
        key = conn.execute("SELECT citekey FROM works WHERE id = ?", (work_id,)).fetchone()[0]
        report["created" if created else "completed"].append(key)
    report["suspected_duplicates"] = conn.execute("SELECT count(*) FROM dedup_candidates WHERE status = 'open'").fetchone()[0] - before
    return report


def scan_inbox(conn: sqlite3.Connection, home: Path, min_age: float = 2.0) -> list[dict]:
    """Importe les PDF déposés dans inbox/. Un fichier importé est retiré de la boîte (il est rangé dans library/) ;
    un fichier refusé est déplacé dans inbox/refusés/. `min_age` (s) évite de lire un fichier en cours de copie."""
    inbox = home / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(inbox.glob("*")):
        if not path.is_file() or path.suffix.lower() != ".pdf" or time.time() - path.stat().st_mtime < min_age:
            continue
        try:
            result = import_pdf(conn, home, path.read_bytes(), path.name, "inbox")
            path.unlink()
        except library.FileError as exc:
            refused = inbox / "refusés"
            refused.mkdir(exist_ok=True)
            shutil.move(path, refused / path.name)
            result = {"status": "refused", "error": str(exc)}
        log.info("Boîte d'entrée : %s → %s", path.name, result["status"])
        results.append({"file": path.name, **result})
    return results
