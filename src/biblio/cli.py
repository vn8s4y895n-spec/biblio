"""Interface en ligne de commande. C'est un client de l'API REST : elle n'accède jamais à la base directement."""

import json
import subprocess
import sys
import time
from typing import Annotated

import httpx
import typer

from . import config

app = typer.Typer(help="Biblio : gestionnaire de bibliographie local.", no_args_is_help=True, add_completion=False)


def _is_biblio(client: httpx.Client) -> bool:
    try:
        return client.get("/api/v1/health").json().get("app") == "biblio"
    except (httpx.HTTPError, ValueError):
        return False


def _start_server(client: httpx.Client, cfg: config.Config) -> None:
    """Lance `biblio serve` en arrière-plan s'il ne tourne pas, puis attend qu'il réponde."""
    log = (cfg.home / "server.log").open("a")
    subprocess.Popen([sys.executable, "-m", "biblio", "serve"], stdout=log, stderr=log, start_new_session=True)
    for _ in range(75):
        if _is_biblio(client):
            return
        time.sleep(0.2)
    _fail(f"Le serveur ne démarre pas ; voir {cfg.home / 'server.log'}")


def client() -> httpx.Client:
    cfg = config.load()
    c = httpx.Client(
        base_url=cfg.client_url,
        headers={"Authorization": f"Bearer {cfg.token}", "X-Biblio-Client": "cli"},
        timeout=60,
    )
    if not _is_biblio(c):
        _start_server(c, cfg)
    return c


def _fail(message: str) -> None:
    typer.secho(message, fg="red", err=True)
    raise typer.Exit(1)


def _call(method: str, path: str, **kwargs) -> httpx.Response:
    response = client().request(method, path, **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json()["detail"]
        except (ValueError, KeyError):
            detail = response.text
        _fail(f"Erreur {response.status_code} : {detail}")
    return response


def _authors(work: dict, limit: int = 3) -> str:
    names = [f"{a['family']} {''.join(p[0] for p in (a['given'] or '').replace('-', ' ').split())}".strip() for a in work["authors"]]
    return ", ".join(names[:limit]) + (", et al." if len(names) > limit else "")


def _source(work: dict) -> str:
    out = f"{work['container_abbrev'] or work['container_title'] or ''} {work['year'] or ''}"
    if work["volume"]:
        out += f";{work['volume']}"
        out += f"({work['issue']})" if work["issue"] else ""
    return out + (f":{work['pages']}" if work["pages"] else "")


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Adresse d'écoute (défaut : config.toml)")] = None,
    port: Annotated[int | None, typer.Option(help="Port (défaut : config.toml)")] = None,
) -> None:
    """Lance le serveur (API REST + documentation sur /docs)."""
    import uvicorn

    from .api import create_app

    cfg = config.load()
    uvicorn.run(create_app(cfg), host=host or cfg.host, port=port or cfg.port)


@app.command()
def stop() -> None:
    """Arrête le serveur s'il tourne (sans le démarrer)."""
    cfg = config.load()
    c = httpx.Client(base_url=cfg.client_url, headers={"Authorization": f"Bearer {cfg.token}"})
    if not _is_biblio(c):
        typer.echo("Le serveur ne tourne pas.")
        return
    c.post("/api/v1/shutdown").raise_for_status()
    typer.echo("Serveur arrêté.")


@app.command()
def add(identifiers: Annotated[list[str], typer.Argument(help="DOI, PMID ou URL doi.org / pubmed")]) -> None:
    """Ajoute des articles par identifiant. Un article déjà présent n'est jamais dupliqué."""
    for identifier in identifiers:
        response = _call("POST", "/api/v1/works", json={"identifier": identifier})
        data = response.json()
        work = data["work"]
        mark, label = ("✓", "ajouté") if data["created"] else ("=", "déjà présent")
        typer.echo(f"{mark} {label} : [{work['citekey']}] {work['title']}")
        for warning in data["warnings"]:
            typer.secho(f"  ⚠ {warning}", fg="yellow")


@app.command()
def get(
    ref: Annotated[str, typer.Argument(help="Clé de citation, id, DOI ou PMID")],
    as_json: Annotated[bool, typer.Option("--json", help="Notice complète en JSON")] = False,
) -> None:
    """Affiche une notice."""
    work = _call("GET", f"/api/v1/works/{ref}").json()
    if as_json:
        typer.echo(json.dumps(work, ensure_ascii=False, indent=2))
        return
    typer.secho(work["title"], bold=True)
    typer.echo(f"{_authors(work, 6)}\n{_source(work)}")
    typer.echo(f"clé : {work['citekey']}   type : {work['study_type'] or '—'}")
    typer.echo("   ".join(f"{k} : {v}" for k, v in work["identifiers"].items()))
    mesh = [s["term"] + ("*" if s["major"] else "") for s in work["subjects"] if s["scheme"] == "mesh"]
    if mesh:
        typer.echo(f"MeSH (* = majeur) : {' ; '.join(mesh)}")  # « ; » car des termes MeSH contiennent des virgules
    if work["abstract"]:
        typer.echo("\n" + work["abstract"])


@app.command("list")
def list_(
    limit: Annotated[int, typer.Option(help="Nombre de notices")] = 20,
    offset: Annotated[int, typer.Option(help="Décalage (pagination)")] = 0,
) -> None:
    """Liste les notices, les plus récentes d'abord."""
    data = _call("GET", "/api/v1/works", params={"limit": limit, "offset": offset}).json()
    for w in data["items"]:
        typer.echo(f"{w['citekey']:<28} {w['year'] or '':<5} {(w['first_author'] or '')[:18]:<18} {w['title'][:70]}")
    typer.secho(f"{len(data['items'])} sur {data['total']} notice(s)", dim=True)


@app.command()
def token() -> None:
    """Affiche le jeton de l'API (pour curl ou un autre client)."""
    typer.echo(config.load().token)
