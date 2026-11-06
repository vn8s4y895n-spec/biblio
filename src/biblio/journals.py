"""Liste des revues recommandées par la Conférence des doyens et le CNU santé (Sorbonne Université).

Le statut de revue d'un article est calculé à la volée, avec la liste la plus récente (vue `work_journal_status`).
Il suit donc chaque mise à jour de la liste sans qu'on retouche les notices.
"""

import hashlib
import html
import io
import json
import re
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import db
from .sources import http

PAGE_URL = "https://sante.sorbonne-universite.fr/recherche/liste-des-revues-presumees-non-predatrices"
ALLOWED_HOST = "sante.sorbonne-universite.fr"
BUNDLED = "liste_de_revues_recommandables_cdd_cnu_sante_01-10-26.xlsx"
BUNDLED_URL = f"https://{ALLOWED_HOST}/sites/default/files/media/2026-10/{BUNDLED}"
CHECK_EVERY = timedelta(days=7)
MAX_DOWNLOAD = 20_000_000  # octets ; le fichier fait ~400 Ko
MAX_UNZIPPED = 100_000_000  # protection contre une archive piégée

STATUSES = ("recommended", "withdrawn", "not_listed", "preprint", "unidentified", "no_list")

# Début de l'en-tête de colonne (en minuscules) → champ. L'ordre des colonnes peut changer.
COLUMNS = {
    "nom complet": "name",
    "nom abr": "abbrev",
    "issn print": "issn_print",
    "issn elec": "issn_e",
    "nlmid": "nlmid",
    "année de début": "start_year",
    "année de fin": "end_year",
    "nouveau nom": "new_name",
    "date retrait": "withdrawn_on",
    "motif retrait": "withdrawal_reason",
}
REQUIRED = {"name", "issn_print", "issn_e", "nlmid"}
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class ListError(ValueError):
    """Fichier ou page de la liste inexploitable."""


# --- Lecture du fichier Excel (bibliothèque standard : un .xlsx est un zip de XML) --------------


def _rows(data: bytes):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ListError("Le fichier n'est pas un .xlsx lisible") from exc
    sheets = sorted(
        (n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
        key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()),
    )
    if not sheets:
        raise ListError("Aucune feuille dans le fichier")
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
        raise ListError("Fichier anormalement volumineux une fois décompressé")
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(si.itertext()) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)]
    for number, row in enumerate(ET.fromstring(z.read(sheets[0])).iter(f"{{{NS['m']}}}row"), 1):
        values = {"_row": int(row.get("r", number))}
        for position, c in enumerate(row.findall("m:c", NS)):
            ref = re.match(r"[A-Z]+", c.get("r", ""))
            col = ref.group() if ref else str(position)
            kind = c.get("t")
            if kind == "s":
                values[col] = shared[int(c.findtext("m:v", namespaces=NS))]
            elif kind == "inlineStr":
                values[col] = "".join(c.find("m:is", NS).itertext())
            else:
                values[col] = c.findtext("m:v", namespaces=NS)
        yield values


def _clean(value: str | None) -> str | None:
    value = re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()
    return value or None


def normalize_issn(value: str | None) -> str | None:
    """« 742-6405 », « 0003-3944\\xa0 » → « 0742-6405 », « 0003-3944 »."""
    digits = re.sub(r"[^0-9X]", "", (value or "").upper())
    if len(digits) == 7:
        digits = "0" + digits
    return f"{digits[:4]}-{digits[4:]}" if len(digits) == 8 else None


def _number(value: str | None) -> str | None:
    """Les nombres Excel arrivent parfois en « 7501160.0 »."""
    return re.sub(r"\.0+$", "", value) if value else None


def _year(value: str | None) -> int | None:
    m = re.search(r"\d{4}", value or "")
    return int(m.group()) if m else None


def _excel_date(value: str | None) -> str | None:
    if not value:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", value):  # numéro de série Excel
        return (date(1899, 12, 30) + timedelta(days=int(float(value)))).isoformat()
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value):
        return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    return None


def _reason(value: str | None) -> int | None:
    m = re.match(r"\s*([123])", value or "")
    return int(m.group(1)) if m else None


@lru_cache(maxsize=4)
def parse(data: bytes) -> tuple[tuple[dict, ...], tuple[str, ...]]:
    """Renvoie (revues, avertissements). Les avertissements signalent les anomalies du fichier sans le rejeter."""
    rows = _rows(data)
    header = next(rows, None) or {}
    header.pop("_row", None)
    mapping = {}
    for col, title in header.items():
        title = (_clean(title) or "").lower()
        for prefix, field in COLUMNS.items():
            if title.startswith(prefix):
                mapping[col] = field
    if missing := REQUIRED - set(mapping.values()):
        raise ListError(f"Colonnes attendues absentes : {', '.join(sorted(missing))}")

    journals = []
    for row in rows:
        raw = {field: _clean(row.get(col)) for col, field in mapping.items()}
        if not any(raw.get(f) for f in ("name", "issn_print", "issn_e", "nlmid")):
            continue
        journals.append({
            "sheet_row": row["_row"],
            "name": raw.get("name"),
            "abbrev": raw.get("abbrev"),
            "issn_print": normalize_issn(raw.get("issn_print")),
            "issn_e": normalize_issn(raw.get("issn_e")),
            "nlmid": _number(raw.get("nlmid")),
            "start_year": _year(raw.get("start_year")),
            "end_year": _year(raw.get("end_year")),
            "new_name": raw.get("new_name"),
            "withdrawn_on": _excel_date(raw.get("withdrawn_on")),
            "withdrawal_reason": _reason(raw.get("withdrawal_reason")),
        })
    if len(journals) < 100:
        raise ListError(f"Liste anormalement courte ({len(journals)} revues)")
    return tuple(journals), tuple(_anomalies(journals))


def _anomalies(journals: list[dict]) -> list[str]:
    warnings = []
    if nameless := [j["sheet_row"] for j in journals if not j["name"]]:
        warnings.append(f"{len(nameless)} ligne(s) avec un identifiant mais sans nom de revue (ligne {nameless[0]}…)")
    # Un même ISSN électronique sur deux lignes voisines trahit souvent une colonne décalée d'une ligne,
    # sauf entre une revue et son ancien nom (« Nouveau Nom »), qui partagent légitimement leur ISSN.
    shifted = [
        b["sheet_row"]
        for a, b in zip(journals, journals[1:])
        if b["issn_e"] and b["issn_e"] == a["issn_e"] and a["name"] not in (b["new_name"],) and b["name"] != a["new_name"]
    ]
    if shifted:
        warnings.append(
            f"ISSN électronique identique sur deux lignes voisines (ligne {shifted[0]}) : la colonne semble décalée "
            "à partir de là. Le statut reste fiable (correspondance d'abord par NLMID et ISSN papier), mais le nom "
            "de revue affiché peut être celui de la ligne voisine."
        )
    return warnings


# --- Stockage des versions ------------------------------------------------------------------


def store(conn: sqlite3.Connection, data: bytes, source_url: str, published_on: str | None, home: Path) -> tuple[int, bool]:
    """Enregistre une version de la liste (si elle est nouvelle). Renvoie (id de la version, nouvelle ?)."""
    sha = hashlib.sha256(data).hexdigest()
    if row := conn.execute("SELECT id FROM journal_lists WHERE sha256 = ?", (sha,)).fetchone():
        return row[0], False
    journals, warnings = parse(data)  # lève ListError avant toute écriture
    folder = home / "journals"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{published_on or date.today().isoformat()}_{sha[:8]}.xlsx"
    path.write_bytes(data)
    with db.transaction(conn):
        list_id = conn.execute(
            "INSERT INTO journal_lists (source_url, sha256, published_on, file_path, row_count, warnings)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (source_url, sha, published_on, str(path), len(journals), json.dumps(warnings, ensure_ascii=False)),
        ).lastrowid
        conn.executemany(
            "INSERT INTO journals (list_id, sheet_row, name, abbrev, abbrev_norm, issn_print, issn_e, nlmid,"
            " start_year, end_year, new_name, withdrawn_on, withdrawal_reason)"
            f" VALUES (?, ?, ?, ?, {ABBREV_KEY.format('?')}, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(list_id, j["sheet_row"], j["name"], j["abbrev"], j["abbrev"], j["issn_print"], j["issn_e"], j["nlmid"],
              j["start_year"], j["end_year"], j["new_name"], j["withdrawn_on"], j["withdrawal_reason"]) for j in journals],
        )  # fmt: skip
    return list_id, True


def _list_row(row: sqlite3.Row | None) -> dict | None:
    return {**dict(row), "warnings": json.loads(row["warnings"])} if row else None


def current_list(conn: sqlite3.Connection) -> dict | None:
    return _list_row(conn.execute("SELECT * FROM journal_lists ORDER BY id DESC LIMIT 1").fetchone())


def history(conn: sqlite3.Connection) -> list[dict]:
    return [_list_row(r) for r in conn.execute("SELECT * FROM journal_lists ORDER BY id DESC")]


# --- Statut de revue (une seule définition SQL, pour la vue et pour les notices hors bibliothèque) ---

# Même normalisation des abréviations des deux côtés : « J. Infect. Dis. » → « j infect dis ».
ABBREV_KEY = "lower(trim(replace({}, '.', '')))"
CURRENT_LIST = "(SELECT max(id) FROM journal_lists)"
# Motif retenu : une date de retrait sans motif est traitée comme « n'est plus recommandée », par prudence.
REASON = "coalesce({0}.withdrawal_reason, CASE WHEN {0}.withdrawn_on IS NULL THEN 0 ELSE 2 END)"


def _status_select(source: str) -> str:
    return f"""
SELECT w.id AS work_id, j.id AS journal_id,
    CASE
        WHEN {CURRENT_LIST} IS NULL THEN 'no_list'
        WHEN j.id IS NOT NULL AND {REASON.format('j')} <> 2 THEN 'recommended'
        WHEN j.id IS NOT NULL THEN 'withdrawn'
        WHEN w.csl_type = 'article' THEN 'preprint'
        WHEN coalesce(w.issn_print, w.issn_e, w.nlmid, w.container_abbrev) IS NULL THEN 'unidentified'
        ELSE 'not_listed'
    END AS status
FROM {source} w
LEFT JOIN journals j ON j.id = ({_best_match()})"""


# (rang, colonne de la liste, valeur côté notice) : identifiant le plus sûr d'abord.
MATCHES = [
    (0, "nlmid", "w.nlmid"),
    (1, "issn_print", "w.issn_print"),
    (1, "issn_print", "w.issn_e"),
    (2, "issn_e", "w.issn_print"),
    (2, "issn_e", "w.issn_e"),
    (3, "abbrev_norm", ABBREV_KEY.format("w.container_abbrev")),
]


def _best_match() -> str:
    """Meilleure ligne de la liste pour la notice `w` : identifiant le plus sûr, puis ligne active plutôt que
    retirée (revues renommées qui partagent un ISSN). Une recherche indexée par identifiant, réunies par
    UNION ALL : un OR entre colonnes empêcherait SQLite d'utiliser les index (20 s pour 10 000 notices)."""
    arms = [
        f"SELECT c.id, {rank} AS q, {REASON.format('c')} = 2 AS dropped, {REASON.format('c')} <> 0 AS gone"
        f" FROM journals c WHERE c.list_id = {CURRENT_LIST} AND c.{column} = {value}"
        for rank, column, value in MATCHES
    ]
    return f"SELECT id FROM ({' UNION ALL '.join(arms)}) ORDER BY q, dropped, gone, id LIMIT 1"


def install(conn: sqlite3.Connection, home: Path) -> None:
    """(Re)crée la vue de statut et charge la liste livrée avec Biblio si la base n'en a aucune."""
    conn.executescript(f"DROP VIEW IF EXISTS work_journal_status;\nCREATE VIEW work_journal_status AS {_status_select('works')};")
    if current_list(conn) is None:
        data = (files("biblio") / "data" / BUNDLED).read_bytes()
        store(conn, data, BUNDLED_URL, published_from_url(BUNDLED_URL), home)


def _journal_details(conn: sqlite3.Connection, status: str, journal_id: int | None) -> dict:
    out = {"status": status, "journal": None, "list_published_on": None}
    if lst := current_list(conn):
        out["list_published_on"] = lst["published_on"]
    if journal_id:
        j = conn.execute("SELECT name, abbrev, issn_print, issn_e, nlmid, new_name, withdrawn_on, withdrawal_reason"
                         " FROM journals WHERE id = ?", (journal_id,)).fetchone()  # fmt: skip
        out["journal"] = dict(j)
    return out


def status_of_work(conn: sqlite3.Connection, work_id: int) -> dict:
    row = conn.execute("SELECT status, journal_id FROM work_journal_status WHERE work_id = ?", (work_id,)).fetchone()
    return _journal_details(conn, row["status"], row["journal_id"])


def lookup(conn: sqlite3.Connection, issn: str | None = None, nlmid: str | None = None, abbrev: str | None = None,
           csl_type: str = "article-journal") -> dict:  # fmt: skip
    """Statut d'une revue qui n'est pas forcément dans la bibliothèque (résultats de recherche en ligne)."""
    issn = normalize_issn(issn)
    row = conn.execute(
        f"WITH w (id, issn_print, issn_e, nlmid, container_abbrev, csl_type) AS (VALUES (NULL, ?, ?, ?, ?, ?))"
        f" {_status_select('w')}",
        (issn, issn, nlmid, abbrev, csl_type),
    ).fetchone()
    return _journal_details(conn, row["status"], row["journal_id"])


# --- Mise à jour depuis la page de Sorbonne Université ----------------------------------------


def published_from_url(url: str) -> str | None:
    """« …_01-10-26.xlsx » → « 2026-10-01 »."""
    m = re.search(r"(\d{2})-(\d{2})-(\d{2}|\d{4})\.xlsx$", url, re.I)
    if not m:
        return None
    year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
    try:
        return date(year, int(m[2]), int(m[1])).isoformat()
    except ValueError:
        return None


def latest_link(page: str) -> str:
    """Lien vers le .xlsx le plus récent de la liste, sur le site de Sorbonne Université uniquement."""
    links = {urljoin(PAGE_URL, html.unescape(h)) for h in re.findall(r'href="([^"]+?\.xlsx)"', page, re.I)}
    links = [u for u in links if urlparse(u).scheme == "https" and urlparse(u).hostname == ALLOWED_HOST and "revues" in u.lower()]
    if not links:
        raise ListError("Aucun lien vers la liste (.xlsx) sur la page de Sorbonne Université")
    return max(links, key=lambda u: (published_from_url(u) or "", u))


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_meta(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def update(conn: sqlite3.Connection, home: Path) -> dict:
    """Télécharge la liste en ligne si elle a changé.

    En cas d'échec, la version précédente reste en vigueur et l'erreur est mémorisée (`journals.last_error`).
    """
    try:
        page = http.get(PAGE_URL)
        if page is None:
            raise ListError("Page de la liste introuvable (404)")
        url = latest_link(page.text)
        response = http.get(url)
        if response is None:
            raise ListError(f"Fichier de la liste introuvable : {url}")
        if len(response.content) > MAX_DOWNLOAD:
            raise ListError("Fichier de la liste anormalement volumineux")
        _, created = store(conn, response.content, url, published_from_url(url), home)
    except (http.SourceError, ListError) as exc:
        set_meta(conn, "journals.last_error", f"{_now()} {exc}")
        raise
    set_meta(conn, "journals.last_check", _now())
    set_meta(conn, "journals.last_error", None)
    return {"updated": created, **info(conn)}


def update_if_stale(conn: sqlite3.Connection, home: Path) -> dict | None:
    last = get_meta(conn, "journals.last_check")
    if last and datetime.fromisoformat(last) > datetime.now(UTC) - CHECK_EVERY:
        return None
    return update(conn, home)


def info(conn: sqlite3.Connection) -> dict:
    return {
        "current": current_list(conn),
        "last_check": get_meta(conn, "journals.last_check"),
        "last_error": get_meta(conn, "journals.last_error"),
        "page_url": PAGE_URL,
    }


def import_file(conn: sqlite3.Connection, data: bytes, home: Path, filename: str | None = None) -> dict:
    """Import manuel d'un .xlsx (par exemple si la page change de forme)."""
    _, created = store(conn, data, f"manual:{filename or 'liste.xlsx'}", published_from_url(filename or ""), home)
    return {"updated": created, **info(conn)}
