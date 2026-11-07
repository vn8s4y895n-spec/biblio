import json
import tomllib

import httpx
import pytest

from biblio import ai, llm

PLAN = {"pubmed_query": '("Escherichia coli"[MeSH]) AND (ESBL[tiab]) AND (household[tiab] OR "family transmission"[tiab])',
        "related_questions": ["Facteurs de risque ?", "Prévalence du portage ?", "Prévention ?"]}  # fmt: skip
ANSWER = ("Le portage d'entérobactéries BLSE est fréquent dans les foyers des patients porteurs [1]. "
          "La lune est faite de fromage. "
          "Une source qui n'existe pas le confirme [9].")  # fmt: skip


class FakeLLM:
    """Serveur compatible OpenAI simulé (Ollama, Mistral…) ; garde les requêtes reçues."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail:
            return httpx.Response(500, json={"error": {"message": "modèle en panne"}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.5:9b"}, {"id": "autre"}]})
        body = json.loads(request.content)
        if body.get("stream"):
            words = [w + " " for w in ANSWER.split(" ")]
            events = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': w}}]})}\n\n" for w in words)
            return httpx.Response(200, text=events + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"})
        content = json.dumps(PLAN) if body.get("response_format") else "Essai randomisé (n = 391) : pas de non-infériorité."
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture
def fake(monkeypatch):
    model = FakeLLM()
    monkeypatch.setattr(llm, "transport", httpx.MockTransport(model))
    return model


@pytest.fixture
def ai_on(api, fake):
    api.put("/api/v1/settings", json={"llm_enabled": True, "llm_provider": "ollama"})
    return fake


def test_settings_and_status(api, cfg, fake):
    assert api.get("/api/v1/settings").json()["llm_enabled"] is False
    out = api.put("/api/v1/settings", json={"llm_enabled": True, "llm_provider": "mistral", "llm_api_key": "secret"}).json()
    assert out["llm_enabled"] and out["llm_provider"] == "mistral" and out["llm_key_set"]
    assert "secret" not in cfg.path.read_text()  # la clé va au trousseau, jamais dans config.toml
    assert tomllib.loads(cfg.path.read_text())["llm"]["provider"] == "mistral"
    status = api.get("/api/v1/llm").json()
    assert status["reachable"] and status["models"] == ["autre", "qwen3.5:9b"] and status["base_url"] == "https://api.mistral.ai/v1"
    assert fake.calls[-1].headers["authorization"] == "Bearer secret"
    assert api.put("/api/v1/settings", json={"llm_api_key": ""}).json()["llm_key_set"] is False
    api.put("/api/v1/settings", json={"llm_provider": "ollama"})
    api.get("/api/v1/llm")
    assert "authorization" not in fake.calls[-1].headers  # Ollama : pas de clé envoyée


def test_ask_with_ai_query(api, ai_on, network):
    out = api.post("/api/v1/ask", json={"question": "Transmission intrafamiliale des E. coli BLSE ?", "all_journals": True}).json()
    assert out["query_by"] == "ai" and out["query"] == PLAN["pubmed_query"] and out["related"] == PLAN["related_questions"]
    pubmed = next(r for r in network if "esearch" in str(r.url))
    assert pubmed.url.params["term"] == PLAN["pubmed_query"]
    body = json.loads(ai_on.calls[0].content)
    assert body["response_format"] == {"type": "json_object"} and body["reasoning_effort"] == "none"
    assert out["sources"]


def test_ai_failure_falls_back_to_keywords(api, ai_on):
    ai_on.fail = True
    out = api.post("/api/v1/ask", json={"question": "Transmission des BLSE dans le foyer ?", "all_journals": True}).json()
    assert out["query_by"] == "keywords" and out["query"] == "transmission ESBL household" and "modèle en panne" in out["warnings"][0]


def test_answer_stream_and_verification(api, ai_on):
    sources = api.post("/api/v1/ask", json={"question": "household ESBL transmission", "all_journals": True}).json()["sources"]
    sources[0]["abstract"] = "Carriage of ESBL-producing Enterobacterales is frequent in households of carriers. Le portage d'entérobactéries BLSE est fréquent dans les foyers des patients porteurs."
    with api.stream("POST", "/api/v1/ask/answer", json={"question": "Transmission dans le foyer ?", "sources": sources}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert "".join(e.get("delta", "") for e in events).strip() == ANSWER
    final = events[-1]
    assert final["done"] and final["model"] == "qwen3.5:9b"
    assert [s["status"] for s in final["sentences"]] == ["ok", "unsourced", "invalid"]
    assert final["sentences"][0]["cites"] == [1]


def test_answer_stream_error_event(api, ai_on):
    ai_on.fail = True
    with api.stream("POST", "/api/v1/ask/answer", json={"question": "Q ?", "sources": [{"title": "T", "abstract": "A"}]}) as r:
        events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert "modèle en panne" in events[-1]["error"]


def test_verify_details():
    sources = [{"title": "Meropenem trial", "abstract": "Meropenem reduced 30-day mortality compared with piperacillin-tazobactam."},
               {"title": "Autre", "abstract": "Household transmission of ESBL is frequent."}]  # fmt: skip
    out = ai.verify("Meropenem reduced 30-day mortality compared with piperacillin [1, 2]. Household transmission is frequent [1-2].\n"
                    "Oui. Something completely unrelated about astronomy and stars [2].", sources)  # fmt: skip
    assert [(s["cites"], s["status"]) for s in out] == [([1, 2], "ok"), ([1, 2], "ok"), ([], "ok"), ([2], "weak")]


def test_summary_is_cached(api, ai_on):
    rec = {"title": "MERINO", "abstract": "Among 391 patients, piperacillin-tazobactam did not result in noninferior mortality."}
    first = api.post("/api/v1/summary", json={"record": rec, "lang": "fr"}).json()
    assert first == {"text": "Essai randomisé (n = 391) : pas de non-infériorité.", "model": "qwen3.5:9b", "cached": False}
    calls = len(ai_on.calls)
    assert api.post("/api/v1/summary", json={"record": rec, "lang": "fr"}).json()["cached"]
    assert len(ai_on.calls) == calls
    assert api.post("/api/v1/summary", json={"record": {"title": "Sans résumé"}}).status_code == 422


def test_disabled_ai(api, fake):
    assert api.post("/api/v1/summary", json={"record": {"title": "T", "abstract": "A"}}).status_code == 503
    out = api.post("/api/v1/ask", json={"question": "Transmission des BLSE dans le foyer ?", "all_journals": True}).json()
    assert out["query_by"] == "keywords" and fake.calls == []


def test_saved_search_keeps_synthesis(api):
    synthesis = {"text": "Réponse [1].", "sentences": [{"text": "Réponse [1].", "cites": [1], "status": "ok"}], "model": "m"}
    saved = api.post("/api/v1/searches", json={"question": "Q ?", "sources": [{"title": "A"}], "synthesis": synthesis}).json()
    assert api.get(f"/api/v1/searches/{saved['id']}").json()["synthesis"] == synthesis


def test_concept_groups_filter():
    from biblio import question

    q = ('("Enterococcus faecium"[MeSH] OR "E. faecium"[tiab]) AND (VRE[tiab] OR "vancomycin resistance"[tiab]) '
         'AND (dalbavancin[tiab]) NOT (review[pt])')  # fmt: skip
    groups = question.concept_groups(q)
    assert groups == [["enterococcus faecium", "e. faecium"], ["vre", "vancomycin resistance"], ["dalbavancin"]]
    on = {"title": "Dalbavancin against vancomycin-resistant E. faecium isolates", "abstract": "VRE bacteremia."}
    off = {"title": "Removing patients from MRSA isolation", "abstract": "Vancomycin resistance screening."}
    assert question.group_coverage(groups, on) == 1.0 and question.group_coverage(groups, off) == 1 / 3
