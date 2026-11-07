"""Serveur MCP : Biblio comme outil pour Claude Desktop, Claude Code ou tout autre client MCP.

Comme la CLI, c'est un client de l'API REST (en-tête X-Biblio-Client: mcp, inscrit au journal des modifications).
`biblio mcp` le lance en stdio (Claude Desktop, Claude Code) ; `biblio mcp --http` en HTTP pour les clients qui ne
lancent pas de commande (port du serveur + 1, adresse /mcp, jeton de Biblio exigé). Les réponses sont courtes et structurées : jamais de notice complète quand un résumé suffit.
"""

import functools
import html
import re
from datetime import datetime
from typing import Annotated, Literal

import httpx
from pydantic import Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, ask, cli, config

INSTRUCTIONS = """Biblio est la bibliothèque bibliographique personnelle d'un médecin biologiste (bactériologie,
maladies infectieuses). Méthode pour répondre à une question médicale :
1. Écris une requête PubMed en anglais (MeSH + synonymes [tiab], concepts reliés par AND) et appelle search_online :
   par défaut, seules les revues de la liste des revues recommandées sont montrées ; ne passe all_journals=true que si
   l'utilisateur le demande ou si rien n'est trouvé, et dis-le.
2. Appelle aussi search_library : ce qui est déjà dans sa bibliothèque compte, et ses notes aussi.
3. Lis les résumés (get_summary) ou les passages (get_passages) avant de citer ; n'affirme rien qui n'y soit pas.
4. Réponds dans la langue de la question, chaque phrase suivie de ses sources [1], [2] (numéros de la liste
   `sources` que tu passeras à save_synthesis), en précisant type d'étude et effectif.
5. Signale toute source rétractée (retracted) ou preprint (non relu par les pairs).
6. Enregistre la réponse et ses sources dans Biblio avec save_synthesis : l'utilisateur la retrouvera dans Biblio.
   Biblio vérifie chaque phrase contre les sources citées ; corrige les phrases signalées `weak` ou `unsourced`.
7. Propose ensuite d'ajouter les articles utiles (add_by_identifier) ou une bibliographie Vancouver
   (export_bibliography). Ne modifie jamais la bibliothèque (ajout, tag, note) sans que l'utilisateur l'ait demandé."""

server = MCPServer(name="biblio", title="Biblio", version=__version__, instructions=INSTRUCTIONS,
                   website_url="http://127.0.0.1:8765/", log_level="WARNING")  # fmt: skip
# log_level : le SDK configure la journalisation à l'import ; en INFO, chaque requête httpx s'afficherait.

_seen: dict[str, dict] = {}  # résultats en ligne déjà montrés (clé : DOI, PMID…), pour save_synthesis


@functools.cache
def client() -> httpx.Client:  # remplacé dans les tests
    return cli.client("mcp")


class BiblioError(ToolError):
    """Échec prévu (notice inconnue, serveur arrêté…) : le modèle lit le message et peut se corriger."""


def _call(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        response = client().request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise BiblioError(f"Biblio injoignable ({exc}) : ouvrir Biblio, puis réessayer") from exc
    if response.status_code >= 400:
        try:
            detail = response.json()["detail"]
        except (ValueError, KeyError):
            detail = response.text[:300]
        raise BiblioError(f"Biblio : erreur {response.status_code} : {detail}")
    return response


def _get(path: str, **params) -> dict | list:
    return _call("GET", path, params={k: v for k, v in params.items() if v is not None}).json()


def _post(path: str, body: dict) -> dict:
    return _call("POST", path, json=body).json()


def _key(rec: dict) -> str | None:
    ids = rec.get("identifiers") or {}
    return f"doi:{ids['doi']}" if ids.get("doi") else (f"pmid:{ids['pmid']}" if ids.get("pmid") else None)


def _first_author(rec: dict) -> str | None:
    authors = rec.get("authors") or []
    if authors and isinstance(authors[0], dict):
        return authors[0].get("family") + (" et al." if len(authors) > 1 else "")
    return rec.get("first_author")


def _status(rec: dict) -> str | None:
    status = rec.get("journal_status")
    return status.get("status") if isinstance(status, dict) else status


def _brief(rec: dict, n: int | None = None) -> dict:
    """Résultat en quelques champs : de quoi choisir les sources sans lire 50 Ko de notices."""
    out = {"n": n, "id": _key(rec) or rec.get("citekey"), "title": rec.get("title"), "first_author": _first_author(rec),
           "journal": rec.get("container_abbrev") or rec.get("container_title"), "year": rec.get("year"),
           "study_type": rec.get("study_type"), "sample_size": rec.get("sample_size"), "journal_status": _status(rec),
           "in_library": rec.get("in_library") or rec.get("citekey"), "retracted": rec.get("retracted") or None,
           "conclusion": rec.get("summary")}  # fmt: skip
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def _plain(snippet: str) -> str:
    return html.unescape(re.sub(r"</?mark>", "", snippet))


# --- Outils -----------------------------------------------------------------------------------------------


@server.tool(title="Chercher dans les bases en ligne")
def search_online(
    query: Annotated[str, Field(description="Requête PubMed en anglais (MeSH, [tiab], AND/OR/NOT) ou mots-clés")],
    question: Annotated[str | None, Field(description="Question de l'utilisateur, telle qu'il l'a posée (gardée dans son historique)")] = None,
    limit: Annotated[int, Field(ge=1, le=30)] = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    study_types: Annotated[list[str], Field(description="rct, meta-analysis, systematic-review, cohort, case-control…")] = [],  # noqa: B006
    open_access: bool = False,
    all_journals: Annotated[bool, Field(description="true : inclure les revues hors de la liste des revues recommandées")] = False,
) -> dict:
    """PubMed, Europe PMC, OpenAlex, Crossref et Semantic Scholar en parallèle, doublons fusionnés, classés par
    pertinence. Par défaut seules les revues recommandées sont montrées (`hidden` : résultats masqués par ce
    filtre). Chaque résultat : id (doi:… ou pmid:…, à réutiliser dans les autres outils), titre, revue, année, type
    d'étude, effectif, statut de la revue, conclusion des auteurs, et `in_library` (clé) s'il y est déjà."""
    out = _post("/api/v1/ask", {"question": question or query, "query": query, "limit": limit, "year_from": year_from,
                                "year_to": year_to, "study_type": study_types, "open_access": open_access,
                                "all_journals": all_journals})  # fmt: skip
    for rec in out["sources"]:
        if key := _key(rec):
            _seen[key] = rec
    return {"query": out["query"], "total": out["total"], "hidden": out["hidden"], "warnings": out["warnings"],
            "results": [_brief(rec, n) for n, rec in enumerate(out["sources"], 1)]}  # fmt: skip


@server.tool(title="Chercher dans la bibliothèque")
def search_library(
    query: Annotated[str, Field(description="Mots ou question, en français ou en anglais (recherche plein texte + sens)")],
    limit: Annotated[int, Field(ge=1, le=30)] = 10,
) -> dict:
    """Notices, résumés, notes et textes intégraux (PDF) de la bibliothèque de l'utilisateur, avec les passages
    trouvés (page du PDF, ou « note » pour ses notes personnelles)."""
    out = _get("/api/v1/search/library", q=query, limit=limit)
    results = [{**_brief(r), "id": r["citekey"],
                "passages": [{"where": p["origin"], "page": p["page"], "text": _plain(p["snippet"])} for p in r["passages"]]}
               for r in out["results"]]  # fmt: skip
    return {"total": out["total"], "results": results}


def _work(ref: str) -> dict:
    return _get(f"/api/v1/works/{ref.removeprefix('doi:').removeprefix('pmid:')}")


@server.tool(title="Lire une notice")
def get_reference(ref: Annotated[str, Field(description="Clé de citation, doi:…, pmid:… ou DOI / PMID nu")]) -> dict:
    """Notice d'un article de la bibliothèque : auteurs, revue, identifiants, résumé, collections, tags, statut de
    lecture, questions qui l'ont fait ajouter, avis de rétractation ou de correction, note personnelle (début)."""
    w = _work(ref)
    note = _get(f"/api/v1/works/{w['citekey']}/note").get("text") or ""
    return {"citekey": w["citekey"], "title": w["title"], "authors": [f"{a['family']} {a.get('given') or ''}".strip() for a in w["authors"]][:10],
            "journal": w.get("container_abbrev") or w.get("container_title"), "year": w["year"], "volume": w.get("volume"),
            "pages": w.get("pages"), "identifiers": w["identifiers"], "study_type": w.get("study_type"),
            "abstract": w.get("abstract"), "labels": [lb["name"] for lb in w.get("labels", [])],
            "read_status": w.get("read_status"), "questions": [q["question"] for q in w.get("questions", [])],
            "retracted": w.get("retracted"), "updates": w.get("updates"), "note": note[:1500] or None}  # fmt: skip


@server.tool(title="Résumé d'un article")
def get_summary(ref: Annotated[str, Field(description="Clé de citation, ou id d'un résultat de search_online (doi:…, pmid:…)")]) -> dict:
    """Résumé de l'article (abstract), conclusion des auteurs et effectif. Marche pour un article de la bibliothèque
    comme pour un résultat de search_online pas encore ajouté."""
    rec = _seen.get(ref)
    if rec is None:
        rec = _work(ref)
    abstract = rec.get("abstract")
    return {"id": _key(rec) or rec.get("citekey"), "title": rec["title"], "study_type": rec.get("study_type"),
            "sample_size": ask.sample_size(abstract), "conclusion": ask.summary(abstract),
            "abstract": abstract or "Pas de résumé disponible"}  # fmt: skip


@server.tool(title="Passages d'un article")
def get_passages(
    ref: Annotated[str, Field(description="Clé de citation d'un article de la bibliothèque")],
    query: Annotated[str | None, Field(description="Ce que tu cherches dans l'article ; omis : premiers passages")] = None,
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> dict:
    """Passages du texte intégral (PDF), du résumé ou de la note d'un article de la bibliothèque, avec leur page :
    pour citer un passage précis."""
    citekey = _work(ref)["citekey"]
    return _get(f"/api/v1/works/{citekey}/passages", q=query, limit=limit)


@server.tool(title="Ajouter un article")
def add_by_identifier(
    identifier: Annotated[str, Field(description="DOI, PMID, PMCID, arXiv ou URL (ou id doi:… / pmid:…)")],
    question: Annotated[str | None, Field(description="Question qui a mené à cet article (gardée avec la notice)")] = None,
) -> dict:
    """Ajoute l'article à la bibliothèque (notice PubMed / Crossref, PDF libre et légal s'il existe). Si l'article y
    est déjà, la notice existante est complétée."""
    out = _post("/api/v1/works", {"identifier": identifier.removeprefix("doi:").removeprefix("pmid:"), "question": question})
    w = out["work"]
    return {"created": out["created"], "citekey": w["citekey"], "title": w["title"], "warnings": out.get("warnings", [])}


def _label(kind: str, name: str, refs: list[str], remove: bool) -> dict:
    label = _post("/api/v1/labels", {"kind": kind, "name": name})
    refs = [r.removeprefix("doi:").removeprefix("pmid:") for r in refs]
    out = _post(f"/api/v1/labels/{label['id']}/works", {"works": refs, "remove": remove})
    return {"label": name, "id": label["id"], **out}


@server.tool(title="Taguer des articles")
def tag(refs: list[str], tag: str, remove: bool = False) -> dict:
    """Pose (ou retire, `remove`) un tag sur des articles de la bibliothèque (clés, doi:…, pmid:…). Le tag est
    créé s'il n'existe pas."""
    return _label("tag", tag, refs, remove)


@server.tool(title="Ranger dans une collection")
def add_to_collection(refs: list[str], collection: str, remove: bool = False) -> dict:
    """Range (ou retire, `remove`) des articles de la bibliothèque dans une collection, créée si elle n'existe pas."""
    return _label("collection", collection, refs, remove)


@server.tool(title="Ajouter à la note")
def add_note(ref: str, text: Annotated[str, Field(description="Texte Markdown ajouté à la fin de la note de l'article")]) -> dict:
    """Ajoute du texte à la note personnelle d'un article (la note existante est gardée)."""
    citekey = _work(ref)["citekey"]
    current = _get(f"/api/v1/works/{citekey}/note").get("text") or ""
    _call("PUT", f"/api/v1/works/{citekey}/note", json={"text": f"{current.rstrip()}\n\n{text.strip()}\n".lstrip()})
    return {"citekey": citekey, "saved": True}


def _source(ref: str) -> dict:
    if ref in _seen:
        return _seen[ref]
    try:
        return _work(ref)
    except BiblioError:
        raise BiblioError(f"Source inconnue : {ref} (ni dans les derniers résultats de search_online, ni dans la bibliothèque)")


@server.tool(title="Enregistrer la synthèse dans Biblio")
def save_synthesis(
    question: Annotated[str, Field(description="Question de l'utilisateur, telle qu'il l'a posée")],
    answer: Annotated[str, Field(description="Ta réponse, chaque phrase suivie de ses sources [1], [2] (rang dans `sources`)")],
    sources: Annotated[list[str], Field(description="Ids des sources dans l'ordre des numéros : doi:…, pmid:… ou clés")],
    query: Annotated[str | None, Field(description="Requête PubMed utilisée")] = None,
    model: Annotated[str, Field(description="Ton nom de modèle, affiché dans Biblio")] = "Claude",
) -> dict:
    """Enregistre la question, la réponse et ses sources comme recherche enregistrée de Biblio (origine « Claude »),
    où l'utilisateur la relira. Biblio vérifie chaque phrase contre les sources qu'elle cite et renvoie celles qui
    ne sont pas appuyées : corrige-les puis réenregistre si besoin."""
    records = [_source(ref) for ref in sources]
    saved = _post("/api/v1/searches", {"question": question, "query": query, "sources": records,
                                       "origin": "claude" if "claude" in model.lower() else "mcp",
                                       "synthesis": {"text": answer, "model": model}})  # fmt: skip
    sentences = saved["synthesis"]["sentences"]
    flagged = [{"text": s["text"], "status": s["status"]} for s in sentences if s["status"] != "ok"]
    return {"id": saved["id"], "saved": True, "sentences": len(sentences), "flagged": flagged,
            "open_in_biblio": f"#/search/{saved['id']}"}  # fmt: skip


FORMATS = {"vancouver": ("markdown", "bibliotheque-universitaire-de-medecine-vancouver"), "apa": ("markdown", "apa"),
           "bibtex": ("bibtex", None), "ris": ("ris", None), "csl-json": ("csljson", None)}  # fmt: skip


def _collection_id(name: str) -> int:
    for label in _get("/api/v1/labels"):
        if label["kind"] == "collection" and label["name"].casefold() == name.casefold():
            return label["id"]
    raise BiblioError(f"Collection inconnue : {name}")


@server.tool(title="Bibliographie")
def export_bibliography(
    refs: Annotated[list[str], Field(description="Articles (clés, doi:…, pmid:…) ; vide avec `collection`")] = [],  # noqa: B006
    collection: Annotated[str | None, Field(description="Nom d'une collection")] = None,
    format: Literal["vancouver", "apa", "bibtex", "ris", "csl-json"] = "vancouver",
) -> dict:
    """Bibliographie formatée (Vancouver par défaut) ou fichier BibTeX / RIS / CSL-JSON d'articles de la
    bibliothèque ou d'une collection."""
    if not refs and not collection:
        raise BiblioError("Indiquer des articles (refs) ou une collection")
    fmt, style = FORMATS[format]
    body = {"format": fmt, "style": style, "works": [r.removeprefix("doi:").removeprefix("pmid:") for r in refs]}
    if collection:
        body["label"] = _collection_id(collection)
    return {"format": format, "text": _call("POST", "/api/v1/export", json=body).text}


@server.tool(title="Créer un paquet .biblio")
def create_package(
    refs: list[str] = [],  # noqa: B006
    title: str | None = None,
    search_ids: Annotated[list[int], Field(description="Recherches enregistrées jointes (id renvoyé par save_synthesis)")] = [],  # noqa: B006
) -> dict:
    """Crée un paquet .biblio (notices, PDF, notes, synthèses) à envoyer à un collègue qui a Biblio. Le fichier est
    déposé dans le dossier Téléchargements ; son chemin est renvoyé."""
    body = {"works": [r.removeprefix("doi:").removeprefix("pmid:") for r in refs], "title": title, "searches": search_ids}
    data = _call("POST", "/api/v1/packages", json=body).content
    name = re.sub(r"[^\w\- ]+", "", title or "articles").strip() or "articles"
    path = config.load().downloads_dir / f"{name} {datetime.now():%Y-%m-%d %H%M%S}.biblio"
    path.write_bytes(data)
    return {"path": str(path), "size": len(data)}


@server.tool(title="Quoi de neuf dans la veille")
def watch_alerts(unseen_only: bool = True) -> dict:
    """Alertes de veille : nouveaux résultats des recherches suivies, rétractations, corrections, expressions de
    préoccupation, preprints publiés depuis."""
    out = _get("/api/v1/alerts", unseen_only=str(unseen_only).lower())
    items = []
    for a in out["items"][:30]:
        item = {"id": a["id"], "kind": a["kind"], "date": a["created_at"], "seen": bool(a["seen_at"])}
        if a["kind"] == "new_results":
            item |= {"question": a["question"], "count": a["data"].get("count"),
                     "results": [_brief(r) for r in a["data"].get("records", [])[:5]]}  # fmt: skip
        else:
            item |= {"citekey": a["citekey"], "title": a["title"], "notice_doi": a["data"].get("notice_doi")}
        items.append(item)
    return {"unseen": out["unseen"], "alerts": items}


# --- Ressources ---------------------------------------------------------------------------------------------


@server.resource("biblio://work/{citekey}", title="Notice", mime_type="application/json")
def work_resource(citekey: str) -> dict:
    """Notice d'un article de la bibliothèque."""
    return get_reference(citekey)


@server.resource("biblio://collection/{collection_id}", title="Collection", mime_type="application/json")
def collection_resource(collection_id: int) -> dict:
    """Articles d'une collection (ou d'un tag)."""
    out = _get("/api/v1/works", label=collection_id, limit=500)
    return {"collection": collection_id, "works": [_brief(w) for w in out["items"]]}


@server.resource("biblio://journals/status", title="Liste des revues recommandées", mime_type="application/json")
def journals_resource() -> dict:
    """Version en vigueur de la liste des revues recommandées (CDD / CNU santé) et date de vérification."""
    return _get("/api/v1/journals")


# --- Prompts ------------------------------------------------------------------------------------------------


@server.prompt(title="Recherche sourcée")
def recherche_sourcee(question: str) -> str:
    """Réponse à une question médicale, sourcée et enregistrée dans Biblio."""
    return (f"Question : {question}\n\nSuis la méthode de Biblio : requête PubMed avec MeSH, search_online (revues "
            "recommandées), search_library, lecture des résumés, réponse citée phrase par phrase, signalement des "
            "rétractations et preprints, puis save_synthesis. Termine en proposant les articles à ajouter.")


@server.prompt(title="Revue rapide de la littérature")
def revue_rapide(sujet: str, depuis: str = "") -> str:
    """Revue rapide : méta-analyses, revues systématiques et essais sur un sujet."""
    since = f" depuis {depuis}" if depuis else " sur les dix dernières années"
    return (f"Fais une revue rapide de la littérature sur : {sujet}{since}. Cherche d'abord les méta-analyses et "
            "revues systématiques (study_types), puis les essais randomisés et grandes cohortes. Présente un tableau "
            "(étude, type, effectif, résultat principal) puis une synthèse citée, et enregistre-la avec save_synthesis.")


@server.prompt(title="Bibliographie Vancouver d'une collection")
def bibliographie_vancouver(collection: str) -> str:
    """Bibliographie Vancouver d'une collection de Biblio."""
    return (f"Donne la bibliographie Vancouver de la collection « {collection} » avec export_bibliography, telle "
            "quelle, puis signale les articles rétractés ou encore à l'état de preprint (get_reference).")


@server.prompt(title="Quoi de neuf dans ma veille")
def quoi_de_neuf() -> str:
    """Résumé des alertes de veille non lues."""
    return ("Appelle watch_alerts et résume ce qui est nouveau : d'abord les rétractations et expressions de "
            "préoccupation (articles à ne plus citer), puis les preprints publiés, puis les nouveaux résultats des "
            "recherches suivies, avec pour chacun la conclusion en une phrase.")


def http_app(token: str):
    """Application ASGI du transport HTTP (`biblio mcp --http`), protégée par le jeton de Biblio. Une seule par
    processus : le gestionnaire de sessions du SDK ne démarre qu'une fois."""
    import secrets

    from starlette.responses import JSONResponse

    from mcp.server.transport_security import TransportSecuritySettings

    inner = server.streamable_http_app(
        stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["127.0.0.1:*", "localhost:*"],
                                                     allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]),
    )  # fmt: skip

    async def app(scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"")
            if not secrets.compare_digest(auth, f"Bearer {token}".encode()):
                await JSONResponse({"detail": "Jeton manquant ou invalide"}, 401)(scope, receive, send)
                return
        await inner(scope, receive, send)

    return app


def http_port(cfg: config.Config) -> int:
    return cfg.port + 1


def connectors(cfg: config.Config) -> dict:
    """Ce que l'écran Connecteurs montre : commandes et configurations à copier pour chaque client MCP."""
    from . import command

    exe = command()[0]
    stdio = {"command": exe, "args": ["mcp"]}
    http_url = f"http://127.0.0.1:{http_port(cfg)}/mcp"
    return {
        "command": exe,
        "claude_code": f"claude mcp add biblio -- {exe} mcp",
        "stdio": {"mcpServers": {"biblio": stdio}},
        "http_command": f"{exe} mcp --http",
        "http": {"mcpServers": {"biblio": {"url": http_url, "headers": {"Authorization": f"Bearer {cfg.token}"}}}},
        "http_url": http_url,
        "tools": sorted(t.name for t in server._tool_manager.list_tools()),
    }
