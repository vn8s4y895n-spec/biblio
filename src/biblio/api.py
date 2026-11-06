"""API REST locale (FastAPI). Documentation interactive : /docs ; schéma : /openapi.json."""

import logging
import os
import secrets
import signal
import sqlite3
import threading
import time
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__, core, db, formats, importer, journals, library, notes, search
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


class AttachRequest(BaseModel):
    identifier: str | None = None  # DOI, PMID… (la notice est ajoutée au besoin)
    work: str | None = None  # ou notice existante : id, clé, DOI, PMID


class MergeRequest(BaseModel):
    keep: str
    drop: str


class NoteBody(BaseModel):
    text: str


def home(request: Request):
    return request.app.state.config.home


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


Client = Annotated[str, Depends(client_name)]


@router.post("/works", summary="Ajouter un article par identifiant (DOI, PMID, PMCID, arXiv, URL)")
def add_work(body: AddRequest, request: Request, response: Response, conn: Conn, client: Client):
    """Récupère la notice (PubMed + Crossref, ou arXiv) et l'ajoute à la bibliothèque, avec son PDF s'il est en
    accès libre (`pdf`). 201 si la notice est créée, 200 si l'article était déjà présent (notice existante,
    éventuellement complétée). `warnings` signale une source injoignable ou un PDF non récupéré."""
    result = importer.add(conn, home(request), body.identifier, client, body.pdf)
    response.status_code = 201 if result["created"] else 200
    return result


@router.post("/works/manual", status_code=201, summary="Saisie manuelle d'une notice")
def add_manual(body: ManualWork, response: Response, conn: Conn, client: Client):
    """Pour un article sans identifiant. Si un identifiant fourni est déjà connu, la notice existante est complétée (200)."""
    work, created = core.add_manual(conn, body.model_dump(), client)
    response.status_code = 201 if created else 200
    return {"created": created, "work": work}


@router.post("/import", summary="Importer un fichier BibTeX, RIS, MEDLINE (.nbib) ou CSL-JSON",
             openapi_extra=binary_body("text/plain"))  # fmt: skip
async def import_file(conn: Conn, client: Client, data: Annotated[bytes, Depends(raw_body)],
                      format: Literal[formats.FORMATS] | None = None):  # type: ignore[valid-type]  # fmt: skip
    """Corps : le fichier tel quel (format détecté si `format` est omis). Import hors ligne : les notices déjà
    présentes sont complétées, les doublons probables signalés (voir /duplicates)."""
    return importer.import_bibliography(conn, data.decode("utf-8-sig", errors="replace"), format, client)


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


@router.get("/search/library", summary="Recherche plein texte dans la bibliothèque")
def search_library(conn: Conn, q: Annotated[str, Query(min_length=1)], limit: Annotated[int, Query(ge=1, le=200)] = 20):
    """Cherche dans les métadonnées, résumés, notes et textes intégraux. Les extraits sont en HTML échappé, les
    mots trouvés entourés de `<mark>`. `mode` : all (tous les mots), any (au moins un), advanced (syntaxe FTS5)."""
    return library.search(conn, q, limit)


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


@router.post("/inbox/scan", summary="Importer maintenant les PDF de la boîte d'entrée")
def scan_inbox(request: Request, conn: Conn):
    return importer.scan_inbox(conn, home(request), min_age=0)


@router.post("/shutdown", status_code=202, summary="Arrêter le serveur")
def shutdown(background: BackgroundTasks):
    # Même effet qu'un Ctrl-C : uvicorn termine proprement les requêtes en cours.
    background.add_task(os.kill, os.getpid(), signal.SIGTERM)
    return {"status": "stopping"}


JournalStatus = Literal[journals.STATUSES]  # type: ignore[valid-type]


@router.get("/works", summary="Lister les notices (les plus récentes d'abord)")
def list_works(
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    journal_status: Annotated[list[JournalStatus] | None, Query(description="Filtre sur le statut de revue (répétable)")] = None,
):
    return core.list_works(conn, limit, offset, journal_status)


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
):
    """Interroge PubMed, Europe PMC, OpenAlex, Crossref et Semantic Scholar en parallèle, fusionne les doublons
    (DOI > PMID > PMCID > titre + année) et classe par fusion des rangs (RRF). Par défaut, seules les revues de la
    liste des revues recommandées sont montrées ; `hidden` compte les résultats masqués par statut."""
    filters = search.Filters(
        year_from=year_from, year_to=year_to, study_types=study_type or [], open_access=open_access,
        mesh=mesh or [], journal=journal, journal_status=[] if all_journals else (journal_status or ["recommended"]),
    )  # fmt: skip
    return search.search(conn, q, source, per_source, limit, filters)


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


def _background(config: Config) -> None:
    """Toutes les 5 s : boîte d'entrée et notes modifiées hors de Biblio. Toutes les 6 h : liste des revues."""
    # ponytail: boucle dans un thread ; passera par la file de tâches de la phase 5.
    next_journal_check = 0.0
    while True:
        conn = db.connect(config.db_path)
        try:
            importer.scan_inbox(conn, config.home)
            notes.sync(conn, config.home)
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


def create_app(config: Config, background: bool = False) -> FastAPI:
    """`background=True` (serveur réel) : boîte d'entrée, notes et liste des revues surveillées en arrière-plan."""
    conn = db.connect(config.db_path)
    core.setup(conn, config.home)
    conn.close()
    http.configure(config.contact_email, config.api_keys, config.db_path)
    if background:
        threading.Thread(target=_background, args=(config,), daemon=True, name="background").start()

    app = FastAPI(
        title="Biblio",
        version=__version__,
        description="Gestionnaire de bibliographie local. Authentification : `Authorization: Bearer <jeton>` "
        "(jeton dans config.toml ou `biblio token`).",
    )
    app.state.config = config

    @app.get("/api/v1/health", summary="État du serveur (sans authentification)")
    def health():
        return {"app": "biblio", "version": __version__, "status": "ok"}

    app.include_router(router)

    # ValueError couvre les saisies invalides (identifiant, format de fichier, PDF refusé, fusion impossible).
    errors = {ValueError: 422, core.NotFound: 404, SourceError: 502}
    for exc_type, status in errors.items():
        app.add_exception_handler(exc_type, lambda _req, exc, status=status: JSONResponse({"detail": str(exc)}, status))
    return app
