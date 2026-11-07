"""Fenêtre de l'application : démarre le serveur au besoin, puis affiche l'interface dans une fenêtre native
(pywebview : WebKit sur Mac, WebView2 sur Windows) ou dans le navigateur.

Un paquet .biblio ouvert par double-clic arrive par la ligne de commande (Windows, `biblio app fichier.biblio`) ou,
sur Mac, par le Finder (événement « ouvrir des documents »), y compris quand la fenêtre est déjà ouverte.
"""

import json
import sys
import threading
import webbrowser
from importlib.resources import files

from . import cli, config


def run(browser: bool = False, paths: list[str] | None = None) -> None:
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
    window = webview.create_window("Biblio", url, width=1440, height=920, min_size=(960, 600))
    pending = list(paths or [])
    loaded = threading.Event()

    def show(new: list[str]) -> None:
        # Hors du fil principal : evaluate_js attend la page, ce qui bloquerait la fenêtre sur Mac.
        for path in new:
            threading.Thread(target=window.evaluate_js, args=(f"window.biblioOpen?.({json.dumps(path)})",), daemon=True).start()

    def on_loaded() -> None:
        loaded.set()
        show(pending)
        pending.clear()

    def on_open(new: list[str]) -> None:
        if loaded.is_set():
            show(new)
        else:
            pending.extend(new)

    window.events.loaded += on_loaded
    if sys.platform == "darwin":
        _accept_finder_documents(on_open)
    webview.start()
    if started_here:  # la fenêtre a lancé le serveur : elle l'arrête en se fermant
        client.post("/api/v1/shutdown")


def _accept_finder_documents(on_open) -> None:
    """Ajoute `application:openFiles:` au délégué d'application de pywebview : le Finder y envoie les fichiers
    ouverts par double-clic (au lancement comme quand Biblio est déjà ouvert)."""
    import AppKit
    import objc
    from webview.platforms.cocoa import BrowserView

    def application_openFiles_(self, app, filenames):
        on_open([str(f) for f in filenames if str(f).lower().endswith(".biblio")])
        app.replyToOpenOrPrint_(AppKit.NSApplicationDelegateReplySuccess)

    objc.classAddMethods(BrowserView.AppDelegate, [
        objc.selector(application_openFiles_, selector=b"application:openFiles:", signature=b"v@:@@")])  # fmt: skip
