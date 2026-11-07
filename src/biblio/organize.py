"""Rangement : collections, tags, statut de lecture, note étoilée, recherches enregistrées."""

import json
import sqlite3

from . import core, db

KINDS = ("collection", "tag")
READ_STATUSES = ("to_read", "read", "to_reread")


def labels(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT l.id, l.kind, l.name, count(wl.work_id) AS count FROM labels l"
        " LEFT JOIN work_labels wl ON wl.label_id = l.id GROUP BY l.id ORDER BY l.kind, l.name"
    )
    return [dict(r) for r in rows]


def create_label(conn: sqlite3.Connection, kind: str, name: str) -> dict:
    """Crée la collection ou le tag, ou renvoie celui qui existe déjà sous ce nom (casse ignorée)."""
    name = " ".join(name.split())
    if kind not in KINDS or not name:
        raise ValueError("Collection ou tag : nom manquant")
    with db.transaction(conn):
        conn.execute("INSERT OR IGNORE INTO labels (kind, name) VALUES (?, ?)", (kind, name))
    row = conn.execute("SELECT id, kind, name FROM labels WHERE kind = ? AND name = ?", (kind, name)).fetchone()
    return dict(row)


def _label(conn: sqlite3.Connection, label_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT id, kind, name FROM labels WHERE id = ?", (label_id,)).fetchone()
    if row is None:
        raise core.NotFound(f"Collection ou tag inconnu : {label_id}")
    return row


def rename_label(conn: sqlite3.Connection, label_id: int, name: str) -> dict:
    _label(conn, label_id)
    name = " ".join(name.split())
    if not name:
        raise ValueError("Nom manquant")
    try:
        with db.transaction(conn):
            conn.execute("UPDATE labels SET name = ? WHERE id = ?", (name, label_id))
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Ce nom existe déjà : {name}") from exc
    return dict(_label(conn, label_id))


def delete_label(conn: sqlite3.Connection, label_id: int) -> None:
    """Supprime la collection ou le tag ; les notices restent dans la bibliothèque."""
    _label(conn, label_id)
    with db.transaction(conn):
        conn.execute("DELETE FROM labels WHERE id = ?", (label_id,))


def set_label(conn: sqlite3.Connection, label_id: int, refs: list[str | int], on: bool, client: str) -> int:
    """Ajoute (`on`) ou retire les notices de la collection / du tag. Renvoie le nombre de notices changées."""
    label = _label(conn, label_id)
    work_ids = [core.resolve(conn, ref) for ref in refs]
    changed = 0
    with db.transaction(conn):
        for work_id in work_ids:
            if on:
                cur = conn.execute("INSERT OR IGNORE INTO work_labels (label_id, work_id) VALUES (?, ?)", (label_id, work_id))
            else:
                cur = conn.execute("DELETE FROM work_labels WHERE label_id = ? AND work_id = ?", (label_id, work_id))
            if cur.rowcount:
                changed += 1
                core._log(conn, work_id, client, "update", label["kind"], None if on else label["name"],
                          label["name"] if on else None)  # fmt: skip
    return changed


def update_work(conn: sqlite3.Connection, ref: str | int, changes: dict, client: str) -> dict:
    """Statut de lecture et note (1 à 5) ; None efface la valeur."""
    work_id = core.resolve(conn, ref)
    if (status := changes.get("read_status")) is not None and status not in READ_STATUSES:
        raise ValueError(f"Statut de lecture inconnu : {status}")
    if (rating := changes.get("rating")) is not None and rating not in range(1, 6):
        raise ValueError("La note va de 1 à 5")
    row = conn.execute("SELECT read_status, rating FROM works WHERE id = ?", (work_id,)).fetchone()
    with db.transaction(conn):
        for field in ("read_status", "rating"):
            if field in changes and changes[field] != row[field]:
                conn.execute(f"UPDATE works SET {field} = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
                             " WHERE id = ?", (changes[field], work_id))  # fmt: skip
                core._log(conn, work_id, client, "update", field, row[field], changes[field])
    return core.get_work(conn, work_id)


# --- Recherches enregistrées ------------------------------------------------------------


def save_search(conn: sqlite3.Connection, data: dict) -> dict:
    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO saved_searches (question, origin, scope, query, answer_json, sources_json) VALUES (?, ?, ?, ?, ?, ?)",
            (data["question"], data.get("origin", "biblio"), data.get("scope", "online"), data.get("query"),
             json.dumps(data.get("answer", []), ensure_ascii=False), json.dumps(data.get("sources", []), ensure_ascii=False)),
        )  # fmt: skip
    return get_search(conn, cur.lastrowid)


def searches(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, question, origin, scope, created_at, json_array_length(sources_json) AS count"
        " FROM saved_searches ORDER BY created_at DESC, id DESC"
    )
    return [dict(r) for r in rows]


def get_search(conn: sqlite3.Connection, search_id: int) -> dict:
    row = conn.execute("SELECT * FROM saved_searches WHERE id = ?", (search_id,)).fetchone()
    if row is None:
        raise core.NotFound(f"Recherche enregistrée inconnue : {search_id}")
    out = {k: row[k] for k in row.keys() if not k.endswith("_json")}
    return {**out, "answer": json.loads(row["answer_json"]), "sources": json.loads(row["sources_json"])}


def delete_search(conn: sqlite3.Connection, search_id: int) -> None:
    get_search(conn, search_id)
    with db.transaction(conn):
        conn.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))
