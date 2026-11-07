"""Paquets `.biblio` : envoyer des articles d'un Biblio à un autre, avec tout leur contexte.

Un paquet est un fichier ZIP :
    manifest.json   format, version, date, nombre d'articles, titre
    works.json      notices (métadonnées, identifiants, auteurs, MeSH, résumé), note, tags et collections,
                    questions d'origine, empreintes des PDF
    searches.json   recherches enregistrées jointes (facultatif)
    files/<sha256>.pdf
Le statut de lecture et la note étoilée restent personnels : ils ne sont pas envoyés. À l'import, un article déjà
présent est complété (jamais dupliqué), une note différente est ajoutée à la suite de la sienne.
"""

import hashlib
import io
import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, core, db, library, notes, organize

FORMAT = "biblio-package"
VERSION = 1
MAX_PACKAGE = 2_000_000_000  # taille décompressée maximale acceptée


class PackageError(ValueError):
    pass


def _entry(conn: sqlite3.Connection, home: Path, work_id: int) -> dict:
    w = core.get_work(conn, work_id)
    fields = {f: w[f] for f in core.FIELDS}
    return {
        **fields, "citekey": w["citekey"], "identifiers": w["identifiers"], "authors": w["authors"],
        "subjects": w["subjects"], "note": notes.read(conn, home, work_id)["text"],
        "labels": [{"kind": label["kind"], "name": label["name"]} for label in w["labels"]],
        "questions": [q["question"] for q in w["questions"]],
        "files": [f["sha256"] for f in library.files_of(conn, work_id)],
    }  # fmt: skip


def create(conn: sqlite3.Connection, home: Path, refs: list, title: str | None = None,
           search_ids: list[int] | None = None) -> bytes:  # fmt: skip
    entries = [_entry(conn, home, core.resolve(conn, ref)) for ref in dict.fromkeys(refs)]
    searches = [organize.get_search(conn, i) for i in search_ids or []]
    if not entries and not searches:
        raise PackageError("Aucun article à mettre dans le paquet")
    manifest = {"format": FORMAT, "version": VERSION, "app_version": __version__, "title": title,
                "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "count": len(entries)}  # fmt: skip
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("works.json", json.dumps(entries, ensure_ascii=False, indent=2))
        if searches:
            z.writestr("searches.json", json.dumps(searches, ensure_ascii=False, indent=2))
        for sha in {sha for e in entries for sha in e["files"]}:
            z.write(library.pdf_path(home, sha), f"files/{sha}.pdf", compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue()


def _open(data: bytes) -> tuple[zipfile.ZipFile, dict, list[dict], list[dict]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        if sum(i.file_size for i in z.infolist()) > MAX_PACKAGE:
            raise PackageError("Paquet trop volumineux")
        manifest = json.loads(z.read("manifest.json"))
        works = json.loads(z.read("works.json"))
        searches = json.loads(z.read("searches.json")) if "searches.json" in z.namelist() else []
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise PackageError(f"Ce fichier n'est pas un paquet .biblio lisible ({exc})") from exc
    if manifest.get("format") != FORMAT:
        raise PackageError("Ce fichier n'est pas un paquet .biblio")
    if manifest.get("version", 0) > VERSION:
        raise PackageError("Paquet créé par une version plus récente de Biblio : mettre Biblio à jour")
    if not isinstance(works, list) or not all(isinstance(w, dict) and w.get("title") for w in works):
        raise PackageError("Paquet incomplet (notices sans titre)")
    return z, manifest, works, searches


def _record(entry: dict) -> dict:
    rec = {f: entry.get(f) for f in core.FIELDS}
    authors = [{"family": a["family"], "given": a.get("given"), "orcid": a.get("orcid")}
               for a in entry.get("authors", []) if a.get("family")]  # fmt: skip
    subjects = [s for s in entry.get("subjects", []) if s.get("scheme") and s.get("term")]
    identifiers = {k: str(v) for k, v in (entry.get("identifiers") or {}).items() if v}
    return {**rec, "authors": authors, "subjects": subjects, "identifiers": identifiers}


def _existing(conn: sqlite3.Connection, entry: dict) -> int | None:
    rec = _record(entry)
    if rec["identifiers"]:
        return core.find_by_identifiers(conn, rec["identifiers"])
    row = conn.execute("SELECT id FROM works WHERE fingerprint = ?", (core.fingerprint(rec),)).fetchone()
    return row[0] if row else None


def preview(conn: sqlite3.Connection, data: bytes) -> dict:
    """Contenu du paquet, et pour chaque article : nouveau, ou déjà dans la bibliothèque."""
    z, manifest, works, searches = _open(data)
    names = set(z.namelist())
    items = []
    for e in works:
        existing = _existing(conn, e)
        items.append({
            "title": e["title"], "year": e.get("year"), "container_title": e.get("container_title"),
            "first_author": (e.get("authors") or [{}])[0].get("family"),
            "status": "existing" if existing else "new",
            "existing": conn.execute("SELECT citekey FROM works WHERE id = ?", (existing,)).fetchone()[0] if existing else None,
            "pdf": any(f"files/{sha}.pdf" in names for sha in e.get("files", [])), "note": bool((e.get("note") or "").strip()),
            "labels": e.get("labels", []), "questions": e.get("questions", []),
        })  # fmt: skip
    return {"manifest": manifest, "works": items, "searches": [{"question": s.get("question")} for s in searches]}


def _merge_note(conn: sqlite3.Connection, home: Path, work_id: int, text: str, when: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    mine = notes.read(conn, home, work_id)["text"].strip()
    if text in mine:
        return False
    merged = f"{mine}\n\n---\n\n*Note reçue dans un paquet .biblio ({when[:10]}) :*\n\n{text}" if mine else text
    notes.write(conn, home, work_id, merged)
    return True


def import_package(conn: sqlite3.Connection, home: Path, data: bytes, client: str = "package") -> dict:
    z, manifest, works, searches = _open(data)
    when = manifest.get("created_at") or ""
    report = {"created": [], "completed": [], "pdfs": 0, "notes": 0, "searches": 0, "errors": []}
    for entry in works:
        try:
            work_id, created = core.add_record(conn, _record(entry), client)
        except ValueError as exc:
            report["errors"].append(f"{entry.get('title', '?')[:80]} : {exc}")
            continue
        key = conn.execute("SELECT citekey FROM works WHERE id = ?", (work_id,)).fetchone()[0]
        report["created" if created else "completed"].append(key)
        for sha in entry.get("files", []):
            name = f"files/{sha}.pdf"
            if name not in z.namelist():
                continue
            pdf = z.read(name)
            if hashlib.sha256(pdf).hexdigest() != sha:
                report["errors"].append(f"{key} : PDF altéré, ignoré")
                continue
            with db.transaction(conn):
                stored, _ = library.store_pdf(conn, home, pdf, f"{key}.pdf")
                report["pdfs"] += library.attach(conn, home, work_id, stored)
        report["notes"] += _merge_note(conn, home, work_id, entry.get("note", ""), when)
        for label in entry.get("labels", []):
            if label.get("kind") in organize.KINDS and (label.get("name") or "").strip():
                organize.set_label(conn, organize.create_label(conn, label["kind"], label["name"])["id"], [work_id], True, client)
        for question in entry.get("questions", []):
            organize.add_question(conn, work_id, question)
    for s in searches:
        if s.get("question"):
            organize.save_search(conn, {**s, "origin": "paquet"})
            report["searches"] += 1
    return report
