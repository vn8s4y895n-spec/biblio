import io
import json
import tarfile
import zipfile

import anyio
import httpx
import pytest
from conftest import MERINO_DOI

from biblio import backup, claude, db, mcp_server, webhooks
from mcp import Client


@pytest.fixture
def mcp(api, monkeypatch):
    """Client MCP branché en mémoire sur le serveur MCP, dont les outils parlent à l'API de test."""
    api.headers["X-Biblio-Client"] = "mcp"
    monkeypatch.setattr(mcp_server, "client", lambda: api)
    monkeypatch.setattr(mcp_server, "_seen", {})

    def call(tool: str, **arguments):
        async def run():
            async with Client(mcp_server.server) as c:
                result = await c.call_tool(tool, arguments)
                assert not result.is_error, result.content[0].text
                return json.loads(result.content[0].text)

        return anyio.run(run)

    return call


def _session(fn):
    async def run():
        async with Client(mcp_server.server) as c:
            return await fn(c)

    return anyio.run(run)


def test_skill_end_to_end(mcp, api, conn):
    """Le parcours du skill : recherche → lecture → synthèse enregistrée, vérifiée et visible dans Biblio."""
    found = mcp("search_online", query='"ESBL"[tiab] AND household[tiab] AND transmission[tiab]',
                question="Transmission intrafamiliale des BLSE ?", all_journals=True, limit=5)  # fmt: skip
    assert found["results"] and all(set(r) <= {"n", "id", "title", "first_author", "journal", "year", "study_type",
                                               "sample_size", "journal_status", "in_library", "retracted", "conclusion"}
                                    for r in found["results"])  # fmt: skip
    first = found["results"][0]
    summary = mcp("get_summary", ref=first["id"])
    assert summary["abstract"].startswith("OBJECTIVES") and summary["sample_size"] == 74
    answer = ("Transmission of ESBL-producing bacteria in households is frequent [1]. "
              "The moon is made of cheese, according to an invented source [2].")
    saved = mcp("save_synthesis", question="Transmission intrafamiliale des BLSE ?", answer=answer,
                sources=[first["id"], found["results"][1]["id"]], query=found["query"], model="Claude Opus")  # fmt: skip
    assert saved["saved"] and [f["status"] for f in saved["flagged"]] == ["weak"]
    row = conn.execute("SELECT origin, synthesis_json, sources_json FROM saved_searches WHERE id = ?", (saved["id"],)).fetchone()
    assert row["origin"] == "claude" and json.loads(row["synthesis_json"])["model"] == "Claude Opus"
    assert json.loads(row["sources_json"])[0]["identifiers"]["pmid"] == "27596534"
    assert api.get("/api/v1/history").json()[0]["question"] == "Transmission intrafamiliale des BLSE ?"


def test_library_tools(mcp, api, conn):
    added = mcp("add_by_identifier", identifier=f"doi:{MERINO_DOI}", question="Pipéracilline-tazobactam et BLSE ?")
    key = added["citekey"]
    assert added["created"] and conn.execute("SELECT client FROM changes WHERE op = 'create'").fetchone()[0] == "mcp"
    assert mcp("tag", refs=[key], tag="à lire")["changed"] == 1
    mcp("add_to_collection", refs=[f"doi:{MERINO_DOI}"], collection="BLSE")
    mcp("add_note", ref=key, text="Essai MERINO : pas de non-infériorité.")
    mcp("add_note", ref=key, text="Deuxième remarque.")
    ref = mcp("get_reference", ref=key)
    assert sorted(ref["labels"]) == ["BLSE", "à lire"] and ref["questions"] == ["Pipéracilline-tazobactam et BLSE ?"]
    assert ref["note"].index("MERINO") < ref["note"].index("Deuxième")
    found = mcp("search_library", query="piperacillin mortality")
    assert found["results"][0]["id"] == key and "<mark>" not in json.dumps(found)
    passages = mcp("get_passages", ref=key, query="mortality")["passages"]
    assert passages and "mortality" in passages[0]["text"].lower()
    biblio = mcp("export_bibliography", collection="BLSE")
    assert biblio["text"].startswith("1. Harris PNA") and "JAMA" in biblio["text"]
    assert "@article{" in mcp("export_bibliography", refs=[key], format="bibtex")["text"]


def test_package_tool(mcp, api, cfg, tmp_path, monkeypatch):
    from dataclasses import replace

    from biblio import config

    monkeypatch.setattr(config, "load", lambda: replace(cfg, downloads_dir=tmp_path))
    key = mcp("add_by_identifier", identifier=MERINO_DOI)["citekey"]
    out = mcp("create_package", refs=[key], title="Pour Marie")
    assert out["path"].startswith(str(tmp_path)) and "Pour Marie" in out["path"]
    assert json.loads(zipfile.ZipFile(out["path"]).read("works.json"))[0]["citekey"] == key


def test_errors_are_tool_errors(mcp, api):
    async def run(c):
        return await c.call_tool("get_reference", {"ref": "inconnu2020"})

    result = _session(run)
    assert result.is_error and "404" in result.content[0].text


def test_resources_prompts_and_listing(mcp, api):
    key = mcp("add_by_identifier", identifier=MERINO_DOI)["citekey"]
    label = api.post("/api/v1/labels", json={"kind": "collection", "name": "BLSE"}).json()
    api.post(f"/api/v1/labels/{label['id']}/works", json={"works": [key]})

    async def run(c):
        tools = {t.name for t in (await c.list_tools()).tools}
        prompts = {p.name for p in (await c.list_prompts()).prompts}
        work = json.loads((await c.read_resource(f"biblio://work/{key}")).contents[0].text)
        coll = json.loads((await c.read_resource(f"biblio://collection/{label['id']}")).contents[0].text)
        prompt = await c.get_prompt("recherche_sourcee", {"question": "BLSE ?"})
        return tools, prompts, work, coll, prompt, c.instructions

    tools, prompts, work, coll, prompt, instructions = _session(run)
    assert {"search_online", "search_library", "get_reference", "get_summary", "get_passages", "add_by_identifier",
            "tag", "add_to_collection", "add_note", "save_synthesis", "export_bibliography", "create_package",
            "watch_alerts"} == tools  # fmt: skip
    assert prompts == {"recherche_sourcee", "revue_rapide", "bibliographie_vancouver", "quoi_de_neuf"}
    assert work["citekey"] == key and coll["works"][0]["in_library"] == key
    assert "save_synthesis" in prompt.messages[0].content.text and "revues recommandées" in instructions


def test_watch_alerts_tool(mcp, conn):
    from biblio import watch

    with db.transaction(conn):
        watch._alert(conn, "retraction", {"notice_doi": "10.1/r", "date": "2020-06-04", "source": "crossref"})
    out = mcp("watch_alerts")
    assert out["unseen"] == 1 and out["alerts"][0]["kind"] == "retraction"


def test_http_transport_requires_token(cfg, api):
    from starlette.testclient import TestClient

    with TestClient(mcp_server.http_app(cfg.token), base_url="http://127.0.0.1:8766") as c:
        assert c.post("/mcp", json={}).status_code == 401
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
                "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}  # fmt: skip
        r = c.post("/mcp", json=init, headers={"Authorization": f"Bearer {cfg.token}", "Accept": "application/json, text/event-stream"})
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "biblio"


def test_claude_packaging(api, cfg):
    connectors = api.get("/api/v1/connectors").json()
    assert connectors["claude_code"].endswith(" mcp") and connectors["claude_code"].startswith("claude mcp add biblio -- ")
    assert connectors["http"]["mcpServers"]["biblio"]["headers"]["Authorization"] == f"Bearer {cfg.token}"
    bundle = zipfile.ZipFile(io.BytesIO(api.get("/api/v1/connectors/claude/biblio.mcpb").content))
    manifest = json.loads(bundle.read("manifest.json"))
    assert manifest["server"]["mcp_config"]["args"] == ["${__dirname}/server/biblio-mcp"]
    assert bundle.read("server/biblio-mcp").decode().endswith(' mcp\n') and cfg.token not in bundle.read("server/biblio-mcp").decode()
    assert len(manifest["tools"]) == 13
    skill = zipfile.ZipFile(io.BytesIO(api.get("/api/v1/connectors/claude/biblio-skill.zip").content)).read("biblio/SKILL.md").decode()
    assert skill.startswith("---\nname: biblio\n") and "save_synthesis" in skill
    assert claude.SKILL == skill


def test_llms_txt_and_openapi(api):
    from fastapi.testclient import TestClient

    text = TestClient(api.app).get("/llms.txt").text  # sans jeton
    assert text.startswith("# Biblio") and "`POST /api/v1/searches`" in text and "`GET /api/v1/connectors`" in text
    spec = api.get("/openapi.json").json()
    missing = [f"{m} {p}" for p, ops in spec["paths"].items() for m, op in ops.items() if not op.get("summary")]
    assert not missing


# --- Webhooks ------------------------------------------------------------------------------------------------


@pytest.fixture
def receiver(monkeypatch):
    received: list[httpx.Request] = []

    def handler(request):
        received.append(request)
        return httpx.Response(500 if "down" in str(request.url) else 204)

    monkeypatch.setattr(webhooks, "transport", httpx.MockTransport(handler))
    return received


def _wait(threads):
    for t in threads:
        t.join(5)


def test_webhooks_signed_and_filtered(api, conn, receiver, monkeypatch):
    threads = []
    original = webhooks.emit
    monkeypatch.setattr(webhooks, "emit", lambda *a: threads.extend(original(*a)) or threads)
    hook = api.post("/api/v1/webhooks", json={"url": "https://example.org/hook", "events": ["search.saved"]}).json()
    api.post("/api/v1/webhooks", json={"url": "https://example.org/other", "events": ["alert.created"]})
    api.post("/api/v1/searches", json={"question": "Q ?", "sources": [{"title": "A"}]})
    _wait(threads)
    assert [str(r.url) for r in receiver] == ["https://example.org/hook"]
    body = receiver[0].content
    assert receiver[0].headers["X-Biblio-Signature"] == webhooks.sign(hook["secret"], body)
    assert json.loads(body)["event"] == "search.saved" and json.loads(body)["data"]["question"] == "Q ?"
    assert api.get("/api/v1/webhooks").json()["items"][0]["last_status"] == 204


def test_webhook_work_created_and_failures(api, conn, receiver, monkeypatch):
    threads = []
    original = webhooks.emit
    monkeypatch.setattr(webhooks, "emit", lambda *a: threads.extend(original(*a)) or threads)
    down = api.post("/api/v1/webhooks", json={"url": "https://down.example.org/", "events": ["work.created"]}).json()
    api.post("/api/v1/works", json={"identifier": MERINO_DOI})
    _wait(threads)
    assert json.loads(receiver[0].content)["data"]["identifiers"]["doi"] == MERINO_DOI
    assert api.get("/api/v1/webhooks").json()["items"][0]["last_error"] == "HTTP 500"
    api.patch(f"/api/v1/webhooks/{down['id']}", json={"active": False})
    assert api.post(f"/api/v1/webhooks/{down['id']}/test").json() == {"status": 500, "error": "HTTP 500"}
    assert api.post("/api/v1/webhooks", json={"url": "ftp://x", "events": ["work.created"]}).status_code == 422
    assert api.post("/api/v1/webhooks", json={"url": "https://x", "events": ["nope"]}).status_code == 422
    assert api.delete(f"/api/v1/webhooks/{down['id']}").status_code == 204


# --- Sauvegarde, restauration, export complet ---------------------------------------------------------------


def test_backup_and_restore(api, cfg):
    key = api.post("/api/v1/works", json={"identifier": MERINO_DOI}).json()["work"]["citekey"]
    api.put(f"/api/v1/works/{key}/note", json={"text": "Note gardée"})
    made = api.post("/api/v1/backups").json()
    assert api.get("/api/v1/backups").json()[0]["name"] == made["name"]
    with tarfile.open(made["path"]) as tar:
        assert {"biblio.sqlite", "config.toml", f"notes/{key}.md", backup.MANIFEST} <= set(tar.getnames())
    assert oct((cfg.home / "backups" / made["name"]).stat().st_mode & 0o777) == "0o600"

    api.put(f"/api/v1/works/{key}/note", json={"text": ""})
    conn = db.connect(cfg.db_path)
    with db.transaction(conn):
        conn.execute("DELETE FROM works")
    conn.close()
    aside = backup.restore(cfg.home / "backups" / made["name"], cfg.home)
    conn = db.connect(cfg.db_path)
    assert conn.execute("SELECT citekey FROM works").fetchone()[0] == key
    conn.close()
    assert (cfg.home / "notes" / f"{key}.md").read_text().strip().endswith("Note gardée")
    assert (aside / "biblio.sqlite").exists()  # anciennes données mises de côté, pas effacées


def test_backup_rejects_foreign_archives(tmp_path):
    bad = tmp_path / "x.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        tar.addfile(tarfile.TarInfo("../evil"), io.BytesIO(b""))
    with pytest.raises(ValueError, match="pas une sauvegarde"):
        backup.check(bad)


def test_daily_backup_keeps_seven(cfg, conn):
    folder = backup.folder(cfg.home)
    folder.mkdir()
    for day in range(1, 10):
        (folder / f"biblio-2026-01-0{day}-120000.tar.gz").write_bytes(b"")
    assert backup.daily(cfg.db_path, cfg.home) is not None
    assert backup.daily(cfg.db_path, cfg.home) is None  # déjà faite aujourd'hui
    names = [b["name"] for b in backup.backups(cfg.home)]
    assert len(names) == 7 and "biblio-2026-01-01-120000.tar.gz" not in names


def test_export_full(api):
    key = api.post("/api/v1/works", json={"identifier": MERINO_DOI}).json()["work"]["citekey"]
    api.put(f"/api/v1/works/{key}/note", json={"text": "Ma note"})
    api.post("/api/v1/searches", json={"question": "Q ?", "sources": [{"title": "A"}]})
    z = zipfile.ZipFile(io.BytesIO(api.post("/api/v1/export/full").content))
    names = set(z.namelist())
    assert {"manifest.json", "works.json", "searches.json", "library.json", "library.bib", "library.ris", f"notes/{key}.md"} <= names
    assert json.loads(z.read("library.json"))[0]["DOI"] == MERINO_DOI
