from conftest import MERINO_DOI

from biblio import db, watch

SURGISPHERE = "10.1016/s0140-6736(20)31180-6"  # Lancet 2020, rétracté (données Retraction Watch)
PREPRINT = "10.2337/figshare.27068230"  # preprint dont Crossref connaît la version publiée
PM_QUERY = '(ESBL[tiab] OR "extended-spectrum beta-lactamase"[tiab]) AND household[tiab] AND transmission[tiab]'


def add_watch_library(api):
    api.post("/api/v1/works", json={"identifier": MERINO_DOI, "pdf": False})
    api.post("/api/v1/works/manual", json={"title": "Hydroxychloroquine or chloroquine with or without a macrolide for "
                                           "treatment of COVID-19: a multinational registry analysis",
                                           "year": 2020, "identifiers": [SURGISPHERE]})  # fmt: skip
    return api.post("/api/v1/works/manual", json={"title": "Un preprint de diabétologie", "year": 2024, "csl_type": "article",
                                                  "identifiers": [PREPRINT]}).json()["work"]  # fmt: skip


def test_library_check_finds_retractions_and_published_versions(api, network):
    add_watch_library(api)
    report = api.post("/api/v1/watch/library").json()
    assert report["checked"] == 3
    assert report["alerts"] == {"retraction": 1, "concern": 1, "correction": 1, "published": 1}  # une alerte par article et type
    crossref_calls = [r for r in network if r.url.host == "api.crossref.org" and r.url.params.get("filter", "").startswith("doi:")]
    assert len(crossref_calls) == 1  # un seul appel pour toute la bibliothèque

    lancet = api.get(f"/api/v1/works/{SURGISPHERE}").json()
    assert lancet["retracted"] and {"retraction", "expression_of_concern", "correction"} <= {u["kind"] for u in lancet["updates"]}
    assert any(u["source"] == "retraction-watch" for u in lancet["updates"])
    assert not api.get("/api/v1/works/harris2018effect").json()["retracted"]
    retracted = api.get("/api/v1/works", params={"retracted": True}).json()
    assert retracted["total"] == 1 and retracted["items"][0]["retracted"]
    preprint = api.get(f"/api/v1/works/{PREPRINT}").json()
    assert preprint["updates"] == [{"kind": "published", "notice_doi": "10.2337/ds24-0025", "date": None, "source": "crossref"}]

    alerts = api.get("/api/v1/alerts").json()
    assert alerts["unseen"] == sum(report["alerts"].values()) and alerts["library_check"]
    first = alerts["items"][0]
    assert first["citekey"] and first["title"] and first["data"]["notice_doi"]
    # Deuxième vérification : rien de nouveau, aucune alerte en double.
    assert api.post("/api/v1/watch/library").json()["alerts"] == {}
    assert api.get("/api/v1/alerts").json()["unseen"] == alerts["unseen"]

    assert api.post("/api/v1/alerts/seen", json={"ids": [first["id"]]}).json() == {"seen": 1}
    assert api.get("/api/v1/alerts", params={"unseen_only": True}).json()["unseen"] == alerts["unseen"] - 1
    assert api.post("/api/v1/alerts/seen", json={}).json()["seen"] == alerts["unseen"] - 1
    assert api.get("/api/v1/alerts", params={"unseen_only": True}).json()["items"] == []


def test_supersede_preprint(api):
    preprint = api.post("/api/v1/works/manual", json={"title": "Piperacillin vs meropenem, preprint", "year": 2018,
                                                      "csl_type": "article", "identifiers": ["10.1101/2018.01.01.000001"]}).json()["work"]  # fmt: skip
    api.put(f"/api/v1/works/{preprint['id']}/note", json={"text": "Lu en preprint."})
    published = api.post(f"/api/v1/works/{preprint['id']}/supersede", json={"doi": MERINO_DOI}).json()
    assert published["citekey"] == "harris2018effect"
    assert published["identifiers"]["doi"] == MERINO_DOI and published["identifiers"]["preprint-doi"] == "10.1101/2018.01.01.000001"
    assert api.get("/api/v1/works").json()["total"] == 1
    assert "Lu en preprint." in api.get("/api/v1/works/harris2018effect/note").json()["text"]
    assert api.post("/api/v1/works/harris2018effect/supersede", json={"doi": MERINO_DOI}).status_code == 422


def test_watched_search_reports_only_new_results(api, conn):
    found = api.post("/api/v1/ask", json={"question": PM_QUERY, "all_journals": True}).json()
    saved = api.post("/api/v1/searches", json={"question": "Transmission des BLSE", "query": PM_QUERY,
                                               "sources": found["sources"][:3]}).json()  # fmt: skip
    assert api.put(f"/api/v1/searches/{saved['id']}/watch", json={"days": 0}).status_code == 422
    watched = api.put(f"/api/v1/searches/{saved['id']}/watch", json={"days": 7}).json()
    assert watched["watch_days"] == 7 and watched["last_run"]
    assert watch.due_searches(conn) == []  # vient d'être enregistrée : pas encore à relancer

    new = api.post(f"/api/v1/searches/{saved['id']}/run").json()["new"]
    shown = {s["title"] for s in found["sources"][:3]}
    assert new and not shown & {r["title"] for r in new}  # les sources déjà vues ne reviennent pas
    assert api.post(f"/api/v1/searches/{saved['id']}/run").json()["new"] == []
    alert = api.get("/api/v1/alerts").json()["items"][0]
    assert alert["kind"] == "new_results" and alert["question"] == "Transmission des BLSE"
    assert alert["data"]["count"] == len(new) and alert["data"]["records"][0]["title"]

    conn.execute("UPDATE saved_searches SET last_run = '2000-01-01T00:00:00Z' WHERE id = ?", (saved["id"],))
    assert watch.due_searches(conn) == [saved["id"]]
    assert api.put(f"/api/v1/searches/{saved['id']}/watch", json={"days": None}).json()["watch_days"] is None
    assert watch.due_searches(conn) == []
    library = api.post("/api/v1/searches", json={"question": "Ma bibliothèque", "scope": "library"}).json()
    assert api.put(f"/api/v1/searches/{library['id']}/watch", json={"days": 7}).status_code == 422


def test_run_due_checks_library_weekly(api, conn, network):
    add_watch_library(api)
    watch.run_due(conn)
    assert db.get_meta(conn, "watch.library_check")
    calls = len(network)
    watch.run_due(conn)  # vérifiée il y a moins d'une semaine : rien à faire
    assert len(network) == calls


def test_neighbours(api):
    api.post("/api/v1/works", json={"identifier": MERINO_DOI, "pdf": False})
    out = api.get("/api/v1/works/harris2018effect/neighbours").json()
    assert len(out["related"]) == 10 and len(out["citing"]) == 10
    assert all("journal_status" in r and "in_library" in r for r in out["related"] + out["citing"])
    assert any("Piperacillin" in r["title"] for r in out["related"])
    no_doi = api.post("/api/v1/works/manual", json={"title": "Sans DOI du tout"}).json()["work"]
    assert api.get(f"/api/v1/works/{no_doi['id']}/neighbours").status_code == 422


def test_medrxiv_preprint_published(api, network):
    api.post("/api/v1/works/manual", json={"title": "Un preprint medRxiv", "year": 2022, "csl_type": "article",
                                           "identifiers": ["10.1101/2022.12.07.22282726"]})  # fmt: skip
    assert api.post("/api/v1/watch/library").json()["alerts"] == {"published": 1}
    work = api.get("/api/v1/works/10.1101/2022.12.07.22282726").json()
    assert work["updates"][0]["notice_doi"] == "10.1016/j.artmed.2023.102751"
    calls = len([r for r in network if r.url.host == "api.biorxiv.org"])
    api.post("/api/v1/watch/library")  # déjà connu : medRxiv n'est plus interrogé
    assert len([r for r in network if r.url.host == "api.biorxiv.org"]) == calls
