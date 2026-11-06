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

from . import db, journals, library
from .sources import arxiv, crossref, http, pubmed
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
    """Prépare la base : migrations, vue de statut des revues, liste des revues livrée, index plein texte."""
    db.migrate(conn)
    journals.install(conn, home)
    with db.transaction(conn):
        library.index_missing(conn)
        library.reindex_if_outdated(conn, home)


class InvalidIdentifier(ValueError):
    pass


class NotFound(LookupError):
    pass


# --- Identifiants -----------------------------------------------------------------


def parse_identifier(text: str) -> tuple[str, str]:
    """Reconnaît un DOI, PMID, PMCID ou identifiant arXiv, nu ou sous forme d'URL (doi.org, PubMed, PMC,
    Europe PMC, arXiv, bioRxiv, medRxiv). Toute autre URL est renvoyée telle quelle (schéma « url »), pour être
    résolue en lisant la page. Renvoie (schéma, valeur normalisée)."""
    t = text.strip()
    if m := re.search(r"(?:arxiv:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?", t, re.I):
        return "arxiv", m.group(1)
    if m := re.search(r"10\.\d{4,9}/[^\s?#]+", t):
        doi = m.group(0).rstrip(".,;").lower()
        if doi.startswith("10.1101/"):  # bioRxiv / medRxiv : l'URL ajoute la version et le format
            doi = re.sub(r"(v\d+)?(\.full(-text)?|\.abstract|\.full\.pdf|\.pdf)?/?$", "", doi)
        return "doi", doi
    if m := re.search(r"\bPMC\d{4,9}\b", t, re.I):
        return "pmcid", m.group(0).upper()
    if m := re.fullmatch(
        r"(?:pmid:?\s*|https?://pubmed\.ncbi\.nlm\.nih\.gov/|https?://europepmc\.org/(?:abstract|article)/MED/)?(\d{1,9})/?",
        t, re.I,
    ):  # fmt: skip
        return "pmid", m.group(1)
    if re.match(r"https?://[^\s/]+\.[^\s/]+", t):
        return "url", t
    raise InvalidIdentifier(f"Identifiant non reconnu : « {text} » (attendu : DOI, PMID, PMCID, arXiv ou URL)")


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
    for field, value in secondary.items():
        if field not in ("identifiers", "authors", "subjects") and out.get(field) in (None, "", False) and value:
            out[field] = value
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


def identifier_from_page(url: str) -> tuple[str, str]:
    """Lit une page web d'article et y cherche son identifiant (balises citation_doi, citation_pmid, DC)."""
    response = http.get(url)
    if response is None:
        raise NotFound(f"Page introuvable : {url}")
    page = response.text[:2_000_000]
    for name in ("citation_doi", "dc.identifier", "prism.doi", "citation_pmid"):
        for value in http.meta_contents(page, name):
            try:
                found = parse_identifier(("pmid:" if name == "citation_pmid" else "") + value)
            except InvalidIdentifier:
                continue
            if found[0] != "url":
                return found
    raise NotFound(f"Aucun DOI ni PMID trouvé sur la page {url}")


def fetch(scheme: str, value: str) -> tuple[dict, list[str]]:
    """Notice complète depuis PubMed (prioritaire : MeSH, résumé structuré) complétée par Crossref.
    arXiv a sa propre source ; un PMCID est converti en PMID ; une URL est d'abord lue pour y trouver l'identifiant.

    Une source injoignable n'empêche pas l'import si l'autre répond : l'incident est renvoyé en avertissement.
    """
    if scheme == "url":
        return fetch(*identifier_from_page(value))
    if scheme == "arxiv":
        rec = arxiv.fetch(value)
        if rec is None:
            raise NotFound(f"Aucune notice arXiv {value}")
        return rec, []
    warnings: list[str] = []
    if scheme == "pmcid":
        pmid = pubmed.find_pmid(f"{value}[pmcid]")
        if pmid is None:
            raise NotFound(f"Aucune notice PubMed pour {value}")
        rec, warnings = fetch("pmid", pmid)
        rec["identifiers"].setdefault("pmcid", value)
        return rec, warnings
    if scheme == "doi":
        cr = _attempt(warnings, crossref.fetch, value)
        pmid = _attempt(warnings, pubmed.find_pmid, f"{value}[doi]")
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
    print_key = fingerprint(rec)
    values = [unique_citekey(conn, citekey_base(rec)), print_key, *(rec.get(f) for f in FIELDS)]
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
    # Même titre, même année, même premier auteur, mais aucun identifiant commun : doublon probable, à confirmer.
    conn.execute(
        "INSERT OR IGNORE INTO dedup_candidates (work_a, work_b, reason)"
        " SELECT min(id, ?), max(id, ?), 'fingerprint' FROM works WHERE fingerprint = ? AND id <> ?",
        (work_id, work_id, print_key, work_id),
    )
    library.index_work(conn, work_id)
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
        library.index_work(conn, work_id)


def add_record(conn: sqlite3.Connection, rec: dict, client: str = "api") -> tuple[int, bool]:
    """Enregistre une notice (format interne). Si l'un de ses identifiants est déjà connu, la notice existante
    est complétée au lieu d'être dupliquée. Renvoie (id, créée ?)."""
    rec = {"authors": [], "subjects": [], "identifiers": {}, **rec}
    if not (rec.get("title") or "").strip():
        raise ValueError("Une notice doit avoir un titre")
    try:
        with db.transaction(conn):
            work_id = find_by_identifiers(conn, rec["identifiers"])
            if work_id is None and not rec["identifiers"]:
                # Sans identifiant, même titre + année + premier auteur : c'est la même notice (ré-import d'un
                # fichier). Avec un identifiant nouveau, la notice est créée et le doublon seulement signalé.
                row = conn.execute("SELECT id FROM works WHERE fingerprint = ? ORDER BY id LIMIT 1", (fingerprint(rec),)).fetchone()
                work_id = row[0] if row else None
            if work_id:
                _complete(conn, work_id, rec, client)
                return work_id, False
            return _insert(conn, rec, client), True
    except sqlite3.IntegrityError:
        # Import concurrent du même article : l'autre l'a inséré entre-temps.
        if work_id := find_by_identifiers(conn, rec["identifiers"]):
            return work_id, False
        raise


def add_by_identifier(conn: sqlite3.Connection, text: str, client: str = "api") -> tuple[dict, bool, list[str]]:
    """Importe un article par DOI, PMID, PMCID, arXiv ou URL. Renvoie (notice, créée ?, avertissements).

    Si l'article est déjà présent (sous n'importe lequel de ses identifiants), aucune notice n'est créée :
    la notice existante est complétée avec les informations nouvelles.
    """
    scheme, value = parse_identifier(text)
    if scheme != "url" and (work_id := find_by_identifiers(conn, {scheme: value})):
        return get_work(conn, work_id), False, []
    rec, warnings = fetch(scheme, value)
    if scheme != "url":
        rec["identifiers"].setdefault(scheme, value)
    work_id, created = add_record(conn, rec, client)
    return get_work(conn, work_id), created, warnings


def add_manual(conn: sqlite3.Connection, data: dict, client: str = "api") -> tuple[dict, bool]:
    """Saisie manuelle (article sans identifiant, ou PDF non reconnu). `authors` : liste de « Nom, Prénom »."""
    rec = {field: data.get(field) for field in FIELDS}
    rec["csl_type"] = rec["csl_type"] or "article-journal"
    rec["authors"] = []
    for name in data.get("authors") or []:
        family, _, given = name.partition(",")
        rec["authors"].append({"family": family.strip(), "given": given.strip() or None, "orcid": None})
    rec["identifiers"] = {}
    for text in data.get("identifiers") or []:
        scheme, value = parse_identifier(text)
        rec["identifiers"][scheme] = value
    rec["subjects"] = [{"scheme": "keyword", "term": k, "ui": None, "major": False} for k in data.get("keywords") or []]
    work_id, created = add_record(conn, rec, client)
    return get_work(conn, work_id), created


# --- Doublons ------------------------------------------------------------------------


def duplicates(conn: sqlite3.Connection) -> list[dict]:
    """Paires de notices probablement identiques (même titre, année, premier auteur), à fusionner ou écarter."""
    pairs = conn.execute("SELECT work_a, work_b, reason, created_at FROM dedup_candidates WHERE status = 'open' ORDER BY created_at")
    return [{"reason": r["reason"], "created_at": r["created_at"],
             "works": [get_work(conn, r["work_a"]), get_work(conn, r["work_b"])]} for r in pairs.fetchall()]  # fmt: skip


def dismiss_duplicate(conn: sqlite3.Connection, a: str | int, b: str | int) -> None:
    ids = sorted((resolve(conn, a), resolve(conn, b)))
    if not conn.execute("UPDATE dedup_candidates SET status = 'dismissed' WHERE work_a = ? AND work_b = ?", ids).rowcount:
        raise NotFound("Cette paire n'est pas signalée comme doublon")


def merge_works(conn: sqlite3.Connection, home: Path, keep: str | int, drop: str | int, client: str = "api") -> dict:
    """Fusionne `drop` dans `keep` : champs vides complétés, identifiants, sujets, fichiers et note repris,
    journal des modifications conservé. La notice `drop` est supprimée ; sa clé de citation ne sert plus."""
    from . import notes  # import local : notes importe ce module

    keep_id, drop_id = resolve(conn, keep), resolve(conn, drop)
    if keep_id == drop_id:
        raise ValueError("Une notice ne peut pas être fusionnée avec elle-même")
    old = get_work(conn, drop_id)
    with db.transaction(conn):
        row = conn.execute("SELECT * FROM works WHERE id = ?", (keep_id,)).fetchone()
        for field in FIELDS:
            if row[field] in (None, "") and old[field] not in (None, ""):
                conn.execute(f"UPDATE works SET {field} = ? WHERE id = ?", (old[field], keep_id))
        for column in ("read_status", "rating"):
            if row[column] is None and old[column] is not None:
                conn.execute(f"UPDATE works SET {column} = ? WHERE id = ?", (old[column], keep_id))
        conn.execute("UPDATE OR IGNORE identifiers SET work_id = ? WHERE work_id = ?", (keep_id, drop_id))
        if not conn.execute("SELECT 1 FROM work_authors WHERE work_id = ?", (keep_id,)).fetchone():
            conn.execute("UPDATE work_authors SET work_id = ? WHERE work_id = ?", (keep_id, drop_id))
        conn.execute("INSERT OR IGNORE INTO work_subjects SELECT ?, subject_id, major FROM work_subjects WHERE work_id = ?", (keep_id, drop_id))
        moved = [r[0] for r in conn.execute("SELECT sha256 FROM work_files WHERE work_id = ?", (drop_id,))]
        conn.execute("UPDATE OR IGNORE work_files SET work_id = ? WHERE work_id = ?", (keep_id, drop_id))
        notes.absorb(conn, home, keep_id, drop_id)
        conn.execute("UPDATE changes SET work_id = ? WHERE work_id = ?", (keep_id, drop_id))
        _log(conn, keep_id, client, "merge", None, old, None)
        conn.execute("DELETE FROM works WHERE id = ?", (drop_id,))
        conn.execute("UPDATE works SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (keep_id,))
        library.index_work(conn, keep_id)
        for sha in moved:
            library.index_fulltext(conn, home, sha)
    return get_work(conn, keep_id)


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
