"""Webhooks : Biblio prévient une autre application (n8n, Zapier, script maison…) quand un événement se produit.

Chaque envoi est un POST JSON `{"event", "at", "data"}` signé par HMAC-SHA256 avec le secret du webhook : en-tête
`X-Biblio-Signature: sha256=<hex du corps>`. L'envoi part dans un fil à part et ne retarde jamais Biblio ;
le résultat du dernier envoi est gardé (statut ou erreur) pour l'écran Connecteurs.
"""

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx

from . import core, db

EVENTS = ("work.created", "search.saved", "alert.created")
TIMEOUT = 10
transport: httpx.BaseTransport | None = None  # remplacé dans les tests
db_path: Path | None = None  # posé par l'API : les envois notent leur résultat dans la base


def _row(row: sqlite3.Row) -> dict:
    out = {k: row[k] for k in row.keys() if k != "events_json"}
    return {**out, "events": json.loads(row["events_json"]), "active": bool(row["active"])}


def hooks(conn: sqlite3.Connection) -> list[dict]:
    return [_row(r) for r in conn.execute("SELECT * FROM webhooks ORDER BY id")]


def get(conn: sqlite3.Connection, hook_id: int) -> dict:
    row = conn.execute("SELECT * FROM webhooks WHERE id = ?", (hook_id,)).fetchone()
    if row is None:
        raise core.NotFound(f"Webhook inconnu : {hook_id}")
    return _row(row)


def _check(url: str, events: list[str]) -> None:
    if not url.startswith(("http://", "https://")):
        raise ValueError("L'adresse d'un webhook commence par http:// ou https://")
    if unknown := set(events) - set(EVENTS):
        raise ValueError(f"Événement inconnu : {', '.join(sorted(unknown))}")
    if not events:
        raise ValueError("Choisir au moins un événement")


def create(conn: sqlite3.Connection, url: str, events: list[str]) -> dict:
    """Le secret est tiré au hasard ; il est montré dans l'écran Connecteurs pour être copié dans l'autre application."""
    _check(url, events)
    with db.transaction(conn):
        cur = conn.execute("INSERT INTO webhooks (url, events_json, secret) VALUES (?, ?, ?)",
                           (url, json.dumps(events), secrets.token_urlsafe(32)))  # fmt: skip
    return get(conn, cur.lastrowid)


def update(conn: sqlite3.Connection, hook_id: int, url: str | None = None, events: list[str] | None = None,
           active: bool | None = None) -> dict:  # fmt: skip
    hook = get(conn, hook_id)
    url, events = url or hook["url"], events if events is not None else hook["events"]
    _check(url, events)
    active = hook["active"] if active is None else active
    with db.transaction(conn):
        conn.execute("UPDATE webhooks SET url = ?, events_json = ?, active = ? WHERE id = ?",
                     (url, json.dumps(events), int(active), hook_id))  # fmt: skip
    return get(conn, hook_id)


def delete(conn: sqlite3.Connection, hook_id: int) -> None:
    get(conn, hook_id)
    with db.transaction(conn):
        conn.execute("DELETE FROM webhooks WHERE id = ?", (hook_id,))


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(hook: dict, event: str, data: dict) -> dict:
    """Envoie un événement et note le résultat ; ne lève jamais (le destinataire peut être éteint)."""
    at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = json.dumps({"event": event, "at": at, "data": data}, ensure_ascii=False, default=str).encode()
    headers = {"Content-Type": "application/json", "X-Biblio-Event": event, "X-Biblio-Signature": sign(hook["secret"], body)}
    status, error = None, None
    try:
        with httpx.Client(timeout=TIMEOUT, transport=transport) as c:
            status = c.post(hook["url"], content=body, headers=headers).status_code
        if status >= 400:
            error = f"HTTP {status}"
    except httpx.HTTPError as exc:
        error = str(exc) or type(exc).__name__
    if db_path:
        conn = db.connect(db_path)
        try:
            with db.transaction(conn):
                conn.execute("UPDATE webhooks SET last_at = ?, last_status = ?, last_error = ? WHERE id = ?",
                             (at, status, error, hook["id"]))  # fmt: skip
        finally:
            conn.close()
    return {"status": status, "error": error}


def emit(conn: sqlite3.Connection, event: str, data: dict) -> list[threading.Thread]:
    """Envoie l'événement à chaque webhook actif qui l'a choisi, en arrière-plan (fils renvoyés pour les tests)."""
    threads = []
    for hook in hooks(conn):
        if hook["active"] and event in hook["events"]:
            t = threading.Thread(target=deliver, args=(hook, event, data), daemon=True, name="webhook")
            t.start()
            threads.append(t)
    return threads
