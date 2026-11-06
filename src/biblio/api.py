"""API REST locale (FastAPI). Documentation interactive : /docs ; schéma : /openapi.json."""

import os
import secrets
import signal
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__, core, db
from .config import Config
from .sources import http
from .sources.http import SourceError


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


@router.get("/works", summary="Lister les notices (les plus récentes d'abord)")
def list_works(conn: Conn, limit: Annotated[int, Query(ge=1, le=500)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    return core.list_works(conn, limit, offset)


# `{ref:path}` accepte les DOI, qui contiennent des « / ». La route /changes doit être déclarée en premier.
@router.get("/works/{ref:path}/changes", summary="Journal des modifications d'une notice")
def get_changes(ref: str, conn: Conn):
    return core.get_changes(conn, ref)


@router.get("/works/{ref:path}", summary="Lire une notice (id, clé de citation, DOI ou PMID)")
def get_work(ref: str, conn: Conn):
    return core.get_work(conn, ref)


def create_app(config: Config) -> FastAPI:
    conn = db.connect(config.db_path)
    db.migrate(conn)
    conn.close()
    http.configure(config.contact_email, config.ncbi_api_key)

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
