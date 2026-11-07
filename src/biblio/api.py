"""API REST locale (FastAPI). Documentation interactive : /docs ; schéma : /openapi.json."""

import html
import logging
import os
import re
import io
import secrets
import signal
import sqlite3
import threading
import time
import zipfile
from collections.abc import Iterator
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (__version__, command, ai, ask, backup, config, core, db, exports, formats, importer, journals, library, notes,
               organize, llm, packages, search, semantic, trace, watch, webhooks, zotero, zotero_rdf)
from .config import Config
from .sources import http
from .sources.http import SourceError

log = logging.getLogger("biblio")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_UPLOAD = 200_000_000


def binary_body(media_type: str) -> dict:
    """Documentation OpenAPI d'un corps de requête brut (fichier envoyé tel quel)."""
    return {"requestBody": {"content": {media_type: {"schema": {"type": "string", "format": "binary"}}}, "required": True}}


async def raw_body(request: Request) -> bytes:
    data = await request.body()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Fichier trop volumineux")
    if not data:
        raise HTTPException(422, "Corps de requête vide : envoyer le fichier tel quel")
    return data


def require_token(request: Request, authorization: Annotated[str, Header()] = "") -> None:
    expected = f"Bearer {request.app.state.config.token}"
    if not secrets.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, "Jeton manquant ou invalide", headers={"WWW-Authenticate": "Bearer"})


def connection(request: Request) -> Iterator:
    conn = db.connect(request.app.state.config.db_path)
    try:
        yield conn
    finally:
        conn.close()


def client_name(x_biblio_client: Annotated[str, Header()] = "api") -> str:
    """Le client qui fait la modification (cli, web, mcp…), inscrit au journal des modifications."""
    if x_biblio_client not in core.CLIENTS:
        raise HTTPException(422, f"X-Biblio-Client inconnu : {x_biblio_client}")
    return x_biblio_client


Conn = Annotated[sqlite3.Connection, Depends(connection)]


class AddRequest(BaseModel):
    identifier: str
    pdf: bool = True  # télécharger la version libre et légale si elle existe
    question: str | None = None  # question posée qui a mené à cet article (écran Question)
    scope: Literal["online", "library"] = "online"


class ManualWork(BaseModel):
    title: str
    authors: list[str] = []  # « Nom, Prénom »
    year: int | None = None
    container_title: str | None = None
    container_abbrev: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    abstract: str | None = None
    csl_type: str | None = None
    study_type: str | None = None
    issn_print: str | None = None
    issn_e: str | None = None
    language: str | None = None
    identifiers: list[str] = []  # DOI, PMID, URL…
    keywords: list[str] = []
    question: str | None = None  # question posée qui a mené à cet article (écran Question)


class AttachRequest(BaseModel):
    identifier: str | None = None  # DOI, PMID… (la notice est ajoutée au besoin)
    work: str | None = None  # ou notice existante : id, clé, DOI, PMID


class MergeRequest(BaseModel):
    keep: str
    drop: str


class NoteBody(BaseModel):
    text: str


class WorkUpdate(BaseModel):
    read_status: Literal[organize.READ_STATUSES] | None = None  # type: ignore[valid-type]
    rating: int | None = None


class LabelCreate(BaseModel):
    kind: Literal[organize.KINDS]  # type: ignore[valid-type]
    name: str


class LabelRename(BaseModel):
    name: str


class LabelWorks(BaseModel):
    works: list[str]  # id, clé, DOI ou PMID
    remove: bool = False


class AskRequest(BaseModel):
    question: str
    scope: Literal["online", "library"] = "online"
    query: str | None = None  # requête PubMed modifiée (sinon : la question)
    limit: int = 20
    year_from: int | None = None
    year_to: int | None = None
    study_type: list[str] = []
    open_access: bool = False
    all_journals: bool = False  # montrer aussi les revues hors liste
    origin: str | None = None  # question de départ, quand celle-ci est une question liée cliquée (IA)


class SavedSearch(BaseModel):
    question: str
    scope: Literal["online", "library"] = "online"
    query: str | None = None
    answer: list[dict] = []
    sources: list[dict] = []
    synthesis: dict | None = None  # réponse rédigée : {"text", "sentences", "model"} ; sans `sentences`, Biblio vérifie
    origin: Literal["biblio", "claude", "mcp"] = "biblio"  # claude / mcp : synthèse écrite par un client MCP


class Settings(BaseModel):
    """Réglages modifiables depuis l'interface (les autres restent dans config.toml)."""

    contact_email: str | None = None
    ncbi_api_key: str | None = None
    openalex_api_key: str | None = None
    semanticscholar_api_key: str | None = None
    watch_downloads: bool | None = None
    downloads_dir: str | None = None
    semantic: bool | None = None
    language: Literal["", "fr", "en"] | None = None
    theme: Literal["system", "light", "dark"] | None = None
    citation_style: str | None = None
    llm_enabled: bool | None = None
    llm_provider: Literal[tuple(llm.PRESETS)] | None = None  # type: ignore[valid-type]
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None  # rangée dans le trousseau du système, jamais renvoyée ; "" la supprime


class AnswerRequest(BaseModel):
    question: str
    sources: list[dict]  # sources renvoyées par POST /ask, dans le même ordre


class SummaryRequest(BaseModel):
    record: dict  # notice (titre, résumé)
    lang: Literal["fr", "en"] = "fr"


class Selection(BaseModel):
    """Notices visées : `works` (id, clé, DOI, PMID), ou une collection / un tag (`label`), ou toute la bibliothèque."""

    works: list[str] = []
    label: int | None = None


class ExportRequest(Selection):
    format: Literal[exports.EXPORT_FORMATS]  # type: ignore[valid-type]
    style: str | None = None  # Markdown : style CSL (défaut : celui des réglages)


class ZoteroRequest(Selection):
    pdf: bool = True


class PackageRequest(Selection):
    title: str | None = None
    searches: list[int] = []  # recherches enregistrées jointes


def selected(conn, sel: Selection) -> list:
    if sel.works:
        return sel.works
    if sel.label is not None:
        return [r[0] for r in conn.execute("SELECT work_id FROM work_labels WHERE label_id = ?", (sel.label,))]
    return exports.all_refs(conn)


def style_of(request: Request, style: str | None = None) -> str:
    return style or request.app.state.config.citation_style or exports.DEFAULT_STYLE


def settings_of(cfg: Config) -> dict:
    return {
        "contact_email": cfg.contact_email, **{f"{k}_api_key": v for k, v in cfg.api_keys.items()},
        "watch_downloads": cfg.watch_downloads, "downloads_dir": str(cfg.downloads_dir), "semantic": cfg.semantic,
        "language": cfg.language, "theme": cfg.theme, "citation_style": cfg.citation_style or exports.DEFAULT_STYLE,
        "llm_enabled": cfg.llm_enabled, "llm_provider": cfg.llm_provider, "llm_base_url": cfg.llm_base_url,
        "llm_model": cfg.llm_model, "llm_key_set": bool(llm.get_key(cfg.llm_provider)),
        "home": str(cfg.home), "version": __version__,
    }  # fmt: skip


def home(request: Request):
    return request.app.state.config.home


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


Client = Annotated[str, Depends(client_name)]


@router.post("/works", summary="Ajouter un article par identifiant (DOI, PMID, PMCID, arXiv, URL)")
def add_work(body: AddRequest, request: Request, response: Response, conn: Conn, client: Client):
    """Récupère la notice (PubMed + Crossref, ou arXiv) et l'ajoute à la bibliothèque, avec son PDF s'il est en
    accès libre (`pdf`). 201 si la notice est créée, 200 si l'article était déjà présent (notice existante,
    éventuellement complétée). `warnings` signale une source injoignable ou un PDF non récupéré. `question` est
    gardée avec la notice (`questions`)."""
    result = importer.add(conn, home(request), body.identifier, client, body.pdf)
    if body.question:
        organize.add_question(conn, result["work"]["id"], body.question, body.scope)
        result["work"] = core.get_work(conn, result["work"]["id"])
    response.status_code = 201 if result["created"] else 200
    return result


@router.post("/works/manual", status_code=201, summary="Saisie manuelle d'une notice")
def add_manual(body: ManualWork, response: Response, conn: Conn, client: Client):
    """Pour un article sans identifiant. Si un identifiant fourni est déjà connu, la notice existante est complétée (200)."""
    work, created = core.add_manual(conn, body.model_dump(exclude={"question"}), client)
    if body.question:
        organize.add_question(conn, work["id"], body.question)
        work = core.get_work(conn, work["id"])
    response.status_code = 201 if created else 200
    return {"created": created, "work": work}


@router.post("/import", summary="Importer un fichier BibTeX, RIS, MEDLINE (.nbib) ou CSL-JSON",
             openapi_extra=binary_body("text/plain"))  # fmt: skip
async def import_file(request: Request, conn: Conn, client: Client, data: Annotated[bytes, Depends(raw_body)],
                      format: Literal[formats.FORMATS] | None = None):  # type: ignore[valid-type]  # fmt: skip
    """Corps : le fichier tel quel (format détecté si `format` est omis). Import hors ligne : les notices déjà
    présentes sont complétées, les doublons probables signalés (voir /duplicates). Un export Zotero RDF envoyé
    ainsi est lu sans ses PDF (ils sont dans le dossier de l'export : voir POST /import/zotero-rdf)."""
    if b"<rdf:RDF" in data[:4000] and b"zotero.org/namespaces/export" in data[:4000]:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Import Zotero.rdf").write_bytes(data)
            return {**zotero_rdf.import_rdf(conn, home(request), Path(tmp) / "Import Zotero.rdf"), "format": "zotero-rdf"}
    return importer.import_bibliography(conn, data.decode("utf-8-sig", errors="replace"), format, client)


class RdfImport(BaseModel):
    path: str  # fichier .rdf de l'export
    label: int | None = None  # collection de Biblio où ranger les articles (celle sélectionnée dans la Bibliothèque)


@router.post("/import/zotero-rdf", summary="Importer un export Zotero RDF (chemin du fichier .rdf sur cet ordinateur)")
def import_zotero_rdf(body: RdfImport, request: Request, conn: Conn):
    """Notices, PDF (dossier files/ de l'export), notes, tags et collections. Dans Zotero : Exporter… → Zotero RDF,
    avec « Exporter les fichiers » et « Exporter les notes ». Un article déjà présent est complété, pas dupliqué."""
    path = Path(body.path).expanduser()
    if path.suffix.lower() != ".rdf" or not path.is_file():
        raise HTTPException(422, f"Pas un export Zotero RDF : {path.name}")
    return {**zotero_rdf.import_rdf(conn, home(request), path, body.label), "format": "zotero-rdf"}


@router.post("/files", summary="Déposer un PDF", openapi_extra=binary_body("application/pdf"))
async def upload_pdf(request: Request, conn: Conn, client: Client, data: Annotated[bytes, Depends(raw_body)],
                     filename: str | None = None):  # fmt: skip
    """Range le PDF par empreinte, repère son DOI (métadonnées, premières pages), vérifie qu'il correspond au titre,
    puis crée ou complète la notice. `status` : attached, duplicate ou needs_metadata (voir `candidates`, puis
    POST /files/{sha256}/attach)."""
    return importer.import_pdf(conn, home(request), data, filename, client)


@router.get("/files", summary="Fichiers rangés sans notice (en attente de saisie assistée)")
def list_unattached(conn: Conn):
    return library.unattached_files(conn)


@router.get("/files/{sha256}", summary="Télécharger un PDF de la bibliothèque", response_class=FileResponse)
def get_file(sha256: str, request: Request):
    path = library.pdf_path(home(request), sha256.lower())
    if not (len(sha256) == 64 and path.is_file()):
        raise HTTPException(404, "Fichier inconnu")
    return FileResponse(path, media_type="application/pdf")


@router.post("/files/{sha256}/attach", summary="Rattacher un PDF à une notice (saisie assistée)")
def attach_file(sha256: str, body: AttachRequest, request: Request, conn: Conn, client: Client):
    return importer.attach_file(conn, home(request), sha256.lower(), body.identifier, body.work, client)


@router.get("/search/library", summary="Recherche dans la bibliothèque (plein texte + sens)")
def search_library(conn: Conn, q: Annotated[str, Query(min_length=1)], limit: Annotated[int, Query(ge=1, le=200)] = 20,
                   engine: Literal["hybrid", "lexical", "semantic"] = "hybrid"):  # fmt: skip
    """Cherche dans les métadonnées, résumés, notes et textes intégraux, en plein texte (BM25) et par le sens
    (modèle local bge-m3, multilingue : une question en français trouve des passages en anglais). Les deux classements
    sont fusionnés (RRF). Sans modèle chargé, `engine` vaut « lexical ». Extraits en HTML échappé, mots trouvés en
    `<mark>` ; `match` indique quel moteur a trouvé chaque passage."""
    return library.search(conn, q, limit, engine=engine)


@router.get("/semantic", summary="État de la recherche sémantique (modèle, indexation)")
def semantic_status(request: Request, conn: Conn):
    return semantic.status(conn, request.app.state.config.models_dir)


@router.post("/semantic/download", status_code=202, summary="Télécharger le modèle sémantique (~2,3 Go, une fois)")
def semantic_download(request: Request, conn: Conn):
    """Télécharge bge-m3 (version ONNX officielle, figée) dans le dossier des modèles, en arrière-plan.
    Suivre l'avancement avec GET /semantic. L'indexation démarre dès que le modèle est prêt."""
    models_dir = request.app.state.config.models_dir
    semantic.start_download(models_dir)
    return semantic.status(conn, models_dir)


@router.get("/duplicates", summary="Doublons probables à fusionner ou écarter")
def list_duplicates(conn: Conn):
    return core.duplicates(conn)


@router.post("/duplicates/merge", summary="Fusionner deux notices (drop → keep)")
def merge_duplicates(body: MergeRequest, request: Request, conn: Conn, client: Client):
    return core.merge_works(conn, home(request), body.keep, body.drop, client)


@router.post("/duplicates/dismiss", status_code=204, summary="Écarter un doublon signalé (ce ne sont pas les mêmes)")
def dismiss_duplicate(conn: Conn, a: Annotated[str, Body()], b: Annotated[str, Body()]):
    core.dismiss_duplicate(conn, a, b)


@router.post("/notes/sync", summary="Réindexer les notes modifiées hors de Biblio (Obsidian…)")
def sync_notes(request: Request, conn: Conn):
    return notes.sync(conn, home(request))


@router.post("/inbox/scan", summary="Importer maintenant les PDF de la boîte d'entrée (et des Téléchargements si activé)")
def scan_inbox(request: Request, conn: Conn):
    config = request.app.state.config
    results = importer.scan_inbox(conn, config.home, min_age=0)
    if config.watch_downloads:
        results += importer.scan_downloads(conn, config.downloads_dir, config.home, min_age=0)
    return results


@router.post("/shutdown", status_code=202, summary="Arrêter le serveur")
def shutdown(background: BackgroundTasks):
    # Même effet qu'un Ctrl-C : uvicorn termine proprement les requêtes en cours.
    background.add_task(os.kill, os.getpid(), signal.SIGTERM)
    return {"status": "stopping"}


@router.get("/settings", summary="Réglages modifiables depuis l'interface")
def get_settings(request: Request):
    return settings_of(request.app.state.config)


@router.put("/settings", summary="Modifier des réglages (champs omis : inchangés) ; écrit config.toml")
def put_settings(body: Settings, request: Request):
    """Les e-mail et clés servent dès la requête suivante. Activer ou couper la recherche sémantique prend effet au
    prochain démarrage (`restart_needed`)."""
    cfg = request.app.state.config
    changes = body.model_dump(exclude_none=True)
    if "contact_email" in changes and changes["contact_email"].strip() and "@" not in changes["contact_email"]:
        raise HTTPException(422, "Adresse e-mail invalide")
    if changes.get("citation_style") and changes["citation_style"] not in exports.style_paths(cfg.home):
        raise HTTPException(422, f"Style inconnu : {changes['citation_style']}")
    keys = {k: changes.pop(f"{k}_api_key", v).strip() for k, v in cfg.api_keys.items()}
    if (llm_key := changes.pop("llm_api_key", None)) is not None:
        llm.set_key(changes.get("llm_provider", cfg.llm_provider), llm_key.strip())
    if "downloads_dir" in changes:
        changes["downloads_dir"] = Path(changes["downloads_dir"]).expanduser()
    new = replace(cfg, **{k: v.strip() if isinstance(v, str) else v for k, v in changes.items()}, api_keys=keys)
    config.save(new)
    request.app.state.config = new
    http.configure(new.contact_email, new.api_keys, new.db_path)
    llm.configure(llm.Settings.of(new))
    return {**settings_of(new), "restart_needed": new.semantic != request.app.state.semantic_at_start}


@router.post("/ask", summary="Poser une question : réponse extraite des sources, avec ses références")
def ask_question(body: AskRequest, conn: Conn):
    """`scope` : online (bases en ligne, filtre des revues recommandées par défaut) ou library. `answer` : phrases
    choisies dans les sources, chacune avec le numéro de sa source (1…). Chaque source a son `summary` (conclusion
    des auteurs) et son `sample_size` quand le résumé les donne."""
    return _ask(body, conn)


def _ask(body: AskRequest, conn: sqlite3.Connection) -> dict:
    organize.remember_question(conn, body.question, body.scope)
    if body.scope == "library":
        return ask.ask_library(conn, body.question, body.limit)
    filters = search.Filters(year_from=body.year_from, year_to=body.year_to, study_types=body.study_type,
                             open_access=body.open_access, journal_status=[] if body.all_journals else ["recommended"])  # fmt: skip
    return ask.ask_online(conn, body.question, filters, body.limit, body.query, body.origin)


@router.post("/ask/stream", summary="Poser une question en suivant son déroulé (SSE)")
def ask_stream(body: AskRequest, request: Request):
    """Comme POST /ask, mais les étapes arrivent au fil de l'eau : `{"step": {"kind", …}}` (mots-clés et
    traductions, requête de l'IA, chaque requête envoyée aux bases, résultats par base, fusion, filtres), puis
    `{"result": …}` (la réponse de POST /ask) ou `{"error": "…"}`."""
    import json as _json
    import queue

    events: queue.Queue = queue.Queue()
    db_path = request.app.state.config.db_path

    def work():
        trace.listen(lambda step: events.put({"step": step}))
        conn = db.connect(db_path)
        try:
            events.put({"result": _ask(body, conn)})
        except (ValueError, core.NotFound, SourceError, llm.LLMError) as exc:
            events.put({"error": str(exc)})
        except Exception as exc:  # erreur imprévue : la montrer plutôt que laisser l'écran en attente
            log.exception("Recherche : erreur inattendue")
            events.put({"error": f"Erreur inattendue : {exc}"})
        finally:
            conn.close()

    threading.Thread(target=work, daemon=True, name="ask").start()

    def stream():
        while True:
            event = events.get()
            yield f"data: {_json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            if "step" not in event:
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class ForgetRequest(BaseModel):
    question: str | None = None  # omise : tout l'historique
    scope: Literal["online", "library"] | None = None


@router.get("/history", summary="Questions posées récemment (la plus récente d'abord)")
def question_history(conn: Conn, limit: Annotated[int, Query(ge=1, le=200)] = 30):
    return organize.question_history(conn, limit)


@router.post("/history/forget", summary="Retirer une question de l'historique (ou tout l'effacer)")
def forget_question(body: ForgetRequest, conn: Conn):
    return {"removed": organize.forget_question(conn, body.question, body.scope)}


@router.get("/llm", summary="Intelligence artificielle : fournisseur, modèle, joignable, modèles proposés")
def llm_status():
    return llm.status()


@router.post("/ask/answer", summary="Réponse rédigée par le modèle de langue, diffusée au fil de l'eau (SSE)")
def ask_answer(body: AnswerRequest):
    """Événements `data:` JSON : `{"delta": "…"}` pendant la rédaction, puis `{"done": true, "text", "sentences",
    "model"}` où chaque phrase est vérifiée contre les sources qu'elle cite (`status` : ok, weak, unsourced,
    invalid), ou `{"error": "…"}`."""
    import json as _json

    def events():
        text = ""
        try:
            for piece in ai.answer_stream(body.question, body.sources):
                text += piece
                yield f"data: {_json.dumps({'delta': piece}, ensure_ascii=False)}\n\n"
            done = {"done": True, "text": text, "sentences": ai.verify(text, body.sources), "model": llm.current.model}
            yield f"data: {_json.dumps(done, ensure_ascii=False)}\n\n"
        except llm.LLMError as exc:
            yield f"data: {_json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/summary", summary="Résumé rédigé d'un article par le modèle de langue (gardé en cache)")
def ai_summary(body: SummaryRequest, conn: Conn):
    return ai.summary(conn, body.record, body.lang)


@router.get("/searches", summary="Recherches enregistrées (les plus récentes d'abord)")
def list_searches(conn: Conn):
    return organize.searches(conn)


@router.post("/searches", status_code=201, summary="Enregistrer une recherche : question, réponse et sources")
def save_search(body: SavedSearch, conn: Conn):
    """Une synthèse envoyée sans `sentences` (Claude par MCP…) est vérifiée ici, phrase par phrase, contre les
    sources qu'elle cite, comme les réponses du modèle de Biblio."""
    data = body.model_dump()
    if (synthesis := data["synthesis"]) and "sentences" not in synthesis:
        synthesis["sentences"] = ai.verify(synthesis.get("text", ""), data["sources"])
    saved = organize.save_search(conn, data)
    webhooks.emit(conn, "search.saved", {"id": saved["id"], "question": saved["question"], "origin": saved["origin"],
                                         "count": len(saved["sources"])})  # fmt: skip
    return saved


@router.get("/searches/{search_id}", summary="Relire une recherche enregistrée")
def get_search(search_id: int, conn: Conn):
    return organize.get_search(conn, search_id)


@router.delete("/searches/{search_id}", status_code=204, summary="Supprimer une recherche enregistrée")
def delete_search(search_id: int, conn: Conn):
    organize.delete_search(conn, search_id)


@router.get("/labels", summary="Collections et tags, avec leur nombre de notices")
def list_labels(conn: Conn):
    return organize.labels(conn)


@router.post("/labels", status_code=201, summary="Créer une collection ou un tag (renvoie l'existant si le nom est pris)")
def create_label(body: LabelCreate, conn: Conn):
    return organize.create_label(conn, body.kind, body.name)


@router.patch("/labels/{label_id}", summary="Renommer une collection ou un tag")
def rename_label(label_id: int, body: LabelRename, conn: Conn):
    return organize.rename_label(conn, label_id, body.name)


@router.delete("/labels/{label_id}", status_code=204, summary="Supprimer une collection ou un tag (pas ses notices)")
def delete_label(label_id: int, conn: Conn):
    organize.delete_label(conn, label_id)


@router.post("/labels/{label_id}/works", summary="Ranger des notices dans une collection / un tag (ou les en retirer)")
def label_works(label_id: int, body: LabelWorks, conn: Conn, client: Client):
    return {"changed": organize.set_label(conn, label_id, body.works, not body.remove, client)}


@router.get("/styles", summary="Styles de citation disponibles (fournis et ajoutés)")
def list_styles(request: Request):
    return exports.styles(home(request))


@router.post("/styles", status_code=201, summary="Ajouter un style de citation (.csl, corps brut)",
             openapi_extra=binary_body("application/xml"))  # fmt: skip
async def add_style(request: Request, data: Annotated[bytes, Depends(raw_body)], filename: str | None = None):
    """Styles : <https://www.zotero.org/styles>. Un style dépendant est refusé (ajouter son style parent)."""
    return exports.add_style(home(request), data, filename)


@router.post("/export", summary="Exporter des notices : CSL-JSON, BibTeX, RIS ou Markdown (liste formatée)")
def export_works(body: ExportRequest, request: Request, conn: Conn):
    content, media = exports.export(conn, home(request), selected(conn, body), body.format, style_of(request, body.style))
    ext = {"csljson": "json", "bibtex": "bib", "ris": "ris", "markdown": "md"}[body.format]
    return Response(content, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="biblio.{ext}"'})  # fmt: skip


class CiteRequest(BaseModel):
    record: dict  # notice au format interne (résultat de recherche en ligne)
    style: str | None = None


@router.post("/citation", summary="Référence formatée d'une notice hors bibliothèque (résultat de recherche)")
def cite_record(body: CiteRequest, request: Request):
    style = style_of(request, body.style)
    return {"style": style, "text": exports.citation(body.record, home(request), style)}


@router.get("/zotero", summary="Zotero est-il ouvert ? Collection de destination")
def zotero_status():
    return zotero.status()


@router.post("/zotero", summary="Envoyer des notices (avec note, tags et PDF) dans Zotero, qui doit être ouvert")
def zotero_send(body: ZoteroRequest, request: Request, conn: Conn):
    """Par le serveur local de Zotero (127.0.0.1:23119) : les notices arrivent dans la collection sélectionnée."""
    return zotero.send(conn, home(request), selected(conn, body), body.pdf)


@router.post("/packages", summary="Créer un paquet .biblio (notices, PDF, notes, tags, questions, recherches)")
def create_package(body: PackageRequest, request: Request, conn: Conn):
    # Une recherche enregistrée seule : ses articles déjà dans la bibliothèque, pas toute la bibliothèque.
    refs = selected(conn, body) if body.works or body.label is not None or not body.searches else []
    data = packages.create(conn, home(request), refs, body.title, body.searches)
    return Response(data, media_type="application/x-biblio",
                    headers={"Content-Disposition": 'attachment; filename="articles.biblio"'})  # fmt: skip


@router.post("/packages/preview", summary="Aperçu d'un paquet .biblio avant import (corps brut)",
             openapi_extra=binary_body("application/x-biblio"))  # fmt: skip
async def preview_package(conn: Conn, data: Annotated[bytes, Depends(raw_body)]):
    return packages.preview(conn, data)


@router.post("/packages/import", summary="Importer un paquet .biblio (corps brut)", openapi_extra=binary_body("application/x-biblio"))
async def import_package(request: Request, conn: Conn, data: Annotated[bytes, Depends(raw_body)]):
    """Articles déjà présents complétés, jamais dupliqués ; notes différentes ajoutées à la suite ; tags,
    collections, questions et recherches enregistrées repris."""
    return packages.import_package(conn, home(request), data)


class LocalFile(BaseModel):
    path: str


@router.post("/packages/file", summary="Lire un paquet .biblio de l'ordinateur (ouvert par double-clic)",
             response_class=Response)  # fmt: skip
def package_file(body: LocalFile):
    """La fenêtre reçoit le chemin du fichier ouvert dans le Finder ; l'interface en lit le contenu ici, puis suit le
    chemin habituel (aperçu, import). Seuls les fichiers .biblio sont lus."""
    path = Path(body.path).expanduser()
    if path.suffix.lower() != ".biblio" or not path.is_file():
        raise HTTPException(422, f"Pas un paquet .biblio : {path.name}")
    return Response(path.read_bytes(), media_type="application/x-biblio")


class WatchRequest(BaseModel):
    days: int | None  # intervalle de relance ; null : ne plus suivre


class SeenRequest(BaseModel):
    ids: list[int] | None = None  # omis : toutes


class SupersedeRequest(BaseModel):
    doi: str  # DOI de la version publiée


@router.put("/searches/{search_id}/watch", summary="Suivre une recherche enregistrée (relance tous les `days` jours)")
def watch_search(search_id: int, body: WatchRequest, conn: Conn):
    """Les sources déjà enregistrées comptent comme vues : seuls les nouveaux articles seront signalés."""
    return watch.set_watch(conn, search_id, body.days)


@router.post("/searches/{search_id}/run", summary="Relancer maintenant une recherche suivie (nouveaux résultats)")
def run_search(search_id: int, conn: Conn):
    return {"new": watch.run_search(conn, search_id)}


@router.get("/alerts", summary="Alertes de veille : nouveaux résultats, rétractations, corrections, preprints publiés")
def list_alerts(conn: Conn, unseen_only: bool = False):
    return watch.alerts(conn, unseen_only)


@router.post("/alerts/seen", summary="Marquer des alertes comme lues (`ids` omis : toutes)")
def alerts_seen(body: SeenRequest, conn: Conn):
    return {"seen": watch.mark_seen(conn, body.ids)}


@router.post("/watch/library", summary="Vérifier maintenant les articles de la bibliothèque (Crossref, Retraction Watch)")
def check_library(conn: Conn):
    return watch.check_library(conn)


JournalStatus = Literal[journals.STATUSES]  # type: ignore[valid-type]


@router.get("/works", summary="Lister les notices (les plus récentes d'abord)")
def list_works(
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    journal_status: Annotated[list[JournalStatus] | None, Query(description="Filtre sur le statut de revue (répétable)")] = None,
    label: Annotated[int | None, Query(description="Collection ou tag (id)")] = None,
    read_status: Literal[(*organize.READ_STATUSES, "unread")] | None = None,  # type: ignore[valid-type]
    retracted: Annotated[bool, Query(description="Seulement les articles rétractés")] = False,
):
    return core.list_works(conn, limit, offset, journal_status, label, read_status, retracted)


SourceName = Literal[tuple(search.SOURCES)]  # type: ignore[valid-type]


@router.get("/search/online", summary="Recherche fédérée dans les bases en ligne")
def search_online(
    conn: Conn,
    q: Annotated[str, Query(min_length=1, description="Requête ; syntaxe PubMed acceptée ([MeSH], [tiab], AND/OR/NOT)")],
    source: Annotated[list[SourceName] | None, Query(description="Sources à interroger (répétable ; défaut : toutes)")] = None,
    per_source: Annotated[int, Query(ge=1, le=100, description="Résultats demandés à chaque source")] = 25,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    year_from: int | None = None,
    year_to: int | None = None,
    study_type: Annotated[list[str] | None, Query(description="rct, meta-analysis, systematic-review, cohort… (répétable)")] = None,
    open_access: bool = False,
    mesh: Annotated[list[str] | None, Query(description="Descripteur MeSH exigé (répétable)")] = None,
    journal: Annotated[str | None, Query(description="Nom, abréviation ou ISSN de revue (contient)")] = None,
    journal_status: Annotated[list[JournalStatus] | None, Query(description="Statuts de revue montrés (défaut : recommended)")] = None,
    all_journals: Annotated[bool, Query(description="Montrer toutes les revues (désactive le filtre de la liste)")] = False,
    rerank: Annotated[bool, Query(description="Reclasser avec le modèle sémantique local s'il est chargé")] = True,
):
    """Interroge PubMed, Europe PMC, OpenAlex, Crossref et Semantic Scholar en parallèle, fusionne les doublons
    (DOI > PMID > PMCID > titre + année) et classe par fusion des rangs (RRF). Par défaut, seules les revues de la
    liste des revues recommandées sont montrées ; `hidden` compte les résultats masqués par statut."""
    filters = search.Filters(
        year_from=year_from, year_to=year_to, study_types=study_type or [], open_access=open_access,
        mesh=mesh or [], journal=journal, journal_status=[] if all_journals else (journal_status or ["recommended"]),
    )  # fmt: skip
    return search.search(conn, q, source, per_source, limit, filters, rerank)


@router.get("/search/open-access", summary="Trouver la version libre et légale d'un article (PMC, Unpaywall)")
def open_access(doi: str | None = None, pmcid: str | None = None):
    if not (doi or pmcid):
        raise HTTPException(422, "Indiquer doi ou pmcid")
    found = search.find_open_access(doi, pmcid)
    if found is None:
        raise HTTPException(404, "Aucune version en accès libre connue")
    return found


@router.get("/journals", summary="Liste des revues recommandées : version en vigueur, dernière vérification")
def journals_info(conn: Conn):
    return {**journals.info(conn), "history": journals.history(conn)}


@router.post("/journals/update", summary="Vérifier en ligne si une nouvelle liste est publiée")
def journals_update(request: Request, conn: Conn):
    """Télécharge la liste depuis la page de Sorbonne Université si elle a changé (`updated`).
    En cas d'échec (502), la version précédente reste en vigueur."""
    try:
        return journals.update(conn, request.app.state.config.home)
    except journals.ListError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post(
    "/journals/import",
    summary="Importer manuellement un fichier .xlsx de la liste",
    openapi_extra={"requestBody": {"content": {XLSX: {"schema": {"type": "string", "format": "binary"}}}, "required": True}},
)
async def journals_import(request: Request, conn: Conn, filename: str | None = None):
    """Corps de la requête : le fichier .xlsx brut. `filename` (facultatif) sert à dater la version (« …_01-10-26.xlsx »)."""
    data = await request.body()
    if len(data) > journals.MAX_DOWNLOAD:
        raise HTTPException(413, "Fichier trop volumineux")
    try:
        return journals.import_file(conn, data, request.app.state.config.home, filename)
    except journals.ListError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/journals/lookup", summary="Statut d'une revue par ISSN, NLMID ou abréviation")
def journals_lookup(conn: Conn, issn: str | None = None, nlmid: str | None = None, abbrev: str | None = None):
    if not (issn or nlmid or abbrev):
        raise HTTPException(422, "Indiquer issn, nlmid ou abbrev")
    return journals.lookup(conn, issn, nlmid, abbrev)


# `{ref:path}` accepte les DOI, qui contiennent des « / ». Les routes /changes, /files… doivent être déclarées
# avant /works/{ref:path}, qui sinon les engloberait.
@router.get("/works/{ref:path}/changes", summary="Journal des modifications d'une notice")
def get_changes(ref: str, conn: Conn):
    return core.get_changes(conn, ref)


@router.get("/works/{ref:path}/files", summary="Fichiers (PDF) d'une notice")
def get_files(ref: str, conn: Conn):
    return library.files_of(conn, core.resolve(conn, ref))


@router.post("/works/{ref:path}/pdf", summary="Chercher et rattacher maintenant le PDF libre d'une notice")
def fetch_pdf(ref: str, request: Request, conn: Conn, client: Client):
    found = importer.fetch_open_pdf(conn, home(request), core.resolve(conn, ref), client)
    if found is None:
        raise HTTPException(404, "Aucun PDF libre trouvé (ou la notice a déjà un PDF)")
    return found


@router.get("/works/{ref:path}/citation", summary="Référence formatée d'une notice (style CSL)")
def get_citation(ref: str, request: Request, conn: Conn, style: str | None = None):
    work = core.get_work(conn, ref)
    style = style_of(request, style)
    return {"style": style, "text": exports.citation(work, home(request), style)}


@router.get("/works/{ref:path}/neighbours", summary="Articles proches et articles récents qui la citent (OpenAlex)")
def get_neighbours(ref: str, conn: Conn):
    return watch.neighbours(conn, ref)


@router.post("/works/{ref:path}/supersede", summary="Remplacer un preprint par sa version publiée (fusion)")
def supersede(ref: str, body: SupersedeRequest, request: Request, conn: Conn, client: Client):
    return watch.supersede(conn, home(request), ref, body.doi, client)


@router.get("/works/{ref:path}/passages", summary="Passages d'une notice (texte intégral, résumé, note)")
def get_passages(ref: str, conn: Conn, q: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 5):
    """Avec `q` : les passages de cette notice les plus proches de `q` (plein texte + sens) ; sinon les premiers."""
    work_id = core.resolve(conn, ref)
    if q:
        hit = next((r for r in library.search(conn, q, limit=500, per_work=limit)["results"] if r["id"] == work_id), None)
        found = [{"origin": p["origin"], "page": p["page"], "text": html.unescape(re.sub(r"</?mark>", "", p["snippet"]))}
                 for p in (hit or {}).get("passages", [])]  # fmt: skip
    else:
        found = [dict(r) for r in conn.execute(
            "SELECT origin, page, text FROM passages WHERE work_id = ? ORDER BY id LIMIT ?", (work_id, limit))]  # fmt: skip
    return {"work": work_id, "passages": found}


@router.get("/works/{ref:path}/note", summary="Lire la note Markdown d'une notice")
def get_note(ref: str, request: Request, conn: Conn):
    """`links` : notices citées par [[clé]] ; `backlinks` : notices dont la note cite celle-ci."""
    return notes.read(conn, home(request), core.resolve(conn, ref))


@router.put("/works/{ref:path}/note", summary="Écrire la note Markdown d'une notice (texte vide : suppression)")
def put_note(ref: str, body: NoteBody, request: Request, conn: Conn, client: Client):
    work_id = core.resolve(conn, ref)
    result = notes.write(conn, home(request), work_id, body.text)
    with db.transaction(conn):
        core._log(conn, work_id, client, "update", "note", None, {"length": len(body.text)})
    return result


@router.get("/works/{ref:path}", summary="Lire une notice (id, clé de citation, DOI ou PMID)")
def get_work(ref: str, conn: Conn):
    return core.get_work(conn, ref)


@router.patch("/works/{ref:path}", summary="Statut de lecture et note (1 à 5) ; null efface")
def update_work(ref: str, body: WorkUpdate, conn: Conn, client: Client):
    return organize.update_work(conn, ref, body.model_dump(exclude_unset=True), client)


class WebhookCreate(BaseModel):
    url: str
    events: list[Literal[webhooks.EVENTS]]  # type: ignore[valid-type]


class WebhookUpdate(BaseModel):
    url: str | None = None
    events: list[Literal[webhooks.EVENTS]] | None = None  # type: ignore[valid-type]
    active: bool | None = None


@router.get("/webhooks", summary="Webhooks : adresses, événements, secret, dernier envoi")
def list_webhooks(conn: Conn):
    return {"events": webhooks.EVENTS, "items": webhooks.hooks(conn)}


@router.post("/webhooks", status_code=201, summary="Ajouter un webhook (secret de signature tiré au hasard)")
def create_webhook(body: WebhookCreate, conn: Conn):
    """Envoi : POST JSON `{"event", "at", "data"}`, en-têtes `X-Biblio-Event` et `X-Biblio-Signature: sha256=<HMAC
    du corps avec le secret>`."""
    return webhooks.create(conn, body.url, body.events)


@router.patch("/webhooks/{hook_id}", summary="Modifier ou désactiver un webhook")
def update_webhook(hook_id: int, body: WebhookUpdate, conn: Conn):
    return webhooks.update(conn, hook_id, body.url, body.events, body.active)


@router.delete("/webhooks/{hook_id}", status_code=204, summary="Supprimer un webhook")
def delete_webhook(hook_id: int, conn: Conn):
    webhooks.delete(conn, hook_id)


@router.post("/webhooks/{hook_id}/test", summary="Envoyer un événement d'essai (`ping`) et renvoyer le résultat")
def test_webhook(hook_id: int, conn: Conn):
    return webhooks.deliver(webhooks.get(conn, hook_id), "ping", {"message": "Essai depuis Biblio"})


@router.get("/backups", summary="Sauvegardes du dossier backups/ (la plus récente d'abord)")
def list_backups(request: Request):
    return backup.backups(home(request))


@router.post("/backups", status_code=201, summary="Sauvegarder maintenant (base, PDF, notes, styles, config)")
def create_backup(request: Request):
    """Restauration : `biblio restore <archive>` (le serveur doit être arrêté ; la commande s'en charge)."""
    cfg = request.app.state.config
    path = backup.create(cfg.db_path, cfg.home)
    return {"name": path.name, "path": str(path), "size": path.stat().st_size}


@router.post("/export/full", summary="Export complet en formats ouverts (zip : CSL-JSON, BibTeX, RIS, notes, PDF…)")
def export_full(request: Request, conn: Conn):
    """Un paquet .biblio de toute la bibliothèque et de toutes les recherches enregistrées (JSON + PDF), lisible sans
    Biblio, auquel s'ajoutent library.json (CSL-JSON), library.bib, library.ris et notes/*.md."""
    refs = exports.all_refs(conn)
    ids = [s["id"] for s in organize.searches(conn)]
    data = packages.create(conn, home(request), refs, "Export complet", ids) if refs or ids else None
    buffer = io.BytesIO(data or b"")
    with zipfile.ZipFile(buffer, "a" if data else "w", zipfile.ZIP_DEFLATED) as z:
        for fmt, name in (("csljson", "library.json"), ("bibtex", "library.bib"), ("ris", "library.ris")):
            z.writestr(name, exports.export(conn, home(request), refs, fmt)[0] if refs else "")
        for note in sorted(notes.folder(home(request)).glob("*.md")):
            z.write(note, f"notes/{note.name}")
    return Response(buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="biblio-export.zip"'})  # fmt: skip


@router.get("/update", summary="Nouvelle version de Biblio publiée ? (GitHub, vérifié une fois par jour)")
def check_update():
    from . import updates

    return updates.check()


@router.post("/update/install", summary="Installer la nouvelle version (Biblio redémarre de lui-même)")
def install_update(request: Request):
    """Téléchargement vérifié (SHA-256), remplacement de l'application, relance. 422 si ce n'est pas possible ici
    (Biblio lancé depuis le code source…) : l'interface propose alors le téléchargement."""
    from . import updates

    return updates.install(home(request))


@router.get("/connectors", summary="Connecteurs : configurations MCP à copier, état de Zotero et du modèle de langue")
def connectors(request: Request):
    from . import mcp_server  # import local : le SDK MCP n'est chargé que pour l'écran Connecteurs

    return mcp_server.connectors(request.app.state.config)


@router.get("/connectors/claude/biblio.mcpb", summary="Extension Claude Desktop (double-clic pour l'installer)")
def claude_extension(request: Request):
    from . import claude

    return Response(claude.mcpb(request.app.state.config), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="biblio.mcpb"'})  # fmt: skip


@router.get("/connectors/claude/biblio-skill.zip", summary="Skill « Biblio » à importer dans Claude")
def claude_skill():
    from . import claude

    return Response(claude.skill_zip(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="biblio-skill.zip"'})  # fmt: skip


def llms_txt(app: FastAPI) -> str:
    """Résumé de l'API pour un modèle de langue (convention llms.txt), tiré du schéma OpenAPI."""
    lines = ["# Biblio", "", "> Gestionnaire de bibliographie local (médecine, microbiologie). API REST sur /api/v1, "
             "jeton `Authorization: Bearer <jeton>` (config.toml ou `biblio token`). Schéma complet : /openapi.json. "
             "Serveur MCP : `biblio mcp` (stdio) ou `biblio mcp --http`.", "", "## Points d'accès", ""]  # fmt: skip
    for path, operations in app.openapi()["paths"].items():
        lines += [f"- `{method.upper()} {path}` : {op['summary']}" for method, op in operations.items()]
    return "\n".join(lines) + "\n"


def _background(app: FastAPI) -> None:
    """Toutes les 5 s : boîte d'entrée, Téléchargements (si activé), notes modifiées hors de Biblio, library.bib.
    Toutes les heures : veille (recherches suivies arrivées à échéance, bibliothèque chaque semaine), sauvegarde
    quotidienne.
    Toutes les 6 h : liste des revues. Les réglages sont relus à chaque tour (écran de réglages)."""
    # ponytail: boucle dans un thread ; passera par la file de tâches de la phase 5.
    next_journal_check = next_watch = 0.0
    while True:
        config = app.state.config
        conn = db.connect(config.db_path)
        try:
            importer.scan_inbox(conn, config.home)
            if config.watch_downloads:
                importer.scan_downloads(conn, config.downloads_dir, config.home)
            notes.sync(conn, config.home)
            exports.sync_library_bib(conn, config.home)
            if time.monotonic() >= next_watch:  # chaque heure : veille selon échéance, sauvegarde du jour
                next_watch = time.monotonic() + 3600
                watch.run_due(conn)
                if path := backup.daily(config.db_path, config.home):
                    log.info("Sauvegarde du jour : %s", path)
            if time.monotonic() >= next_journal_check:
                next_journal_check = time.monotonic() + 6 * 3600
                if result := journals.update_if_stale(conn, config.home):
                    log.info("Liste des revues vérifiée (nouvelle version : %s)", result["updated"])
        except (SourceError, journals.ListError) as exc:
            log.warning("Tâche de fond : %s", exc)
        except Exception:  # une erreur imprévue ne doit pas arrêter la boucle
            log.exception("Tâche de fond : erreur inattendue")
        finally:
            conn.close()
        time.sleep(5)


class WebFiles(StaticFiles):
    """Fichiers de l'interface. index.html est revalidé à chaque ouverture : après une mise à jour, la fenêtre charge
    la nouvelle interface (les autres fichiers changent de nom à chaque compilation et restent en cache)."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if not path.startswith("assets/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(config: Config, background: bool = False) -> FastAPI:
    """`background=True` (serveur réel) : boîte d'entrée, notes et liste des revues surveillées en arrière-plan,
    indexation sémantique dès que le modèle est téléchargé."""
    conn = db.connect(config.db_path)
    core.setup(conn, config.home)
    conn.close()
    http.configure(config.contact_email, config.api_keys, config.db_path)
    llm.configure(llm.Settings.of(config))
    webhooks.db_path = config.db_path
    app = FastAPI(
        title="Biblio",
        version=__version__,
        description="Gestionnaire de bibliographie local. Authentification : `Authorization: Bearer <jeton>` "
        "(jeton dans config.toml ou `biblio token`).",
    )
    app.state.config = config
    app.state.semantic_at_start = config.semantic
    if background:
        threading.Thread(target=_background, args=(app,), daemon=True, name="background").start()
        if config.semantic:  # charge le modèle s'il est téléchargé, puis indexe les passages au fil de l'eau
            threading.Thread(target=semantic.run_indexer, args=(config.db_path, config.models_dir), daemon=True,
                             name="semantic").start()  # fmt: skip

    @app.get("/api/v1/health", summary="État du serveur (sans authentification)")
    def health():
        """`executable` : le programme qui a lancé ce serveur (l'app installée, ou Biblio en développement)."""
        return {"app": "biblio", "version": __version__, "status": "ok", "executable": command()[0]}

    app.include_router(router)

    @app.get("/llms.txt", summary="Résumé de l'API pour un modèle de langue (sans authentification)",
             response_class=PlainTextResponse)  # fmt: skip
    def llms():
        return llms_txt(app)

    # ValueError couvre les saisies invalides (identifiant, format de fichier, PDF refusé, fusion impossible).
    errors = {ValueError: 422, core.NotFound: 404, SourceError: 502, zotero.ZoteroError: 503, llm.LLMError: 503}
    for exc_type, status in errors.items():
        app.add_exception_handler(exc_type, lambda _req, exc, status=status: JSONResponse({"detail": str(exc)}, status))

    # Interface compilée (ui/, `npm run build`) : fichiers statiques sans données, servis sans jeton. L'interface
    # reçoit le jeton dans l'adresse (#token=…), qui n'est jamais envoyée au serveur.
    web = files("biblio") / "web"
    if web.is_dir():
        app.mount("/", WebFiles(directory=str(web), html=True), name="web")
    return app
