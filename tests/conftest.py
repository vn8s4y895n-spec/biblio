"""Aucun test n'accède au réseau : les sources reçoivent des réponses enregistrées (tests/fixtures)."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from biblio import config, db
from biblio.api import create_app
from biblio.sources import http

FIXTURES = Path(__file__).parent / "fixtures"
MERINO_DOI = "10.1001/jama.2018.12163"
MERINO_PMID = "30208454"
EMPTY_ESEARCH = {"esearchresult": {"count": "0", "idlist": []}}


def fake_sources(request: httpx.Request) -> httpx.Response:
    url = request.url
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
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def api(cfg) -> TestClient:
    return TestClient(create_app(cfg), headers={"Authorization": f"Bearer {cfg.token}"})
