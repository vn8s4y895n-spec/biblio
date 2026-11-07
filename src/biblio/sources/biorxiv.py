"""bioRxiv et medRxiv : version publiée d'un preprint (https://api.biorxiv.org). Ces serveurs ne déclarent pas
le lien dans Crossref ; leur API le donne par DOI."""

from . import http

API = "https://api.biorxiv.org/details"
SERVERS = ("medrxiv", "biorxiv")
TTL = 24 * 3600


def published(doi: str) -> str | None:
    """DOI de la version publiée dans une revue, ou None (pas encore publié, ou preprint inconnu)."""
    for server in SERVERS:
        response = http.get(f"{API}/{server}/{doi}", ttl=TTL)
        versions = response.json().get("collection") or [] if response else []
        if versions:
            found = versions[-1].get("published")
            return found.lower() if found and found != "NA" else None
    return None
