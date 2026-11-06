"""Aucun test n'accède au réseau : les sources reçoivent des réponses enregistrées (tests/fixtures)."""

import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest
from fastapi.testclient import TestClient

from biblio import config, core, db
from biblio.api import create_app
from biblio.sources import http

FIXTURES = Path(__file__).parent / "fixtures"
MERINO_DOI = "10.1001/jama.2018.12163"
MERINO_PMID = "30208454"
EMPTY_ESEARCH = {"esearchresult": {"count": "0", "idlist": []}}

# Site de Sorbonne Université simulé : page HTML et fichiers .xlsx, réglables par chaque test.
SORBONNE_PAGE = "/recherche/liste-des-revues-presumees-non-predatrices"
SORBONNE: dict = {}
LIST_HEADER = [
    "Nom complet", "Nom abrévié", "ISSN Print/Link", "ISSN Electronic", "NLMID", "Année de début",
    "Année de fin de publication", "Nouveau Nom", "Date Retrait de la liste",
    "Motif Retrait\n1. Nouveau nom\n2. N'est plus recommandée\n3. Arrêt publi",
]  # fmt: skip


def make_xlsx(rows: list[list[str]]) -> bytes:
    """Fichier .xlsx minimal (cellules en texte direct), comme en produit Excel."""
    body = "".join(
        f'<row r="{i}">'
        + "".join(
            f'<c r="{chr(65 + j)}{i}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for j, v in enumerate(row) if v
        )
        + "</row>"
        for i, row in enumerate(rows, 1)
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>',
        )
    return buf.getvalue()


def small_list(*extra: list[str], count: int = 120) -> bytes:
    """Liste de `count` revues fictives (ISSN 1100-0000, 1100-0001…), plus les lignes `extra`."""
    fake = [[f"Journal {i}", f"J {i}", f"1100-{i:04d}", "", f"9{i:06d}", "2000"] for i in range(count)]
    return make_xlsx([LIST_HEADER, *fake, *extra])


def sorbonne_page(*links: str) -> str:
    return "<html><body>" + "".join(f'<a href="{link}">liste</a>' for link in links) + "</body></html>"


@pytest.fixture(autouse=True)
def sorbonne():
    SORBONNE.clear()
    SORBONNE.update(page=None, files={})
    return SORBONNE


def fake_sources(request: httpx.Request) -> httpx.Response:
    url = request.url
    if url.host == "sante.sorbonne-universite.fr":
        if url.path == SORBONNE_PAGE and SORBONNE["page"] is not None:
            return httpx.Response(200, text=SORBONNE["page"])
        if str(url) in SORBONNE["files"]:
            return httpx.Response(200, content=SORBONNE["files"][str(url)])
        return httpx.Response(404, text="Not found")
    if url.host == "api.crossref.org":
        if url.path == f"/works/{MERINO_DOI}":
            return httpx.Response(200, content=(FIXTURES / "crossref_merino.json").read_bytes())
        return httpx.Response(404, content=(FIXTURES / "crossref_404.txt").read_bytes())
    if url.path.endswith("esearch.fcgi"):
        if url.params["term"] == f"{MERINO_DOI}[doi]":
            return httpx.Response(200, content=(FIXTURES / "pubmed_esearch_merino.json").read_bytes())
        return httpx.Response(200, json=EMPTY_ESEARCH)
    if url.path.endswith("efetch.fcgi"):
        if url.params["id"] == MERINO_PMID:
            return httpx.Response(200, content=(FIXTURES / "pubmed_efetch_30208454.xml").read_bytes())
        return httpx.Response(200, text="<?xml version='1.0'?><PubmedArticleSet></PubmedArticleSet>")
    raise AssertionError(f"Requête inattendue : {url}")


@pytest.fixture(autouse=True)
def network(monkeypatch) -> list[httpx.Request]:
    """Remplace le réseau ; renvoie la liste des requêtes émises."""
    calls: list[httpx.Request] = []

    def handler(request):
        calls.append(request)
        return fake_sources(request)

    monkeypatch.setattr(http, "transport", httpx.MockTransport(handler))
    return calls


@pytest.fixture
def cfg(tmp_path, monkeypatch) -> config.Config:
    monkeypatch.setenv("BIBLIO_HOME", str(tmp_path))
    return config.load()


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg.db_path)
    core.setup(c, cfg.home)
    yield c
    c.close()


@pytest.fixture
def api(cfg) -> TestClient:
    return TestClient(create_app(cfg), headers={"Authorization": f"Bearer {cfg.token}"})
