"""Interface en ligne de commande. C'est un client de l'API REST : elle n'accède jamais à la base directement."""

import html
import json
import logging
import re
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Annotated

import httpx
import typer

from . import command, config

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = typer.Typer(help="Biblio : gestionnaire de bibliographie local.", no_args_is_help=True, add_completion=False)
journals_app = typer.Typer(help="Liste des revues recommandées (CDD / CNU santé, Sorbonne Université).")
app.add_typer(journals_app, name="journals")

STATUS_LABELS = {
    "recommended": "● revue recommandée",
    "withdrawn": "▲ revue retirée de la liste",
    "not_listed": "○ hors liste",
    "preprint": "◇ preprint",
    "unidentified": "? revue non identifiée",
    "no_list": "— aucune liste chargée",
}
STATUS_MARKS = {k: v[0] for k, v in STATUS_LABELS.items()}
REASONS = {1: "nouveau nom", 2: "n'est plus recommandée", 3: "arrêt de publication"}


def _is_biblio(client: httpx.Client) -> bool:
    try:
        return client.get("/api/v1/health").json().get("app") == "biblio"
    except (httpx.HTTPError, ValueError):
        return False


def _start_server(client: httpx.Client, cfg: config.Config) -> None:
    """Lance `biblio serve` en arrière-plan s'il ne tourne pas, puis attend qu'il réponde."""
    log = (cfg.home / "server.log").open("a")
    subprocess.Popen([*command(), "serve"], stdout=log, stderr=log, start_new_session=True)
    for _ in range(75):
        if _is_biblio(client):
            return
        time.sleep(0.2)
    _fail(f"Le serveur ne démarre pas ; voir {cfg.home / 'server.log'}")


def client(name: str = "cli") -> httpx.Client:
    """Client de l'API (démarre le serveur au besoin) ; `name` est inscrit au journal des modifications."""
    cfg = config.load()
    c = httpx.Client(
        base_url=cfg.client_url,
        headers={"Authorization": f"Bearer {cfg.token}", "X-Biblio-Client": name},
        timeout=300,  # envoi vers Zotero, paquets volumineux
    )
    if not _is_biblio(c):
        _start_server(c, cfg)
    return c


def _fail(message: str) -> None:
    typer.secho(message, fg="red", err=True)
    raise typer.Exit(1)


def _call(method: str, path: str, **kwargs) -> httpx.Response:
    response = client().request(method, path, **kwargs)
    if response.status_code == 401:  # le serveur qui répond n'a pas le jeton de cette bibliothèque
        _fail("Jeton refusé : un autre Biblio (autre dossier de données) répond sur ce port : quitter Biblio, puis le rouvrir.")
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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
    uvicorn.run(create_app(cfg, background=True), host=host or cfg.host, port=port or cfg.port)


@app.command("app")
def open_app(
    paths: Annotated[list[Path] | None, typer.Argument(help="Paquets .biblio à ouvrir (aperçu avant import)")] = None,
    browser: Annotated[bool, typer.Option("--browser", help="Ouvrir dans le navigateur plutôt que dans une fenêtre")] = False,
) -> None:
    """Ouvre Biblio dans sa fenêtre (démarre le serveur au besoin)."""
    from .app import run

    run(browser, [str(p.resolve()) for p in paths or []])


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
def add(
    identifiers: Annotated[list[str], typer.Argument(help="DOI, PMID, PMCID, arXiv ou URL d'article")],
    pdf: Annotated[bool, typer.Option("--pdf/--no-pdf", help="Télécharger le PDF s'il est en accès libre")] = True,
) -> None:
    """Ajoute des articles par identifiant, avec leur PDF libre. Un article déjà présent n'est jamais dupliqué."""
    for identifier in identifiers:
        data = _call("POST", "/api/v1/works", json={"identifier": identifier, "pdf": pdf}).json()
        work = data["work"]
        mark, label = ("✓", "ajouté") if data["created"] else ("=", "déjà présent")
        typer.echo(f"{mark} {label} : [{work['citekey']}] {work['title']}")
        if data["pdf"]:
            typer.echo(f"  PDF libre rattaché ({data['pdf']['source']})")
        for warning in data["warnings"]:
            typer.secho(f"  ⚠ {warning}", fg="yellow")


@app.command("import")
def import_(
    paths: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False, help="PDF, .bib, .ris, .nbib, .json ou .rdf")],
) -> None:
    """Importe des PDF (DOI repéré automatiquement), des fichiers bibliographiques (BibTeX, RIS, MEDLINE, CSL-JSON),
    un export Zotero RDF (avec ses PDF, notes, tags et collections) ou des paquets .biblio reçus d'un autre Biblio."""
    for path in paths:
        if path.suffix.lower() == ".rdf":
            r = _call("POST", "/api/v1/import/zotero-rdf", json={"path": str(path.resolve())}).json()
            typer.echo(f"✓ {path.name} (Zotero RDF) : {r['read']} articles lus, {len(r['created'])} créés, "
                       f"{len(r['completed'])} déjà présents, {r['pdfs']} PDF, {r['notes']} notes, {r['collections']} collections")  # fmt: skip
            if r["missing_pdfs"]:
                typer.secho(f"  ⚠ {r['missing_pdfs']} PDF absents de l'export (cocher « Exporter les fichiers » dans Zotero)", fg="yellow")
            for e in r["errors"]:
                typer.secho(f"  ⚠ {e['title']} : {e['error']}", fg="yellow")
            continue
        if path.suffix.lower() == ".biblio":
            r = _call("POST", "/api/v1/packages/import", content=path.read_bytes(),
                      headers={"Content-Type": "application/x-biblio"}).json()  # fmt: skip
            typer.echo(f"✓ {path.name} : {len(r['created'])} articles ajoutés, {len(r['completed'])} déjà présents "
                       f"(complétés), {r['pdfs']} PDF, {r['notes']} notes, {r['searches']} recherches")  # fmt: skip
            for e in r["errors"]:
                typer.secho(f"  ⚠ {e}", fg="yellow")
            continue
        if path.suffix.lower() == ".pdf":
            r = _call("POST", "/api/v1/files", params={"filename": path.name}, content=path.read_bytes(),
                      headers={"Content-Type": "application/pdf"}).json()  # fmt: skip
            if r["status"] in ("attached", "duplicate"):
                label = "rattaché" if r["status"] == "attached" else "déjà dans la bibliothèque"
                typer.echo(f"✓ {path.name} : {label} → [{r['work']['citekey']}] {r['work']['title']}")
            else:
                typer.secho(f"? {path.name} : notice non trouvée automatiquement (fichier {r['sha256'][:12]})", fg="yellow")
                for c in r["candidates"]:
                    typer.echo(f"    {c['doi']}  {c['year'] or ''} {c['first_author'] or ''} · {c['title'][:80]}")
                typer.echo(f"  → biblio attach {r['sha256'][:12]} <DOI ou PMID>")
            continue
        r = _call("POST", "/api/v1/import", content=path.read_bytes(), headers={"Content-Type": "text/plain"}).json()
        typer.echo(f"✓ {path.name} ({r['format']}) : {r['read']} notices lues, {len(r['created'])} créées, "
                   f"{len(r['completed'])} déjà présentes")  # fmt: skip
        for e in r["errors"]:
            typer.secho(f"  ⚠ {e['title']} : {e['error']}", fg="yellow")
        if r["suspected_duplicates"]:
            typer.secho(f"  {r['suspected_duplicates']} doublon(s) probable(s) : biblio duplicates", fg="yellow")


@app.command()
def attach(
    file: Annotated[str, typer.Argument(help="Empreinte du PDF (ses premiers caractères suffisent)")],
    ref: Annotated[str, typer.Argument(help="DOI, PMID… ou clé d'une notice existante")],
) -> None:
    """Rattache un PDF non reconnu à une notice (saisie assistée)."""
    files = [f["sha256"] for f in _call("GET", "/api/v1/files").json() if f["sha256"].startswith(file.lower())]
    if len(files) != 1:
        _fail("Empreinte inconnue ou ambiguë (voir les fichiers en attente : biblio files)")
    body = {"identifier": ref} if _looks_like_identifier(ref) else {"work": ref}
    work = _call("POST", f"/api/v1/files/{files[0]}/attach", json=body).json()
    typer.echo(f"✓ rattaché à [{work['citekey']}] {work['title']}")


def _looks_like_identifier(text: str) -> bool:
    return bool(re.search(r"10\.\d{4,9}/|^\s*(pmid:?\s*)?\d+\s*$|^PMC\d+|arxiv|^https?://", text, re.I))


@app.command()
def files() -> None:
    """Liste les PDF rangés qui attendent une notice."""
    pending = _call("GET", "/api/v1/files").json()
    for f in pending:
        typer.echo(f"{f['sha256'][:12]}  {f['pages'] or '?'} p.  {f['original_name'] or ''}  ({f['added_at']})")
    typer.secho(f"{len(pending)} PDF en attente de notice", dim=True)


@app.command()
def find(
    query: Annotated[str, typer.Argument(help="Mots à chercher (syntaxe FTS5 acceptée : \"phrase\", OR, NOT, préfixe*)")],
    limit: Annotated[int, typer.Option(help="Nombre de notices")] = 10,
    engine: Annotated[str, typer.Option(help="hybrid (plein texte + sens), lexical ou semantic")] = "hybrid",
) -> None:
    """Recherche dans la bibliothèque (notices, résumés, notes, PDF), en plein texte et par le sens.
    Une question en langage courant, en français, trouve aussi des passages en anglais. ≈ : trouvé par le sens."""
    data = _call("GET", "/api/v1/search/library", params={"q": query, "limit": limit, "engine": engine}).json()
    origins = {"metadata": "notice", "abstract": "résumé", "fulltext": "PDF", "note": "note"}
    for w in data["results"]:
        typer.echo(f"{STATUS_MARKS[w['journal_status']]} {w['citekey']:<28} {w['year'] or '':<5} {w['title'][:70]}")
        for p in w["passages"]:
            where = origins[p["origin"]] + (f" p. {p['page']}" if p["page"] else "") + (" ≈" if p["match"] == "semantic" else "")
            text = html.unescape(p["snippet"].replace("<mark>", "\x1b[1;33m").replace("</mark>", "\x1b[0m"))
            typer.echo(f"    [{where}] {text}")
    mode = " (plein texte : au moins un des mots)" if data["mode"] == "any" else ""
    engines = {"hybrid": "plein texte + sens", "lexical": "plein texte seul (modèle sémantique non chargé)", "semantic": "sens seul"}
    typer.secho(f"{len(data['results'])} sur {data['total']} notice(s) · {engines[data['engine']]}{mode}", dim=True)


semantic_app = typer.Typer(help="Recherche par le sens (modèle local bge-m3).", invoke_without_command=True)
app.add_typer(semantic_app, name="semantic")


def _semantic_summary(s: dict) -> None:
    labels = {"absent": "non téléchargé (biblio semantic download)", "downloading": "téléchargement en cours",
              "downloaded": "téléchargé, chargement au démarrage du serveur", "ready": "prêt", "error": "erreur"}  # fmt: skip
    typer.echo(f"Modèle : {s['model']} — {labels.get(s['status'], s['status'])}")
    if s["status"] == "downloading":
        typer.echo(f"  {s['downloaded_bytes'] / 1e9:.2f} Go sur ~{s['expected_bytes'] / 1e9:.1f} Go")
    if s["error"]:
        typer.secho(f"  ⚠ {s['error']}", fg="yellow")
    typer.echo(f"Passages indexés : {s['indexed']} sur {s['passages']} ({s['progress']:.0%})")


@semantic_app.callback()
def semantic_info(ctx: typer.Context) -> None:
    """État du modèle et de l'indexation."""
    if ctx.invoked_subcommand is None:
        _semantic_summary(_call("GET", "/api/v1/semantic").json())


@semantic_app.command("download")
def semantic_download() -> None:
    """Télécharge le modèle (~2,3 Go, une seule fois) ; l'indexation démarre ensuite en arrière-plan."""
    _semantic_summary(_call("POST", "/api/v1/semantic/download").json())


@app.command()
def note(
    ref: Annotated[str, typer.Argument(help="Clé de citation, id, DOI ou PMID")],
    edit: Annotated[bool, typer.Option("--edit", help="Ouvrir la note dans l'éditeur ($EDITOR)")] = False,
    text: Annotated[str | None, typer.Option("--set", help="Remplacer le texte de la note")] = None,
) -> None:
    """Affiche ou modifie la note Markdown d'une notice (fichier notes/<clé>.md, ouvrable dans Obsidian)."""
    current = _call("GET", f"/api/v1/works/{ref}/note").json()
    if edit:
        text = typer.edit(current["text"], extension=".md")
        if text is None:
            typer.echo("Note inchangée.")
            return
    if text is not None:
        current = _call("PUT", f"/api/v1/works/{ref}/note", json={"text": text}).json()
        typer.echo("✓ Note enregistrée.")
    typer.secho(current["path"], dim=True)
    if current["text"]:
        typer.echo(current["text"].rstrip())
    if current["links"]:
        typer.echo(f"→ cite : {', '.join(current['links'])}")
    if current["backlinks"]:
        typer.echo(f"← citée par : {', '.join(current['backlinks'])}")


@app.command()
def duplicates() -> None:
    """Liste les doublons probables (même titre, année, premier auteur, sans identifiant commun)."""
    pairs = _call("GET", "/api/v1/duplicates").json()
    for pair in pairs:
        a, b = pair["works"]
        typer.echo(f"[{a['citekey']}] ⇄ [{b['citekey']}]  {a['title'][:70]}")
        for w in (a, b):
            ids = ", ".join(f"{k}:{v}" for k, v in w["identifiers"].items()) or "aucun identifiant"
            typer.echo(f"    {w['citekey']} : {ids}")
    typer.secho(f"{len(pairs)} paire(s). Fusionner : biblio merge <garder> <supprimer> ; écarter : biblio merge --dismiss A B",
                dim=True)  # fmt: skip


@app.command()
def merge(
    keep: Annotated[str, typer.Argument(help="Notice conservée")],
    drop: Annotated[str, typer.Argument(help="Notice fusionnée puis supprimée")],
    dismiss: Annotated[bool, typer.Option("--dismiss", help="Ce ne sont pas des doublons : écarter la paire")] = False,
) -> None:
    """Fusionne deux notices (identifiants, PDF, note et champs manquants repris), ou écarte un faux doublon."""
    if dismiss:
        _call("POST", "/api/v1/duplicates/dismiss", json={"a": keep, "b": drop})
        typer.echo("✓ Paire écartée.")
        return
    work = _call("POST", "/api/v1/duplicates/merge", json={"keep": keep, "drop": drop}).json()
    typer.echo(f"✓ Fusionné dans [{work['citekey']}] {work['title']}")


@app.command()
def pdf(
    ref: Annotated[str, typer.Argument(help="Clé de citation, id, DOI ou PMID")],
    open_: Annotated[bool, typer.Option("--open", help="Ouvrir le PDF, ou à défaut la page de l'article dans le navigateur")] = False,
) -> None:
    """Affiche les PDF d'une notice ; en cherche un librement accessible s'il n'y en a pas.

    Si aucun n'est récupérable automatiquement, `--open` ouvre la page de l'article dans ton navigateur : tu y
    télécharges le PDF avec ta session, puis tu le déposes dans la boîte d'entrée (ou il est pris dans les
    Téléchargements si leur surveillance est activée)."""
    found = _call("GET", f"/api/v1/works/{ref}/files").json()
    if not found:
        response = client().post(f"/api/v1/works/{ref}/pdf")
        if response.status_code == 200:
            found = [response.json()]
            typer.echo(f"✓ PDF trouvé et rattaché ({found[0]['source']}).")
        else:
            typer.secho(f"Aucun PDF récupérable automatiquement : {response.json().get('detail')}", fg="yellow")
    home = config.load().home
    paths = [home / "library" / f["sha256"][:2] / f["sha256"][2:4] / (f["sha256"] + ".pdf") for f in found]
    for path in paths:
        typer.echo(str(path))
    if open_:
        if paths:
            webbrowser.open(paths[0].as_uri())
        else:
            ids = _call("GET", f"/api/v1/works/{ref}").json()["identifiers"]
            page = (f"https://doi.org/{ids['doi']}" if "doi" in ids else
                    f"https://europepmc.org/article/PMC/{ids['pmcid']}" if "pmcid" in ids else
                    f"https://pubmed.ncbi.nlm.nih.gov/{ids['pmid']}/" if "pmid" in ids else None)  # fmt: skip
            if page is None:
                _fail("Aucun identifiant pour ouvrir la page de l'article")
            typer.echo(f"Ouverture de {page} dans le navigateur.")
            webbrowser.open(page)


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
    typer.echo(_status_line(work["journal_status"]))
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
    revue: Annotated[
        list[str] | None,
        typer.Option(help=f"Filtrer par statut de revue (répétable) : {', '.join(STATUS_LABELS)}"),
    ] = None,
) -> None:
    """Liste les notices, les plus récentes d'abord. ● recommandée ▲ retirée ○ hors liste ◇ preprint ? non identifiée."""
    params = {"limit": limit, "offset": offset, "journal_status": revue or []}
    data = _call("GET", "/api/v1/works", params=params).json()
    for w in data["items"]:
        mark = STATUS_MARKS.get(w["journal_status"], " ")
        typer.echo(f"{mark} {w['citekey']:<28} {w['year'] or '':<5} {(w['first_author'] or '')[:18]:<18} {w['title'][:70]}")
    typer.secho(f"{len(data['items'])} sur {data['total']} notice(s)", dim=True)


SOURCE_LETTERS = {"pubmed": "P", "europepmc": "E", "openalex": "O", "crossref": "C", "semanticscholar": "S"}


@app.command("search")
def search_(
    query: Annotated[str, typer.Argument(help="Requête ; syntaxe PubMed acceptée ([MeSH], [tiab], AND/OR/NOT)")],
    source: Annotated[list[str] | None, typer.Option(help=f"Source (répétable) : {', '.join(SOURCE_LETTERS)}")] = None,
    limit: Annotated[int, typer.Option(help="Nombre de résultats affichés")] = 20,
    per_source: Annotated[int, typer.Option(help="Résultats demandés à chaque source")] = 25,
    year_from: Annotated[int | None, typer.Option("--from", help="Année minimale")] = None,
    year_to: Annotated[int | None, typer.Option("--to", help="Année maximale")] = None,
    study_type: Annotated[list[str] | None, typer.Option("--type", help="rct, meta-analysis, systematic-review, cohort…")] = None,
    oa: Annotated[bool, typer.Option("--oa", help="Accès libre seulement")] = False,
    mesh: Annotated[list[str] | None, typer.Option(help="Descripteur MeSH exigé (répétable)")] = None,
    journal: Annotated[str | None, typer.Option(help="Revue (nom, abréviation ou ISSN)")] = None,
    all_journals: Annotated[bool, typer.Option("--all-journals", help="Montrer aussi les revues hors liste")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Résultat complet en JSON")] = False,
) -> None:
    """Recherche dans PubMed, Europe PMC, OpenAlex, Crossref et Semantic Scholar, résultats fusionnés.
    Par défaut, seules les revues recommandées sont montrées. Sources : P E O C S. ★ = déjà dans la bibliothèque."""
    params = {"q": query, "source": source or [], "limit": limit, "per_source": per_source, "study_type": study_type or [],
              "mesh": mesh or [], "open_access": oa, "all_journals": all_journals}  # fmt: skip
    params |= {k: v for k, v in {"year_from": year_from, "year_to": year_to, "journal": journal}.items() if v is not None}
    data = _call("GET", "/api/v1/search/online", params=params).json()
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    for i, r in enumerate(data["results"], 1):
        letters = "".join(SOURCE_LETTERS[s] for s in SOURCE_LETTERS if s in r["sources"])
        first = r["authors"][0]["family"] if r["authors"] else "—"
        venue = r.get("container_abbrev") or r.get("container_title") or "—"
        star = "  ★ " + r["in_library"] if r["in_library"] else ""
        typer.echo(f"{i:>3}. {STATUS_MARKS[r['journal_status']['status']]} {r.get('year') or '':<5} {first} · {venue}  [{letters}]{star}")
        typer.secho(f"     {r['title']}", bold=True)
        ids = "  ".join(f"{k}:{v}" for k, v in r["identifiers"].items() if k in ("doi", "pmid"))
        extra = "  ".join(x for x in (r.get("study_type") or "", "PDF libre" if r.get("oa_pdf_url") else "") if x)
        typer.echo(f"     {ids}  {extra}".rstrip())
    states = []
    for s in data["sources"].values():
        states.append(f"{s['label']} {s['count']}" if s["status"] == "ok" else f"{s['label']} ✗")
    typer.secho(f"{len(data['results'])} sur {data['total']} résultat(s) · " + " · ".join(states), dim=True)
    for s in data["sources"].values():
        if s["status"] != "ok":
            typer.secho(f"⚠ {s['label']} : {s['error']}", fg="yellow")
    if data["hidden"]:
        detail = ", ".join(f"{n} {STATUS_LABELS[k][2:]}" for k, n in data["hidden"].items())
        typer.secho(f"Masqués par le filtre des revues : {detail} (--all-journals pour les voir)", dim=True)


@app.command("oa")
def open_access(ref: Annotated[str, typer.Argument(help="DOI ou PMCID")]) -> None:
    """Cherche la version libre et légale d'un article (PMC, Unpaywall)."""
    params = {"pmcid": ref} if ref.upper().startswith("PMC") else {"doi": ref}
    found = _call("GET", "/api/v1/search/open-access", params=params).json()
    typer.echo(f"PDF : {found['pdf_url'] or '—'}\nPage : {found['landing_url'] or '—'}")
    typer.echo(f"Source : {found['source']}   licence : {found['license'] or '—'}   version : {found['version'] or '—'}")


def _status_line(status: dict) -> str:
    line = STATUS_LABELS[status["status"]]
    j = status["journal"]
    if j and j["withdrawn_on"]:
        reason = REASONS.get(j["withdrawal_reason"], "motif non précisé")
        line += f" (retirée le {j['withdrawn_on']} : {reason}" + (f" → {j['new_name']}" if j["new_name"] else "") + ")"
    if status["list_published_on"]:
        line += f"   [liste du {status['list_published_on']}]"
    return line


def _list_summary(data: dict) -> None:
    current = data["current"]
    typer.echo(f"Liste en vigueur : {current['published_on'] or 'date inconnue'} ({current['row_count']} revues)")
    typer.echo(f"Source : {current['source_url']}")
    typer.echo(f"Dernière vérification en ligne : {data['last_check'] or 'jamais'}")
    for warning in current["warnings"]:
        typer.secho(f"⚠ Anomalie du fichier : {warning}", fg="yellow")
    if data["last_error"]:
        typer.secho(f"⚠ Dernière erreur : {data['last_error']}", fg="yellow")


@journals_app.callback(invoke_without_command=True)
def journals_info(ctx: typer.Context) -> None:
    """Affiche la version de la liste en vigueur."""
    if ctx.invoked_subcommand is None:
        _list_summary(_call("GET", "/api/v1/journals").json())


@journals_app.command("update")
def journals_update() -> None:
    """Vérifie en ligne si une nouvelle liste est publiée, et la charge."""
    data = _call("POST", "/api/v1/journals/update").json()
    typer.echo("✓ Nouvelle liste chargée." if data["updated"] else "= La liste en vigueur est déjà la plus récente.")
    _list_summary(data)


@journals_app.command("import")
def journals_import(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Fichier .xlsx de la liste")]) -> None:
    """Importe manuellement un fichier .xlsx de la liste."""
    data = _call(
        "POST", "/api/v1/journals/import", params={"filename": path.name},
        content=path.read_bytes(), headers={"Content-Type": XLSX},
    ).json()  # fmt: skip
    typer.echo("✓ Liste importée." if data["updated"] else "= Cette liste était déjà chargée.")
    _list_summary(data)


@journals_app.command("lookup")
def journals_lookup(value: Annotated[str, typer.Argument(help="ISSN, NLMID ou abréviation (ex. « Clin Infect Dis »)")]) -> None:
    """Indique si une revue figure dans la liste."""
    if re.fullmatch(r"\s*\d{3,4}-?\d{3}[\dXx]\s*", value):
        params = {"issn": value}
    elif re.fullmatch(r"\s*\d+R?\s*", value):
        params = {"nlmid": value.strip()}
    else:
        params = {"abbrev": value}
    status = _call("GET", "/api/v1/journals/lookup", params=params).json()
    if status["journal"]:
        typer.secho(status["journal"]["name"], bold=True)
    typer.echo(_status_line(status))


def _selection(refs: list[str] | None, label: str | None) -> dict:
    """Notices visées par une commande : références données, collection / tag nommé, ou toute la bibliothèque."""
    if label:
        match = [x for x in _call("GET", "/api/v1/labels").json() if x["name"].casefold() == label.casefold()]
        if not match:
            _fail(f"Collection ou tag inconnu : {label}")
        return {"label": match[0]["id"]}
    return {"works": refs or []}


Refs = Annotated[list[str] | None, typer.Argument(help="Clés, DOI ou PMID (défaut : toute la bibliothèque)")]
LabelOpt = Annotated[str | None, typer.Option("--collection", "-c", help="Collection ou tag (nom)")]


@app.command()
def cite(
    refs: Annotated[list[str], typer.Argument(help="Clés, DOI ou PMID")],
    style: Annotated[str | None, typer.Option(help="Style CSL (voir biblio styles ; défaut : réglages)")] = None,
) -> None:
    """Affiche la référence formatée (Vancouver par défaut), prête à coller."""
    for ref in refs:
        typer.echo(_call("GET", f"/api/v1/works/{ref}/citation", params={"style": style} if style else {}).json()["text"])


@app.command()
def styles() -> None:
    """Styles de citation disponibles (ajouter un .csl : biblio styles-add fichier.csl)."""
    for s in _call("GET", "/api/v1/styles").json():
        typer.echo(f"{'*' if s['default'] else ' '} {s['id']:<52} {s['title']}{' (ajouté)' if s['added'] else ''}")


@app.command("styles-add")
def styles_add(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Fichier .csl")]) -> None:
    """Ajoute un style de citation (.csl, par exemple depuis zotero.org/styles)."""
    s = _call("POST", "/api/v1/styles", params={"filename": path.name}, content=path.read_bytes(),
              headers={"Content-Type": "application/xml"}).json()  # fmt: skip
    typer.echo(f"✓ Style ajouté : {s['id']} ({s['title']})")


@app.command()
def export(
    refs: Refs = None,
    format: Annotated[str, typer.Option("--format", "-f", help="bibtex, ris, csljson ou markdown")] = "bibtex",
    collection: LabelOpt = None,
    style: Annotated[str | None, typer.Option(help="Style CSL pour markdown")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Fichier de sortie (défaut : écran)")] = None,
) -> None:
    """Exporte des notices (BibTeX, RIS, CSL-JSON ou liste Markdown formatée)."""
    body = {**_selection(refs, collection), "format": format, **({"style": style} if style else {})}
    text = _call("POST", "/api/v1/export", json=body).text
    if output:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"✓ {output}")
    else:
        typer.echo(text, nl=False)


@app.command("zotero")
def zotero_send(
    refs: Refs = None,
    collection: LabelOpt = None,
    pdf: Annotated[bool, typer.Option("--pdf/--no-pdf", help="Joindre les PDF")] = True,
) -> None:
    """Envoie des notices dans Zotero (qui doit être ouvert), dans la collection sélectionnée dans Zotero."""
    if not refs and not collection:
        _fail("Indiquer des notices ou une collection (--collection)")
    r = _call("POST", "/api/v1/zotero", json={**_selection(refs, collection), "pdf": pdf}).json()
    typer.echo(f"✓ {r['sent']} notices et {r['pdfs']} PDF envoyés dans Zotero → {r['collection'] or r['library']}")


@app.command()
def pack(
    refs: Refs = None,
    collection: LabelOpt = None,
    output: Annotated[Path, typer.Option("--output", "-o", help="Fichier .biblio à créer")] = Path("articles.biblio"),
    title: Annotated[str | None, typer.Option(help="Titre du paquet (ex. « Pour Marie »)")] = None,
) -> None:
    """Crée un paquet .biblio (notices, PDF, notes, tags, questions) à envoyer à un autre Biblio."""
    data = _call("POST", "/api/v1/packages", json={**_selection(refs, collection), "title": title}).content
    output.write_bytes(data)
    typer.echo(f"✓ {output} ({len(data) / 1e6:.1f} Mo)")


@app.command()
def mcp(
    http: Annotated[bool, typer.Option("--http", help="Transport HTTP (port du serveur + 1, adresse /mcp) au lieu de stdio")] = False,
) -> None:
    """Serveur MCP pour Claude Desktop, Claude Code ou un autre client (stdio par défaut)."""
    from . import mcp_server

    cfg = config.load()
    client("mcp").close()  # démarre le serveur de Biblio s'il ne tourne pas : les outils passent par son API
    if not http:
        mcp_server.server.run("stdio")
        return
    import uvicorn

    host = "127.0.0.1" if cfg.host in ("0.0.0.0", "::") else cfg.host
    uvicorn.run(mcp_server.http_app(cfg.token), host=host, port=mcp_server.http_port(cfg))


@app.command()
def backup(
    dest: Annotated[Path | None, typer.Argument(help="Dossier où écrire l'archive (défaut : backups/ du dossier de données)")] = None,
) -> None:
    """Sauvegarde la base, les PDF, les notes, les styles et config.toml dans une archive .tar.gz."""
    if dest:
        from . import backup as backups

        cfg = config.load()
        path = backups.create(cfg.db_path, cfg.home, dest.expanduser())
        typer.echo(f"✓ {path} ({path.stat().st_size / 1e6:.1f} Mo)")
        return
    r = _call("POST", "/api/v1/backups").json()
    typer.echo(f"✓ {r['path']} ({r['size'] / 1e6:.1f} Mo)")


@app.command()
def restore(
    archive: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Archive biblio-….tar.gz")],
    yes: Annotated[bool, typer.Option("--yes", help="Ne pas demander confirmation")] = False,
) -> None:
    """Restaure une sauvegarde. Le serveur est arrêté ; les données actuelles sont mises de côté, pas effacées."""
    from . import backup as backups

    cfg = config.load()
    try:
        info = backups.check(archive)
    except ValueError as exc:
        _fail(str(exc))
    if not yes:
        typer.confirm(f"Remplacer les données de {cfg.home} par la sauvegarde du {info['created_at']} ?", abort=True)
    stop()
    c = httpx.Client(base_url=cfg.client_url)
    for _ in range(50):
        if not _is_biblio(c):
            break
        time.sleep(0.2)
    else:
        _fail("Le serveur ne s'arrête pas ; fermer Biblio puis recommencer")
    aside = backups.restore(archive, cfg.home)
    typer.echo(f"✓ Sauvegarde restaurée. Anciennes données mises de côté dans {aside}")


@app.command("export-all")
def export_all(
    output: Annotated[Path, typer.Option("--output", "-o", help="Fichier .zip à créer")] = Path("biblio-export.zip"),
) -> None:
    """Export complet en formats ouverts : CSL-JSON, BibTeX, RIS, notes Markdown, PDF, recherches (JSON)."""
    data = _call("POST", "/api/v1/export/full").content
    output.write_bytes(data)
    typer.echo(f"✓ {output} ({len(data) / 1e6:.1f} Mo)")


@app.command()
def token() -> None:
    """Affiche le jeton de l'API (pour curl ou un autre client)."""
    typer.echo(config.load().token)
