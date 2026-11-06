"""Bibliothèque locale : PDF rangés par empreinte, texte intégral, passages indexés, recherche plein texte."""

import hashlib
import html
import re
import sqlite3
from pathlib import Path

import pypdfium2 as pdfium

from . import db

MAX_PDF = 200_000_000
PASSAGE_CHARS = 1200  # taille visée d'un passage : un paragraphe ou deux
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>(){}\[\]]+", re.I)
MARK_START, MARK_END = "", ""  # balises internes des extraits, converties en <mark> après échappement


class FileError(ValueError):
    """Fichier refusé (pas un PDF, illisible, trop gros)."""


# --- Fichiers -------------------------------------------------------------------------------------


def pdf_path(home: Path, sha: str) -> Path:
    """library/ab/cd/<sha256>.pdf : chemin stable, dérivé du contenu seul."""
    return home / "library" / sha[:2] / sha[2:4] / f"{sha}.pdf"


def store_pdf(conn: sqlite3.Connection, home: Path, data: bytes, original_name: str | None = None) -> tuple[str, bool]:
    """Range un PDF par son empreinte. Renvoie (sha256, nouveau ?). Un PDF déjà connu n'est jamais copié deux fois."""
    if len(data) > MAX_PDF:
        raise FileError("PDF trop volumineux")
    if b"%PDF-" not in data[:1024]:
        raise FileError("Ce fichier n'est pas un PDF")
    sha = hashlib.sha256(data).hexdigest()
    if conn.execute("SELECT 1 FROM files WHERE sha256 = ?", (sha,)).fetchone():
        return sha, False
    path = pdf_path(home, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    try:
        pages = len(pdfium.PdfDocument(tmp))
    except pdfium.PdfiumError as exc:
        tmp.unlink()
        raise FileError(f"PDF illisible : {exc}") from exc
    tmp.replace(path)
    conn.execute(
        "INSERT INTO files (sha256, size, mime, pages, original_name) VALUES (?, ?, 'application/pdf', ?, ?)",
        (sha, len(data), pages, original_name),
    )
    return sha, True


def pdf_text(path: Path, max_pages: int | None = None) -> list[str]:
    """Texte de chaque page (césures de fin de ligne recollées)."""
    doc = pdfium.PdfDocument(path)
    try:
        pages = []
        for i in range(len(doc) if max_pages is None else min(max_pages, len(doc))):
            raw = doc[i].get_textpage().get_text_range()
            # pdfium marque les césures par U+FFFE ; les autres coupures « mot-\nsuite » sont recollées aussi.
            raw = re.sub(r"\ufffe\s*", "", raw)
            text = re.sub(r"(\w)-\r?\n(\w)", r"\1\2", raw)
            pages.append(re.sub(r"[ \t]*\r?\n[ \t]*", "\n", text).replace("\x00", "").strip())
        return pages
    finally:
        doc.close()


def pdf_metadata(path: Path) -> dict:
    doc = pdfium.PdfDocument(path)
    try:
        return {k: v for k, v in doc.get_metadata_dict(skip_empty=True).items()}
    finally:
        doc.close()


def _clean_doi(doi: str) -> str:
    doi = re.sub(r"[.,;:]+$", "", doi)
    if doi.count(")") > doi.count("("):
        doi = doi.rstrip(")")
    return doi.lower()


def find_dois(data: bytes, metadata: dict, pages: list[str]) -> list[str]:
    """DOI candidats d'un PDF, du plus sûr au moins sûr : métadonnées XMP et du document, puis première page,
    puis deuxième page. Les DOI des pages suivantes (références citées) sont ignorés."""
    found: list[str] = []
    xmp = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", data, re.S)
    sources = [xmp.group().decode("utf-8", "ignore") if xmp else "", " ".join(str(v) for v in metadata.values())]
    for text in sources + pages[:2]:
        # Les DOI coupés en fin de ligne se recollent rarement bien : on lit la page sans retours à la ligne.
        for doi in DOI_RE.findall(text.replace("\n", " ")):
            doi = _clean_doi(doi)
            if doi not in found:
                found.append(doi)
    return found


def title_matches(title: str | None, text: str) -> bool:
    """Le titre d'une notice correspond-il au début d'un PDF ? (au moins 70 % de ses mots significatifs)"""
    from .core import normalize  # import local : core importe ce module

    words = [w for w in normalize(title).split() if len(w) > 3]
    if not words:
        return False
    page = set(normalize(text).split())
    return sum(w in page for w in words) / len(words) >= 0.7


def guess_title(metadata: dict, first_page: str) -> str | None:
    """Titre probable d'un PDF sans DOI, pour la saisie assistée."""
    meta = (metadata.get("Title") or "").strip()
    if len(meta.split()) >= 4 and not re.match(r"(microsoft word|untitled|document\d*)", meta, re.I):
        return meta
    lines = [line.strip() for line in first_page.splitlines()[:25] if len(line.split()) >= 5]
    return max(lines, key=len) if lines else None


# --- Passages ----------------------------------------------------------------------------------------


def chunk(text: str, size: int = PASSAGE_CHARS) -> list[str]:
    """Découpe en passages d'environ `size` caractères, aux limites de phrases."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\[«\"])", text)
    passages, current = [], ""
    for sentence in sentences:
        while len(sentence) > size:  # phrase démesurée (tableau, liste) : coupée à la taille
            if current:
                passages.append(current)
                current = ""
            passages.append(sentence[:size])
            sentence = sentence[size:]
        if current and len(current) + 1 + len(sentence) > size:
            passages.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        passages.append(current)
    return passages


def _replace(conn: sqlite3.Connection, work_id: int, origin: str, rows: list[tuple], sha: str | None = None) -> None:
    if sha:
        conn.execute("DELETE FROM passages WHERE work_id = ? AND origin = ? AND sha256 = ?", (work_id, origin, sha))
    else:
        conn.execute("DELETE FROM passages WHERE work_id = ? AND origin = ?", (work_id, origin))
    conn.executemany(
        "INSERT INTO passages (work_id, origin, sha256, page, ord, text) VALUES (?, ?, ?, ?, ?, ?)",
        [(work_id, origin, sha, page, i, text) for i, (page, text) in enumerate(rows)],
    )


def index_work(conn: sqlite3.Connection, work_id: int) -> None:
    """(Ré)indexe les métadonnées et le résumé d'une notice."""
    w = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
    authors = [f"{r['given'] or ''} {r['family']}".strip() for r in
               conn.execute("SELECT family, given FROM work_authors WHERE work_id = ? ORDER BY position", (work_id,))]  # fmt: skip
    subjects = [r[0] for r in conn.execute(
        "SELECT s.term FROM work_subjects ws JOIN subjects s ON s.id = ws.subject_id WHERE ws.work_id = ?", (work_id,))]  # fmt: skip
    meta = ". ".join(x for x in [w["title"], ", ".join(authors), w["container_title"], w["container_abbrev"],
                                  str(w["year"] or ""), "; ".join(subjects)] if x)  # fmt: skip
    _replace(conn, work_id, "metadata", [(None, meta)])
    abstract = [(None, part) for line in (w["abstract"] or "").splitlines() for part in chunk(line)]
    _replace(conn, work_id, "abstract", abstract)


INDEX_VERSION = "2"  # à augmenter quand l'extraction du texte change : les PDF sont alors réindexés


def reindex_if_outdated(conn: sqlite3.Connection, home: Path) -> int:
    """Réextrait le texte des PDF si la méthode d'extraction a changé depuis leur indexation."""
    if db.get_meta(conn, "index.version") == INDEX_VERSION:
        return 0
    shas = [r[0] for r in conn.execute("SELECT DISTINCT sha256 FROM work_files")]
    for sha in shas:  # ponytail: synchrone au démarrage ; à passer en tâche de fond pour une très grosse bibliothèque
        if pdf_path(home, sha).exists():
            index_fulltext(conn, home, sha)
    db.set_meta(conn, "index.version", INDEX_VERSION)
    return len(shas)


def index_missing(conn: sqlite3.Connection) -> int:
    """Indexe les notices qui n'ont pas encore de passages (bases antérieures à la migration 4)."""
    ids = [r[0] for r in conn.execute("SELECT id FROM works WHERE id NOT IN (SELECT work_id FROM passages WHERE origin = 'metadata')")]
    for work_id in ids:
        index_work(conn, work_id)
    return len(ids)


REFERENCES_HEADING = re.compile(r"^\s*(references|bibliography|literature cited|références( bibliographiques)?|bibliographie)\s*:?\s*$",
                                re.I | re.M)  # fmt: skip


def without_references(pages: list[str]) -> list[str]:
    """Retire la liste des références en fin d'article (à partir du titre « References », dans la seconde moitié
    du document) : elle n'apporte que du bruit à la recherche et rallonge l'indexation."""
    for n in range(len(pages) - 1, len(pages) // 2 - 1, -1):
        if m := REFERENCES_HEADING.search(pages[n]):
            return pages[:n] + [pages[n][: m.start()]]
    return pages


def index_fulltext(conn: sqlite3.Connection, home: Path, sha: str) -> str:
    """Extrait le texte d'un PDF et l'indexe pour chaque notice à laquelle il est rattaché. Renvoie le statut."""
    try:
        pages = pdf_text(pdf_path(home, sha))
    except pdfium.PdfiumError:
        conn.execute("UPDATE files SET text_status = 'error' WHERE sha256 = ?", (sha,))
        return "error"
    rows = [(n, part) for n, page in enumerate(without_references(pages), 1) for part in chunk(page)]
    status = "done" if rows else "empty"  # « empty » : PDF scanné, sans couche texte (pas d'OCR)
    for (work_id,) in conn.execute("SELECT work_id FROM work_files WHERE sha256 = ?", (sha,)).fetchall():
        _replace(conn, work_id, "fulltext", rows, sha)
    conn.execute("UPDATE files SET text_status = ? WHERE sha256 = ?", (status, sha))
    return status


def attach(conn: sqlite3.Connection, home: Path, work_id: int, sha: str, source_url: str | None = None) -> bool:
    """Rattache un fichier à une notice et indexe son texte. Renvoie False s'il l'était déjà."""
    added = conn.execute(
        "INSERT OR IGNORE INTO work_files (work_id, sha256, source_url) VALUES (?, ?, ?)", (work_id, sha, source_url)
    ).rowcount
    if added:
        index_fulltext(conn, home, sha)
    return bool(added)


def files_of(conn: sqlite3.Connection, work_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT f.sha256, f.size, f.pages, f.original_name, f.text_status, wf.role, wf.source_url, wf.added_at"
        " FROM work_files wf JOIN files f ON f.sha256 = wf.sha256 WHERE wf.work_id = ? ORDER BY wf.added_at", (work_id,))]  # fmt: skip


def unattached_files(conn: sqlite3.Connection) -> list[dict]:
    """PDF rangés mais rattachés à aucune notice : ils attendent une saisie assistée."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM files WHERE sha256 NOT IN (SELECT sha256 FROM work_files) ORDER BY added_at DESC")]  # fmt: skip


# --- Recherche plein texte -------------------------------------------------------------------------------


# Mots outils ignorés dans une question en langage courant (« études sur la transmission de BLSE… ») : sinon
# « de » ou « la » trouvent des noms d'auteurs (De Rekeneire) et des passages sans rapport.
QUESTION_STOPWORDS = set("""
a à au aux avec ce ces chez comment dans de des du elle en est et être il ils la le les leur lors mais ou où par
pas pour quel quelle quelles quels qui que quoi sa se ses son sont sous sur un une y sont-ils est-il est-ce
an and are as at be by can do does for from how in into is it of on or the to was what when which who why with
""".split())  # fmt: skip


def fts_query(text: str, any_word: bool = False) -> str:
    """Requête FTS5 sûre : chaque mot entre guillemets, reliés par AND (ou OR). Les mots outils sont retirés,
    sauf si la requête n'est faite que de mots outils."""
    words = re.findall(r"[\w][\w'-]*", text)
    meaningful = [w for w in words if w.lower() not in QUESTION_STOPWORDS and len(w) > 1]
    return f" {'OR' if any_word else 'AND'} ".join(f'"{w}"' for w in meaningful or words)


def _snippet(raw: str) -> str:
    return html.escape(raw).replace(MARK_START, "<mark>").replace(MARK_END, "</mark>")


RRF_K = 60  # fusion par rangs réciproques : valeur usuelle
CANDIDATES = 300  # passages retenus par chaque moteur avant la fusion
# En dessous de ce cosinus, un passage trouvé par le sens seul n'est pas montré (bge-m3 : ~0,3 pour un texte sans rapport).
SEMANTIC_MIN = 0.45


def _lexical(conn: sqlite3.Connection, text: str) -> tuple[str, list[sqlite3.Row]]:
    """Plein texte (BM25) : tous les mots, sinon n'importe lequel ; syntaxe FTS5 acceptée telle quelle."""
    attempts = []
    if re.search(r'"|\*|\b(AND|OR|NOT|NEAR)\b', text):
        attempts.append(("advanced", text))
    attempts += [("all", fts_query(text)), ("any", fts_query(text, any_word=True))]
    for mode, query in attempts:
        if not query:
            continue
        try:
            rows = conn.execute(
                f"""SELECT p.id, bm25(passages_fts) AS score,
                           snippet(passages_fts, 0, '{MARK_START}', '{MARK_END}', '…', 24) AS snippet
                    FROM passages_fts JOIN passages p ON p.id = passages_fts.rowid
                    WHERE passages_fts MATCH ? ORDER BY score LIMIT ?""",
                (query, CANDIDATES),
            ).fetchall()
        except sqlite3.OperationalError:  # syntaxe FTS5 invalide : on retombe sur la requête protégée
            continue
        if rows:
            return mode, rows
    return "all", []


def search(conn: sqlite3.Connection, text: str, limit: int = 20, per_work: int = 3, engine: str = "hybrid") -> dict:
    """Recherche dans les métadonnées, résumés, notes et textes intégraux.

    `engine` : `hybrid` (défaut : plein texte + sens, classements fusionnés par RRF), `lexical` ou `semantic`.
    Le sens n'est utilisé que si le modèle est chargé ; sinon la recherche reste plein texte (`engine` le dit).
    Extraits : HTML échappé, mots trouvés en `<mark>` ; un passage trouvé par le sens seul est montré tel quel.
    `mode` (plein texte) : all (tous les mots), any (au moins un), advanced (syntaxe FTS5)."""
    from . import semantic  # import local : semantic dépend de la base, ce module n'en a besoin qu'ici

    if engine not in ("hybrid", "lexical", "semantic"):
        raise ValueError(f"Moteur inconnu : {engine}")
    model = semantic.embedder() if engine != "lexical" else None
    if engine == "semantic" and model is None:
        raise ValueError("Recherche sémantique indisponible : le modèle n'est pas encore chargé")
    mode, lexical = _lexical(conn, text) if engine != "semantic" else ("none", [])
    nearby = [(pid, cos) for pid, cos in semantic.nearest(conn, model.encode([text])[0], CANDIDATES)] if model else []

    scores: dict[int, float] = {}
    snippets: dict[int, str] = {}
    matched: dict[int, set] = {}
    # Une question en langage courant ne trouve souvent qu'une partie de ses mots (mode « any ») : cet indice faible
    # pèse alors moitié moins que le sens, sinon un mot isolé (« réservoir ») l'emporterait sur la question entière.
    weight = 0.5 if mode == "any" and nearby else 1.0
    for rank, row in enumerate(lexical, 1):
        scores[row["id"]] = weight / (RRF_K + rank)
        snippets[row["id"]] = _snippet(row["snippet"])
        matched.setdefault(row["id"], set()).add("lexical")
    for rank, (pid, cos) in enumerate(nearby, 1):
        if cos < SEMANTIC_MIN and pid not in scores:
            continue
        scores[pid] = scores.get(pid, 0) + 1 / (RRF_K + rank)
        matched.setdefault(pid, set()).add("semantic")
    if not scores:
        return {"engine": "semantic" if engine == "semantic" else ("hybrid" if model else "lexical"),
                "mode": mode, "total": 0, "results": []}  # fmt: skip

    ids = list(scores)
    passages = {r["id"]: r for r in conn.execute(
        f"SELECT id, work_id, origin, page, text FROM passages WHERE id IN ({','.join('?' * len(ids))})", ids)}  # fmt: skip
    works: dict[int, list[int]] = {}
    for pid in sorted(ids, key=lambda i: -scores[i]):
        if pid in passages:
            works.setdefault(passages[pid]["work_id"], []).append(pid)
    # Une notice vaut son meilleur passage ; à égalité, celle qui a le plus de passages pertinents passe devant.
    ranked = sorted(works.items(), key=lambda kv: (-scores[kv[1][0]], -len(kv[1])))[:limit]
    results = []
    for work_id, pids in ranked:
        w = conn.execute(
            "SELECT w.id, w.citekey, w.title, w.year, w.container_title, w.container_abbrev, s.status AS journal_status,"
            " (SELECT family FROM work_authors WHERE work_id = w.id AND position = 0) AS first_author"
            " FROM works w JOIN work_journal_status s ON s.work_id = w.id WHERE w.id = ?", (work_id,),
        ).fetchone()  # fmt: skip
        shown = []
        for pid in pids[:per_work]:
            p = passages[pid]
            text_ = snippets.get(pid) or html.escape(p["text"][:320] + ("…" if len(p["text"]) > 320 else ""))
            how = "both" if len(matched[pid]) == 2 else next(iter(matched[pid]))
            shown.append({"origin": p["origin"], "page": p["page"], "snippet": text_, "match": how})
        results.append({**dict(w), "score": round(scores[pids[0]], 6), "hits": len(pids), "passages": shown})
    used = "semantic" if engine == "semantic" else ("hybrid" if model else "lexical")
    return {"engine": used, "mode": mode, "total": len(works), "results": results}
