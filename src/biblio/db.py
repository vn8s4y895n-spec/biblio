"""Connexion SQLite et migrations versionnées (`migrations/NNNN_nom.sql`)."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

import sqlite_vec


def connect(path: Path) -> sqlite3.Connection:
    # Mode autocommit : les transactions sont ouvertes explicitement par `transaction()`.
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # sqlite-vec (vecteurs sémantiques) : nécessaire à chaque connexion, car des déclencheurs y écrivent.
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Applique les migrations manquantes, chacune dans sa transaction. Renvoie les versions appliquées."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))"
    )
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    folder = files("biblio") / "migrations"
    scripts = sorted((f for f in folder.iterdir() if f.name.endswith(".sql")), key=lambda f: f.name)
    for script in scripts:
        version = int(script.name.split("_", 1)[0])
        if version in done:
            continue
        try:
            conn.executescript(
                f"BEGIN;\n{script.read_text(encoding='utf-8')}\n"
                f"INSERT INTO schema_migrations (version) VALUES ({version});\nCOMMIT;"
            )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def set_meta(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    """Petit état du programme (table `meta`) ; None l'efface."""
    if value is None:
        conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None
