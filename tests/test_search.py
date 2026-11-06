import time

import httpx
import pytest
from conftest import fake_sources

from biblio import core, search
from biblio.sources import Query, europepmc, http, openalex, pubmed, semanticscholar

PM_QUERY = '(ESBL[tiab] OR "extended-spectrum beta-lactamase"[tiab]) AND household[tiab] AND transmission[tiab]'
ALL = search.Filters(journal_status=[])  # sans le filtre des revues


def test_query_simplification():
    q = Query('(ESBL[tiab] OR "beta-lactamase"[MeSH Terms]) AND household[tiab] NOT pig[tiab]')
    assert q.boolean == '(ESBL OR "beta-lactamase" ) AND household NOT pig'
    assert q.plain == "ESBL beta-lactamase household"
    assert Query("x", year_from=2018).years == (2018, 2999)
    assert Query("x").years is None


def test_pubmed_search_keeps_relevance_order_and_syntax(network):
    records = pubmed.search(Query(PM_QUERY, limit=5))
    assert [r["identifiers"]["pmid"] for r in records] == ["31822301", "29550142", "33421572", "27927087", "27596534"]
    assert network[0].url.params["term"] == PM_QUERY and network[0].url.params["sort"] == "relevance"
    assert records[2]["container_abbrev"] == "Clin Microbiol Infect" and records[2]["subjects"]


def test_filters_are_pushed_to_sources(network):
    pubmed.search(Query(PM_QUERY, limit=5, year_from=2018, year_to=2021, mesh=("Escherichia coli",)))
    term = network[0].url.params["term"]
    assert term == f'({PM_QUERY}) AND 2018:2021[dp] AND "Escherichia coli"[MeSH Terms]'
    europepmc.search(Query(PM_QUERY, year_from=2018))
    assert "PUB_YEAR:[2018 TO 2999]" in network[-1].url.params["query"] and "[tiab]" not in network[-1].url.params["query"]
    openalex.search(Query(PM_QUERY, year_to=2020))
    assert network[-1].url.params["filter"] == "from_publication_date:1800-01-01,to_publication_date:2020-12-31"


def test_europepmc_parse():
    first = europepmc.search(Query(PM_QUERY, limit=5))[0]
    assert first["title"].startswith("Evaluation of detection performance") and "<i>" not in first["title"]
    assert (first["issn_print"], first["issn_e"], first["nlmid"]) == ("2165-0497", "2165-0497", "101634614")
    assert first["identifiers"] == {"doi": "10.1128/spectrum.01695-25", "pmid": "42159414", "pmcid": "PMC13340141"}
    assert first["oa_pdf_url"] == "https://europepmc.org/articles/PMC13340141?pdf=render" and first["is_oa"]
    assert first["authors"][0] == {"family": "Masoamphambe", "given": "Effita Fifi", "orcid": "0009-0009-6659-4677"}


def test_openalex_parse():
    first = openalex.search(Query(PM_QUERY, limit=5))[0]
    assert first["identifiers"] == {"openalex": "W2167174938", "doi": "10.3389/fmicb.2011.00246", "pmid": "22203818",
                                    "pmcid": "PMC3244693"}  # fmt: skip
    assert first["abstract"] and first["abstract"].split()[0].isalpha()  # résumé reconstruit depuis l'index inversé
    assert first["issn_print"] == "1664-302X" and first["oa_pdf_url"].endswith("/pdf") and first["cited_by_count"] == 390
    assert first["authors"][0] == {"family": "Guenther", "given": "Sebastian", "orcid": "0000-0003-0173-7314"}


def test_semanticscholar_parse(sorbonne):
    records = semanticscholar.search(Query(PM_QUERY))
    assert records[0]["identifiers"] == {"doi": "10.1016/j.cmi.2016.08.021", "pmid": "27596534", "s2": "1001"}
    assert (records[0]["issn_print"], records[0]["issn_e"]) == ("1198-743X", "1469-0691")
    assert records[1]["study_type"] == "review" and records[1]["oa_pdf_url"]
    assert records[2]["csl_type"] == "article" and records[2]["authors"][0]["family"] == "López"
    sorbonne["s2_status"] = 429
    with pytest.raises(http.SourceError, match="429"):
        semanticscholar.search(Query("autre requête"))


def test_federated_search_merges_duplicates(conn):
    result = search.search(conn, PM_QUERY, per_source=5, filters=ALL)
    assert {n: s["status"] for n, s in result["sources"].items()} == dict.fromkeys(search.SOURCES, "ok")
    assert result["query"]["plain"] == "ESBL extended-spectrum beta-lactamase household transmission"
    haverkate = next(r for r in result["results"] if r["identifiers"].get("pmid") == "27596534")
    assert haverkate["sources"] == {"pubmed": 5, "semanticscholar": 1}
    # Fusion : MeSH et résumé de PubMed, nombre de citations de Semantic Scholar.
    assert haverkate["subjects"] and haverkate["abstract"] and haverkate["cited_by_count"] == 61
    assert haverkate["identifiers"]["s2"] == "1001"
    mdpi = next(r for r in result["results"] if r["identifiers"].get("doi") == "10.3390/antibiotics10020171")
    assert set(mdpi["sources"]) == {"openalex", "semanticscholar"}
    total = sum(s["count"] for s in result["sources"].values())
    assert result["total"] == total - 2  # deux doublons fusionnés
    # Classement : un article trouvé par deux sources passe devant à rang égal.
    assert result["results"][0]["score"] >= result["results"][-1]["score"]


def test_merge_by_title_when_no_shared_identifier():
    a = {"title": "Household transmission of ESBL-producing Enterobacterales in Europe", "year": 2020,
         "identifiers": {"pmid": "1"}, "authors": [], "subjects": []}  # fmt: skip
    b = {**a, "title": "Household Transmission of ESBL-Producing Enterobacterales in Europe.", "identifiers": {"s2": "9"}}
    short = {**a, "title": "Editorial", "identifiers": {"s2": "10"}}
    other = {**short, "identifiers": {"s2": "11"}}
    merged = search.merge_results({"pubmed": [a], "semanticscholar": [b, short, other]})
    assert len(merged) == 3 and merged[0]["identifiers"] == {"s2": "9", "pmid": "1"}


def test_journal_filter_by_default(conn):
    result = search.search(conn, PM_QUERY, per_source=5)
    statuses = {r["journal_status"]["status"] for r in result["results"]}
    assert statuses == {"recommended"}
    assert result["hidden"].get("not_listed", 0) >= 1  # Antibiotics (MDPI) n'est pas dans la liste
    assert result["hidden"].get("unidentified", 0) >= 1  # thèses et posters remontés par Crossref
    everything = search.search(conn, PM_QUERY, per_source=5, filters=ALL)
    assert everything["total"] == result["total"] + sum(result["hidden"].values())


def test_post_filters(conn):
    def ids(**kw):
        return {r["identifiers"].get("pmid") for r in search.search(conn, PM_QUERY, per_source=5, filters=search.Filters(journal_status=[], **kw))["results"]}

    assert ids(year_from=2020, year_to=2021) >= {"31822301", "33421572"} and "27596534" not in ids(year_from=2020)
    assert ids(journal="Clin Microbiol Infect") == {"33421572", "27596534"}
    # PubMed ne donne que l'ISSN électronique de ces deux articles : l'ISSN papier vient de la liste des revues.
    assert ids(journal="1198-743X") == {"33421572", "27596534"}
    assert ids(journal="Clinical Microbiology and Infection") == {"33421572", "27596534"}
    with_mesh = ids(mesh=["Humans"])
    assert with_mesh and None not in with_mesh  # seules les notices indexées en MeSH passent
    assert all(r["is_oa"] or r["oa_pdf_url"] for r in search.search(conn, PM_QUERY, per_source=5, filters=search.Filters(journal_status=[], open_access=True))["results"])


def test_one_source_failing_does_not_block(conn, sorbonne):
    sorbonne["s2_status"] = 429
    result = search.search(conn, PM_QUERY, per_source=5, filters=ALL)
    assert result["sources"]["semanticscholar"]["status"] == "error" and "429" in result["sources"]["semanticscholar"]["error"]
    assert result["sources"]["pubmed"]["count"] == 5 and result["total"] > 10


def test_source_selection_and_in_library(conn):
    # Un des résultats de la recherche est déjà dans la bibliothèque.
    conn.execute("INSERT INTO works (citekey, title, fingerprint) VALUES ('haverkate2017quantifying', 't', 'f')")
    conn.execute("INSERT INTO identifiers VALUES ('pmid', '27596534', last_insert_rowid())")
    result = search.search(conn, PM_QUERY, sources=["pubmed"], per_source=5)
    assert set(result["sources"]) == {"pubmed"}
    assert {r["in_library"] for r in result["results"]} == {None, "haverkate2017quantifying"}
    with pytest.raises(ValueError):
        search.search(conn, PM_QUERY, sources=["google"])


def test_cache_serves_repeated_search(conn, network):
    search.search(conn, PM_QUERY, per_source=5)
    calls = len(network)
    search.search(conn, PM_QUERY, per_source=5)
    assert len(network) == calls  # tout vient du cache
    conn.execute("UPDATE http_cache SET expires_at = 0")
    search.search(conn, PM_QUERY, per_source=5)
    assert len(network) == 2 * calls
    assert conn.execute("SELECT count(*) FROM http_cache WHERE url LIKE '%api_key%'").fetchone()[0] == 0


def test_rate_limit_spaces_requests(monkeypatch):
    monkeypatch.setattr(http, "MIN_INTERVAL", {"api.openalex.org": 0.05})
    start = time.perf_counter()
    for _ in range(3):
        http.get("https://api.openalex.org/works", {"search": "x"})
    assert time.perf_counter() - start >= 0.1


def test_429_is_retried_once(monkeypatch):
    answers = [httpx.Response(429, headers={"retry-after": "0"}), httpx.Response(200, json={"ok": True})]
    monkeypatch.setattr(http, "transport", httpx.MockTransport(lambda r: answers.pop(0)))
    assert http.get("https://api.semanticscholar.org/x").json() == {"ok": True}
    calls = []
    monkeypatch.setattr(http, "transport", httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(429, headers={"retry-after": "0"})))
    with pytest.raises(http.SourceError, match="429"):
        http.get("https://api.semanticscholar.org/x")
    assert len(calls) == 2
    with pytest.raises(http.SourceError, match="en pause"):  # pas de nouvel appel pendant la pause
        http.get("https://api.semanticscholar.org/y")
    assert len(calls) == 2


def test_open_access(monkeypatch):
    assert search.find_open_access(pmcid="pmc3244693")["pdf_url"] == "https://europepmc.org/articles/PMC3244693?pdf=render"
    assert search.find_open_access(doi="10.1016/j.cmi.2016.08.021") is None  # sans e-mail, pas d'Unpaywall
    monkeypatch.setattr(http, "contact_email", "test@example.org")
    found = search.find_open_access(doi="10.1016/j.cmi.2016.08.021")
    assert found["pdf_url"] == "https://repository.example/haverkate.pdf" and found["version"] == "acceptedVersion"
    assert search.find_open_access(doi="10.9999/absent") is None


def test_unexpected_source_format_is_reported(conn, monkeypatch):
    def broken(request):
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"surprise": True})
        return fake_sources(request)

    monkeypatch.setattr(http, "transport", httpx.MockTransport(broken))
    result = search.search(conn, PM_QUERY, per_source=5, filters=ALL)
    assert result["sources"]["openalex"]["status"] == "error" and result["sources"]["pubmed"]["status"] == "ok"
