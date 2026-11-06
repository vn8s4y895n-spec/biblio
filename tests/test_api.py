from conftest import MERINO_DOI, MERINO_PMID


def test_token_required(api):
    for headers in ({"Authorization": ""}, {"Authorization": "Bearer faux"}):
        assert api.get("/api/v1/works", headers=headers).status_code == 401
        assert api.post("/api/v1/works", json={"identifier": MERINO_DOI}, headers=headers).status_code == 401


def test_health_is_public(api):
    assert api.get("/api/v1/health", headers={"Authorization": ""}).json()["app"] == "biblio"


def test_add_then_readd(api):
    first = api.post("/api/v1/works", json={"identifier": MERINO_DOI})
    assert first.status_code == 201 and first.json()["created"]
    again = api.post("/api/v1/works", json={"identifier": MERINO_PMID})
    assert again.status_code == 200 and not again.json()["created"]
    assert again.json()["work"]["id"] == first.json()["work"]["id"]
    listing = api.get("/api/v1/works").json()
    assert listing["total"] == 1 and listing["items"][0]["citekey"] == "harris2018effect"
    assert listing["items"][0]["first_author"] == "Harris" and listing["items"][0]["doi"] == MERINO_DOI


def test_get_by_citekey_doi_and_changes(api):
    api.post("/api/v1/works", json={"identifier": MERINO_DOI}, headers={"X-Biblio-Client": "mcp"})
    assert api.get("/api/v1/works/harris2018effect").json()["identifiers"]["pmid"] == MERINO_PMID
    assert api.get(f"/api/v1/works/{MERINO_DOI}").json()["citekey"] == "harris2018effect"
    changes = api.get("/api/v1/works/harris2018effect/changes").json()
    assert changes[0]["op"] == "create" and changes[0]["client"] == "mcp"
    assert api.get(f"/api/v1/works/{MERINO_DOI}/changes").json() == changes


def test_errors(api):
    assert api.get("/api/v1/works/inconnu").status_code == 404
    assert api.post("/api/v1/works", json={"identifier": "bonjour"}).status_code == 422
    assert api.post("/api/v1/works", json={"identifier": "10.9999/x.y"}).status_code == 404
    bad_client = api.post("/api/v1/works", json={"identifier": MERINO_DOI}, headers={"X-Biblio-Client": "pirate"})
    assert bad_client.status_code == 422


def test_shutdown_requires_token_and_signals(api, monkeypatch):
    sent = []
    monkeypatch.setattr("biblio.api.os.kill", lambda pid, sig: sent.append(sig))
    assert api.post("/api/v1/shutdown", headers={"Authorization": ""}).status_code == 401
    assert sent == []
    assert api.post("/api/v1/shutdown").status_code == 202
    assert len(sent) == 1


def test_openapi_documents_routes(api):
    paths = api.get("/openapi.json").json()["paths"]
    assert {"/api/v1/works", "/api/v1/works/{ref}", "/api/v1/works/{ref}/changes"} <= set(paths), set(paths)
