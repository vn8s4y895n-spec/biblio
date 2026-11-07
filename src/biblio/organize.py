"""Rangement : collections, tags, statut de lecture, note étoilée, recherches enregistrées."""

import json
import sqlite3

from . import core, db

KINDS = ("collection", "tag")
READ_STATUSES = ("to_read", "read", "to_reread")


def labels(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT l.id, l.kind, l.name, l.color, l.pinned, count(wl.work_id) AS count FROM labels l"
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


COLORS = ("red", "orange", "yellow", "green", "teal", "blue", "purple", "gray")


def rename_label(conn: sqlite3.Connection, label_id: int, name: str) -> dict:
    """Renomme. Une collection emmène ses sous-collections (« Parent / Enfant ») : renommer « BLSE » en
    « Projets / BLSE » la range sous « Projets » avec tout ce qu'elle contient."""
    label = _label(conn, label_id)
    name = " / ".join(" ".join(part.split()) for part in name.split("/") if part.strip())
    if not name:
        raise ValueError("Nom manquant")
    if label["kind"] == "collection" and (name + " / ").startswith(label["name"] + " / ") and name != label["name"]:
        raise ValueError("Une collection ne peut pas être rangée dans l'une de ses sous-collections")
    try:
        with db.transaction(conn):
            conn.execute("UPDATE labels SET name = ? WHERE id = ?", (name, label_id))
            if label["kind"] == "collection":
                old = label["name"] + " / "
                conn.execute("UPDATE labels SET name = ? || substr(name, ?) WHERE kind = 'collection' AND substr(name, 1, ?) = ?",
                             (name + " / ", len(old) + 1, len(old), old))  # fmt: skip
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Ce nom existe déjà : {name}") from exc
    return dict(_label(conn, label_id))


def update_label(conn: sqlite3.Connection, label_id: int, changes: dict) -> dict:
    """Nom (voir rename_label), couleur (COLORS, None : aucune) et épingle (collection en cours)."""
    _label(conn, label_id)
    if "name" in changes:
        rename_label(conn, label_id, changes["name"])
    if (color := changes.get("color")) is not None and color not in COLORS:
        raise ValueError(f"Couleur inconnue : {color}")
    with db.transaction(conn):
        for field in ("color", "pinned"):
            if field in changes:
                value = int(bool(changes[field])) if field == "pinned" else changes[field]
                conn.execute(f"UPDATE labels SET {field} = ? WHERE id = ?", (value, label_id))
    return dict(conn.execute("SELECT id, kind, name, color, pinned FROM labels WHERE id = ?", (label_id,)).fetchone())


# --- Vues enregistrées ---------------------------------------------------------------------------------------


def views(conn: sqlite3.Connection) -> list[dict]:
    return [{"id": r["id"], "name": r["name"], "state": json.loads(r["state_json"])}
            for r in conn.execute("SELECT * FROM views ORDER BY name")]  # fmt: skip


def save_view(conn: sqlite3.Connection, name: str, state: dict) -> dict:
    """Enregistre (ou remplace, même nom) l'état de l'écran Bibliothèque sous un nom."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Nom manquant")
    with db.transaction(conn):
        conn.execute("INSERT INTO views (name, state_json) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET state_json = excluded.state_json",
                     (name, json.dumps(state, ensure_ascii=False)))  # fmt: skip
    return next(v for v in views(conn) if v["name"].casefold() == name.casefold())


def delete_view(conn: sqlite3.Connection, view_id: int) -> None:
    with db.transaction(conn):
        if not conn.execute("DELETE FROM views WHERE id = ?", (view_id,)).rowcount:
            raise core.NotFound(f"Vue inconnue : {view_id}")


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


def leave_collections(conn: sqlite3.Connection, refs: list[str | int], client: str, keep: int | None = None) -> None:
    """Retire des notices de toutes leurs collections (sauf `keep`) : « déplacer vers » une autre collection."""
    for row in conn.execute("SELECT id FROM labels WHERE kind = 'collection' AND id IS NOT ?", (keep,)).fetchall():
        set_label(conn, row[0], refs, False, client)


def update_work(conn: sqlite3.Connection, ref: str | int, changes: dict, client: str) -> dict:
    """Statut de lecture, note (1 à 5) et favori ; None efface le statut ou la note."""
    work_id = core.resolve(conn, ref)
    if (status := changes.get("read_status")) is not None and status not in READ_STATUSES:
        raise ValueError(f"Statut de lecture inconnu : {status}")
    if (rating := changes.get("rating")) is not None and rating not in range(1, 6):
        raise ValueError("La note va de 1 à 5")
    if "favorite" in changes:
        changes = {**changes, "favorite": int(bool(changes["favorite"]))}
    row = conn.execute("SELECT read_status, rating, favorite FROM works WHERE id = ?", (work_id,)).fetchone()
    with db.transaction(conn):
        for field in ("read_status", "rating", "favorite"):
            if field in changes and changes[field] != row[field]:
                conn.execute(f"UPDATE works SET {field} = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
                             " WHERE id = ?", (changes[field], work_id))  # fmt: skip
                core._log(conn, work_id, client, "update", field, row[field], changes[field])
    return core.get_work(conn, work_id)


def add_question(conn: sqlite3.Connection, work_id: int, question: str, scope: str = "online") -> None:
    """Garde la question qui a mené à ajouter l'article (une seule fois par question)."""
    question = " ".join(question.split())
    if question:
        with db.transaction(conn):
            conn.execute("INSERT OR IGNORE INTO work_questions (work_id, question, scope) VALUES (?, ?, ?)",
                         (work_id, question, scope))  # fmt: skip


# --- Historique des questions ---------------------------------------------------------------------


def remember_question(conn: sqlite3.Connection, question: str, scope: str) -> None:
    question = " ".join(question.split())
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO question_history (question, scope) VALUES (?, ?) ON CONFLICT (question, scope) DO UPDATE"
            " SET asked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), times = times + 1",
            (question, scope),
        )


def question_history(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute("SELECT question, scope, asked_at, times FROM question_history ORDER BY asked_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def forget_question(conn: sqlite3.Connection, question: str | None = None, scope: str | None = None) -> int:
    """Retire une question de l'historique (toutes si `question` est omise)."""
    with db.transaction(conn):
        if question is None:
            return conn.execute("DELETE FROM question_history").rowcount
        return conn.execute("DELETE FROM question_history WHERE question = ? AND (? IS NULL OR scope = ?)",
                            (question, scope, scope)).rowcount  # fmt: skip


# --- Recherches enregistrées ------------------------------------------------------------


def save_search(conn: sqlite3.Connection, data: dict) -> dict:
    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO saved_searches (question, origin, scope, query, answer_json, sources_json, synthesis_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (data["question"], data.get("origin", "biblio"), data.get("scope", "online"), data.get("query"),
             json.dumps(data.get("answer", []), ensure_ascii=False), json.dumps(data.get("sources", []), ensure_ascii=False),
             json.dumps(data["synthesis"], ensure_ascii=False) if data.get("synthesis") else None),
        )  # fmt: skip
    return get_search(conn, cur.lastrowid)


def searches(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, question, origin, scope, created_at, watch_days, last_run, json_array_length(sources_json) AS count"
        " FROM saved_searches ORDER BY created_at DESC, id DESC"
    )
    return [dict(r) for r in rows]


def get_search(conn: sqlite3.Connection, search_id: int) -> dict:
    row = conn.execute("SELECT * FROM saved_searches WHERE id = ?", (search_id,)).fetchone()
    if row is None:
        raise core.NotFound(f"Recherche enregistrée inconnue : {search_id}")
    out = {k: row[k] for k in row.keys() if not k.endswith("_json")}
    return {**out, "answer": json.loads(row["answer_json"]), "sources": json.loads(row["sources_json"]),
            "synthesis": json.loads(row["synthesis_json"]) if row["synthesis_json"] else None}


def delete_search(conn: sqlite3.Connection, search_id: int) -> None:
    get_search(conn, search_id)
    with db.transaction(conn):
        conn.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))
