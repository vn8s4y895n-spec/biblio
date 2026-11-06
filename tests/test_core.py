import httpx
import pytest
from conftest import MERINO_DOI, MERINO_PMID, fake_sources

from biblio import core, db
from biblio.sources import http


@pytest.mark.parametrize(
    "text, expected",
    [
        ("10.1001/jama.2018.12163", ("doi", MERINO_DOI)),
        ("10.1001/JAMA.2018.12163.", ("doi", MERINO_DOI)),
        ("doi:10.1001/jama.2018.12163", ("doi", MERINO_DOI)),
        ("https://doi.org/10.1001/jama.2018.12163", ("doi", MERINO_DOI)),
        ("30208454", ("pmid", MERINO_PMID)),
        ("PMID: 30208454", ("pmid", MERINO_PMID)),
        ("https://pubmed.ncbi.nlm.nih.gov/30208454/", ("pmid", MERINO_PMID)),
    ],
)
def test_parse_identifier(text, expected):
    assert core.parse_identifier(text) == expected


@pytest.mark.parametrize("text", ["", "bonjour", "PMC6143100", "10.12/x"])
def test_parse_identifier_rejects(text):
    with pytest.raises(core.InvalidIdentifier):
        core.parse_identifier(text)


def test_add_by_doi_builds_full_record(conn):
    work, created, warnings = core.add_by_identifier(conn, MERINO_DOI, "cli")
    assert created and warnings == []
    assert work["citekey"] == "harris2018effect"
    assert work["title"].startswith("Effect of Piperacillin-Tazobactam vs Meropenem on 30-Day Mortality for Patients With E coli or")
    assert not work["title"].endswith(".")
    assert work["abstract"].startswith("IMPORTANCE: Extended-spectrum")
    assert "\nRESULTS: " in work["abstract"]
    assert work["identifiers"] == {"doi": MERINO_DOI, "pmcid": "PMC6143100", "pmid": MERINO_PMID}
    assert (work["year"], work["issued"], work["volume"], work["issue"], work["pages"]) == (2018, "2018-09-11", "320", "10", "984-994")
    assert (work["issn_print"], work["issn_e"], work["nlmid"]) == ("0098-7484", "1538-3598", "7501160")
    assert (work["container_title"], work["language"], work["study_type"]) == ("JAMA", "en", "rct")
    assert len(work["authors"]) == 35
    assert work["authors"][0] == {"family": "Harris", "given": "Patrick N A", "orcid": None}
    mesh = [s for s in work["subjects"] if s["scheme"] == "mesh"]
    assert len(mesh) == 20 and {"term": "Bacteremia", "ui": "D016470"}.items() <= next(s for s in mesh if s["term"] == "Bacteremia").items()


def test_readding_never_duplicates(conn, network):
    core.add_by_identifier(conn, MERINO_DOI)
    calls = len(network)
    for again in (MERINO_DOI, f"https://doi.org/{MERINO_DOI.upper()}", MERINO_PMID, f"PMID {MERINO_PMID}"):
        _, created, _ = core.add_by_identifier(conn, again)
        assert not created
    assert len(network) == calls  # déjà connu : aucune requête
    assert conn.execute("SELECT count(*) FROM works").fetchone()[0] == 1


def test_pmid_then_doi_is_same_work(conn):
    first, created, _ = core.add_by_identifier(conn, MERINO_PMID)
    assert created and first["identifiers"]["doi"] == MERINO_DOI
    second, created, _ = core.add_by_identifier(conn, MERINO_DOI)
    assert not created and second["id"] == first["id"]


def test_unknown_identifier(conn):
    with pytest.raises(core.NotFound):
        core.add_by_identifier(conn, "10.9999/nexiste.pas")
    with pytest.raises(core.NotFound):
        core.add_by_identifier(conn, "1")


def test_one_source_down_still_imports_with_warning(conn, monkeypatch):
    def pubmed_down(request):
        if request.url.host == "eutils.ncbi.nlm.nih.gov":
            raise httpx.ConnectError("réseau coupé", request=request)
        return fake_sources(request)

    monkeypatch.setattr(http, "transport", httpx.MockTransport(pubmed_down))
    work, created, warnings = core.add_by_identifier(conn, MERINO_DOI)
    assert created and len(warnings) == 1 and "réseau coupé" in warnings[0]
    assert work["container_title"] == "JAMA" and work["abstract"] is None
    assert work["title"].startswith("Effect of Piperacillin-Tazobactam vs Meropenem")


def test_all_sources_down_raises_source_error(conn, monkeypatch):
    def down(request):
        raise httpx.ConnectError("réseau coupé", request=request)

    monkeypatch.setattr(http, "transport", httpx.MockTransport(down))
    with pytest.raises(http.SourceError):
        core.add_by_identifier(conn, MERINO_DOI)


def test_reimport_completes_existing_work_and_logs_it(conn, monkeypatch):
    def pubmed_down(request):
        if request.url.host == "eutils.ncbi.nlm.nih.gov":
            raise httpx.ConnectError("réseau coupé", request=request)
        return fake_sources(request)

    monkeypatch.setattr(http, "transport", httpx.MockTransport(pubmed_down))
    first, _, _ = core.add_by_identifier(conn, MERINO_DOI, "cli")
    monkeypatch.setattr(http, "transport", httpx.MockTransport(fake_sources))
    # Le PMID n'est pas encore connu : la notice est récupérée puis fusionnée dans l'existante.
    work, created, _ = core.add_by_identifier(conn, MERINO_PMID, "web")
    assert not created and work["id"] == first["id"]
    assert work["abstract"].startswith("IMPORTANCE") and work["identifiers"]["pmid"] == MERINO_PMID
    changes = core.get_changes(conn, work["id"])
    assert [c["op"] for c in changes][0] == "create" and changes[0]["client"] == "cli"
    fields = {c["field"] for c in changes if c["op"] == "update"}
    assert {"abstract", "nlmid", "identifiers.pmid", "subjects"} <= fields
    assert all(c["client"] == "web" for c in changes[1:])


def test_citekey_collision_gets_suffix(conn):
    for expected in ("harris2018effect", "harris2018effecta", "harris2018effectb"):
        assert core.unique_citekey(conn, "harris2018effect") == expected
        conn.execute("INSERT INTO works (citekey, title, fingerprint) VALUES (?, 't', 'f')", (expected,))


def test_resolve_by_every_reference(conn):
    work, _, _ = core.add_by_identifier(conn, MERINO_DOI)
    for ref in (work["id"], str(work["id"]), "harris2018effect", MERINO_DOI, MERINO_PMID):
        assert core.resolve(conn, ref) == work["id"]
    with pytest.raises(core.NotFound):
        core.resolve(conn, "inconnu2020")


def test_migrations_are_idempotent_and_wal(conn):
    assert db.migrate(conn) == []
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
