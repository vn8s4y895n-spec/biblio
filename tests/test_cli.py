import json
from pathlib import Path

import pytest
from conftest import MERINO_DOI
from typer.testing import CliRunner

from biblio import cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def cli_uses_test_api(api, monkeypatch):
    api.headers["X-Biblio-Client"] = "cli"
    monkeypatch.setattr(cli, "client", lambda: api)


def test_add_get_list(cfg):
    out = runner.invoke(cli.app, ["add", MERINO_DOI, "30208454"])
    assert out.exit_code == 0, out.output
    assert "✓ ajouté : [harris2018effect]" in out.output and "= déjà présent : [harris2018effect]" in out.output

    out = runner.invoke(cli.app, ["get", "harris2018effect"])
    assert out.exit_code == 0
    assert "Harris PNA, Tambyah PA, Lye DC" in out.output and "JAMA 2018;320(10):984-994" in out.output
    assert "type : rct" in out.output and "Bacteremia" in out.output

    work = json.loads(runner.invoke(cli.app, ["get", "harris2018effect", "--json"]).output)
    assert work["identifiers"]["doi"] == MERINO_DOI

    out = runner.invoke(cli.app, ["list"])
    assert "harris2018effect" in out.output and "1 sur 1 notice(s)" in out.output


def test_errors_exit_nonzero(cfg):
    out = runner.invoke(cli.app, ["add", "bonjour"])
    assert out.exit_code == 1 and "Erreur 422" in out.output


def test_token_matches_config(cfg):
    assert runner.invoke(cli.app, ["token"]).output.strip() == cfg.token


def test_journal_commands(cfg):
    out = runner.invoke(cli.app, ["journals"])
    assert out.exit_code == 0 and "Liste en vigueur : 2026-10-01 (3554 revues)" in out.output
    assert "Anomalie du fichier" in out.output
    out = runner.invoke(cli.app, ["journals", "lookup", "Clin Infect Dis"])
    assert "Clinical Infectious Diseases" in out.output and "● revue recommandée" in out.output
    out = runner.invoke(cli.app, ["journals", "lookup", "2050-084X"])
    assert "eLife" in out.output and "n'est plus recommandée" in out.output
    out = runner.invoke(cli.app, ["journals", "update"])
    assert out.exit_code == 1 and "Erreur 502" in out.output


def test_list_shows_and_filters_status(cfg):
    runner.invoke(cli.app, ["add", MERINO_DOI])
    out = runner.invoke(cli.app, ["list"])
    assert out.output.startswith("● harris2018effect")
    out = runner.invoke(cli.app, ["list", "--revue", "not_listed"])
    assert "0 sur 0 notice(s)" in out.output
    out = runner.invoke(cli.app, ["get", "harris2018effect"])
    assert "● revue recommandée   [liste du 2026-10-01]" in out.output


def test_search_command(cfg):
    query = '(ESBL[tiab] OR "extended-spectrum beta-lactamase"[tiab]) AND household[tiab] AND transmission[tiab]'
    out = runner.invoke(cli.app, ["search", query, "--per-source", "5"])
    assert out.exit_code == 0, out.output
    assert "Quantifying within-household transmission" in out.output and "[PS]" in out.output
    assert "Masqués par le filtre des revues" in out.output and "PubMed 5" in out.output
    out = runner.invoke(cli.app, ["search", query, "--per-source", "5", "--all-journals", "--source", "openalex"])
    assert "Antibiotics" in out.output and "○" in out.output
    assert json.loads(runner.invoke(cli.app, ["search", query, "--json", "--per-source", "5"]).output)["total"] > 0


def test_oa_command(cfg):
    out = runner.invoke(cli.app, ["oa", "PMC3244693"])
    assert "europepmc.org/articles/PMC3244693?pdf=render" in out.output


def test_import_find_note_commands(cfg, tmp_path):
    from conftest import MERINO_PDF, make_pdf
    from test_library import RIS

    (tmp_path / "merino.pdf").write_bytes(MERINO_PDF)
    (tmp_path / "biblio.ris").write_text(RIS)
    (tmp_path / "inconnu.pdf").write_bytes(make_pdf(["Household acquisition and transmission of ESBL Enterobacterales study"]))
    out = runner.invoke(cli.app, ["import", str(tmp_path / "merino.pdf"), str(tmp_path / "biblio.ris"), str(tmp_path / "inconnu.pdf")])
    assert out.exit_code == 0, out.output
    assert "merino.pdf : rattaché → [harris2018effect]" in out.output and "(ris) : 2 notices lues, 2 créées" in out.output
    assert "inconnu.pdf : notice non trouvée" in out.output and "biblio attach" in out.output
    assert "1 PDF en attente" in runner.invoke(cli.app, ["files"]).output
    sha = runner.invoke(cli.app, ["files"]).output.split()[0]
    out = runner.invoke(cli.app, ["attach", sha, "harris2018effect"])
    assert "rattaché à [harris2018effect]" in out.output, out.output

    out = runner.invoke(cli.app, ["find", "mortality meropenem"])
    assert "harris2018effect" in out.output and "[PDF p." in out.output
    out = runner.invoke(cli.app, ["note", "harris2018effect", "--set", "Cf. [[martischang2020household]]"])
    assert "Note enregistrée" in out.output and "→ cite : martischang2020household" in out.output
    assert "← citée par : harris2018effect" in runner.invoke(cli.app, ["note", "martischang2020household"]).output
    assert "0 paire(s)" in runner.invoke(cli.app, ["duplicates"]).output
    assert "library/" in runner.invoke(cli.app, ["pdf", "harris2018effect"]).output


def test_pdf_open_falls_back_to_browser(cfg, monkeypatch):
    import httpx

    from biblio.sources import http
    from conftest import fake_sources

    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    monkeypatch.setattr(http, "transport", httpx.MockTransport(
        lambda r: httpx.Response(403) if r.url.host == "europepmc.org" else fake_sources(r)))  # fmt: skip
    runner.invoke(cli.app, ["add", MERINO_DOI])
    out = runner.invoke(cli.app, ["pdf", "harris2018effect", "--open"])
    assert "Aucun PDF récupérable automatiquement" in out.output and opened == [f"https://doi.org/{MERINO_DOI}"]


def test_downloads_settings(tmp_path, monkeypatch):
    from biblio import config

    monkeypatch.setenv("BIBLIO_HOME", str(tmp_path))
    assert config.load().watch_downloads is False
    text = (tmp_path / "config.toml").read_text().replace("watch_downloads = false", "watch_downloads = true")
    (tmp_path / "config.toml").write_text(text.replace('downloads_dir = "~/Downloads"', 'downloads_dir = "~/Desktop/pdf"'))
    loaded = config.load()
    assert loaded.watch_downloads and loaded.downloads_dir == Path.home() / "Desktop" / "pdf"


def test_semantic_commands(cfg):
    from biblio import core, db, semantic
    from test_semantic import FakeEmbedder

    out = runner.invoke(cli.app, ["semantic"])
    assert "Modèle : BAAI/bge-m3" in out.output and "Passages indexés" in out.output
    semantic.set_embedder(FakeEmbedder())
    try:
        conn = db.connect(cfg.db_path)
        core.add_manual(conn, {"title": "Household transmission of ESBL Enterobacterales within families"})
        while semantic.index_batch(conn, semantic.embedder()):
            pass
        out = runner.invoke(cli.app, ["find", "BLSE foyer"])
        assert "household" in out.output.lower() and "≈" in out.output and "plein texte + sens" in out.output
    finally:
        semantic.set_embedder(None)


def test_cite_export_pack(cfg, tmp_path):
    runner.invoke(cli.app, ["add", MERINO_DOI, "--no-pdf"])
    out = runner.invoke(cli.app, ["cite", "harris2018effect"])
    assert out.exit_code == 0 and out.output.startswith("Harris PNA, Tambyah PA")
    assert runner.invoke(cli.app, ["cite", "harris2018effect", "--style", "apa"]).output.startswith("Harris, P. N. A.")
    assert "apa" in runner.invoke(cli.app, ["styles"]).output
    assert "@article{harris2018effect," in runner.invoke(cli.app, ["export"]).output
    target = tmp_path / "out.ris"
    assert runner.invoke(cli.app, ["export", "harris2018effect", "-f", "ris", "-o", str(target)]).exit_code == 0
    assert target.read_text().startswith("TY  - JOUR")
    assert runner.invoke(cli.app, ["export", "-c", "inconnue"]).exit_code == 1
    package = tmp_path / "envoi.biblio"
    out = runner.invoke(cli.app, ["pack", "harris2018effect", "-o", str(package)])
    assert out.exit_code == 0 and package.stat().st_size > 0
    out = runner.invoke(cli.app, ["import", str(package)])
    assert "0 articles ajoutés, 1 déjà présents" in out.output
    assert runner.invoke(cli.app, ["zotero"]).exit_code == 1  # il faut désigner les notices
