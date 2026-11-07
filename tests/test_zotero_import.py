"""Import depuis Zotero : API locale de Zotero simulée (formes de réponse tirées de son code, server_localAPI.js)."""

import json

import httpx
import pytest
from conftest import MERINO_DOI, MERINO_PDF

from biblio import zotero

CSL = {"id": "ABCD1234", "type": "article-journal", "title": "Effect of Piperacillin-Tazobactam vs Meropenem on 30-Day Mortality",
       "container-title": "JAMA", "DOI": MERINO_DOI, "issued": {"date-parts": [[2018, 9, 11]]},
       "author": [{"family": "Harris", "given": "Patrick N. A."}]}  # fmt: skip
CHICAGO = (f"Harris, Patrick N. A., et al. « Effect of Piperacillin-Tazobactam vs Meropenem ». JAMA 320, no 10 (2018) : 984. "
           f"https://doi.org/{MERINO_DOI}.\nDupont, Jean. « Un article sans identifiant ». Revue 1 (2020).")  # fmt: skip


class FakeLocalZotero:
    """Zotero ouvert ; `enabled` : API locale activée dans ses réglages."""

    def __init__(self, pdf_path, enabled=True):
        self.pdf_path, self.enabled, self.calls = pdf_path, enabled, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path, params = request.url.path, request.url.params
        if path == "/connector/getSelectedCollection":
            return httpx.Response(200, json={"libraryName": "Ma bibliothèque", "id": 34, "name": "BLSE"})
        if not self.enabled:
            return httpx.Response(403, text="Local API is not enabled")
        if path == "/api/users/0/collections":
            return httpx.Response(200, json=[
                {"key": "COLL0001", "meta": {"numItems": 1}, "data": {"name": "BLSE", "parentCollection": False}},
                {"key": "COLL0002", "meta": {"numItems": 0}, "data": {"name": "Enfants", "parentCollection": "COLL0001"}},
                {"key": "COLL0003", "meta": {"numItems": 4}, "data": {"name": "Anaérobies", "parentCollection": False}},
            ][int(params.get("start", 0)):])  # fmt: skip
        if path == "/api/users/0/collections/COLL0001/items/top":
            return httpx.Response(200, text="ABCD1234\nNOTE0001" if params.get("start", "0") == "0" else "")
        if path == "/api/users/0/items/top" and params.get("q") == MERINO_DOI:
            return httpx.Response(200, text="ABCD1234")
        if path == "/api/users/0/items/top":
            return httpx.Response(200, text="")
        if path == "/api/users/0/items" and params.get("itemKey") == "ABCD1234":
            return httpx.Response(200, text=json.dumps([CSL]))
        if path == "/api/users/0/items/ABCD1234":
            return httpx.Response(200, json={"key": "ABCD1234", "data": {"itemType": "journalArticle", "title": CSL["title"],
                                                                         "tags": [{"tag": "carbapénèmes"}, {"tag": "BLSE", "type": 1}]}})  # fmt: skip
        if path == "/api/users/0/items/NOTE0001":
            return httpx.Response(200, json={"key": "NOTE0001", "data": {"itemType": "note"}})
        if path == "/api/users/0/items/ABCD1234/children":
            return httpx.Response(200, json=[
                {"key": "PDF00001", "data": {"itemType": "attachment", "contentType": "application/pdf", "linkMode": "imported_file"}},
                {"key": "SNAP0001", "data": {"itemType": "attachment", "contentType": "text/html", "linkMode": "imported_url"}},
            ])  # fmt: skip
        if path == "/api/users/0/items/PDF00001/file/view/url":
            return httpx.Response(200, text=self.pdf_path.as_uri())
        return httpx.Response(404, text="Not found")


@pytest.fixture
def local(monkeypatch, tmp_path):
    pdf = tmp_path / "Zotero storage" / "Harris 2018.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(MERINO_PDF)
    fake = FakeLocalZotero(pdf)
    monkeypatch.setattr(zotero, "transport", httpx.MockTransport(fake))
    return fake


def test_dropped_items_imported_with_pdf_and_tags_into_a_collection(api, local):
    label = api.post("/api/v1/labels", json={"kind": "collection", "name": "Lectures"}).json()
    out = api.post("/api/v1/zotero/import", json={"text": CHICAGO, "label": label["id"]}).json()
    assert out["via"] == "zotero" and len(out["created"]) == 1 and out["pdfs"] == 1 and out["unmatched"] == 1
    work = api.get(f"/api/v1/works/{MERINO_DOI}").json()
    assert sorted(lb["name"] for lb in work["labels"]) == ["BLSE", "Lectures", "carbapénèmes"]
    assert len(api.get(f"/api/v1/works/{MERINO_DOI}/files").json()) == 1
    assert api.get(f"/api/v1/works/{MERINO_DOI}/changes").json()[0]["client"] == "zotero"
    again = api.post("/api/v1/zotero/import", json={"text": CHICAGO}).json()  # déjà là : complété, pas dupliqué
    assert again["created"] == [] and len(again["completed"]) == 1 and again["pdfs"] == 0


def test_dropped_collection_and_collection_list(api, local):
    assert [(c["name"], c["level"]) for c in api.get("/api/v1/zotero/collections").json()] == [
        ("Anaérobies", 0), ("BLSE", 0), ("Enfants", 1)]  # fmt: skip
    out = api.post("/api/v1/zotero/import", json={"collection": "selected"}).json()
    assert out["collection"] == "BLSE" and len(out["created"]) == 1 and out["skipped"] == 1  # la note est ignorée
    assert api.post("/api/v1/zotero/import", json={}).status_code == 422


def test_without_local_api(api, local, network):
    local.enabled = False
    response = api.get("/api/v1/zotero/collections")
    assert response.status_code == 503 and "Autoriser d'autres applications" in response.json()["detail"]
    out = api.post("/api/v1/zotero/import", json={"text": CHICAGO}).json()  # en ligne, par le DOI
    assert out["via"] == "online" and len(out["created"]) == 1 and out["unmatched"] == 1
    assert any(MERINO_DOI in str(r.url) for r in network)


def test_dropped_export_format(api, local):
    out = api.post("/api/v1/zotero/import", json={"text": json.dumps([CSL])}).json()
    assert out["via"] == "export" and out["created"] == ["harris2018effect"]


def test_identifiers_in_formatted_references():
    text = ("Ref. https://doi.org/10.1001/JAMA.2018.12163. Autre : PMID: 30208454; doi:10.1/x. "
            "(Lancet, https://doi.org/10.1016/S0140-6736(20)30183-5).")  # fmt: skip
    assert zotero.identifiers_in(text) == [("doi", "10.1001/jama.2018.12163"), ("doi", "10.1016/s0140-6736(20)30183-5"),
                                           ("pmid", "30208454")]  # fmt: skip
