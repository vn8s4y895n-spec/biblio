"""Cœur métier : import par identifiant, dédoublonnage, lecture des notices.

Toutes les interfaces (API REST, CLI, web, MCP) passent par ces fonctions.
"""

import itertools
import json
import re
import sqlite3
import string
import unicodedata
from collections.abc import Callable
from pathlib import Path

from . import db, journals
from .sources import crossref, pubmed
from .sources.http import SourceError

FIELDS = (
    "title", "abstract", "csl_type", "study_type", "container_title", "container_abbrev",
    "issn_print", "issn_e", "nlmid", "year", "issued", "volume", "issue", "pages", "language", "license",
)  # fmt: skip
CLIENTS = {"api", "cli", "web", "mcp", "inbox", "worker", "package"}

STOPWORDS = set(
    "a an and are as at by de des du en et for from in into is la le les of on or the to un une vs with".split()
)


def setup(conn: sqlite3.Connection, home: Path) -> None:
    """Prépare la base : migrations, vue de statut des revues, liste des revues livrée."""
    db.migrate(conn)
    journals.install(conn, home)


class InvalidIdentifier(ValueError):
    pass


class NotFound(LookupError):
    pass


# --- Identifiants -----------------------------------------------------------------


def parse_identifier(text: str) -> tuple[str, str]:
    """Reconnaît un DOI ou un PMID, nu ou sous forme d'URL. Renvoie (schéma, valeur normalisée)."""
    t = text.strip()
    if m := re.search(r"10\.\d{4,9}/\S+", t):
        return "doi", m.group(0).rstrip(".,;").lower()
    if m := re.fullmatch(r"(?:pmid:?\s*|https?://pubmed\.ncbi\.nlm\.nih\.gov/)?(\d{1,9})/?", t, re.I):
        return "pmid", m.group(1)
    raise InvalidIdentifier(f"Identifiant non reconnu : « {text} » (attendu : DOI ou PMID)")


def find_by_identifiers(conn: sqlite3.Connection, identifiers: dict[str, str]) -> int | None:
    for scheme, value in identifiers.items():
        row = conn.execute("SELECT work_id FROM identifiers WHERE scheme = ? AND value = ?", (scheme, value)).fetchone()
        if row:
            return row[0]
    return None


# --- Clés et empreintes -------------------------------------------------------------


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def normalize(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _ascii(text or "").lower()).strip()


def fingerprint(rec: dict) -> str:
    """Titre normalisé | année | premier auteur : sert à repérer les doublons sans identifiant commun."""
    first = rec["authors"][0]["family"] if rec["authors"] else ""
    return f"{normalize(rec['title'])}|{rec.get('year') or ''}|{normalize(first)}"


def citekey_base(rec: dict) -> str:
    """auteurAnnéeMot, ex. harris2018effect."""
    family = re.sub(r"[^a-z]", "", _ascii(rec["authors"][0]["family"]).lower()) if rec["authors"] else ""
    words = (w for w in normalize(rec["title"]).split() if w not in STOPWORDS and len(w) > 2 and not w.isdigit())
    return f"{family or 'anon'}{rec.get('year') or 'nd'}{next(words, '')}"


def unique_citekey(conn: sqlite3.Connection, base: str) -> str:
    suffixes = ("".join(p) for n in itertools.count(1) for p in itertools.product(string.ascii_lowercase, repeat=n))
    for key in itertools.chain([base], (base + s for s in suffixes)):
        if not conn.execute("SELECT 1 FROM works WHERE citekey = ?", (key,)).fetchone():
            return key
    raise AssertionError("unreachable")


# --- Récupération en ligne --------------------------------------------------------


def merge(primary: dict, secondary: dict) -> dict:
    """Complète `primary` avec les champs vides trouvés dans `secondary`."""
    out = dict(primary)
    for field in FIELDS:
        if not out.get(field) and secondary.get(field):
            out[field] = secondary[field]
    out["identifiers"] = {**secondary["identifiers"], **primary["identifiers"]}
    out["authors"] = [dict(a) for a in primary["authors"] or secondary["authors"]]
    if primary["authors"] and len(primary["authors"]) == len(secondary["authors"]):
        for mine, other in zip(out["authors"], secondary["authors"]):
            if not mine.get("orcid") and normalize(mine["family"]) == normalize(other["family"]):
                mine["orcid"] = other.get("orcid")
    seen = {(s["scheme"], s["term"]) for s in primary["subjects"]}
    out["subjects"] = primary["subjects"] + [s for s in secondary["subjects"] if (s["scheme"], s["term"]) not in seen]
    return out


def _attempt(warnings: list[str], fn: Callable, *args):
    try:
        return fn(*args)
    except SourceError as exc:
        warnings.append(str(exc))
        return None


def fetch(scheme: str, value: str) -> tuple[dict, list[str]]:
    """Notice complète depuis PubMed (prioritaire : MeSH, résumé structuré) complétée par Crossref.

    Une source injoignable n'empêche pas l'import si l'autre répond : l'incident est renvoyé en avertissement.
    """
    warnings: list[str] = []
    if scheme == "doi":
        cr = _attempt(warnings, crossref.fetch, value)
        pmid = _attempt(warnings, pubmed.find_pmid_by_doi, value)
        pm = _attempt(warnings, pubmed.fetch, pmid) if pmid else None
    else:
        pm = _attempt(warnings, pubmed.fetch, value)
        doi = pm["identifiers"].get("doi") if pm else None
        cr = _attempt(warnings, crossref.fetch, doi) if doi else None
    records = [r for r in (pm, cr) if r]
    if not records:
        if warnings:
            raise SourceError(" ; ".join(warnings))
        raise NotFound(f"Aucune notice trouvée pour {scheme} {value}")
    rec = merge(*records) if len(records) == 2 else records[0]
    if not rec.get("title"):
        raise NotFound(f"Notice sans titre pour {scheme} {value}")
    return rec, warnings


# --- Écriture ------------------------------------------------------------------------


def _log(conn: sqlite3.Connection, work_id: int, client: str, op: str, field=None, old=None, new=None) -> None:
    conn.execute(
        "INSERT INTO changes (work_id, client, op, field, old_json, new_json) VALUES (?, ?, ?, ?, ?, ?)",
        (work_id, client, op, field,
         None if old is None else json.dumps(old, ensure_ascii=False),
         None if new is None else json.dumps(new, ensure_ascii=False)),
    )  # fmt: skip


def _insert_authors(conn: sqlite3.Connection, work_id: int, authors: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO work_authors (work_id, position, family, given, orcid) VALUES (?, ?, ?, ?, ?)",
        [(work_id, i, a["family"], a.get("given"), a.get("orcid")) for i, a in enumerate(authors)],
    )


def _insert_subjects(conn: sqlite3.Connection, work_id: int, subjects: list[dict]) -> None:
    for s in subjects:
        conn.execute("INSERT OR IGNORE INTO subjects (scheme, term, ui) VALUES (?, ?, ?)", (s["scheme"], s["term"], s.get("ui")))
        conn.execute(
            "INSERT OR IGNORE INTO work_subjects (work_id, subject_id, major)"
            " SELECT ?, id, ? FROM subjects WHERE scheme = ? AND term = ?",
            (work_id, int(s.get("major", False)), s["scheme"], s["term"]),
        )


def _insert(conn: sqlite3.Connection, rec: dict, client: str) -> int:
    cols = ["citekey", "fingerprint", *FIELDS]
    values = [unique_citekey(conn, citekey_base(rec)), fingerprint(rec), *(rec.get(f) for f in FIELDS)]
    work_id = conn.execute(
        f"INSERT INTO works ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values
    ).lastrowid
    conn.executemany(
        "INSERT INTO identifiers (scheme, value, work_id) VALUES (?, ?, ?)",
        [(scheme, value, work_id) for scheme, value in rec["identifiers"].items()],
    )
    _insert_authors(conn, work_id, rec["authors"])
    _insert_subjects(conn, work_id, rec["subjects"])
    _log(conn, work_id, client, "create", new=rec)
    return work_id


def _complete(conn: sqlite3.Connection, work_id: int, rec: dict, client: str) -> None:
    """Complète une notice existante : champs vides, identifiants manquants, auteurs et sujets absents."""
    row = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
    changed = False
    for field in FIELDS:
        if row[field] in (None, "") and rec.get(field):
            conn.execute(f"UPDATE works SET {field} = ? WHERE id = ?", (rec[field], work_id))
            _log(conn, work_id, client, "update", field, None, rec[field])
            changed = True
    for scheme, value in rec["identifiers"].items():
        if conn.execute(
            "INSERT OR IGNORE INTO identifiers (scheme, value, work_id) VALUES (?, ?, ?)", (scheme, value, work_id)
        ).rowcount:
            _log(conn, work_id, client, "update", f"identifiers.{scheme}", None, value)
            changed = True
    if rec["authors"] and not conn.execute("SELECT 1 FROM work_authors WHERE work_id = ?", (work_id,)).fetchone():
        _insert_authors(conn, work_id, rec["authors"])
        _log(conn, work_id, client, "update", "authors", None, rec["authors"])
        changed = True
    if rec["subjects"] and not conn.execute("SELECT 1 FROM work_subjects WHERE work_id = ?", (work_id,)).fetchone():
        _insert_subjects(conn, work_id, rec["subjects"])
        _log(conn, work_id, client, "update", "subjects", None, rec["subjects"])
        changed = True
    if changed:
        conn.execute("UPDATE works SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (work_id,))


def add_by_identifier(conn: sqlite3.Connection, text: str, client: str = "api") -> tuple[dict, bool, list[str]]:
    """Importe un article par DOI ou PMID. Renvoie (notice, créée ?, avertissements).

    Si l'article est déjà présent (sous n'importe lequel de ses identifiants), aucune notice n'est créée :
    la notice existante est complétée avec les informations nouvelles.
    """
    scheme, value = parse_identifier(text)
    if work_id := find_by_identifiers(conn, {scheme: value}):
        return get_work(conn, work_id), False, []
    rec, warnings = fetch(scheme, value)
    rec["identifiers"].setdefault(scheme, value)
    try:
        with db.transaction(conn):
            if work_id := find_by_identifiers(conn, rec["identifiers"]):
                _complete(conn, work_id, rec, client)
                created = False
            else:
                work_id, created = _insert(conn, rec, client), True
    except sqlite3.IntegrityError:
        # Import concurrent du même article : l'autre l'a inséré entre-temps.
        work_id, created = find_by_identifiers(conn, rec["identifiers"]), False
        if work_id is None:
            raise
    return get_work(conn, work_id), created, warnings


# --- Lecture -------------------------------------------------------------------------


def resolve(conn: sqlite3.Connection, ref: str | int) -> int:
    """Trouve une notice par id numérique, clé de citation, DOI ou PMID."""
    ref = str(ref).strip()
    row = conn.execute("SELECT id FROM works WHERE citekey = ? OR CAST(id AS TEXT) = ?", (ref, ref)).fetchone()
    if row:
        return row[0]
    try:
        work_id = find_by_identifiers(conn, dict([parse_identifier(ref)]))
    except InvalidIdentifier:
        work_id = None
    if work_id is None:
        raise NotFound(f"Aucune notice « {ref} » dans la bibliothèque")
    return work_id


def get_work(conn: sqlite3.Connection, ref: str | int) -> dict:
    work_id = resolve(conn, ref)
    work = dict(conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone())
    work["identifiers"] = dict(conn.execute("SELECT scheme, value FROM identifiers WHERE work_id = ? ORDER BY scheme", (work_id,)).fetchall())
    work["authors"] = [
        dict(r) for r in conn.execute("SELECT family, given, orcid FROM work_authors WHERE work_id = ? ORDER BY position", (work_id,))
    ]
    work["subjects"] = [
        {**dict(r), "major": bool(r["major"])}
        for r in conn.execute(
            "SELECT s.scheme, s.term, s.ui, ws.major FROM work_subjects ws JOIN subjects s ON s.id = ws.subject_id"
            " WHERE ws.work_id = ? ORDER BY s.scheme, s.term",
            (work_id,),
        )
    ]
    work["journal_status"] = journals.status_of_work(conn, work_id)
    return work


def list_works(conn: sqlite3.Connection, limit: int = 50, offset: int = 0, journal_status: list[str] | None = None) -> dict:
    """Notices les plus récentes d'abord ; `journal_status` filtre sur le statut de revue (voir journals.STATUSES)."""
    if unknown := set(journal_status or ()) - set(journals.STATUSES):
        raise ValueError(f"Statut de revue inconnu : {', '.join(sorted(unknown))}")
    where, params = "", []
    if journal_status:
        where = f"WHERE s.status IN ({', '.join('?' * len(journal_status))})"
        params = list(journal_status)
    base = f"FROM works w JOIN work_journal_status s ON s.work_id = w.id {where}"
    rows = conn.execute(
        f"""SELECT w.id, w.citekey, w.title, w.year, w.container_title, w.study_type, s.status AS journal_status,
                   (SELECT family FROM work_authors WHERE work_id = w.id AND position = 0) AS first_author,
                   (SELECT value FROM identifiers WHERE work_id = w.id AND scheme = 'doi') AS doi
            {base} ORDER BY w.created_at DESC, w.id DESC LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    )
    total = conn.execute(f"SELECT count(*) {base}", params).fetchone()[0]
    return {"total": total, "items": [dict(r) for r in rows]}


def get_changes(conn: sqlite3.Connection, ref: str | int) -> list[dict]:
    work_id = resolve(conn, ref)
    rows = conn.execute("SELECT id, client, at, op, field, old_json, new_json FROM changes WHERE work_id = ? ORDER BY id", (work_id,))
    return [
        {"id": r["id"], "client": r["client"], "at": r["at"], "op": r["op"], "field": r["field"],
         "old": json.loads(r["old_json"]) if r["old_json"] else None,
         "new": json.loads(r["new_json"]) if r["new_json"] else None}
        for r in rows
    ]  # fmt: skip
