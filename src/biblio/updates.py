"""Nouvelle version de Biblio ? Lit la dernière version publiée sur GitHub (une requête par jour au plus, sans
aucune donnée de la bibliothèque) ; l'installation reste manuelle : télécharger, remplacer l'application."""

import re
import sys

from . import REPOSITORY, __version__
from .sources import http

LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:3])


def _asset(assets: list[dict]) -> str | None:
    suffix = {"darwin": ".dmg", "win32": "-setup.exe"}.get(sys.platform)
    return next((a["browser_download_url"] for a in assets if suffix and a["name"].endswith(suffix)), None)


def check() -> dict:
    """`available` : une version plus récente est publiée ; `download` : son fichier pour ce système."""
    out = {"current": __version__, "latest": None, "available": False, "url": f"https://github.com/{REPOSITORY}/releases"}
    response = http.get(LATEST, ttl=24 * 3600, headers={"Accept": "application/vnd.github+json"})
    if response is None:  # aucune version publiée
        return out
    release = response.json()
    latest = release["tag_name"].lstrip("v")
    return {**out, "latest": latest, "available": _parse(latest) > _parse(__version__), "url": release["html_url"],
            "download": _asset(release.get("assets", [])), "notes": (release.get("body") or "")[:3000]}  # fmt: skip
