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

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__, core, db, journals
from .config import Config
from .sources import http
from .sources.http import SourceError

log = logging.getLogger("biblio")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


@router.post("/works", summary="Ajouter un article par DOI ou PMID")
def add_work(body: AddRequest, response: Response, conn: Conn, client: Annotated[str, Depends(client_name)]):
    """Récupère la notice (PubMed + Crossref) et l'ajoute à la bibliothèque.

    201 si la notice est créée, 200 si l'article était déjà présent (la notice existante est renvoyée,
    éventuellement complétée). `warnings` signale une source injoignable.
    """
    work, created, warnings = core.add_by_identifier(conn, body.identifier, client)
    response.status_code = 201 if created else 200
    return {"created": created, "warnings": warnings, "work": work}


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


# `{ref:path}` accepte les DOI, qui contiennent des « / ». La route /changes doit être déclarée en premier.
@router.get("/works/{ref:path}/changes", summary="Journal des modifications d'une notice")
def get_changes(ref: str, conn: Conn):
    return core.get_changes(conn, ref)


@router.get("/works/{ref:path}", summary="Lire une notice (id, clé de citation, DOI ou PMID)")
def get_work(ref: str, conn: Conn):
    return core.get_work(conn, ref)


def _watch_journal_list(config: Config) -> None:
    # ponytail: boucle dans un thread ; passera par la file de tâches de la phase 5.
    while True:
        conn = db.connect(config.db_path)
        try:
            if result := journals.update_if_stale(conn, config.home):
                log.info("Liste des revues vérifiée (nouvelle version : %s)", result["updated"])
        except (SourceError, journals.ListError) as exc:
            log.warning("Vérification de la liste des revues impossible : %s", exc)
        finally:
            conn.close()
        time.sleep(6 * 3600)


def create_app(config: Config, background: bool = False) -> FastAPI:
    """`background=True` (serveur réel) : vérification hebdomadaire de la liste des revues en arrière-plan."""
    conn = db.connect(config.db_path)
    core.setup(conn, config.home)
    conn.close()
    http.configure(config.contact_email, config.ncbi_api_key)
    if background:
        threading.Thread(target=_watch_journal_list, args=(config,), daemon=True, name="journal-list").start()

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

    errors = {core.InvalidIdentifier: 422, core.NotFound: 404, SourceError: 502}
    for exc_type, status in errors.items():
        app.add_exception_handler(exc_type, lambda _req, exc, status=status: JSONResponse({"detail": str(exc)}, status))
    return app
