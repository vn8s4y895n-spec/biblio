import pytest
from conftest import MERINO_DOI, SORBONNE_PAGE, make_xlsx, small_list, sorbonne_page

from biblio import core, db, journals
from biblio.sources import http

SITE = "https://sante.sorbonne-universite.fr"
NEW_URL = f"{SITE}/sites/default/files/media/2027-01/liste_de_revues_recommandables_cdd_cnu_sante_05-01-27.xlsx"
OLD_URL = f"{SITE}/sites/default/files/media/2026-10/liste_de_revues_recommandables_cdd_cnu_sante_01-10-26.xlsx"


def add_work(conn, **fields) -> int:
    """Notice minimale insérée directement, pour tester le calcul du statut."""
    key = f"k{conn.execute('SELECT count(*) FROM works').fetchone()[0]}"
    fields = {"citekey": key, "title": "t", "fingerprint": "f", **fields}
    cols = ", ".join(fields)
    return conn.execute(f"INSERT INTO works ({cols}) VALUES ({', '.join('?' * len(fields))})", list(fields.values())).lastrowid


def status(conn, work_id) -> str:
    return journals.status_of_work(conn, work_id)["status"]


@pytest.mark.parametrize(
    "raw, expected",
    [("0098-7484", "0098-7484"), ("742-6405", "0742-6405"), ("0003-3944\xa0", "0003-3944"),
     (" 0066-4219", "0066-4219"), ("2050-084x", "2050-084X"), ("00987484", "0098-7484"), ("abc", None), (None, None)],
)  # fmt: skip
def test_normalize_issn(raw, expected):
    assert journals.normalize_issn(raw) == expected


def test_bundled_list_is_parsed():
    from importlib.resources import files

    rows, warnings = journals.parse((files("biblio") / "data" / journals.BUNDLED).read_bytes())
    assert len(rows) == 3554
    # Le fichier du 01-10-26 a sa colonne « ISSN Electronic » décalée d'une ligne à partir de la ligne 2650 :
    # la dernière ligne ne porte plus que l'ISSN électronique de Zygote.
    assert rows[-1] == {**rows[-1], "name": None, "issn_e": "1469-8730", "sheet_row": 3555}
    assert len(warnings) == 2 and "ligne 3555" in warnings[0] and "ligne 2650" in warnings[1], warnings
    jama = next(r for r in rows if r["issn_print"] == "0098-7484")
    assert (jama["name"], jama["issn_e"], jama["nlmid"], jama["withdrawal_reason"]) == ("JAMA", "1538-3598", "7501160", None)
    derm = next(r for r in rows if r["name"] == "A.M.A. Archives of Dermatology")
    assert (derm["withdrawn_on"], derm["withdrawal_reason"], derm["new_name"]) == ("2025-07-01", 1, "Archives of Dermatology")
    elife = next(r for r in rows if r["name"] == "eLife")
    assert elife["withdrawal_reason"] == 2
    assert all(r["issn_print"] is None or len(r["issn_print"]) == 9 for r in rows)


def test_bundled_list_installed_on_setup(conn):
    current = journals.current_list(conn)
    assert (current["published_on"], current["row_count"]) == ("2026-10-01", 3554)
    assert journals.update_if_stale is not None and db.get_meta(conn, "journals.last_check") is None


def test_statuses(conn):
    merino, _, _ = core.add_by_identifier(conn, MERINO_DOI)
    assert merino["journal_status"]["status"] == "recommended"
    assert merino["journal_status"]["journal"]["name"] == "JAMA"
    assert merino["journal_status"]["list_published_on"] == "2026-10-01"
    # Revue renommée (motif 1) : les articles anciens restent dans une revue recommandée.
    assert status(conn, add_work(conn, issn_print="0096-5359")) == "recommended"
    # Motif 2 : n'est plus recommandée.
    elife = add_work(conn, issn_e="2050-084X")
    assert status(conn, elife) == "withdrawn"
    assert journals.status_of_work(conn, elife)["journal"]["withdrawal_reason"] == 2
    assert status(conn, add_work(conn, issn_print="1234-5678")) == "not_listed"
    assert status(conn, add_work(conn, csl_type="article")) == "preprint"
    assert status(conn, add_work(conn)) == "unidentified"
    # Sans ISSN : correspondance par NLMID, puis par abréviation (points ignorés).
    assert status(conn, add_work(conn, nlmid="7501160")) == "recommended"
    assert status(conn, add_work(conn, container_abbrev="J.A.M.A.")) == "recommended"
    assert status(conn, add_work(conn, container_abbrev="Rev. Inexistante")) == "not_listed"
    assert status(conn, add_work(conn, container_abbrev="Clin. Infect. Dis.")) == "recommended"


def test_most_reliable_identifier_wins(conn, cfg):
    # Colonne décalée : l'ISSN électronique de Beta est sur la ligne d'Alpha, Alpha étant retirée (motif 2).
    alpha = ["Alpha", "Alpha", "2000-0001", "2000-0099", "111", "", "", "", "45839", "2"]
    beta = ["Beta", "Beta", "2000-0002", "2000-0001", "222"]
    journals.import_file(conn, small_list(alpha, beta), cfg.home)
    work = add_work(conn, issn_e="2000-0099", nlmid="222")
    found = journals.status_of_work(conn, work)
    assert found["status"] == "recommended" and found["journal"]["name"] == "Beta"
    # Sans NLMID, l'ISSN électronique désigne la ligne d'Alpha : c'est la limite annoncée par l'avertissement.
    assert journals.lookup(conn, issn="2000-0099")["journal"]["name"] == "Alpha"


def test_nameless_row_still_matches(conn):
    found = journals.lookup(conn, issn="1469-8730")  # e-ISSN de Zygote, seul sur la dernière ligne
    assert found["status"] == "recommended" and found["journal"]["name"] is None


def test_lookup_without_library(conn):
    found = journals.lookup(conn, issn="00987484")
    assert found["status"] == "recommended" and found["journal"]["name"] == "JAMA"
    assert journals.lookup(conn, issn="1234-5678")["status"] == "not_listed"
    assert journals.lookup(conn, abbrev="Clin Infect Dis")["journal"]["issn_e"] == "1537-6591"


def test_list_filter_by_status(conn):
    core.add_by_identifier(conn, MERINO_DOI)
    add_work(conn, issn_print="1234-5678")
    assert core.list_works(conn)["total"] == 2
    only = core.list_works(conn, journal_status=["recommended"])
    assert only["total"] == 1 and only["items"][0]["journal_status"] == "recommended"
    assert core.list_works(conn, journal_status=["withdrawn", "not_listed"])["total"] == 1
    with pytest.raises(ValueError):
        core.list_works(conn, journal_status=["inconnu"])


def test_latest_link_and_date():
    page = sorbonne_page(
        OLD_URL.removeprefix(SITE),  # lien relatif
        NEW_URL,
        "https://pirate.example/liste_de_revues_31-12-99.xlsx",
        f"http://sante.sorbonne-universite.fr/liste_de_revues_31-12-30.xlsx",
        f"{SITE}/charte.pdf",
    )
    assert journals.latest_link(page) == NEW_URL
    assert journals.published_from_url(NEW_URL) == "2027-01-05"
    with pytest.raises(journals.ListError):
        journals.latest_link(sorbonne_page("https://pirate.example/revues.xlsx"))


def test_update_follows_new_list(conn, cfg, sorbonne):
    merino, _, _ = core.add_by_identifier(conn, MERINO_DOI)
    sorbonne["page"] = sorbonne_page(OLD_URL, NEW_URL)
    sorbonne["files"][NEW_URL] = small_list()  # nouvelle liste, sans JAMA

    result = journals.update(conn, cfg.home)
    assert result["updated"] and result["current"]["published_on"] == "2027-01-05"
    assert result["last_check"] and result["last_error"] is None
    assert (cfg.home / "journals").is_dir() and len(list((cfg.home / "journals").iterdir())) == 2
    # Le statut suit la liste en vigueur, sans retoucher la notice.
    assert status(conn, merino["id"]) == "not_listed"
    assert len(journals.history(conn)) == 2

    again = journals.update(conn, cfg.home)
    assert not again["updated"] and len(journals.history(conn)) == 2


def test_failed_update_keeps_previous_list(conn, cfg, sorbonne):
    sorbonne["page"] = sorbonne_page(NEW_URL)
    sorbonne["files"][NEW_URL] = make_xlsx([["Titre", "Autre"], ["a", "b"]])
    with pytest.raises(journals.ListError, match="Colonnes attendues absentes"):
        journals.update(conn, cfg.home)
    assert journals.current_list(conn)["published_on"] == "2026-10-01"
    assert "Colonnes attendues absentes" in db.get_meta(conn, "journals.last_error")
    assert db.get_meta(conn, "journals.last_check") is None  # réessai à la prochaine vérification

    sorbonne["files"][NEW_URL] = small_list()
    assert journals.update(conn, cfg.home)["last_error"] is None


@pytest.mark.parametrize(
    "data, message",
    [(b"pas un zip", "pas un .xlsx"), (make_xlsx([["Nom complet"]]), "Colonnes attendues"),
     (small_list(count=10), "anormalement courte")],
)  # fmt: skip
def test_invalid_files_rejected(conn, cfg, data, message):
    with pytest.raises(journals.ListError, match=message):
        journals.import_file(conn, data, cfg.home)
    assert len(journals.history(conn)) == 1


def test_source_errors_are_recorded(conn, cfg):
    with pytest.raises(journals.ListError, match="introuvable"):
        journals.update(conn, cfg.home)  # page absente (404)
    assert "introuvable" in db.get_meta(conn, "journals.last_error")


def test_update_if_stale(conn, cfg, sorbonne, network):
    sorbonne["page"] = sorbonne_page(OLD_URL)
    from importlib.resources import files

    sorbonne["files"][OLD_URL] = (files("biblio") / "data" / journals.BUNDLED).read_bytes()
    assert journals.update_if_stale(conn, cfg.home) == {**journals.info(conn), "updated": False}
    calls = len(network)
    assert journals.update_if_stale(conn, cfg.home) is None  # vérifiée il y a moins de 7 jours
    assert len(network) == calls
    db.set_meta(conn, "journals.last_check", "2020-01-01T00:00:00Z")
    assert journals.update_if_stale(conn, cfg.home) is not None


def test_status_view_is_plain_sql(cfg, conn):
    """La vue doit rester lisible par n'importe quel client SQLite (pérennité des données)."""
    import sqlite3

    core.add_by_identifier(conn, MERINO_DOI)
    raw = sqlite3.connect(cfg.db_path)
    assert raw.execute("SELECT status FROM work_journal_status").fetchall() == [("recommended",)]
    raw.close()


def test_http_source_error_is_recorded(conn, cfg, monkeypatch):
    def down(*_args, **_kwargs):
        raise http.SourceError("réseau coupé")

    monkeypatch.setattr(http, "get", down)
    with pytest.raises(http.SourceError):
        journals.update(conn, cfg.home)
    assert "réseau coupé" in db.get_meta(conn, "journals.last_error")


def test_page_route_constant_matches():
    assert journals.PAGE_URL.endswith(SORBONNE_PAGE)


def test_status_filter_stays_fast(conn):
    """Garde-fou : un OR entre colonnes dans la correspondance faisait 20 s pour 10 000 notices."""
    import time

    issns = [r[0] for r in conn.execute("SELECT issn_print FROM journals WHERE issn_print IS NOT NULL LIMIT 500")]
    conn.executemany(
        "INSERT INTO works (citekey, title, fingerprint, issn_print) VALUES (?, 't', 'f', ?)",
        [(f"perf{i}", issns[i % len(issns)] if i % 3 else None) for i in range(3000)],
    )
    start = time.perf_counter()
    assert core.list_works(conn, journal_status=["recommended"])["total"] == 2000
    assert time.perf_counter() - start < 1.0
