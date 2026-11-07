"""Fenêtre de l'application : démarre le serveur au besoin, puis affiche l'interface dans une fenêtre native
(pywebview : WebKit sur Mac, WebView2 sur Windows) ou dans le navigateur."""

import webbrowser
from importlib.resources import files

from . import cli, config


def run(browser: bool = False) -> None:
    if not (files("biblio") / "web" / "index.html").is_file():
        cli._fail("Interface non compilée : lancer « npm --prefix ui install && npm --prefix ui run build ».")
    cfg = config.load()
    probe = cli.httpx.Client(base_url=cfg.client_url, timeout=2)
    started_here = not cli._is_biblio(probe)
    client = cli.client()  # démarre le serveur s'il ne tourne pas
    # Le jeton passe dans le fragment (#…), qui n'est jamais envoyé au serveur ni écrit dans ses journaux.
    url = f"{cfg.client_url}/#token={cfg.token}"
    if browser:
        webbrowser.open(url)
        return
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True  # exports, paquets .biblio
    webview.create_window("Biblio", url, width=1440, height=920, min_size=(960, 600))
    webview.start()
    if started_here:  # la fenêtre a lancé le serveur : elle l'arrête en se fermant
        client.post("/api/v1/shutdown")
