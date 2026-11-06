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


def _pdf_sources(doi: str | None, pmcid: str | None):
    """Sources essayées dans l'ordre, interrogées seulement si les précédentes ont échoué : PMC, la page de
    l'éditeur (lien PDF qu'il déclare, comme le fait Zotero), puis OpenAlex et Unpaywall."""
    if pmcid:
        yield from search.open_access_candidates(pmcid=pmcid)
    if doi:
        yield {"pdf_url": None, "landing_url": f"https://doi.org/{doi}", "license": None, "version": None,
               "host_type": "publisher", "source": "publisher"}  # fmt: skip
        yield from search.open_access_candidates(doi=doi)


def fetch_open_pdf(conn: sqlite3.Connection, home: Path, work_id: int, client: str = "api") -> dict | None:
    """Télécharge et rattache un PDF librement accessible de l'article, s'il n'en a pas déjà un.
    Un site qui refuse (HTTP 403…) n'est pas contourné : on passe à la source suivante."""
    if library.files_of(conn, work_id):
        return None
    ids = core.get_work(conn, work_id)["identifiers"]
    failures, tried = [], set()
    for found in _pdf_sources(ids.get("doi"), ids.get("pmcid")):
        if found["pdf_url"] in tried:
            continue
        try:
            if found["pdf_url"]:
                data = http.download(found["pdf_url"], library.MAX_PDF)
            else:
                data, found["pdf_url"] = http.download_from_page(found["landing_url"], library.MAX_PDF)
            tried.add(found["pdf_url"])
            with db.transaction(conn):
                sha, _ = library.store_pdf(conn, home, data, found["pdf_url"].rsplit("/", 1)[-1][:200])
        except (http.SourceError, library.FileError) as exc:
            tried.add(found["pdf_url"])
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


def import_pdf(conn: sqlite3.Connection, home: Path, data: bytes, filename: str | None = None, client: str = "api",
               keep_unmatched: bool = True) -> dict:  # fmt: skip
    """Range un PDF puis cherche sa notice : DOI des métadonnées ou des premières pages, **vérifié** en comparant
    le titre de la notice trouvée au texte du PDF (un DOI cité en première page n'est pas celui de l'article).

    Statuts : `attached` (notice trouvée ou créée), `duplicate` (PDF déjà rattaché), `needs_metadata`
    (PDF rangé sans notice : `candidates` propose des notices Crossref, à confirmer avec `attach_file`).
    `keep_unmatched=False` : un PDF non reconnu n'est pas gardé (statut `ignored`), pour un dossier surveillé
    où arrivent aussi des PDF qui ne sont pas des articles."""
    with db.transaction(conn):
        sha, new = library.store_pdf(conn, home, data, filename)
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
    if not keep_unmatched:
        if new:
            with db.transaction(conn):
                conn.execute("DELETE FROM files WHERE sha256 = ?", (sha,))
            path.unlink(missing_ok=True)
        return {"status": "ignored", "sha256": sha, "dois_found": dois, "warnings": warnings}
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


def scan_downloads(conn: sqlite3.Connection, folder: Path, home: Path, min_age: float = 2.0) -> list[dict]:
    """Importe les PDF d'articles arrivés dans un dossier surveillé (Téléchargements) depuis l'activation de la
    surveillance. Les fichiers ne sont ni déplacés ni supprimés ; un PDF qui n'est pas reconnu comme un article
    (facture, formulaire…) n'est pas gardé. Chaque fichier n'est examiné qu'une fois."""
    since = db.get_meta(conn, "downloads.since")
    if since is None:  # premier passage : on ne reprend pas l'historique du dossier
        since = str(time.time())
        db.set_meta(conn, "downloads.since", since)
    results = []
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        stat = path.stat()
        if stat.st_mtime <= float(since) or time.time() - stat.st_mtime < min_age:
            continue
        key = (str(path), stat.st_mtime, stat.st_size)
        if conn.execute("SELECT 1 FROM watched_files WHERE path = ? AND mtime = ? AND size = ?", key).fetchone():
            continue
        try:
            result = import_pdf(conn, home, path.read_bytes(), path.name, "inbox", keep_unmatched=False)
        except library.FileError as exc:
            result = {"status": "refused", "error": str(exc)}
        conn.execute("INSERT OR REPLACE INTO watched_files (path, mtime, size, status) VALUES (?, ?, ?, ?)", (*key, result["status"]))
        log.info("Téléchargements : %s → %s", path.name, result["status"])
        results.append({"file": path.name, **result})
    return results
