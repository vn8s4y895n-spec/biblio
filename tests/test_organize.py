import json
import tomllib
from pathlib import Path

from conftest import MERINO_DOI, MERINO_PMID

from biblio import ask, config, core

PM_QUERY = '(ESBL[tiab] OR "extended-spectrum beta-lactamase"[tiab]) AND household[tiab] AND transmission[tiab]'
UI = Path(__file__).parent.parent / "ui"


def test_settings_roundtrip(api, cfg):
    before = api.get("/api/v1/settings").json()
    assert before["contact_email"] == "" and before["theme"] == "system" and "token" not in before
    out = api.put("/api/v1/settings", json={"contact_email": " moi@exemple.org ", "openalex_api_key": "k1",
                                            "watch_downloads": True, "language": "en"}).json()  # fmt: skip
    assert out["contact_email"] == "moi@exemple.org" and out["openalex_api_key"] == "k1" and not out["restart_needed"]
    saved = tomllib.loads(cfg.path.read_text())
    assert saved["sources"]["openalex_api_key"] == "k1" and saved["inbox"]["watch_downloads"] is True
    assert saved["server"]["token"] == cfg.token and saved["ui"]["language"] == "en"
    assert cfg.path.stat().st_mode & 0o777 == 0o600
    assert config.load().api_keys["openalex"] == "k1"
    # Le serveur utilise les nouveaux réglages sans redémarrer.
    from biblio.sources import http

    assert http.contact_email == "moi@exemple.org"
    assert api.put("/api/v1/settings", json={"semantic": False}).json()["restart_needed"]
    assert api.put("/api/v1/settings", json={"contact_email": "pas-une-adresse"}).status_code == 422
    assert api.put("/api/v1/settings", json={"theme": "rose"}).status_code == 422


def test_config_escapes_values(cfg):
    from dataclasses import replace

    config.save(replace(cfg, contact_email='a"b\\c@x.org'))
    assert config.load().contact_email == 'a"b\\c@x.org'


def test_labels_and_filters(api):
    api.post("/api/v1/works", json={"identifier": MERINO_DOI, "pdf": False})
    coll = api.post("/api/v1/labels", json={"kind": "collection", "name": "  Thèse  "}).json()
    assert coll["name"] == "Thèse"
    assert api.post("/api/v1/labels", json={"kind": "collection", "name": "thèse"}).json()["id"] == coll["id"]
    tag = api.post("/api/v1/labels", json={"kind": "tag", "name": "BLSE"}).json()
    assert api.post(f"/api/v1/labels/{coll['id']}/works", json={"works": [MERINO_PMID]}).json() == {"changed": 1}
    assert api.post(f"/api/v1/labels/{coll['id']}/works", json={"works": [MERINO_PMID]}).json() == {"changed": 0}
    api.post(f"/api/v1/labels/{tag['id']}/works", json={"works": ["harris2018effect"]})
    work = api.get("/api/v1/works/harris2018effect").json()
    assert [label["name"] for label in work["labels"]] == ["Thèse", "BLSE"]
    assert {r["name"]: r["count"] for r in api.get("/api/v1/labels").json()} == {"Thèse": 1, "BLSE": 1}
    assert api.get("/api/v1/works", params={"label": coll["id"]}).json()["total"] == 1
    assert api.get("/api/v1/works", params={"read_status": "unread"}).json()["total"] == 1

    patched = api.patch(f"/api/v1/works/{MERINO_DOI}", json={"read_status": "read", "rating": 4}).json()
    assert (patched["read_status"], patched["rating"]) == ("read", 4)
    assert api.patch("/api/v1/works/harris2018effect", json={"rating": None}).json()["rating"] is None
    assert api.patch("/api/v1/works/harris2018effect", json={"rating": 9}).status_code == 422
    item = api.get("/api/v1/works", params={"read_status": "read"}).json()["items"][0]
    assert item["read_status"] == "read" and item["has_pdf"] is False
    fields = [c["field"] for c in api.get("/api/v1/works/harris2018effect/changes").json()]
    assert {"collection", "tag", "read_status", "rating"} <= set(fields)

    assert api.post(f"/api/v1/labels/{tag['id']}/works", json={"works": ["harris2018effect"], "remove": True}).json()["changed"] == 1
    assert api.patch(f"/api/v1/labels/{tag['id']}", json={"name": "thèse"}).status_code == 200  # autre type : permis
    assert api.patch(f"/api/v1/labels/{tag['id']}", json={"name": "  "}).status_code == 422
    assert api.delete(f"/api/v1/labels/{coll['id']}").status_code == 204
    assert api.get("/api/v1/works/harris2018effect").json()["labels"] == []
    assert api.delete("/api/v1/labels/999").status_code == 404


def test_merge_keeps_labels(conn, cfg):
    from biblio import organize

    a, _ = core.add_manual(conn, {"title": "Une étude sur la transmission des BLSE en famille", "year": 2020}, "cli")
    b, _ = core.add_manual(conn, {"title": "Transmission des BLSE en famille (version préprint)", "year": 2020}, "cli")
    tag = organize.create_label(conn, "tag", "à lire")
    organize.set_label(conn, tag["id"], [b["id"]], True, "cli")
    merged = core.merge_works(conn, cfg.home, a["id"], b["id"], "cli")
    assert [label["name"] for label in merged["labels"]] == ["à lire"]


def test_saved_searches(api):
    body = {"question": "Transmission des BLSE ?", "query": "ESBL AND household", "answer": [{"text": "x", "source": 1}],
            "sources": [{"title": "A"}, {"title": "B"}]}  # fmt: skip
    saved = api.post("/api/v1/searches", json=body).json()
    assert saved["answer"] == body["answer"] and saved["origin"] == "biblio"
    assert api.get("/api/v1/searches").json()[0] | {"created_at": None} == {
        "id": saved["id"], "question": "Transmission des BLSE ?", "origin": "biblio", "scope": "online",
        "created_at": None, "count": 2, "watch_days": None, "last_run": None}  # fmt: skip
    assert api.get(f"/api/v1/searches/{saved['id']}").json()["sources"][1]["title"] == "B"
    assert api.delete(f"/api/v1/searches/{saved['id']}").status_code == 204
    assert api.get(f"/api/v1/searches/{saved['id']}").status_code == 404


def test_summary_and_sample_size():
    structured = ("IMPORTANCE: Les BLSE progressent.\nRESULTS: Among 391 patients randomized, mortality was 12.3% "
                  "with piperacillin.\nCONCLUSIONS AND RELEVANCE: Piperacillin-tazobactam did not result in a "
                  "noninferior 30-day mortality.")  # fmt: skip
    assert ask.summary(structured).startswith("Piperacillin-tazobactam did not")
    assert ask.sample_size(structured) == 391
    assert ask.sample_size("We enrolled n = 1,250 adult participants in 2019.") == 1250
    plain = "First sentence is about the context here. Second sentence gives the main result clearly. Third concludes the whole study well."
    assert ask.summary(plain) == "Second sentence gives the main result clearly. Third concludes the whole study well."
    assert ask.summary(None) is None and ask.sample_size("no number here") is None
    glued = "Carriage is common in the community.IMPORTANCE Few data exist.CONCLUSIONS Households amplify ESBL spread widely."
    assert ask.summary(glued) == "Households amplify ESBL spread widely."
    assert ask.sample_size("In 4 households, 37 individuals were sampled.") == 37


def test_ask_online_composes_cited_answer(api):
    out = api.post("/api/v1/ask", json={"question": PM_QUERY, "all_journals": True}).json()
    assert out["scope"] == "online" and out["sources"] and out["answer"]
    assert "ESBL" in out["query"]
    for sentence in out["answer"]:
        source = out["sources"][sentence["source"] - 1]
        assert sentence["text"] in source["abstract"]
    assert len({s["source"] for s in out["answer"]}) == len(out["answer"])  # une phrase par source au plus
    assert any(s["summary"] for s in out["sources"])
    filtered = api.post("/api/v1/ask", json={"question": PM_QUERY}).json()
    assert {s["journal_status"]["status"] for s in filtered["sources"]} == {"recommended"} and filtered["hidden"]


def test_ask_library(api):
    api.post("/api/v1/works", json={"identifier": MERINO_DOI, "pdf": False})
    out = api.post("/api/v1/ask", json={"question": "piperacillin mortality", "scope": "library"}).json()
    assert out["sources"][0]["citekey"] == "harris2018effect" and out["sources"][0]["in_library"] == "harris2018effect"
    assert out["answer"][0]["source"] == 1 and "<mark>" not in out["answer"][0]["text"]


def test_locales_have_same_keys():
    def keys(d, prefix=""):
        return {k2 for k, v in d.items() for k2 in (keys(v, f"{prefix}{k}.") if isinstance(v, dict) else {prefix + k})}

    fr, en = (json.loads((UI / "src" / "locales" / f"{lang}.json").read_text()) for lang in ("fr", "en"))
    assert keys(fr) == keys(en), keys(fr) ^ keys(en)
    used = set()
    import re

    for f in (UI / "src").rglob("*.svelte"):
        used |= set(re.findall(r"""\$t\(\s*['"]([\w.]+)['"]""", f.read_text()))
    assert used <= keys(fr), used - keys(fr)


def test_web_ui_is_served_without_token(api):
    from importlib.resources import files

    import pytest

    if not (files("biblio") / "web" / "index.html").is_file():
        pytest.skip("interface non compilée (npm --prefix ui run build)")
    page = api.get("/", headers={"Authorization": ""})
    assert page.status_code == 200 and "<div id=\"app\">" in page.text
    assert page.headers["cache-control"] == "no-cache"  # une mise à jour de l'interface est vue tout de suite
    assert api.get("/api/v1/works", headers={"Authorization": ""}).status_code == 401  # les données restent protégées


def test_question_to_keywords():
    from biblio import question

    q = "Chez un E.faecium résistant à la vancomycine, est il possible d’utiliser du Ceftobiprole et Ceftaroline ? Et la dalbavancine ?"
    assert question.keywords(q) == ['"Enterococcus faecium"', "resistant", "vancomycin", "Ceftobiprole", "Ceftaroline", "dalbavancin"]
    assert question.keywords("transmission intrafamiliale des E. coli BLSE") == ["transmission", "household", '"Escherichia coli"', "ESBL"]
    assert question.keywords("K. pneumoniae et méropénème")[0:2] == ['"Klebsiella pneumoniae"', "meropenem"]
    assert question.is_syntax('ESBL[tiab] AND household') and not question.is_syntax("ESBL household ?")
    assert question.relaxed(["a", "b", "c"]) == "(a AND b) OR (a AND c) OR (b AND c)"
    rec = {"title": "Ceftaroline et vancomycine chez Enterococcus faecium", "abstract": None}
    assert question.coverage(['"Enterococcus faecium"', "vancomycin", "ceftaroline", "dalbavancin"], rec) == 0.75


def test_ask_sends_keywords_and_drops_off_topic(api, network):
    from biblio import question

    out = api.post("/api/v1/ask", json={"question": "Quelle transmission des BLSE dans le foyer ?", "all_journals": True}).json()
    terms = ["transmission", "ESBL", "household"]
    pubmed = next(r for r in network if "esearch" in str(r.url))
    assert pubmed.url.params["term"] == out["query"] == "transmission ESBL household"
    assert out["sources"] and all(question.coverage(terms, s) >= 0.5 for s in out["sources"])
    assert out["off_topic"] > 0  # les fixtures contiennent des notices sans rapport (thèses, posters)


def test_question_kept_with_added_work(api, conn, cfg):
    q = "Pipéracilline-tazobactam contre méropénème dans les bactériémies à BLSE ?"
    first = api.post("/api/v1/works", json={"identifier": MERINO_DOI, "pdf": False, "question": q}).json()
    assert [x["question"] for x in first["work"]["questions"]] == [q]
    again = api.post("/api/v1/works", json={"identifier": MERINO_PMID, "pdf": False, "question": "  MERINO   trial "}).json()
    assert {x["question"] for x in again["work"]["questions"]} == {q, "MERINO trial"}
    api.post("/api/v1/works", json={"identifier": MERINO_PMID, "pdf": False, "question": q})  # pas de doublon
    assert len(api.get("/api/v1/works/harris2018effect").json()["questions"]) == 2
    manual = api.post("/api/v1/works/manual", json={"title": "Une étude sans identifiant", "question": q}).json()
    assert manual["work"]["questions"][0]["question"] == q
    merged = core.merge_works(conn, cfg.home, "harris2018effect", manual["work"]["id"], "cli")
    assert len(merged["questions"]) == 2  # même question : gardée une fois
