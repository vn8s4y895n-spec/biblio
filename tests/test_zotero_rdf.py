"""Import d'un export Zotero RDF (fichier écrit d'après le traducteur « Zotero RDF.js » de Zotero)."""

import shutil
from pathlib import Path

import pytest
from conftest import MERINO_DOI, MERINO_PDF

EXPORT = Path(__file__).parent / "fixtures" / "zotero" / "Export.rdf"


@pytest.fixture
def export(tmp_path):
    """Dossier tel que Zotero l'écrit : Export.rdf et files/<id>/<nom>.pdf (le second PDF manque)."""
    folder = tmp_path / "Export"
    (folder / "files" / "23").mkdir(parents=True)
    shutil.copy(EXPORT, folder / "Export.rdf")
    (folder / "files" / "23" / "Harris et al. - 2018.pdf").write_bytes(MERINO_PDF)
    return folder / "Export.rdf"


def test_import_zotero_rdf(api, export):
    out = api.post("/api/v1/import/zotero-rdf", json={"path": str(export)}).json()
    assert out["read"] == 2 and len(out["created"]) == 2 and out["pdfs"] == 1 and out["missing_pdfs"] == 1
    assert out["notes"] == 1 and out["collections"] == 2 and out["skipped"] == 1  # la note isolée
    work = api.get(f"/api/v1/works/{MERINO_DOI}").json()
    assert work["identifiers"]["pmid"] == "30208454" and work["container_title"] == "JAMA" and work["volume"] == "320"
    assert work["issue"] == "10" and work["pages"] == "984-994" and work["year"] == 2018 and work["issn_print"] == "0098-7484"
    assert [a["family"] for a in work["authors"]] == ["Harris", "Tambyah"]
    assert sorted(lb["name"] for lb in work["labels"]) == ["BLSE", "BLSE"]  # le tag et la collection
    assert {lb["kind"] for lb in work["labels"]} == {"tag", "collection"}  # pas le tag automatique
    note = api.get(f"/api/v1/works/{MERINO_DOI}/note").json()["text"]
    assert note == "Essai MERINO : non-infériorité non démontrée.\n\nÀ relire."
    assert len(api.get(f"/api/v1/works/{MERINO_DOI}/files").json()) == 1
    labels = {lb["name"]: lb["count"] for lb in api.get("/api/v1/labels").json() if lb["kind"] == "collection"}
    assert labels == {"BLSE": 1, "BLSE / Foyers": 1}
    other = api.get("/api/v1/works", params={"limit": 5}).json()["items"]
    assert any(w["title"].startswith("Quantifying") and w["year"] == 2017 for w in other)

    again = api.post("/api/v1/import/zotero-rdf", json={"path": str(export)}).json()  # réimport : rien en double
    assert again["created"] == [] and len(again["completed"]) == 2 and again["pdfs"] == 0 and again["notes"] == 0


def test_rdf_sent_as_file_is_read_without_pdfs(api):
    out = api.post("/api/v1/import", content=EXPORT.read_bytes(), headers={"Content-Type": "text/plain"}).json()
    assert out["format"] == "zotero-rdf" and len(out["created"]) == 2 and out["pdfs"] == 0 and out["missing_pdfs"] == 2


def test_bad_paths(api, tmp_path):
    assert api.post("/api/v1/import/zotero-rdf", json={"path": str(tmp_path / "x.txt")}).status_code == 422
    broken = tmp_path / "broken.rdf"
    broken.write_text("<rdf:RDF")
    assert api.post("/api/v1/import/zotero-rdf", json={"path": str(broken)}).status_code == 422


def test_pdf_outside_the_export_is_refused(api, tmp_path):
    folder = tmp_path / "Export"
    folder.mkdir()
    (tmp_path / "secret.pdf").write_bytes(MERINO_PDF)
    (folder / "Export.rdf").write_text(EXPORT.read_text().replace("files/23/Harris%20et%20al.%20-%202018.pdf", "../secret.pdf"))
    out = api.post("/api/v1/import/zotero-rdf", json={"path": str(folder / "Export.rdf")}).json()
    assert out["pdfs"] == 0 and out["missing_pdfs"] == 2
