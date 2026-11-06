"""Unpaywall : version en accès libre (légale) d'un article, par DOI (https://unpaywall.org).
Le service exige une adresse e-mail de contact (`contact_email` dans config.toml)."""

from urllib.parse import quote

from . import http

API = "https://api.unpaywall.org/v2/"
TTL = 7 * 24 * 3600


def find(doi: str) -> dict | None:
    """Meilleure version libre, ou None si l'article n'est pas en accès libre."""
    if not http.contact_email:
        raise http.SourceError("Unpaywall exige une adresse e-mail de contact (contact_email dans config.toml)")
    response = http.get(API + quote(doi, safe="/"), {"email": http.contact_email}, ttl=TTL)
    if response is None:
        return None
    best = response.json().get("best_oa_location") or {}
    if not best:
        return None
    return {
        "pdf_url": best.get("url_for_pdf"),
        "landing_url": best.get("url_for_landing_page") or best.get("url"),
        "license": best.get("license"),
        "version": best.get("version"),
        "host_type": best.get("host_type"),
        "source": "unpaywall",
    }
