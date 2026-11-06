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


def test_journals_routes(api):
    info = api.get("/api/v1/journals").json()
    assert info["current"]["published_on"] == "2026-10-01" and len(info["history"]) == 1
    assert len(info["current"]["warnings"]) == 2
    jama = api.get("/api/v1/journals/lookup", params={"issn": "0098-7484"}).json()
    assert jama["status"] == "recommended" and jama["journal"]["name"] == "JAMA"
    assert api.get("/api/v1/journals/lookup").status_code == 422
    assert api.post("/api/v1/journals/update").status_code == 502  # page absente : la liste reste en place
    assert api.get("/api/v1/journals").json()["last_error"]


def test_journal_import_and_status_filter(api):
    from conftest import small_list

    api.post("/api/v1/works", json={"identifier": MERINO_DOI})
    assert api.get("/api/v1/works", params={"journal_status": "recommended"}).json()["total"] == 1
    assert api.get("/api/v1/works", params={"journal_status": ["withdrawn", "not_listed"]}).json()["total"] == 0
    assert api.get("/api/v1/works", params={"journal_status": "inconnu"}).status_code == 422

    bad = api.post("/api/v1/journals/import", content=b"pas un zip")
    assert bad.status_code == 422
    imported = api.post("/api/v1/journals/import", params={"filename": "liste_05-01-27.xlsx"}, content=small_list())
    assert imported.status_code == 200 and imported.json()["updated"]
    assert imported.json()["current"]["published_on"] == "2027-01-05"
    work = api.get("/api/v1/works/harris2018effect").json()
    assert work["journal_status"]["status"] == "not_listed"
    assert api.get("/api/v1/works", params={"journal_status": "not_listed"}).json()["total"] == 1


def test_search_online_route(api):
    from conftest import SORBONNE

    query = '(ESBL[tiab] OR "extended-spectrum beta-lactamase"[tiab]) AND household[tiab] AND transmission[tiab]'
    data = api.get("/api/v1/search/online", params={"q": query, "per_source": 5}).json()
    assert data["total"] > 0 and {r["journal_status"]["status"] for r in data["results"]} == {"recommended"}
    assert data["hidden"]
    every = api.get("/api/v1/search/online", params={"q": query, "per_source": 5, "all_journals": True}).json()
    assert every["total"] == data["total"] + sum(data["hidden"].values())
    only = api.get("/api/v1/search/online", params={"q": query, "source": ["pubmed", "europepmc"], "per_source": 5}).json()
    assert set(only["sources"]) == {"pubmed", "europepmc"}
    assert api.get("/api/v1/search/online", params={"q": query, "source": "google"}).status_code == 422
    assert api.get("/api/v1/search/online").status_code == 422
    SORBONNE["s2_status"] = 429
    # Requête différente : la précédente serait resservie par le cache.
    degraded = api.get("/api/v1/search/online", params={"q": query + " AND child[tiab]", "per_source": 5}).json()
    assert degraded["sources"]["semanticscholar"]["status"] == "error"


def test_open_access_route(api):
    assert api.get("/api/v1/search/open-access", params={"pmcid": "PMC3244693"}).json()["source"] == "pmc"
    assert api.get("/api/v1/search/open-access").status_code == 422
    # Inconnu d'OpenAlex, et Unpaywall n'est pas interrogé sans e-mail de contact.
    assert api.get("/api/v1/search/open-access", params={"doi": "10.1016/j.cmi.2016.08.021"}).status_code == 404


def test_phase4_routes(api):
    from conftest import MERINO_PDF
    from test_library import BIBTEX

    added = api.post("/api/v1/works", json={"identifier": MERINO_DOI})
    assert added.status_code == 201 and added.json()["pdf"]["source"] == "pmc"
    files = api.get("/api/v1/works/harris2018effect/files").json()
    pdf = api.get(f"/api/v1/files/{files[0]['sha256']}")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content == MERINO_PDF
    assert api.get("/api/v1/files/" + "0" * 64).status_code == 404
    assert api.get("/api/v1/files/../config.toml").status_code == 404
    assert api.post("/api/v1/files", content=MERINO_PDF).json()["status"] == "duplicate"
    assert api.post("/api/v1/files", content=b"pas un pdf").status_code == 422
    assert api.post("/api/v1/files", content=b"").status_code == 422

    report = api.post("/api/v1/import", content=BIBTEX).json()
    assert report["format"] == "bibtex" and report["completed"] == ["harris2018effect"]
    assert api.post("/api/v1/import", content=b"texte quelconque").status_code == 422

    hits = api.get("/api/v1/search/library", params={"q": "meropenem mortality"}).json()
    assert hits["results"][0]["citekey"] == "harris2018effect"

    put = api.put("/api/v1/works/harris2018effect/note", json={"text": "Voir [[model2021]]"}, headers={"X-Biblio-Client": "web"})
    assert put.json()["links"] == ["model2021"]
    assert api.get(f"/api/v1/works/{MERINO_DOI}/note").json()["text"] == "Voir [[model2021]]"
    assert api.get("/api/v1/works/harris2018effect/changes").json()[-1]["client"] == "web"

    manual = api.post("/api/v1/works/manual", json={"title": "Note de service CLIN 2026", "authors": ["Martin, Léa"]})
    assert manual.status_code == 201
    assert api.post("/api/v1/works/manual", json={"authors": []}).status_code == 422
    assert api.get("/api/v1/duplicates").json() == []
    assert api.post("/api/v1/duplicates/merge", json={"keep": "harris2018effect", "drop": "harris2018effect"}).status_code == 422
    assert api.post("/api/v1/notes/sync").json()["updated"] == []
    assert api.post("/api/v1/inbox/scan").json() == []
    assert api.post("/api/v1/works/harris2018effect/pdf").status_code == 404  # a déjà un PDF
