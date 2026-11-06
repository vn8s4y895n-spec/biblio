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
