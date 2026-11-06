"""Aucun test n'accède au réseau : les sources reçoivent des réponses enregistrées (tests/fixtures)."""

import io
import textwrap
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest
from fastapi.testclient import TestClient

from biblio import config, core, db
from biblio.api import create_app
from biblio.sources import http

FIXTURES = Path(__file__).parent / "fixtures"
MERINO_DOI = "10.1001/jama.2018.12163"
MERINO_PMID = "30208454"
EMPTY_ESEARCH = {"esearchresult": {"count": "0", "idlist": []}}

# Site de Sorbonne Université simulé : page HTML et fichiers .xlsx, réglables par chaque test.
SORBONNE_PAGE = "/recherche/liste-des-revues-presumees-non-predatrices"
SORBONNE: dict = {}
LIST_HEADER = [
    "Nom complet", "Nom abrévié", "ISSN Print/Link", "ISSN Electronic", "NLMID", "Année de début",
    "Année de fin de publication", "Nouveau Nom", "Date Retrait de la liste",
    "Motif Retrait\n1. Nouveau nom\n2. N'est plus recommandée\n3. Arrêt publi",
]  # fmt: skip


def make_xlsx(rows: list[list[str]]) -> bytes:
    """Fichier .xlsx minimal (cellules en texte direct), comme en produit Excel."""
    body = "".join(
        f'<row r="{i}">'
        + "".join(
            f'<c r="{chr(65 + j)}{i}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for j, v in enumerate(row) if v
        )
        + "</row>"
        for i, row in enumerate(rows, 1)
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>',
        )
    return buf.getvalue()


def small_list(*extra: list[str], count: int = 120) -> bytes:
    """Liste de `count` revues fictives (ISSN 1100-0000, 1100-0001…), plus les lignes `extra`."""
    fake = [[f"Journal {i}", f"J {i}", f"1100-{i:04d}", "", f"9{i:06d}", "2000"] for i in range(count)]
    return make_xlsx([LIST_HEADER, *fake, *extra])


def make_pdf(pages: list[str], title: str | None = None, xmp_doi: str | None = None) -> bytes:
    """PDF minimal (Helvetica, texte réel) avec titre et DOI XMP facultatifs, pour tester l'import sans vrais PDF."""

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objects = ["<< /Type /Catalog /Pages 2 0 R" + (" /Metadata 4 0 R" if xmp_doi else "") + " >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]  # fmt: skip
    xmp = (f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           f'<rdf:Description xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"><prism:doi>{xmp_doi}</prism:doi>'
           f"</rdf:Description></rdf:RDF></x:xmpmeta>")  # fmt: skip
    objects.append(f"<< /Type /Metadata /Subtype /XML /Length {len(xmp)} >>\nstream\n{xmp}\nendstream")
    kids = []
    for text in pages:
        lines = [chunk for para in text.split("\n") for chunk in (textwrap.wrap(para, 90) or [""])]
        stream = "BT /F1 10 Tf 12 TL 50 780 Td " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
        objects.append(f"<< /Length {len(stream.encode('latin-1', 'replace'))} >>\nstream\n{stream}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 3 0 R >> >>"
                       f" /Contents {len(objects)} 0 R >>")  # fmt: skip
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    objects.append(f"<< /Title ({esc(title)}) >>" if title else "<< >>")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1", "replace")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


MERINO_TITLE = ("Effect of Piperacillin-Tazobactam vs Meropenem on 30-Day Mortality for Patients With E coli or "
                "Klebsiella pneumoniae Bloodstream Infection and Ceftriaxone Resistance: A Randomized Clinical Trial")  # fmt: skip
MERINO_PDF = make_pdf(
    [f"JAMA | Original Investigation\n{MERINO_TITLE}\nPatrick N. A. Harris, MBBS; Paul A. Tambyah, MBBS\n"
     "doi:10.1001/jama.2018.12163\nIMPORTANCE Extended-spectrum beta-lactamases mediate resistance to third-generation "
     "cephalosporins in Escherichia coli and Klebsiella pneumoniae.",
     "RESULTS Mortality at 30 days occurred in 23 of 187 patients randomized to piperacillin-tazobactam compared with "
     "7 of 191 patients randomized to meropenem.\nReferences: see also doi:10.1056/NEJMoa1411587"],
)  # fmt: skip


def sorbonne_page(*links: str) -> str:
    return "<html><body>" + "".join(f'<a href="{link}">liste</a>' for link in links) + "</body></html>"


@pytest.fixture(autouse=True)
def sorbonne():
    SORBONNE.clear()
    SORBONNE.update(page=None, files={}, s2_status=200)
    return SORBONNE


SEARCH = FIXTURES / "search"
SEARCH_PMIDS = "31822301,29550142,33421572,27927087,27596534"


def _search_sources(request: httpx.Request) -> httpx.Response | None:
    """Recherche « BLSE et transmission intrafamiliale » enregistrée auprès des 5 sources (tests/fixtures/search)."""
    url = request.url
    if url.path.endswith("esearch.fcgi") and "household" in url.params["term"]:
        return httpx.Response(200, content=(SEARCH / "pubmed_esearch.json").read_bytes())
    if url.path.endswith("efetch.fcgi") and url.params["id"] == SEARCH_PMIDS:
        return httpx.Response(200, content=(SEARCH / "pubmed_efetch.xml").read_bytes())
    if url.host == "api.crossref.org" and url.path == "/works":
        return httpx.Response(200, content=(SEARCH / "crossref.json").read_bytes())
    if url.host == "www.ebi.ac.uk":
        return httpx.Response(200, content=(SEARCH / "europepmc.json").read_bytes())
    if url.host == "europepmc.org" and url.path == "/articles/PMC6143100":
        return httpx.Response(200, content=MERINO_PDF, headers={"content-type": "application/pdf"})
    if url.host == "api.openalex.org":
        if url.path != "/works":  # notice unique (/works/doi:…) : inconnue d'OpenAlex dans ces tests
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, content=(SEARCH / "openalex.json").read_bytes())
    if url.host == "api.semanticscholar.org":
        if SORBONNE["s2_status"] == 429:
            return httpx.Response(429, content=(SEARCH / "s2_429.json").read_bytes(), headers={"retry-after": "0"})
        return httpx.Response(200, content=(SEARCH / "s2.json").read_bytes())
    if url.host == "api.unpaywall.org":
        if url.path.endswith("10.1016/j.cmi.2016.08.021"):
            return httpx.Response(200, content=(SEARCH / "unpaywall_haverkate.json").read_bytes())
        return httpx.Response(404, json={"error": True})
    return None


ARXIV_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2101.01234v2</id><published>2021-01-05T10:00:00Z</published>
<title>A  stochastic model of
 household transmission of resistant bacteria</title>
<summary>We model the household transmission of ESBL-producing bacteria.</summary>
<author><name>Ana García López</name></author><author><name>John Smith</name></author>
</entry></feed>"""
ARTICLE_PAGE = '<html><head><meta name="citation_title" content="MERINO"><meta name="citation_doi" content="10.1001/jama.2018.12163"></head></html>'


def _other_sources(request: httpx.Request) -> httpx.Response | None:
    url = request.url
    if url.path.endswith("esearch.fcgi") and url.params["term"] == "PMC6143100[pmcid]":
        return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": [MERINO_PMID]}})
    if url.host == "export.arxiv.org":
        if url.params["id_list"] == "2101.01234":
            return httpx.Response(200, text=ARXIV_ATOM)
        return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    if url.host == "journal.example.org":
        return httpx.Response(200, text=ARTICLE_PAGE) if url.path == "/merino" else httpx.Response(404)
    if url.host == "doi.org":  # page d'éditeur : absente sauf si un test en fournit une (SORBONNE["publisher"])
        return SORBONNE["publisher"](request) if SORBONNE.get("publisher") else httpx.Response(404)
    return None


def fake_sources(request: httpx.Request) -> httpx.Response:
    url = request.url
    if (response := _search_sources(request) or _other_sources(request)) is not None:
        return response
    if url.host == "sante.sorbonne-universite.fr":
        if url.path == SORBONNE_PAGE and SORBONNE["page"] is not None:
            return httpx.Response(200, text=SORBONNE["page"])
        if str(url) in SORBONNE["files"]:
            return httpx.Response(200, content=SORBONNE["files"][str(url)])
        return httpx.Response(404, text="Not found")
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
    monkeypatch.setattr(http, "MIN_INTERVAL", {})  # pas d'attente entre requêtes simulées
    monkeypatch.setattr(http, "_paused_until", {})
    monkeypatch.setattr(http, "contact_email", "")
    monkeypatch.setattr(http, "api_keys", {})
    monkeypatch.setattr(http, "cache_path", None)
    return calls


@pytest.fixture
def cfg(tmp_path, monkeypatch) -> config.Config:
    monkeypatch.setenv("BIBLIO_HOME", str(tmp_path))
    return config.load()


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg.db_path)
    core.setup(c, cfg.home)
    http.cache_path = cfg.db_path  # remis à None par la fixture `network` au test suivant
    yield c
    c.close()


@pytest.fixture
def api(cfg) -> TestClient:
    return TestClient(create_app(cfg), headers={"Authorization": f"Bearer {cfg.token}"})
