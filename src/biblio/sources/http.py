"""Client HTTP commun aux sources : identification, limites de débit, cache, erreurs."""

import hashlib
import html
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

import httpx

from .. import __version__, trace

# Réglés au démarrage par `configure()` ; `transport` est remplacé dans les tests (aucun accès réseau).
contact_email = ""
api_keys: dict[str, str] = {}  # ncbi, openalex, semanticscholar
cache_path: Path | None = None
transport: httpx.BaseTransport | None = None

# Délai minimal entre deux requêtes vers un même hôte (secondes), selon les règles de chaque service.
MIN_INTERVAL = {
    "eutils.ncbi.nlm.nih.gov": 0.34,  # 3 requêtes/s sans clé (10 avec)
    "api.semanticscholar.org": 1.0,
    "api.openalex.org": 0.1,
    "api.crossref.org": 0.1,
    "www.ebi.ac.uk": 0.1,
    "api.unpaywall.org": 0.1,
}
_last_request: dict[str, float] = {}
_lock = threading.Lock()
# Après un refus persistant (429), l'hôte est laissé en pause : inutile de ralentir chaque recherche.
COOLDOWN = 600
_paused_until: dict[str, float] = {}


class SourceError(Exception):
    """Source injoignable ou réponse inattendue."""


def configure(email: str, keys: dict[str, str], cache: Path | None = None) -> None:
    global contact_email, api_keys, cache_path
    contact_email, api_keys, cache_path = email, {k: v for k, v in keys.items() if v}, cache


def _wait_turn(host: str) -> None:
    interval = MIN_INTERVAL.get(host, 0)
    if host == "eutils.ncbi.nlm.nih.gov" and api_keys.get("ncbi"):
        interval = 0.1
    with _lock:
        now = time.monotonic()
        start = max(now, _last_request.get(host, 0) + interval)
        _last_request[host] = start
    if start > now:
        time.sleep(start - now)


def _cache_key(url: str, params: dict | None) -> str:
    return hashlib.sha256(json.dumps([url, sorted((params or {}).items())], default=str).encode()).hexdigest()


def _cache_read(key: str, url: str, params: dict | None) -> httpx.Response | None:
    conn = sqlite3.connect(cache_path, timeout=10)
    try:
        row = conn.execute(
            "SELECT status, content_type, body FROM http_cache WHERE key = ? AND expires_at > ?", (key, time.time())
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    headers = {"content-type": row[1]} if row[1] else {}
    return httpx.Response(row[0], content=row[2], headers=headers, request=httpx.Request("GET", url, params=params))


def _cache_write(key: str, url: str, response: httpx.Response, ttl: int) -> None:
    conn = sqlite3.connect(cache_path, timeout=10)
    try:
        with conn:
            conn.execute("DELETE FROM http_cache WHERE expires_at <= ?", (time.time(),))
            conn.execute(
                "INSERT OR REPLACE INTO http_cache (key, url, status, content_type, body, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (key, url, response.status_code, response.headers.get("content-type"), response.content, time.time() + ttl),
            )
    finally:
        conn.close()


def get(url: str, params: dict | None = None, ttl: int | None = None, headers: dict | None = None) -> httpx.Response | None:
    """GET ; renvoie None sur 404, lève SourceError sur toute autre erreur.

    `ttl` (secondes) : réponse mise en cache et resservie tant qu'elle n'a pas expiré.
    Un 429 (trop de requêtes) est réessayé une fois, après le délai demandé par le service (5 s au plus).
    """
    cached = ttl and cache_path
    key = _cache_key(url, params) if cached else ""
    if cached and (hit := _cache_read(key, url, params)):
        trace.step("request", url=trace.public_url(hit.request.url), status=hit.status_code, cached=True)
        return hit
    agent = f"Biblio/{__version__}" + (f" (mailto:{contact_email})" if contact_email else "")
    host = httpx.URL(url).host
    if (until := _paused_until.get(host, 0)) > time.monotonic():
        raise SourceError(f"{host} : en pause encore {round(until - time.monotonic())} s après un refus (HTTP 429)")
    start = time.perf_counter()
    try:
        with httpx.Client(transport=transport, timeout=20, follow_redirects=True, headers={"User-Agent": agent}) as client:
            for attempt in (1, 2):
                _wait_turn(host)
                response = client.get(url, params=params, headers=headers)
                if response.status_code != 429 or attempt == 2:
                    break
                time.sleep(min(float(response.headers.get("retry-after", 1) or 1), 5))
    except httpx.HTTPError as exc:
        trace.step("request", url=trace.public_url(httpx.URL(url, params=params)), error=str(exc) or type(exc).__name__)
        raise SourceError(f"{host} : {exc}") from exc
    trace.step("request", url=trace.public_url(response.request.url), status=response.status_code,
               ms=round((time.perf_counter() - start) * 1000))  # fmt: skip
    if response.status_code == 404:
        return None
    if response.status_code == 429:
        _paused_until[host] = time.monotonic() + COOLDOWN
        raise SourceError(f"{host} : trop de requêtes (HTTP 429). Une clé API facultative relève cette limite.")
    if response.status_code >= 400:
        raise SourceError(f"{host} : HTTP {response.status_code}")
    if cached:
        _cache_write(key, url, response, ttl)
    return response


def _session() -> httpx.Client:
    agent = f"Biblio/{__version__}" + (f" (mailto:{contact_email})" if contact_email else "")
    return httpx.Client(transport=transport, timeout=60, follow_redirects=True, headers={"User-Agent": agent})


def _fetch_bytes(client: httpx.Client, url: str, max_bytes: int, headers: dict | None = None) -> bytes:
    host = httpx.URL(url).host
    _wait_turn(host)
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code >= 400:
            raise SourceError(f"{host} : HTTP {response.status_code}")
        data = bytearray()
        for block in response.iter_bytes():
            data += block
            if len(data) > max_bytes:
                raise SourceError(f"{host} : fichier trop volumineux")
    return bytes(data)


def download(url: str, max_bytes: int) -> bytes:
    """Télécharge un fichier (PDF en accès libre), en refusant au-delà de `max_bytes`."""
    try:
        with _session() as client:
            return _fetch_bytes(client, url, max_bytes)
    except httpx.HTTPError as exc:
        raise SourceError(f"{httpx.URL(url).host} : {exc}") from exc


def meta_contents(page: str, name: str) -> list[str]:
    """Valeurs des balises <meta name|property="name" content="…"> d'une page HTML, dans l'ordre."""
    values = []
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        if re.search(rf"\b(?:name|property)\s*=\s*[\"']{re.escape(name)}[\"']", tag, re.I):
            if m := re.search(r"\bcontent\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", tag, re.I):
                values.append(html.unescape(m.group(1) if m.group(1) is not None else m.group(2)).strip())
    return values


def download_from_page(page_url: str, max_bytes: int) -> tuple[bytes, str]:
    """Comme Zotero : ouvre la page de l'article (par exemple https://doi.org/<DOI>), y lit le lien PDF que
    l'éditeur déclare (`citation_pdf_url`, balise destinée aux moteurs académiques) et le télécharge dans la même
    session (cookies de la page, page d'origine en Referer). Rien n'est contourné : un refus reste un refus.
    Renvoie (contenu, adresse du PDF)."""
    try:
        with _session() as client:
            _wait_turn(httpx.URL(page_url).host)
            page = client.get(page_url)
            if page.status_code >= 400:
                raise SourceError(f"{page.url.host} : HTTP {page.status_code}")
            links = meta_contents(page.text[:3_000_000], "citation_pdf_url")
            if not links:
                raise SourceError(f"{page.url.host} : la page ne déclare pas de PDF")
            pdf_url = str(page.url.join(links[0]))
            return _fetch_bytes(client, pdf_url, max_bytes, headers={"Referer": str(page.url)}), pdf_url
    except httpx.HTTPError as exc:
        raise SourceError(f"{httpx.URL(page_url).host} : {exc}") from exc


def clean(text: str | None) -> str | None:
    """Retire les balises (HTML, JATS, y compris encodées « &lt;i&gt; ») et normalise les espaces."""
    if not text:
        return None
    text = html.unescape(text)
    text = re.sub(r"<jats:title>.*?</jats:title>", " ", text, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+([,.;:)])", r"\1", re.sub(r"\s+", " ", text)).strip()
    return text or None


def split_name(full: str) -> dict:
    """« Patrick N. A. Harris » → family Harris, given Patrick N. A. (sources qui ne séparent pas les noms)."""
    parts = full.strip().rsplit(" ", 1)
    return {"family": parts[-1], "given": parts[0] if len(parts) == 2 else None}
