import json

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
