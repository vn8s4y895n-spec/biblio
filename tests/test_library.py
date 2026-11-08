from pathlib import Path

import httpx
import pytest
from conftest import MERINO_DOI, MERINO_PDF, MERINO_PMID, MERINO_TITLE, fake_sources, make_pdf

from biblio import core, db, formats, importer, library, notes
from biblio.sources import http

# --- Identifiants -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [("arXiv:2101.01234v2", ("arxiv", "2101.01234")), ("https://arxiv.org/abs/2101.01234", ("arxiv", "2101.01234")),
     ("PMC6143100", ("pmcid", "PMC6143100")), ("https://pmc.ncbi.nlm.nih.gov/articles/PMC6143100/", ("pmcid", "PMC6143100")),
     ("https://www.medrxiv.org/content/10.1101/2020.04.16.20067835v2.full.pdf", ("doi", "10.1101/2020.04.16.20067835")),
     ("https://www.biorxiv.org/content/10.1101/2021.01.01.425001v1", ("doi", "10.1101/2021.01.01.425001")),
     ("https://europepmc.org/article/MED/30208454", ("pmid", "30208454")),
     ("https://journal.example.org/merino", ("url", "https://journal.example.org/merino"))],
)  # fmt: skip
def test_new_identifiers(text, expected):
    assert core.parse_identifier(text) == expected


def test_add_by_pmcid_arxiv_and_url(conn):
    work, created, _ = core.add_by_identifier(conn, "PMC6143100")
    assert created and work["identifiers"]["pmid"] == MERINO_PMID and work["identifiers"]["pmcid"] == "PMC6143100"
    # Page web d'article : l'identifiant est lu dans ses balises <meta> ; l'article est déjà là.
    again, created, _ = core.add_by_identifier(conn, "https://journal.example.org/merino")
    assert not created and again["id"] == work["id"]
    preprint, created, _ = core.add_by_identifier(conn, "arXiv:2101.01234")
    assert created and preprint["title"] == "A stochastic model of household transmission of resistant bacteria"
    assert preprint["identifiers"] == {"arxiv": "2101.01234", "doi": "10.48550/arxiv.2101.01234"}
    assert preprint["journal_status"]["status"] == "preprint" and preprint["authors"][0]["family"] == "López"
    with pytest.raises(core.NotFound):
        core.add_by_identifier(conn, "arXiv:2101.99999")
    with pytest.raises(core.NotFound):
        core.add_by_identifier(conn, "https://journal.example.org/absent")


# --- PDF ---------------------------------------------------------------------------------------------


def test_store_pdf_by_hash(conn, cfg):
    sha, new = library.store_pdf(conn, cfg.home, MERINO_PDF, "merino.pdf")
    path = library.pdf_path(cfg.home, sha)
    assert new and path == cfg.home / "library" / sha[:2] / sha[2:4] / f"{sha}.pdf" and path.read_bytes() == MERINO_PDF
    assert library.store_pdf(conn, cfg.home, MERINO_PDF, "copie.pdf") == (sha, False)
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1
    with pytest.raises(library.FileError, match="pas un PDF"):
        library.store_pdf(conn, cfg.home, b"<html>pas un pdf</html>")
    with pytest.raises(library.FileError, match="illisible"):
        library.store_pdf(conn, cfg.home, b"%PDF-1.4\nn'importe quoi")
    assert not list((cfg.home / "library").rglob("*.tmp"))


def test_find_dois_prefers_metadata_and_first_pages():
    pdf = make_pdf(["cited: doi:10.9999/cited.one.", "p2", "References doi:10.8888/ignored"], xmp_doi="10.1234/XMP.Doi")
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "a.pdf"
    path.write_bytes(pdf)
    assert library.find_dois(pdf, library.pdf_metadata(path), library.pdf_text(path)) == ["10.1234/xmp.doi", "10.9999/cited.one"]


def test_chunk_respects_size_and_sentences():
    text = " ".join(f"Phrase numéro {i} sur la transmission." for i in range(200))
    parts = library.chunk(text, 300)
    assert all(len(p) <= 300 for p in parts) and all(p.endswith(".") for p in parts)
    assert " ".join(parts) == text
    assert library.chunk("x" * 700, 300) == ["x" * 300, "x" * 300, "x" * 100]
    assert library.chunk("   ") == []


def test_import_pdf_with_doi(conn, cfg):
    result = importer.import_pdf(conn, cfg.home, MERINO_PDF, "merino.pdf", "cli")
    assert result["status"] == "attached" and result["doi"] == MERINO_DOI
    work = result["work"]
    assert work["citekey"] == "harris2018effect" and work["identifiers"]["pmid"] == MERINO_PMID
    files = library.files_of(conn, work["id"])
    assert len(files) == 1 and files[0]["text_status"] == "done" and files[0]["pages"] == 2
    pages = {r[0] for r in conn.execute("SELECT page FROM passages WHERE work_id = ? AND origin = 'fulltext'", (work["id"],))}
    assert pages == {1, 2}
    assert importer.import_pdf(conn, cfg.home, MERINO_PDF)["status"] == "duplicate"


def test_pdf_with_cited_doi_needs_confirmation(conn, cfg):
    """Le DOI trouvé est celui d'un article cité : le titre ne correspond pas, rien n'est rattaché d'office."""
    pdf = make_pdf(["Household acquisition and transmission of ESBL Enterobacterales: a prospective cohort study",
                    f"As shown previously (doi:{MERINO_DOI}), carbapenems remain the reference."])  # fmt: skip
    result = importer.import_pdf(conn, cfg.home, pdf, "cohort.pdf")
    assert result["status"] == "needs_metadata" and result["dois_found"] == [MERINO_DOI]
    assert result["title_guess"].startswith("Household acquisition and transmission")
    assert len(result["candidates"]) == 5 and result["candidates"][0]["doi"]  # recherche Crossref enregistrée
    assert conn.execute("SELECT count(*) FROM works").fetchone()[0] == 0
    assert [f["sha256"] for f in library.unattached_files(conn)] == [result["sha256"]]
    work = importer.attach_file(conn, cfg.home, result["sha256"], identifier=MERINO_PMID)
    assert work["citekey"] == "harris2018effect" and library.unattached_files(conn) == []


def test_scanned_pdf_without_text(conn, cfg):
    result = importer.import_pdf(conn, cfg.home, make_pdf([""]), "scan.pdf")
    assert result["status"] == "needs_metadata" and result["title_guess"] is None
    work, _, _ = core.add_by_identifier(conn, MERINO_DOI)
    importer.attach_file(conn, cfg.home, result["sha256"], work_ref=work["citekey"])
    assert library.files_of(conn, work["id"])[0]["text_status"] == "empty"


def test_add_downloads_open_access_pdf(conn, cfg, network):
    result = importer.add(conn, cfg.home, MERINO_DOI, "cli")
    assert result["created"] and result["pdf"]["source"] == "pmc" and not result["warnings"]
    files = library.files_of(conn, result["work"]["id"])
    assert files[0]["source_url"] == "https://europepmc.org/articles/PMC6143100?pdf=render"
    calls = len(network)
    again = importer.add(conn, cfg.home, MERINO_PMID, "cli")
    assert not again["created"] and again["pdf"] is None and len(network) == calls  # rien à refaire


def test_pdf_failure_does_not_block_add(conn, cfg, monkeypatch):
    def no_pdf(request):
        if request.url.host == "europepmc.org":
            return httpx.Response(403, text="Forbidden")
        return fake_sources(request)

    monkeypatch.setattr(http, "transport", httpx.MockTransport(no_pdf))
    result = importer.add(conn, cfg.home, MERINO_DOI)
    assert result["created"] and result["pdf"] is None and "pmc : europepmc.org : HTTP 403" in result["warnings"][0]
    assert importer.add(conn, cfg.home, "arXiv:2101.01234")["pdf"] is None  # pas de PMCID ni de PDF connu


def test_next_open_access_source_is_tried(conn, cfg, monkeypatch):
    """Europe PMC refuse ; OpenAlex connaît une copie libre (HTML refusé d'abord, puis vrai PDF)."""
    def sources(request):
        if request.url.host == "europepmc.org":
            return httpx.Response(403)
        if request.url.host == "api.openalex.org" and request.url.path.startswith("/works/doi:"):
            return httpx.Response(200, json={"best_oa_location": {"pdf_url": "https://repo.example/merino.pdf", "license": "cc-by"}})
        if request.url.host == "repo.example":
            return httpx.Response(200, content=MERINO_PDF)
        return fake_sources(request)

    monkeypatch.setattr(http, "transport", httpx.MockTransport(sources))
    result = importer.add(conn, cfg.home, MERINO_DOI)
    assert result["pdf"]["source"] == "openalex" and result["warnings"] == []
    assert library.files_of(conn, result["work"]["id"])[0]["source_url"] == "https://repo.example/merino.pdf"


# --- Recherche plein texte -------------------------------------------------------------------------------


def test_library_search(conn, cfg):
    importer.import_pdf(conn, cfg.home, MERINO_PDF)
    found = library.search(conn, "mortality meropenem")
    assert found["mode"] == "all" and found["results"][0]["citekey"] == "harris2018effect"
    origins = {p["origin"] for p in found["results"][0]["passages"]}
    assert "fulltext" in origins and any("<mark>" in p["snippet"] for p in found["results"][0]["passages"])
    page2 = library.search(conn, "randomized 187 patients")["results"][0]["passages"]
    assert any(p["origin"] == "fulltext" and p["page"] == 2 for p in page2)
    # Aucun passage n'a tous les mots : on accepte n'importe lequel.
    assert library.search(conn, "meropenem zygomycose")["mode"] == "any"
    assert library.search(conn, "zygomycose")["results"] == []
    # Syntaxe FTS5, préfixe, et requête invalide qui ne plante pas.
    assert library.search(conn, '"bloodstream infection" NOT dengue')["mode"] == "advanced"
    assert library.search(conn, "piperac*")["results"]
    assert library.search(conn, 'NEAR("meropenem" "')["mode"] in ("all", "any")  # syntaxe invalide : requête protégée
    # Les accents et le stemming (porter) aident : « mortalities » trouve « mortality ».
    assert library.search(conn, "mortalities")["results"]


def test_snippets_are_html_escaped(conn):
    core.add_manual(conn, {"title": "Le seuil <script>alert(1)</script> de CMI", "authors": ["Dupont, Marie"], "year": 2024})
    snippet = library.search(conn, "seuil")["results"][0]["passages"][0]["snippet"]
    assert "<script>" not in snippet and "&lt;script&gt;" in snippet and "<mark>seuil</mark>" in snippet


def test_existing_works_are_indexed_on_setup(cfg, conn):
    conn.execute("INSERT INTO works (citekey, title, fingerprint) VALUES ('ancien2019', 'Ancienne notice sans passages', 'f')")
    assert library.search(conn, "ancienne")["results"] == []
    core.setup(conn, cfg.home)
    assert library.search(conn, "ancienne")["results"][0]["citekey"] == "ancien2019"


def test_search_10k_works_is_fast(conn):
    import time

    rows = [(f"k{i}", f"Title {i} about household transmission of resistant Enterobacterales number {i}", "f") for i in range(10_000)]
    with conn:
        conn.executemany("INSERT INTO works (citekey, title, fingerprint) VALUES (?, ?, ?)", rows)
        conn.executemany("INSERT INTO passages (work_id, origin, ord, text) SELECT id, 'metadata', 0, title FROM works WHERE citekey = ?",
                         [(r[0],) for r in rows])  # fmt: skip
    start = time.perf_counter()
    found = library.search(conn, "household transmission 4242")
    assert found["results"][0]["citekey"] == "k4242"
    assert time.perf_counter() - start < 0.2


# --- Notes ----------------------------------------------------------------------------------------------------


def test_notes_are_markdown_files_with_links(conn, cfg):
    merino, _, _ = core.add_by_identifier(conn, MERINO_DOI)
    other, _ = core.add_manual(conn, {"title": "Une revue sur les BLSE en ville", "authors": ["Durand, Paul"], "year": 2022})
    note = notes.write(conn, cfg.home, other["id"], "Compare avec [[harris2018effect]] : pas de **non-infériorité**.")
    path = cfg.home / "notes" / f"{other['citekey']}.md"
    assert note["path"] == str(path) and path.read_text().startswith(f"---\ncitekey: {other['citekey']}\n")
    assert note["links"] == ["harris2018effect"] and notes.read(conn, cfg.home, merino["id"])["backlinks"] == [other["citekey"]]
    assert library.search(conn, "non-infériorité")["results"][0]["passages"][0]["origin"] == "note"

    # Modification hors de Biblio (Obsidian) : réindexée par sync().
    path.write_text(path.read_text().replace("non-infériorité", "supériorité"), encoding="utf-8")
    assert notes.sync(conn, cfg.home)["updated"] == [other["citekey"]]
    assert library.search(conn, "supériorité")["results"] and notes.sync(conn, cfg.home)["updated"] == []
    (cfg.home / "notes" / "inconnue2020.md").write_text("orpheline")
    assert notes.sync(conn, cfg.home)["unknown"] == ["inconnue2020.md"]
    path.unlink()
    assert notes.sync(conn, cfg.home)["removed"] == [other["citekey"]]
    assert notes.read(conn, cfg.home, merino["id"])["backlinks"] == []
    # Une note créée directement dans le dossier est prise en compte.
    (cfg.home / "notes" / "harris2018effect.md").write_text("Lu en staff le 6 novembre.")
    assert notes.sync(conn, cfg.home)["updated"] == ["harris2018effect"]
    assert notes.read(conn, cfg.home, merino["id"])["text"] == "Lu en staff le 6 novembre."
    assert notes.write(conn, cfg.home, merino["id"], "  ")["exists"] is False


# --- Fichiers bibliographiques -----------------------------------------------------------------------------------

BIBTEX = r"""
@article{harris2018,
  title = {Effect of {Piperacillin-Tazobactam} vs Meropenem on 30-Day Mortality},
  author = {Harris, Patrick N. A. and Tambyah, Paul A.},
  journal = {JAMA}, year = {2018}, volume = {320}, number = {10}, pages = {984--994},
  doi = {10.1001/jama.2018.12163}, issn = {0098-7484}, keywords = {ESBL; carbapenem}
}
@misc{model2021, title = {A stochastic model of household transmission}, author = {Garc{\'\i}a L{\'o}pez, Ana},
  year = {2021}, eprint = {2101.01234}, archivePrefix = {arXiv}}
@book{livre, title = {Bactériologie médicale}, author = {François Denis}, year = 2016}
"""

RIS = """TY  - JOUR
TI  - Household carriage and acquisition of ESBL-producing Enterobacteriaceae
AU  - Martischang, Romain
AU  - Riccio, Marie-Eve
JO  - Infection Control and Hospital Epidemiology
PY  - 2020/01/01/
VL  - 41
SP  - 69
EP  - 75
DO  - 10.1017/ice.2019.336
SN  - 0899-823X
KW  - ESBL
ER  -
TY  - JOUR
TI  - Sans identifiant
AU  - Personne, Anne
PY  - 2019
ER  - """

NBIB = """PMID- 30208454
TI  - Effect of Piperacillin-Tazobactam vs Meropenem on 30-Day Mortality for Patients
      With E coli or Klebsiella pneumoniae Bloodstream Infection and Ceftriaxone
      Resistance: A Randomized Clinical Trial.
AB  - IMPORTANCE: Extended-spectrum beta-lactamases mediate resistance.
FAU - Harris, Patrick N A
AU  - Harris PNA
FAU - Tambyah, Paul A
AU  - Tambyah PA
LA  - eng
PT  - Journal Article
PT  - Randomized Controlled Trial
DP  - 2018 Sep 11
TA  - JAMA
JT  - JAMA
JID - 7501160
IS  - 1538-3598 (Electronic)
IS  - 0098-7484 (Linking)
VI  - 320
IP  - 10
PG  - 984-994
MH  - *Bacteremia/drug therapy/mortality
MH  - Humans
AID - 10.1001/jama.2018.12163 [doi]
PMC - PMC6143100
"""

CSLJSON = """[{"id": "x", "type": "article-journal", "title": "Delineating the risk factors of ESBL acquisition",
  "author": [{"family": "Nguyen", "given": "Lan"}, {"literal": "ESBL Study Group"}],
  "container-title": "Nature Communications", "ISSN": ["2041-1723"], "issued": {"date-parts": [[2026, 3]]},
  "DOI": "10.1038/S41467-026-75634-0", "keyword": "ESBL, community"}]"""


def test_parse_formats():
    bib = formats.parse(BIBTEX)
    assert [r["title"] for r in bib] == ["Effect of Piperacillin-Tazobactam vs Meropenem on 30-Day Mortality",
                                         "A stochastic model of household transmission", "Bactériologie médicale"]  # fmt: skip
    assert bib[0]["identifiers"] == {"doi": MERINO_DOI} and bib[0]["pages"] == "984-994" and bib[0]["issn_print"] == "0098-7484"
    assert bib[1]["identifiers"] == {"arxiv": "2101.01234"} and bib[1]["csl_type"] == "article"
    assert bib[1]["authors"][0] == {"family": "García López", "given": "Ana", "orcid": None}
    assert bib[2]["csl_type"] == "book" and bib[2]["authors"][0]["family"] == "Denis"
    ris = formats.parse(RIS)
    assert ris[0]["year"] == 2020 and ris[0]["pages"] == "69-75" and ris[0]["identifiers"] == {"doi": "10.1017/ice.2019.336"}
    assert [a["family"] for a in ris[0]["authors"]] == ["Martischang", "Riccio"] and ris[1]["identifiers"] == {}
    nbib = formats.parse(NBIB)[0]
    assert nbib["title"].endswith("A Randomized Clinical Trial") and "  " not in nbib["title"]
    assert nbib["identifiers"] == {"pmid": MERINO_PMID, "pmcid": "PMC6143100", "doi": MERINO_DOI}
    assert (nbib["issn_print"], nbib["issn_e"], nbib["nlmid"], nbib["study_type"]) == ("0098-7484", "1538-3598", "7501160", "rct")
    assert {"scheme": "mesh", "term": "Bacteremia", "ui": None, "major": True} in nbib["subjects"]
    csl = formats.parse(CSLJSON)[0]
    assert csl["identifiers"] == {"doi": "10.1038/s41467-026-75634-0"} and csl["issued"] == "2026-03"
    assert csl["authors"][1]["family"] == "ESBL Study Group"
    assert [formats.detect(t) for t in (BIBTEX, RIS, NBIB, CSLJSON)] == ["bibtex", "ris", "nbib", "csljson"]
    with pytest.raises(formats.FormatError):
        formats.detect("du texte quelconque")


def test_import_bibliography_never_duplicates(conn):
    core.add_by_identifier(conn, MERINO_DOI)
    report = importer.import_bibliography(conn, BIBTEX)
    assert report["format"] == "bibtex" and report["completed"] == ["harris2018effect"] and len(report["created"]) == 2
    again = importer.import_bibliography(conn, BIBTEX)
    assert again["created"] == [] and len(again["completed"]) == 3
    assert importer.import_bibliography(conn, NBIB)["completed"] == ["harris2018effect"]
    assert conn.execute("SELECT count(*) FROM works").fetchone()[0] == 3
    # Une notice sans identifiant (le livre) ré-importée n'est pas dupliquée non plus.
    assert conn.execute("SELECT count(*) FROM works WHERE csl_type = 'book'").fetchone()[0] == 1


# --- Doublons -----------------------------------------------------------------------------------------------------


def test_suspected_duplicate_then_merge(conn, cfg):
    first = importer.import_bibliography(conn, RIS)["created"][1]  # « Sans identifiant »
    imported = importer.import_bibliography(conn, RIS.replace("PY  - 2019", "PY  - 2019\nUR  - https://doi.org/10.5555/sans.id"))
    assert imported["suspected_duplicates"] == 1
    pairs = core.duplicates(conn)
    assert len(pairs) == 1 and {w["citekey"] for w in pairs[0]["works"]} == {first, imported["created"][0]}
    keep, drop = pairs[0]["works"]
    notes.write(conn, cfg.home, drop["id"], "Note de la notice supprimée.")
    sha, _ = library.store_pdf(conn, cfg.home, MERINO_PDF)
    library.attach(conn, cfg.home, drop["id"], sha)

    merged = core.merge_works(conn, cfg.home, keep["citekey"], drop["citekey"], "cli")
    assert merged["identifiers"] == {"doi": "10.5555/sans.id"} and core.duplicates(conn) == []
    assert [f["sha256"] for f in library.files_of(conn, merged["id"])] == [sha]
    assert notes.read(conn, cfg.home, merged["id"])["text"].startswith("Note de la notice supprimée.")
    assert not (cfg.home / "notes" / f"{drop['citekey']}.md").exists()
    assert library.search(conn, "randomized 187 patients")["results"][0]["citekey"] == merged["citekey"]
    assert [c["op"] for c in core.get_changes(conn, merged["id"])][-1] == "merge"
    with pytest.raises(core.NotFound):
        core.get_work(conn, drop["citekey"])
    with pytest.raises(ValueError):
        core.merge_works(conn, cfg.home, keep["citekey"], keep["citekey"])


def test_dismiss_duplicate(conn):
    importer.import_bibliography(conn, RIS)
    importer.import_bibliography(conn, RIS.replace("PY  - 2019", "PY  - 2019\nUR  - https://doi.org/10.5555/autre"))
    a, b = core.duplicates(conn)[0]["works"]
    core.dismiss_duplicate(conn, a["citekey"], b["citekey"])
    assert core.duplicates(conn) == []
    core.dismiss_duplicate(conn, a["citekey"], b["citekey"])  # sans effet la deuxième fois
    other, _ = core.add_manual(conn, {"title": "Sans rapport avec les autres"})
    with pytest.raises(core.NotFound):  # paire jamais signalée
        core.dismiss_duplicate(conn, a["citekey"], other["citekey"])


# --- Boîte d'entrée -----------------------------------------------------------------------------------------------


def test_inbox(conn, cfg):
    inbox = cfg.home / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "merino.pdf").write_bytes(MERINO_PDF)
    (inbox / "faux.pdf").write_bytes(b"pas un pdf")
    (inbox / "notes.txt").write_text("ignoré")
    results = {r["file"]: r["status"] for r in importer.scan_inbox(conn, cfg.home, min_age=0)}
    assert results == {"faux.pdf": "refused", "merino.pdf": "attached"}
    assert sorted(p.name for p in inbox.iterdir()) == ["notes.txt", "refusés"]
    assert (inbox / "refusés" / "faux.pdf").exists()
    assert core.get_changes(conn, "harris2018effect")[0]["client"] == "inbox"
    (inbox / "recent.pdf").write_bytes(MERINO_PDF)
    assert importer.scan_inbox(conn, cfg.home) == []  # fichier tout juste déposé (peut-être en cours de copie)


def test_manual_entry(conn):
    work, created = core.add_manual(conn, {"title": "Rapport du CNR Résistance 2024", "authors": ["Dortet, Laurent"],
                                           "year": 2025, "csl_type": "report", "identifiers": ["https://cnr.example/2024"]})  # fmt: skip
    assert created and work["citekey"] == "dortet2025rapport" and work["identifiers"] == {"url": "https://cnr.example/2024"}
    with pytest.raises(ValueError):
        core.add_manual(conn, {"title": " "})
    _, created = core.add_manual(conn, {"title": "Autre titre", "identifiers": [MERINO_DOI]})
    assert created  # identifiant inconnu : nouvelle notice
    _, created = core.add_manual(conn, {"title": "Encore", "identifiers": [MERINO_DOI]})
    assert not created


def test_merino_title_constant():
    assert MERINO_TITLE.startswith("Effect of Piperacillin")


# --- PDF déclaré par l'éditeur, Téléchargements ---------------------------------------------------------------------


def publisher_site(request: httpx.Request) -> httpx.Response:
    """Éditeur simulé : doi.org redirige vers la page de l'article, qui déclare son PDF ; le PDF n'est servi
    qu'avec le cookie de session posé par la page et la page en Referer (comme beaucoup d'éditeurs)."""
    url = request.url
    if url.host == "doi.org":
        return httpx.Response(302, headers={"location": "https://editeur.example/article/merino"})
    if url.path == "/article/merino":
        page = '<html><head><meta name="citation_title" content="x"><meta content="/pdf/merino.pdf?download=1&amp;v=2" name="citation_pdf_url"></head></html>'
        return httpx.Response(200, text=page, headers={"set-cookie": "session=abc; Path=/"})
    if url.path == "/pdf/merino.pdf":
        ok = request.headers.get("cookie") == "session=abc" and request.headers.get("referer") == "https://editeur.example/article/merino"
        assert url.params["download"] == "1" and url.params["v"] == "2"
        return httpx.Response(200, content=MERINO_PDF) if ok else httpx.Response(403)
    return httpx.Response(404)


def test_pdf_declared_by_publisher(conn, cfg, monkeypatch, sorbonne):
    def sources(request):
        if request.url.host == "europepmc.org":
            return httpx.Response(403)
        if request.url.host in ("doi.org", "editeur.example"):
            return publisher_site(request)
        return fake_sources(request)

    monkeypatch.setattr(http, "transport", httpx.MockTransport(sources))
    result = importer.add(conn, cfg.home, MERINO_DOI)
    assert result["pdf"]["source"] == "publisher" and result["warnings"] == []
    assert result["pdf"]["pdf_url"] == "https://editeur.example/pdf/merino.pdf?download=1&v=2"
    assert library.files_of(conn, result["work"]["id"])[0]["source_url"] == result["pdf"]["pdf_url"]


def test_publisher_page_without_pdf_link(conn, cfg, monkeypatch, sorbonne):
    sorbonne["publisher"] = lambda request: httpx.Response(200, text="<html><head><title>Article</title></head></html>")
    monkeypatch.setattr(http, "transport", httpx.MockTransport(
        lambda r: httpx.Response(403) if r.url.host == "europepmc.org" else fake_sources(r)))  # fmt: skip
    result = importer.add(conn, cfg.home, MERINO_DOI)
    assert result["pdf"] is None and "publisher : doi.org : la page ne déclare pas de PDF" in result["warnings"][0]


def test_meta_contents_variants():
    page = """<meta name="citation_pdf_url" content="a.pdf"><META CONTENT='b.pdf' PROPERTY='citation_pdf_url'>
              <meta name="other" content="c.pdf"><meta content="d&amp;e" name="citation_pdf_url" />"""
    assert http.meta_contents(page, "citation_pdf_url") == ["a.pdf", "b.pdf", "d&e"]


def test_watch_downloads(conn, cfg, tmp_path, network):
    import os
    import time

    downloads = tmp_path / "Téléchargements"
    downloads.mkdir()
    old = downloads / "ancien.pdf"
    old.write_bytes(MERINO_PDF)
    assert importer.scan_downloads(conn, downloads, cfg.home, min_age=0) == []  # l'historique n'est pas repris
    since = time.time() - 100  # surveillance activée il y a 100 s ; fichiers arrivés depuis
    db.set_meta(conn, "downloads.since", str(since))
    os.utime(old, (since - 10, since - 10))
    later = since + 10
    for name, data in [("merino (1).pdf", MERINO_PDF), ("facture.pdf", make_pdf(["Facture n° 42 — 120 euros"])),
                       ("casse.pdf", b"%PDF-1.4 pas lisible"), ("photo.jpg", b"jpeg")]:  # fmt: skip
        (downloads / name).write_bytes(data)
        os.utime(downloads / name, (later, later))
    results = {r["file"]: r["status"] for r in importer.scan_downloads(conn, downloads, cfg.home, min_age=0)}
    assert results == {"merino (1).pdf": "attached", "facture.pdf": "ignored", "casse.pdf": "refused"}
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1  # la facture n'est pas gardée
    assert sorted(p.name for p in downloads.iterdir()) == ["ancien.pdf", "casse.pdf", "facture.pdf", "merino (1).pdf", "photo.jpg"]
    calls = len(network)
    assert importer.scan_downloads(conn, downloads, cfg.home, min_age=0) == []  # déjà examinés
    assert len(network) == calls
    assert importer.scan_downloads(conn, tmp_path / "absent", cfg.home) == []


def test_reference_list_and_stopwords():
    pages = ["Introduction text.", "Methods.", "Results and discussion.\nReferences\n1. De Rekeneire N. et al. 2011."]
    assert library.without_references(pages) == ["Introduction text.", "Methods.", "Results and discussion.\n"]
    early = ["References\nnot really the end", "Body text continues here", "More body"]  # titre en première moitié : gardé
    assert library.without_references(early) == early
    assert library.fts_query("études sur la transmission de BLSE en foyer") == '"études" AND "transmission" AND "BLSE" AND "foyer"'
    assert library.fts_query("de la") == '"de" AND "la"'  # que des mots outils : on les garde


def test_outdated_index_is_rebuilt(conn, cfg):
    importer.import_pdf(conn, cfg.home, MERINO_PDF)
    conn.execute("DELETE FROM passages WHERE origin = 'fulltext'")
    db.set_meta(conn, "index.version", "1")
    core.setup(conn, cfg.home)
    assert conn.execute("SELECT count(*) FROM passages WHERE origin = 'fulltext'").fetchone()[0] > 0
    assert db.get_meta(conn, "index.version") == library.INDEX_VERSION


def test_downloads_cleanup_moves_only_stored_articles_to_trash(conn, cfg, tmp_path, network, monkeypatch):
    """Corbeille simulée : les tests ne touchent jamais à la vraie Corbeille."""
    import os
    import time

    import send2trash

    trashed = []
    monkeypatch.setattr(send2trash, "send2trash", lambda path: (trashed.append(Path(path).name), Path(path).unlink()))
    downloads = tmp_path / "Téléchargements"
    downloads.mkdir()
    db.set_meta(conn, "downloads.since", str(time.time() - 100))
    for name, data in [("merino.pdf", MERINO_PDF), ("merino (1).pdf", MERINO_PDF), ("facture.pdf", make_pdf(["Facture n° 42"]))]:
        (downloads / name).write_bytes(data)
        os.utime(downloads / name, (time.time() - 50, time.time() - 50))
    results = {r["file"]: (r["status"], r.get("trashed")) for r in importer.scan_downloads(conn, downloads, cfg.home, min_age=0, cleanup=True)}
    assert results == {"merino (1).pdf": ("attached", True), "merino.pdf": ("duplicate", True), "facture.pdf": ("ignored", None)}
    assert sorted(trashed) == ["merino (1).pdf", "merino.pdf"]
    assert [p.name for p in downloads.iterdir()] == ["facture.pdf"]  # une facture n'est jamais touchée
    assert len(list((cfg.home / "library").rglob("*.pdf"))) == 1  # la copie est bien rangée dans Biblio


def test_delete_works_sends_note_and_unshared_pdf_to_trash(conn, cfg, monkeypatch):
    import send2trash

    trashed = []
    monkeypatch.setattr(send2trash, "send2trash", lambda path: (trashed.append(Path(path).suffix), Path(path).unlink()))
    a, _ = core.add_manual(conn, {"title": "Notice à supprimer", "year": 2020}, "cli")
    b, _ = core.add_manual(conn, {"title": "Notice qui partage le PDF", "year": 2021}, "cli")
    c, _ = core.add_manual(conn, {"title": "Notice gardée", "year": 2022}, "cli")
    sha, _ = library.store_pdf(conn, cfg.home, MERINO_PDF, "merino.pdf")
    own, _ = library.store_pdf(conn, cfg.home, make_pdf("Autre article"), "autre.pdf")
    for work, file in ((a, sha), (b, sha), (a, own)):
        library.attach(conn, cfg.home, work["id"], file)
    notes.write(conn, cfg.home, a["id"], "Ma note")

    assert core.delete_works(conn, cfg.home, [a["citekey"], a["id"]], "ui") == [a["citekey"]]
    assert sorted(trashed) == [".md", ".pdf"]  # la note, et le PDF que personne d'autre n'utilise
    assert library.pdf_path(cfg.home, sha).is_file() and not library.pdf_path(cfg.home, own).exists()
    assert [w["id"] for w in core.list_works(conn)["items"]] == [c["id"], b["id"]]
    assert conn.execute("SELECT op FROM changes WHERE op = 'delete'").fetchone()  # trace gardée au journal
    with pytest.raises(core.NotFound):
        core.get_work(conn, a["id"])
