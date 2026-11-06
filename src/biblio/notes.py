"""Notes par article : fichiers notes/<citekey>.md, compatibles Obsidian (liens [[clé]]), indexés dans la base.

Le fichier est la source de vérité. Une note modifiée hors de Biblio (dans Obsidian, un éditeur) est réindexée
par `sync()`, appelée régulièrement par le serveur.
"""

import hashlib
import re
import sqlite3
from pathlib import Path

from . import db, library

LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
FRONT_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.S)


def folder(home: Path) -> Path:
    return home / "notes"


def body_of(text: str) -> str:
    """Texte de la note sans l'en-tête YAML."""
    return FRONT_RE.sub("", text, count=1).lstrip("\n")


def _front(work: sqlite3.Row) -> str:
    doi = work["doi"] or ""
    title = (work["title"] or "").replace('"', "'")
    return f'---\ncitekey: {work["citekey"]}\ntitle: "{title}"\nyear: {work["year"] or ""}\ndoi: {doi}\n---\n\n'


def _work(conn: sqlite3.Connection, work_id: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT w.id, w.citekey, w.title, w.year,"
        " (SELECT value FROM identifiers WHERE work_id = w.id AND scheme = 'doi') AS doi FROM works w WHERE w.id = ?",
        (work_id,),
    ).fetchone()


def _index(conn: sqlite3.Connection, work_id: int, name: str, text: str) -> None:
    body = body_of(text)
    library._replace(conn, work_id, "note", [(None, p) for p in library.chunk(body)])
    conn.execute("DELETE FROM note_links WHERE work_id = ?", (work_id,))
    links = {m.strip() for m in LINK_RE.findall(body)}
    conn.executemany("INSERT INTO note_links (work_id, target_citekey) VALUES (?, ?)", [(work_id, k) for k in links])
    conn.execute(
        "INSERT INTO notes (work_id, path, content_sha) VALUES (?, ?, ?) ON CONFLICT (work_id) DO UPDATE"
        " SET path = excluded.path, content_sha = excluded.content_sha, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
        (work_id, name, hashlib.sha256(text.encode()).hexdigest()),
    )


def _path(conn: sqlite3.Connection, home: Path, work_id: int) -> Path:
    row = conn.execute("SELECT path FROM notes WHERE work_id = ?", (work_id,)).fetchone()
    return folder(home) / (row[0] if row else f"{_work(conn, work_id)['citekey']}.md")


def read(conn: sqlite3.Connection, home: Path, work_id: int) -> dict:
    work = _work(conn, work_id)
    path = _path(conn, home, work_id)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    body = body_of(text).rstrip()
    backlinks = [r[0] for r in conn.execute(
        "SELECT w.citekey FROM note_links l JOIN works w ON w.id = l.work_id WHERE l.target_citekey = ? ORDER BY w.citekey",
        (work["citekey"],))]  # fmt: skip
    return {"citekey": work["citekey"], "path": str(path), "exists": path.exists(), "text": body,
            "links": sorted({m.strip() for m in LINK_RE.findall(body)}), "backlinks": backlinks}  # fmt: skip


def write(conn: sqlite3.Connection, home: Path, work_id: int, body: str) -> dict:
    """Enregistre le texte de la note (l'en-tête YAML est géré par Biblio). Un texte vide supprime la note."""
    path = _path(conn, home, work_id)
    if not body.strip():
        path.unlink(missing_ok=True)
        with db.transaction(conn):
            _forget(conn, work_id)
        return read(conn, home, work_id)
    text = _front(_work(conn, work_id)) + body.strip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    with db.transaction(conn):
        _index(conn, work_id, path.name, text)
    return read(conn, home, work_id)


def _forget(conn: sqlite3.Connection, work_id: int) -> None:
    conn.execute("DELETE FROM notes WHERE work_id = ?", (work_id,))
    conn.execute("DELETE FROM note_links WHERE work_id = ?", (work_id,))
    conn.execute("DELETE FROM passages WHERE work_id = ? AND origin = 'note'", (work_id,))


def sync(conn: sqlite3.Connection, home: Path) -> dict:
    """Réindexe les notes modifiées, ajoutées ou supprimées hors de Biblio. Un fichier dont le nom n'est la clé
    d'aucune notice est ignoré (et signalé)."""
    report = {"updated": [], "removed": [], "unknown": []}
    known = {r["path"]: (r["work_id"], r["content_sha"]) for r in conn.execute("SELECT * FROM notes")}
    present = set()
    for path in sorted(folder(home).glob("*.md")):
        present.add(path.name)
        text = path.read_text(encoding="utf-8", errors="replace")
        sha = hashlib.sha256(text.encode()).hexdigest()
        if path.name in known:
            work_id, old_sha = known[path.name]
            if old_sha == sha:
                continue
        else:
            row = conn.execute("SELECT id FROM works WHERE citekey = ?", (path.stem,)).fetchone()
            if row is None:
                report["unknown"].append(path.name)
                continue
            work_id = row[0]
        with db.transaction(conn):
            _index(conn, work_id, path.name, text)
        report["updated"].append(path.stem)
    for name, (work_id, _) in known.items():
        if name not in present:
            with db.transaction(conn):
                _forget(conn, work_id)
            report["removed"].append(Path(name).stem)
    return report


def absorb(conn: sqlite3.Connection, home: Path, keep_id: int, drop_id: int) -> None:
    """Fusion de notices : la note de `drop` est ajoutée à celle de `keep` (ou devient la sienne).
    S'exécute dans la transaction de la fusion."""
    if not conn.execute("SELECT 1 FROM notes WHERE work_id = ?", (drop_id,)).fetchone():
        return
    drop_path = _path(conn, home, drop_id)
    dropped = body_of(drop_path.read_text(encoding="utf-8")) if drop_path.exists() else ""
    keep_path = _path(conn, home, keep_id)
    kept = body_of(keep_path.read_text(encoding="utf-8")) if keep_path.exists() else ""
    drop_key = _work(conn, drop_id)["citekey"]
    body = f"{kept.rstrip()}\n\n---\n*Note reprise de [[{drop_key}]] lors d'une fusion de doublons.*\n\n{dropped}" if kept else dropped
    text = _front(_work(conn, keep_id)) + body.strip() + "\n"
    keep_path.parent.mkdir(parents=True, exist_ok=True)
    keep_path.write_text(text, encoding="utf-8")
    drop_path.unlink(missing_ok=True)
    _forget(conn, drop_id)
    _index(conn, keep_id, keep_path.name, text)
