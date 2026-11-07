import io
import json
import zipfile
from importlib.resources import files

import httpx
import pytest
from conftest import MERINO_DOI, MERINO_PDF, MERINO_PMID
from fastapi.testclient import TestClient

from biblio import config, core, exports, formats, zotero
from biblio.api import create_app


@pytest.fixture
def merino(api):
    work = api.post("/api/v1/works", json={"identifier": MERINO_DOI, "pdf": False, "question": "MERINO ?"}).json()["work"]
    return work


def test_citations(api, merino):
    vancouver = api.get("/api/v1/works/harris2018effect/citation").json()
    assert vancouver["style"] == exports.DEFAULT_STYLE
    assert vancouver["text"].startswith("Harris PNA, Tambyah PA, Lye DC")
    assert "JAMA. 2018;320(10):984–94." in vancouver["text"] and "DOI: 10.1001/jama.2018.12163" in vancouver["text"]
    apa = api.get("/api/v1/works/harris2018effect/citation", params={"style": "apa"}).json()["text"]
    assert apa.startswith("Harris, P. N. A., Tambyah, P. A.") and "(2018)" in apa
    assert api.get("/api/v1/works/harris2018effect/citation", params={"style": "inconnu"}).status_code == 422
    assert {s["id"] for s in api.get("/api/v1/styles").json()} == {"apa", exports.DEFAULT_STYLE}


@pytest.mark.parametrize("fmt", ["bibtex", "ris", "csljson"])
def test_export_roundtrip(api, merino, fmt):
    out = api.post("/api/v1/export", json={"works": ["harris2018effect"], "format": fmt})
    assert out.status_code == 200 and "attachment" in out.headers["content-disposition"]
    rec = formats.parse(out.text, fmt)[0]
    work = api.get("/api/v1/works/harris2018effect").json()
    assert rec["title"] == work["title"] and rec["year"] == 2018 and rec["volume"] == "320"
    assert rec["identifiers"]["doi"] == MERINO_DOI and rec["identifiers"]["pmid"] == MERINO_PMID
    assert [a["family"] for a in rec["authors"]] == [a["family"] for a in work["authors"]]
    assert rec["pages"] == "984-994" and rec["container_title"] == work["container_title"]


def test_export_markdown_and_selection(api, merino):
    api.post("/api/v1/works/manual", json={"title": "Un rapport sans identifiant & 100 % local", "authors": ["Dupont, Jean"], "year": 2024})
    md = api.post("/api/v1/export", json={"format": "markdown"}).text
    assert md.startswith("1. ") and "[[harris2018effect]]" in md and "(https://doi.org/10.1001/jama.2018.12163)" in md
    assert md.count("\n") == 2
    bib = api.post("/api/v1/export", json={"format": "bibtex"}).text
    assert r"\& 100 \% local" in bib and "@article{harris2018effect," in bib
    tag = api.post("/api/v1/labels", json={"kind": "tag", "name": "essai"}).json()
    api.post(f"/api/v1/labels/{tag['id']}/works", json={"works": ["harris2018effect"]})
    assert api.post("/api/v1/export", json={"format": "ris", "label": tag["id"]}).text.count("ER  - ") == 1


def test_add_style(api, merino, cfg):
    apa = (files("biblio") / "data" / "styles" / "apa.csl").read_bytes()
    added = api.post("/api/v1/styles", content=apa.replace(b"<title>APA Style 7th edition", b"<title>Mon APA"),
                     params={"filename": "Mon style.csl"}).json()  # fmt: skip
    assert added == {"id": "mon-style", "title": "Mon APA", "default": False, "added": True}
    assert api.get("/api/v1/works/harris2018effect/citation", params={"style": "mon-style"}).json()["text"].startswith("Harris, P.")
    assert api.post("/api/v1/styles", content=b"<html>pas un style</html>").status_code == 422
    dependent = b'<style xmlns="http://purl.org/net/xbiblio/csl"><info><link href="x" rel="independent-parent"/></info></style><!-- citation-style-language -->'
    assert api.post("/api/v1/styles", content=dependent).status_code == 422
    assert api.put("/api/v1/settings", json={"citation_style": "mon-style"}).json()["citation_style"] == "mon-style"
    assert api.get("/api/v1/works/harris2018effect/citation").json()["style"] == "mon-style"
    assert api.put("/api/v1/settings", json={"citation_style": "rien"}).status_code == 422


def test_library_bib_follows_changes(conn, cfg):
    assert exports.sync_library_bib(conn, cfg.home)
    assert (cfg.home / "library.bib").read_text() == ""
    core.add_manual(conn, {"title": "Une première notice de test", "authors": ["Martin, Léa"], "year": 2021}, "cli")
    assert exports.sync_library_bib(conn, cfg.home)
    assert "martin2021premiere" in (cfg.home / "library.bib").read_text()
    assert not exports.sync_library_bib(conn, cfg.home)  # rien n'a changé


class FakeZotero:
    """Serveur local de Zotero simulé : il garde les requêtes reçues."""

    def __init__(self, running=True):
        self.running, self.calls = running, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.running:
            raise httpx.ConnectError("refusé")
        self.calls.append(request)
        if request.url.path == "/connector/getSelectedCollection":
            return httpx.Response(200, json={"libraryName": "Ma bibliothèque", "name": "Thèse", "editable": True})
        return httpx.Response(201, json={})


def test_send_to_zotero(api, merino, monkeypatch):
    fake = FakeZotero()
    monkeypatch.setattr(zotero, "transport", httpx.MockTransport(fake))
    assert api.get("/api/v1/zotero").json() == {"running": True, "library": "Ma bibliothèque", "collection": "Thèse", "editable": True}
    api.post("/api/v1/files", content=MERINO_PDF, params={"filename": "merino.pdf"})
    api.put("/api/v1/works/harris2018effect/note", json={"text": "Non-infériorité **non démontrée**.\n\n<b>Attention</b>"})
    tag = api.post("/api/v1/labels", json={"kind": "tag", "name": "BLSE"}).json()
    api.post(f"/api/v1/labels/{tag['id']}/works", json={"works": ["harris2018effect"]})
    out = api.post("/api/v1/zotero", json={"works": ["harris2018effect"]}).json()
    assert out == {"sent": 1, "pdfs": 1, "library": "Ma bibliothèque", "collection": "Thèse"}
    save = next(c for c in fake.calls if c.url.path == "/connector/saveItems")
    assert save.headers["X-Zotero-Connector-API-Version"] == "3"
    item = json.loads(save.content)["items"][0]
    assert item["itemType"] == "journalArticle" and item["DOI"] == MERINO_DOI and item["tags"] == [{"tag": "BLSE"}]
    assert item["creators"][0] == {"lastName": "Harris", "firstName": "Patrick N A", "creatorType": "author"}
    assert "PMID: 30208454" in item["extra"] and "&lt;b&gt;Attention&lt;/b&gt;" in item["notes"][0]["note"]
    attachment = next(c for c in fake.calls if c.url.path == "/connector/saveAttachment")
    meta = json.loads(attachment.headers["X-Metadata"])
    assert meta["parentItemID"] == item["id"] and meta["sessionID"] == json.loads(save.content)["sessionID"]
    assert attachment.content == MERINO_PDF

    fake.running = False
    assert api.get("/api/v1/zotero").json() == {"running": False}
    refused = api.post("/api/v1/zotero", json={"works": ["harris2018effect"]})
    assert refused.status_code == 503 and "Zotero n'est pas ouvert" in refused.json()["detail"]


def other_library(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("BIBLIO_HOME", str(tmp_path / "collegue"))
    cfg = config.load()
    return TestClient(create_app(cfg), headers={"Authorization": f"Bearer {cfg.token}"})


def test_package_between_two_libraries(api, merino, tmp_path, monkeypatch):
    api.post("/api/v1/files", content=MERINO_PDF, params={"filename": "merino.pdf"})
    api.put("/api/v1/works/harris2018effect/note", json={"text": "Essai MERINO : méropénème supérieur."})
    coll = api.post("/api/v1/labels", json={"kind": "collection", "name": "BLSE"}).json()
    api.post(f"/api/v1/labels/{coll['id']}/works", json={"works": ["harris2018effect"]})
    api.patch("/api/v1/works/harris2018effect", json={"read_status": "read", "rating": 5})
    search = api.post("/api/v1/searches", json={"question": "Traitement des bactériémies BLSE ?", "sources": [{"title": "A"}]}).json()
    package = api.post("/api/v1/packages", json={"label": coll["id"], "title": "Pour Marie", "searches": [search["id"]]})
    assert package.headers["content-type"] == "application/x-biblio"
    names = zipfile.ZipFile(io.BytesIO(package.content)).namelist()
    assert {"manifest.json", "works.json", "searches.json"} <= set(names) and any(n.startswith("files/") for n in names)

    colleague = other_library(tmp_path, monkeypatch)
    preview = colleague.post("/api/v1/packages/preview", content=package.content).json()
    assert preview["manifest"]["title"] == "Pour Marie" and preview["manifest"]["count"] == 1
    assert preview["works"][0] | {"labels": None} == {
        "title": merino["title"], "year": 2018, "container_title": merino["container_title"], "first_author": "Harris",
        "status": "new", "existing": None, "pdf": True, "note": True, "labels": None, "questions": ["MERINO ?"]}  # fmt: skip
    report = colleague.post("/api/v1/packages/import", content=package.content).json()
    assert report == {"created": ["harris2018effect"], "completed": [], "pdfs": 1, "notes": 1, "searches": 1, "errors": []}
    work = colleague.get("/api/v1/works/harris2018effect").json()
    assert work["identifiers"]["pmid"] == MERINO_PMID and work["abstract"] == merino["abstract"]
    assert [label["name"] for label in work["labels"]] == ["BLSE"] and work["questions"][0]["question"] == "MERINO ?"
    assert work["read_status"] is None and work["rating"] is None  # personnels : pas envoyés
    assert colleague.get("/api/v1/works/harris2018effect/note").json()["text"] == "Essai MERINO : méropénème supérieur."
    assert len(colleague.get("/api/v1/works/harris2018effect/files").json()) == 1
    assert colleague.get("/api/v1/searches").json()[0]["origin"] == "paquet"
    assert colleague.get("/api/v1/search/library", params={"q": "piperacillin"}).json()["total"] == 1  # texte intégral indexé

    # Second envoi avec une note complétée : pas de doublon, la note reçue s'ajoute à la suite.
    colleague.put("/api/v1/works/harris2018effect/note", json={"text": "Ma note à moi."})
    assert colleague.post("/api/v1/packages/preview", content=package.content).json()["works"][0]["status"] == "existing"
    again = colleague.post("/api/v1/packages/import", content=package.content).json()
    assert again["created"] == [] and again["completed"] == ["harris2018effect"] and again["pdfs"] == 0
    note = colleague.get("/api/v1/works/harris2018effect/note").json()["text"]
    assert note.startswith("Ma note à moi.") and "Note reçue dans un paquet .biblio" in note
    assert colleague.get("/api/v1/works").json()["total"] == 1


def test_bad_packages(api, merino):
    assert api.post("/api/v1/packages/import", content=b"pas un zip").status_code == 422
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": "biblio-package", "version": 99}))
        z.writestr("works.json", "[]")
    assert "plus récente" in api.post("/api/v1/packages/preview", content=buffer.getvalue()).json()["detail"]
    package = api.post("/api/v1/packages", json={"works": ["harris2018effect"]}).content
    assert api.post("/api/v1/packages", json={"works": []}).status_code == 200  # vide = toute la bibliothèque
    tampered = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package)) as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        works = json.loads(src.read("works.json"))
        dst.writestr(f"files/{'0' * 64}.pdf", MERINO_PDF)
    works[0]["files"] = ["0" * 64]
    rebuilt = io.BytesIO()
    with zipfile.ZipFile(tampered) as src, zipfile.ZipFile(rebuilt, "w") as dst:
        for name in src.namelist():
            if name != "works.json":
                dst.writestr(name, src.read(name))
        dst.writestr("works.json", json.dumps(works))
    report = api.post("/api/v1/packages/import", content=rebuilt.getvalue()).json()
    assert report["pdfs"] == 0 and "PDF altéré" in report["errors"][0]


def test_cite_online_record_and_search_only_package(api):
    rec = {"title": "Household transmission of ESBL", "authors": [{"family": "Riccio", "given": "Marco"}], "year": 2021,
           "container_title": "Clin Microbiol Infect", "identifiers": {"doi": "10.1016/x"}}  # fmt: skip
    assert api.post("/api/v1/citation", json={"record": rec}).json()["text"].startswith("Riccio M. Household transmission of ESBL.")
    search = api.post("/api/v1/searches", json={"question": "BLSE ?", "sources": [rec]}).json()
    data = api.post("/api/v1/packages", json={"searches": [search["id"]]}).content
    assert json.loads(zipfile.ZipFile(io.BytesIO(data)).read("works.json")) == []
