"""Veille : recherches suivies relancées à intervalle régulier, suivi des articles de la bibliothèque
(rétractation, correction, expression de préoccupation, version publiée d'un preprint), articles proches.

Tout passe par des alertes (table `alerts`), affichées dans l'écran Veille jusqu'à ce qu'elles soient lues.
"""

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import core, db, importer, organize, search
from .sources import biorxiv, crossref, openalex
from .sources.http import SourceError

log = logging.getLogger("biblio")

LIBRARY_EVERY = timedelta(days=7)  # vérification des articles de la bibliothèque
MAX_NEW = 50  # nouveaux résultats gardés par alerte
# Types d'avis Crossref → type d'alerte. Les autres (erratum, addendum…) sont gardés sans alerte.
ALERT_KINDS = {"retraction": "retraction", "removal": "retraction", "withdrawal": "retraction",
               "expression_of_concern": "concern", "correction": "correction", "published": "published"}  # fmt: skip


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _alert(conn: sqlite3.Connection, kind: str, data: dict, work_id: int | None = None, search_id: int | None = None) -> None:
    conn.execute("INSERT INTO alerts (kind, work_id, search_id, data_json) VALUES (?, ?, ?, ?)",
                 (kind, work_id, search_id, json.dumps(data, ensure_ascii=False)))  # fmt: skip


# --- Recherches suivies ---------------------------------------------------------------------------------


def set_watch(conn: sqlite3.Connection, search_id: int, days: int | None) -> dict:
    """Suit la recherche tous les `days` jours (None : ne plus la suivre). Les sources déjà affichées comptent
    comme vues : seuls les articles parus ou indexés ensuite seront signalés."""
    saved = organize.get_search(conn, search_id)
    if days is not None and not 1 <= days <= 365:
        raise ValueError("Intervalle de veille : 1 à 365 jours")
    if days is not None and saved["scope"] != "online":
        raise ValueError("Seules les recherches dans les bases en ligne peuvent être suivies")
    with db.transaction(conn):
        conn.execute("UPDATE saved_searches SET watch_days = ?, last_run = coalesce(last_run, ?) WHERE id = ?",
                     (days, _now(), search_id))  # fmt: skip
        conn.executemany("INSERT OR IGNORE INTO search_seen (search_id, key) VALUES (?, ?)",
                         [(search_id, search.result_key(rec)) for rec in saved["sources"] if rec.get("identifiers") is not None])  # fmt: skip
    return organize.get_search(conn, search_id)


def run_search(conn: sqlite3.Connection, search_id: int) -> list[dict]:
    """Relance une recherche suivie ; renvoie (et signale) les résultats jamais vus."""
    saved = organize.get_search(conn, search_id)
    found = search.search(conn, saved["query"] or saved["question"], limit=200, rerank=False)
    seen = {r[0] for r in conn.execute("SELECT key FROM search_seen WHERE search_id = ?", (search_id,))}
    new = [rec for rec in found["results"] if search.result_key(rec) not in seen]
    with db.transaction(conn):
        conn.executemany("INSERT OR IGNORE INTO search_seen (search_id, key) VALUES (?, ?)",
                         [(search_id, search.result_key(rec)) for rec in new])  # fmt: skip
        conn.execute("UPDATE saved_searches SET last_run = ? WHERE id = ?", (_now(), search_id))
        if new:
            _alert(conn, "new_results", {"question": saved["question"], "count": len(new), "records": new[:MAX_NEW]},
                   search_id=search_id)  # fmt: skip
    return new


def due_searches(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM saved_searches WHERE watch_days IS NOT NULL"
        " AND (last_run IS NULL OR datetime(last_run, '+' || watch_days || ' days') <= datetime('now'))"
    )
    return [r[0] for r in rows]


# --- Articles de la bibliothèque ----------------------------------------------------------------------


def check_library(conn: sqlite3.Connection) -> dict:
    """Interroge Crossref pour tous les articles qui ont un DOI (avis publiés à leur sujet, version publiée des
    preprints), et bioRxiv / medRxiv pour leurs preprints. Chaque nouvel avis est gardé (`work_updates`) et signalé
    s'il compte (voir ALERT_KINDS)."""
    dois = dict(conn.execute("SELECT lower(value), work_id FROM identifiers WHERE scheme = 'doi'").fetchall())
    found = crossref.notices(list(dois))
    # bioRxiv / medRxiv : version publiée par leur propre API (pour les preprints pas encore signalés).
    known = {r[0] for r in conn.execute("SELECT work_id FROM work_updates WHERE kind = 'published'")}
    for doi in [d for d in dois if d.startswith("10.1101/") and dois[d] not in known]:
        if journal_doi := biorxiv.published(doi):
            entry = found.setdefault(doi, {"updates": [], "published": []})
            entry["published"] = sorted({*entry["published"], journal_doi})
    counts: dict[str, int] = {}
    with db.transaction(conn):
        for doi, info in found.items():
            work_id = dois.get(doi)
            if work_id is None:
                continue
            notices = info["updates"] + [{"kind": "published", "doi": d, "date": None, "source": "crossref"} for d in info["published"]]
            for n in notices:
                if n["doi"] == doi:
                    continue  # un article qui se désigne lui-même (données d'éditeur)
                added = conn.execute(
                    "INSERT OR IGNORE INTO work_updates (work_id, kind, notice_doi, date, source) VALUES (?, ?, ?, ?, ?)",
                    (work_id, n["kind"], n["doi"], n["date"], n["source"]),
                ).rowcount
                kind = ALERT_KINDS.get(n["kind"])
                # Une seule alerte non lue par article et par type, même si plusieurs avis sont publiés.
                pending = conn.execute("SELECT 1 FROM alerts WHERE work_id = ? AND kind = ? AND seen_at IS NULL",
                                       (work_id, kind)).fetchone()  # fmt: skip
                if added and kind and not pending:
                    _alert(conn, kind, {"notice_doi": n["doi"], "date": n["date"], "source": n["source"]}, work_id=work_id)
                    counts[kind] = counts.get(kind, 0) + 1
        db.set_meta(conn, "watch.library_check", _now())
    return {"checked": len(dois), "alerts": counts}


def updates_of(conn: sqlite3.Connection, work_id: int) -> list[dict]:
    rows = conn.execute("SELECT kind, notice_doi, date, source FROM work_updates WHERE work_id = ? ORDER BY date", (work_id,))
    return [dict(r) for r in rows]


def supersede(conn: sqlite3.Connection, home: Path, preprint: str | int, published_doi: str, client: str = "api") -> dict:
    """Remplace un preprint par sa version publiée : celle-ci est ajoutée (avec son PDF libre), puis le preprint
    y est fusionné (notes, tags, PDF repris). Le DOI du preprint reste connu sous `preprint-doi`."""
    preprint_id = core.resolve(conn, preprint)
    published = importer.add(conn, home, published_doi, client)["work"]
    if published["id"] == preprint_id:
        raise ValueError("Le preprint et la version publiée sont déjà la même notice")
    with db.transaction(conn):
        conn.execute("UPDATE OR IGNORE identifiers SET scheme = 'preprint-doi' WHERE work_id = ? AND scheme = 'doi'", (preprint_id,))
        conn.execute("UPDATE alerts SET seen_at = ? WHERE work_id = ? AND kind = 'published'", (_now(), preprint_id))
    return core.merge_works(conn, home, published["id"], preprint_id, client)


def neighbours(conn: sqlite3.Connection, ref: str | int) -> dict:
    """Articles proches et articles récents qui citent la notice (OpenAlex), avec statut de revue et présence dans
    la bibliothèque."""
    work = core.get_work(conn, ref)
    doi = work["identifiers"].get("doi")
    if not doi:
        raise ValueError("Articles proches : la notice n'a pas de DOI")
    out = openalex.neighbours(doi)
    for records in out.values():
        for rec in records:
            rec["journal_status"] = search.journal_status(conn, rec)
            rec["in_library"] = search.in_library(conn, rec)
    return out


# --- Alertes et tâche de fond ----------------------------------------------------------------------------


def alerts(conn: sqlite3.Connection, unseen_only: bool = False, limit: int = 100) -> dict:
    rows = conn.execute(
        f"""SELECT a.id, a.kind, a.work_id, a.search_id, a.data_json, a.created_at, a.seen_at,
                   w.citekey, w.title, w.year, s.question
            FROM alerts a LEFT JOIN works w ON w.id = a.work_id LEFT JOIN saved_searches s ON s.id = a.search_id
            {'WHERE a.seen_at IS NULL' if unseen_only else ''} ORDER BY a.seen_at IS NOT NULL, a.created_at DESC, a.id DESC
            LIMIT ?""",
        (limit,),
    )
    items = [{**{k: r[k] for k in r.keys() if k != "data_json"}, "data": json.loads(r["data_json"])} for r in rows]
    unseen = conn.execute("SELECT count(*) FROM alerts WHERE seen_at IS NULL").fetchone()[0]
    return {"unseen": unseen, "items": items, "library_check": db.get_meta(conn, "watch.library_check")}


def mark_seen(conn: sqlite3.Connection, ids: list[int] | None = None) -> int:
    """Marque des alertes comme lues (toutes si `ids` est omis)."""
    with db.transaction(conn):
        if ids is None:
            return conn.execute("UPDATE alerts SET seen_at = ? WHERE seen_at IS NULL", (_now(),)).rowcount
        return conn.execute(f"UPDATE alerts SET seen_at = ? WHERE seen_at IS NULL AND id IN ({','.join('?' * len(ids))})",
                            (_now(), *ids)).rowcount  # fmt: skip


def run_due(conn: sqlite3.Connection) -> None:
    """Appelée par la tâche de fond : recherches suivies arrivées à échéance, puis vérification hebdomadaire de la
    bibliothèque. Une source injoignable n'empêche pas les autres vérifications."""
    for search_id in due_searches(conn):
        try:
            if new := run_search(conn, search_id):
                log.info("Veille : %d nouveaux résultats pour la recherche %d", len(new), search_id)
        except SourceError as exc:
            log.warning("Veille, recherche %d : %s", search_id, exc)
    last = db.get_meta(conn, "watch.library_check")
    if last is None or datetime.fromisoformat(last.replace("Z", "+00:00")) <= datetime.now(UTC) - LIBRARY_EVERY:
        check_library(conn)
