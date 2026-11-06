"""Client HTTP commun aux sources : identification, délai, erreurs."""

import html
import re

import httpx

from .. import __version__

# Réglés au démarrage par `configure()` ; `transport` est remplacé dans les tests (aucun accès réseau).
contact_email = ""
ncbi_api_key = ""
transport: httpx.BaseTransport | None = None


class SourceError(Exception):
    """Source injoignable ou réponse inattendue."""


def configure(email: str, ncbi_key: str) -> None:
    global contact_email, ncbi_api_key
    contact_email, ncbi_api_key = email, ncbi_key


def get(url: str, params: dict | None = None) -> httpx.Response | None:
    """GET ; renvoie None sur 404, lève SourceError sur toute autre erreur."""
    agent = f"Biblio/{__version__}" + (f" (mailto:{contact_email})" if contact_email else "")
    try:
        with httpx.Client(transport=transport, timeout=20, follow_redirects=True, headers={"User-Agent": agent}) as client:
            response = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise SourceError(f"{url} : {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise SourceError(f"{url} : HTTP {response.status_code}")
    return response


def clean(text: str | None) -> str | None:
    """Retire les balises (HTML, JATS) et normalise les espaces."""
    if not text:
        return None
    text = re.sub(r"<jats:title>.*?</jats:title>", " ", text, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+([,.;:)])", r"\1", re.sub(r"\s+", " ", text)).strip()
    return text or None
